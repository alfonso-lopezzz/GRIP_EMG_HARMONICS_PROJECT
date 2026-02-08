"""Processing tab mixin embedding live EMG to MIDI pipeline inside Tk UI."""

from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

from .processing_core import (
    CAPTURE_SECONDS,
    EMGProcessor,
    MidiController,
    RAW_BUFFER_SECONDS,
)
from .ui_plots import LivePlotWidget


class ProcessingTabMixin:
    def _init_processing_state(self) -> None:
        self.processing_target_var = tk.StringVar(value="")
        self.processing_target_combo: ttk.Combobox | None = None
        self.processing_raw_plot: LivePlotWidget | None = None
        self.processing_proc_plot: LivePlotWidget | None = None
        self.processing_rest_lbl: ttk.Label | None = None
        self.processing_max_lbl: ttk.Label | None = None
        self.processing_readout_raw: ttk.Label | None = None
        self.processing_readout_env: ttk.Label | None = None
        self.processing_readout_midi: ttk.Label | None = None
        self.processing_readout_fs: ttk.Label | None = None
        self.processing_midi_combo: ttk.Combobox | None = None
        self.processing_midi_status: ttk.Label | None = None

        self.processing_processors: Dict[str, EMGProcessor] = {}
        self.processing_calibration_versions: Dict[str, float] = {}
        self.processing_last_seen_timestamps: Dict[str, int] = {}
        self.processing_last_output_timestamps: Dict[str, int] = {}
        self.processing_sample_period_ms = 500  # emit twice per second
        self.processing_window_span_ms = 1000  # evaluate past 1 s of envelope data
        self.processing_recent_windows: Dict[str, Deque[Tuple[int, float]]] = {}
        self.processing_window_outputs: Dict[str, float] = {}
        self.processing_window_smoothing_alpha = 0.25
        self.processing_capture_buffers: Dict[str, Deque[float]] = {}
        self.processing_processed_history: Dict[str, Deque[Tuple[int, float]]] = {}

        self.processing_midi = MidiController()

    def _build_processing_tab(self) -> None:
        frame = ttk.Frame(self.tab_processing)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        top = ttk.Frame(frame)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Target Channel:").pack(side="left")
        self.processing_target_combo = ttk.Combobox(
            top,
            textvariable=self.processing_target_var,
            width=50,
            state="readonly",
        )
        self.processing_target_combo.pack(side="left", padx=6)
        ttk.Button(top, text="Refresh", command=self._refresh_processing_targets).pack(side="left")

        midi_frame = ttk.Frame(frame)
        midi_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(midi_frame, text="MIDI Output:").pack(side="left")
        self.processing_midi_combo = ttk.Combobox(midi_frame, width=40, state="readonly")
        self.processing_midi_combo.pack(side="left", padx=6)
        ttk.Button(midi_frame, text="Refresh", command=self._refresh_midi_ports).pack(side="left")
        ttk.Button(midi_frame, text="Connect", command=self._connect_midi_port).pack(side="left", padx=4)
        self.processing_midi_status = ttk.Label(midi_frame, text="MIDI: disconnected")
        self.processing_midi_status.pack(side="left", padx=8)

        calib_frame = ttk.Frame(frame)
        calib_frame.pack(fill="x", pady=(0, 8))
        ttk.Button(calib_frame, text="Set Rest (10th %)", command=self._processing_set_rest).pack(side="left")
        ttk.Button(calib_frame, text="Set Max (90th %)", command=self._processing_set_max).pack(side="left", padx=4)
        ttk.Label(calib_frame, text="Rest:").pack(side="left", padx=(16, 4))
        self.processing_rest_lbl = ttk.Label(calib_frame, text="-")
        self.processing_rest_lbl.pack(side="left")
        ttk.Label(calib_frame, text="Max:").pack(side="left", padx=(16, 4))
        self.processing_max_lbl = ttk.Label(calib_frame, text="-")
        self.processing_max_lbl.pack(side="left")

        plots = ttk.Frame(frame)
        plots.pack(fill="both", expand=True)
        raw_frame = ttk.Labelframe(plots, text="Raw A0 (0-1023)")
        raw_frame.pack(side="left", fill="both", expand=True, padx=(0, 8))
        proc_frame = ttk.Labelframe(plots, text="Processed / MIDI (0-127)")
        proc_frame.pack(side="left", fill="both", expand=True)
        self.processing_raw_plot = LivePlotWidget(raw_frame, title="Raw Signal", window_seconds=RAW_BUFFER_SECONDS)
        self.processing_proc_plot = LivePlotWidget(proc_frame, title="Processed Signal", window_seconds=RAW_BUFFER_SECONDS)

        readouts = ttk.Frame(frame)
        readouts.pack(fill="x", pady=(6, 0))
        self.processing_readout_raw = ttk.Label(readouts, text="Raw: -")
        self.processing_readout_raw.pack(side="left", padx=6)
        self.processing_readout_env = ttk.Label(readouts, text="Envelope: -")
        self.processing_readout_env.pack(side="left", padx=6)
        self.processing_readout_midi = ttk.Label(readouts, text="MIDI: -")
        self.processing_readout_midi.pack(side="left", padx=6)
        self.processing_readout_fs = ttk.Label(readouts, text="Fs: - Hz")
        self.processing_readout_fs.pack(side="left", padx=6)

        self._refresh_processing_targets()
        self._refresh_midi_ports()
        self.after(120, self._update_processing_tab)

    # -------------------------- Target helpers --------------------------
    def _refresh_processing_targets(self) -> None:
        values: List[str] = []
        for device in getattr(self, "device_configs", []):
            for pin, channel in device.channels.items():
                if channel.enabled:
                    label = f"{device.port} | {pin} | {channel.muscle or '(unnamed muscle)'}"
                    values.append(label)
        if self.processing_target_combo:
            self.processing_target_combo["values"] = values
        if values and not self.processing_target_var.get():
            self.processing_target_var.set(values[0])

    def _parse_processing_target(self) -> Optional[Tuple[str, str, str]]:
        selection = self.processing_target_var.get().strip()
        if not selection:
            return None
        parts = [p.strip() for p in selection.split("|")]
        if len(parts) < 2:
            return None
        port, pin = parts[0], parts[1]
        muscle = parts[2] if len(parts) > 2 else ""
        return port, pin, muscle

    # ----------------------------- MIDI ---------------------------------
    def _refresh_midi_ports(self) -> None:
        if not self.processing_midi_combo:
            return
        try:
            ports = self.processing_midi.list_ports()
        except Exception as exc:  # Missing dependency or backend issue
            ports = []
            if self.processing_midi_status:
                self.processing_midi_status.config(text=str(exc))
        current = self.processing_midi_combo.get()
        self.processing_midi_combo["values"] = ports
        if current in ports:
            self.processing_midi_combo.set(current)
        elif ports:
            self.processing_midi_combo.current(0)

    def _connect_midi_port(self) -> None:
        if not self.processing_midi_combo:
            return
        name = self.processing_midi_combo.get()
        if not name:
            return
        try:
            self.processing_midi.open(name)
            if self.processing_midi_status:
                self.processing_midi_status.config(text=f"MIDI: {name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    def _send_processing_midi(self, value: int) -> None:
        try:
            self.processing_midi.send(value)
        except Exception:
            pass

    # -------------------------- Calibration -----------------------------
    def _processing_set_rest(self) -> None:
        key = self._processing_key()
        if not key:
            messagebox.showinfo("Processing", "Select a target channel first.")
            return
        samples = list(self.processing_capture_buffers.get(key, []))
        if not samples:
            messagebox.showinfo("Processing", "No samples captured yet.")
            return
        processor = self.processing_processors.setdefault(key, EMGProcessor())
        processor.set_rest(samples)
        if self.processing_rest_lbl:
            self.processing_rest_lbl.config(text=f"{processor.rest_min:.1f}")

    def _processing_set_max(self) -> None:
        key = self._processing_key()
        if not key:
            messagebox.showinfo("Processing", "Select a target channel first.")
            return
        samples = list(self.processing_capture_buffers.get(key, []))
        if not samples:
            messagebox.showinfo("Processing", "No samples captured yet.")
            return
        processor = self.processing_processors.setdefault(key, EMGProcessor())
        processor.set_max(samples)
        if self.processing_max_lbl:
            self.processing_max_lbl.config(text=f"{processor.max_contraction:.1f}")

    # ---------------------------- Updates -------------------------------
    def _processing_key(self) -> Optional[str]:
        parsed = self._parse_processing_target()
        if not parsed:
            return None
        port, pin, _ = parsed
        return f"{port}|{pin}"

    def _update_processing_tab(self) -> None:
        parsed = self._parse_processing_target()
        if not parsed:
            self.after(150, self._update_processing_tab)
            return
        port, pin, muscle = parsed
        stream = self.dev_mgr.streams.get(port)
        if not stream:
            self.after(150, self._update_processing_tab)
            return
        buf = stream.raw.get(pin)
        if not buf:
            self.after(150, self._update_processing_tab)
            return

        key = f"{port}|{pin}"
        processor = self.processing_processors.setdefault(key, EMGProcessor())
        self._maybe_apply_processing_calibration(key, port, pin, processor)
        capture_buf = self.processing_capture_buffers.setdefault(key, deque(maxlen=int(CAPTURE_SECONDS * 2000)))
        processed_hist = self.processing_processed_history.setdefault(key, deque(maxlen=int(RAW_BUFFER_SECONDS * 2000)))
        env_stream = stream.envelope.get(pin)
        window_buf = self.processing_recent_windows.setdefault(key, deque())

        data = list(buf)
        last_seen = self.processing_last_seen_timestamps.get(key)
        start_idx = 0
        if last_seen is not None:
            for idx, (sample_t, _) in enumerate(data):
                if sample_t > last_seen:
                    start_idx = idx
                    break
            else:
                start_idx = len(data)
        new_samples = data[start_idx:]
        if new_samples:
            self.processing_last_seen_timestamps[key] = int(new_samples[-1][0])

        last_raw = None
        last_env = None
        last_midi = None
        last_output = self.processing_last_output_timestamps.get(key)
        output_updated = False
        interval_ms = getattr(self, "processing_sample_period_ms", 0)
        window_span = getattr(self, "processing_window_span_ms", interval_ms or 100)
        for t_ms, raw_val in new_samples:
            raw_v, env, midi_cc = processor.process(int(t_ms), float(raw_val))
            capture_buf.append(env)
            window_buf.append((t_ms, float(env)))
            cutoff = t_ms - window_span
            while window_buf and window_buf[0][0] < cutoff:
                window_buf.popleft()
            last_raw = raw_v
            last_env = env

            should_emit = True
            if interval_ms and last_output is not None:
                should_emit = (t_ms - last_output) >= interval_ms
            if should_emit:
                emit_value = self._windowed_midi_value(
                    key,
                    processor,
                    window_buf,
                    env_stream,
                    latest_t=t_ms,
                    window_span_ms=window_span,
                )
                processed_hist.append((t_ms, emit_value))
                last_midi = emit_value
                self._send_processing_midi(emit_value)
                last_output = int(t_ms)
                output_updated = True
        if last_env is not None and self.processing_readout_env:
            self.processing_readout_env.config(text=f"Envelope: {last_env:.2f}")
        if last_midi is not None and self.processing_readout_midi:
            self.processing_readout_midi.config(text=f"MIDI: {last_midi:3d}")
        if self.processing_readout_fs and processor.sample_rate_hz > 0:
            self.processing_readout_fs.config(text=f"Fs: {processor.sample_rate_hz:.1f} Hz")
        if self.processing_rest_lbl:
            self.processing_rest_lbl.config(text=f"{processor.rest_min:.1f}")
        if self.processing_max_lbl:
            self.processing_max_lbl.config(text=f"{processor.max_contraction:.1f}")

        self._update_processing_plots(port, pin, muscle, processed_hist)
        self.after(120, self._update_processing_tab)

    def _update_processing_plots(self, port: str, pin: str, muscle: str, processed_hist: Deque[Tuple[int, float]]) -> None:
        calibration_params = self._get_processing_calibration(port, pin)
        raw_y_limits = None
        if calibration_params:
            y_max = max(1.0, calibration_params.mvc + 1.0)
            raw_y_limits = (0.0, y_max)

        if self.processing_raw_plot:
            times, values = self._gather_plot_points(port, pin, window_seconds=RAW_BUFFER_SECONDS)
            subtitle = f"{port} {pin} ({muscle or 'muscle'})"
            self.processing_raw_plot.update(times, values, subtitle, y_limits=raw_y_limits)
        if self.processing_proc_plot:
            times, values = self._convert_history(processed_hist, window_seconds=RAW_BUFFER_SECONDS)
            subtitle = f"{port} {pin} MIDI"
            self.processing_proc_plot.update(times, values, subtitle, y_limits=(0.0, 127.0))

    def _convert_history(self, history: Deque[Tuple[int, float]], window_seconds: float) -> Tuple[List[float], List[float]]:
        if not history:
            return [], []
        data = list(history)
        latest = data[-1][0]
        window_ms = int(window_seconds * 1000)
        filtered = [(t, v) for (t, v) in data if t >= latest - window_ms]
        if not filtered:
            return [], []
        base = filtered[0][0]
        times = [(t - base) / 1000.0 for t, _ in filtered]
        values = [v for _, v in filtered]
        return times, values

    def _windowed_midi_value(
        self,
        key: str,
        processor: EMGProcessor,
        window_buf: Deque[Tuple[int, float]],
        env_stream: Optional[Deque[Tuple[int, float]]],
        latest_t: int,
        window_span_ms: int,
    ) -> int:
        window_vals = [env for _, env in window_buf]
        if not window_vals and env_stream:
            cutoff = latest_t - window_span_ms
            snapshot = list(env_stream)
            window_vals = [float(val) for (t, val) in snapshot if t >= cutoff]
        if not window_vals:
            window_vals = [float(processor.envelope)]

        env_peak = max(window_vals)
        env_rest = processor.rest_min

        if env_peak >= processor.max_contraction:
            target = 127
        elif env_peak <= env_rest:
            target = 0
        else:
            norm = processor._normalize(env_peak)
            target = int(round(max(0.0, min(1.0, norm)) * 127.0))

        prev = self.processing_window_outputs.get(key, float(target))
        if target in (0, 127):
            smoothed = float(target)
        else:
            alpha = max(0.0, min(1.0, getattr(self, "processing_window_smoothing_alpha", 1.0)))
            smoothed = (alpha * float(target)) + ((1.0 - alpha) * prev)
        self.processing_window_outputs[key] = smoothed
        return int(round(smoothed))

    # ------------------------ Calibration sync ------------------------
    def _maybe_apply_processing_calibration(
        self,
        key: str,
        port: str,
        pin: str,
        processor: EMGProcessor,
    ) -> None:
        params = self._get_processing_calibration(port, pin)
        if not params:
            return
        last_applied = self.processing_calibration_versions.get(key)
        if last_applied == params.ts_unix and processor.rest_min == params.baseline and processor.max_contraction == params.mvc:
            return
        processor.rest_min = params.baseline
        processor.max_contraction = params.mvc
        self.processing_calibration_versions[key] = params.ts_unix
        if self.processing_rest_lbl:
            self.processing_rest_lbl.config(text=f"{processor.rest_min:.1f}")
        if self.processing_max_lbl:
            self.processing_max_lbl.config(text=f"{processor.max_contraction:.1f}")

    def _get_processing_calibration(self, port: str, pin: str):
        calibration_ctrl = getattr(self, "calibration", None)
        if not calibration_ctrl:
            return None
        for tgt, cal in calibration_ctrl.calibrations.items():
            if tgt.port == port and tgt.pin == pin:
                return cal
        return None
