"""Calibration utilities for EMG envelope normalization."""

from __future__ import annotations

import json
import statistics
import time
from typing import Dict, List

from .constants import (
	CAL_BASELINE_SECONDS,
	CAL_LOG_PATH,
	CAL_MVC_SECONDS,
	EMA_ALPHA,
	EPS,
)
from .models import CalibrationParams, DeviceConfig, TargetKey


class CalibrationController:
	"""Owns calibration runs, quality checks, and persistence."""

	def __init__(self, device_manager, event_q, log_path: str = CAL_LOG_PATH):
		self.dev_mgr = device_manager
		self.event_q = event_q
		self.log_path = log_path
		self.calibrations: Dict[TargetKey, CalibrationParams] = {}
		self.mvc_ema: Dict[TargetKey, float] = {}

	def run_calibration(self, device: DeviceConfig, pin: str) -> None:
		tgt = TargetKey(port=device.port, pin=pin)
		muscle = device.channels[pin].muscle or "(unnamed muscle)"

		def set_status(msg: str) -> None:
			self.event_q.put(("cal_status", msg))

		stream = self.dev_mgr.streams.get(device.port)
		if not stream:
			set_status("Calibration status: device stream unavailable.")
			return

		set_status(
			f"Calibration: {tgt.to_str()} baseline — relax {muscle} for {CAL_BASELINE_SECONDS:.1f}s"
		)
		baseline_vals = self._collect_envelope(stream, pin, CAL_BASELINE_SECONDS)

		if len(baseline_vals) < 20:
			set_status("Calibration failed: insufficient baseline data (check stream format / sample rate).")
			return

		baseline = statistics.median(baseline_vals)
		baseline_std = statistics.pstdev(baseline_vals) if len(baseline_vals) > 1 else 0.0
		baseline_stable = baseline_std <= (0.20 * (baseline + 1.0))

		set_status(f"Calibration: {tgt.to_str()} MVC — contract {muscle} MAX for {CAL_MVC_SECONDS:.1f}s")
		mvc_vals = self._collect_envelope(stream, pin, CAL_MVC_SECONDS)

		if len(mvc_vals) < 20:
			set_status("Calibration failed: insufficient MVC data (check stream format / sample rate).")
			return

		if len(mvc_vals) >= 100:
			mvc = statistics.quantiles(mvc_vals, n=100)[94]
		else:
			mvc = sorted(mvc_vals)[int(0.95 * (len(mvc_vals) - 1))]

		separation = (mvc - baseline)
		relative_tolerance = max(0.35 * abs(separation), 0.5)
		stable = baseline_stable or (baseline_std <= relative_tolerance)
		enough_sep = separation > max(3.0 * baseline_std, 1.0)

		if not stable or not enough_sep:
			set_status(
				f"Calibration quality check failed. stable={stable}, sep_ok={enough_sep}. "
				f"(baseline={baseline:.2f}, std={baseline_std:.2f}, mvc={mvc:.2f})"
			)
			return

		params = CalibrationParams(
			baseline=float(baseline),
			mvc=float(mvc),
			ts_unix=time.time(),
			body_part=device.body_part,
			muscle=muscle,
			device_type=device.device_type,
			port=device.port,
			pin=pin,
		)
		self.calibrations[tgt] = params
		self.mvc_ema.pop(tgt, None)
		self._append_log(params)
		set_status(f"Calibration complete: {tgt.to_str()} baseline={baseline:.2f}, mvc={mvc:.2f}")

	def compute_percent_mvc(self, tgt: TargetKey, envelope_value: float) -> float:
		cal = self.calibrations.get(tgt)
		if not cal:
			return 0.0
		numerator = max(0.0, envelope_value - cal.baseline)
		denom = max(EPS, cal.mvc - cal.baseline)
		pct = (numerator / denom) * 100.0
		pct = max(0.0, min(100.0, pct))
		prev = self.mvc_ema.get(tgt, pct)
		smoothed = (EMA_ALPHA * pct) + ((1.0 - EMA_ALPHA) * prev)
		self.mvc_ema[tgt] = smoothed
		return smoothed

	def _collect_envelope(self, stream, pin: str, seconds: float) -> List[float]:
		t_end = time.time() + seconds
		values: List[float] = []
		last_env_ts: float | None = None
		last_raw_ts: float | None = None
		while time.time() < t_end:
			env = stream.envelope[pin]
			if env:
				t_env, v_env = env[-1]
				if t_env != last_env_ts:
					values.append(float(v_env))
					last_env_ts = t_env
			else:
				raw_map = getattr(stream, "raw", None)
				if raw_map:
					raw_buf = raw_map.get(pin)
					if raw_buf:
						t_raw, v_raw = raw_buf[-1]
						if t_raw != last_raw_ts:
							window = [abs(float(val)) for (_, val) in list(raw_buf)[-20:]]
							stat = statistics.fmean(window) if window else abs(float(v_raw))
							values.append(stat)
							last_raw_ts = t_raw
			time.sleep(0.005)
		return values

	def _append_log(self, params: CalibrationParams) -> None:
		try:
			existing = []
			try:
				with open(self.log_path, "r", encoding="utf-8") as file:
					existing = json.load(file).get("calibrations", [])
			except FileNotFoundError:
				existing = []
			existing.append(params.__dict__)
			with open(self.log_path, "w", encoding="utf-8") as file:
				json.dump({"calibrations": existing}, file, indent=2)
		except Exception:
			pass
