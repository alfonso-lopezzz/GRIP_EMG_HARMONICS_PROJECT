"""Shared EMG processing primitives."""

from __future__ import annotations

from collections import deque
from statistics import mean, median
from typing import Deque, Optional, Sequence, Tuple

# Processing defaults
BASELINE_TAU_SEC = 3.0
ENVELOPE_ALPHA = 0.12
MIDI_SMOOTH_ALPHA = 0.2
MIDI_VALUE_MAX = 127
DEFAULT_REST_MIN = 50.0
DEFAULT_MAX_CONTRACTION = 400.0


class EMGProcessor:
    """Processes raw EMG samples into smoothed MIDI-ready activations."""

    def __init__(
        self,
        baseline_tau_sec: float = BASELINE_TAU_SEC,
        envelope_alpha: float = ENVELOPE_ALPHA,
        midi_alpha: float = MIDI_SMOOTH_ALPHA,
        rest_min: float = DEFAULT_REST_MIN,
        max_contraction: float = DEFAULT_MAX_CONTRACTION,
    ) -> None:
        self.baseline_tau = baseline_tau_sec
        self.envelope_alpha = envelope_alpha
        self.midi_alpha = midi_alpha
        self.rest_min = rest_min
        self.max_contraction = max_contraction

        self.baseline = 0.0
        self.baseline_ready = False
        self.prev_t_ms: Optional[int] = None
        self.envelope = 0.0
        self._ema_envelope = 0.0
        self.midi_norm = 0.0
        self.sample_rate_hz = 0.0
        self._dt_window: Deque[int] = deque(maxlen=200)
        self._max_hold_interval_ms = 1000
        self._window_start_ms: Optional[int] = None
        self._window_peak_envelope = 0.0
        self._last_emitted_envelope = 0.0

    def process(self, t_ms: int, raw: float) -> Tuple[float, float, int]:
        """Return (raw_value, envelope_value, midi_cc)."""
        if self.prev_t_ms is not None:
            dt = max(1, t_ms - self.prev_t_ms)
            self._dt_window.append(dt)
            median_dt = float(median(self._dt_window)) if self._dt_window else float(dt)
            if median_dt > 0:
                self.sample_rate_hz = 1000.0 / median_dt
        self.prev_t_ms = t_ms

        alpha = self._compute_alpha(self.baseline_tau)
        if not self.baseline_ready:
            self.baseline = float(raw)
            self.baseline_ready = True
        else:
            self.baseline = (1.0 - alpha) * self.baseline + alpha * float(raw)

        highpassed = float(raw) - self.baseline
        rectified = abs(highpassed)
        self._ema_envelope = (1.0 - self.envelope_alpha) * self._ema_envelope + self.envelope_alpha * rectified
        self.envelope = self._apply_max_hold(t_ms, self._ema_envelope)

        norm = self._normalize(self.envelope)
        self.midi_norm = self._apply_midi_smoothing(norm)
        midi_int = int(round(self.midi_norm * float(MIDI_VALUE_MAX)))
        midi_int = max(0, min(MIDI_VALUE_MAX, midi_int))
        return float(raw), self.envelope, midi_int

    def set_rest(self, samples: Sequence[float]) -> None:
        if samples:
            self.rest_min = float(_percentile(samples, 10.0))

    def set_max(self, samples: Sequence[float]) -> None:
        if samples:
            self.max_contraction = float(_percentile(samples, 90.0))

    def _normalize(self, value: float) -> float:
        lo = min(self.rest_min, self.max_contraction)
        hi = max(self.rest_min, self.max_contraction)
        clamped = min(hi, max(lo, value))
        if hi - lo <= 1e-6:
            return 0.0
        return (clamped - lo) / (hi - lo)

    def _compute_alpha(self, tau: float) -> float:
        if not self._dt_window:
            return 0.01
        mean_dt = mean(self._dt_window) / 1000.0
        if mean_dt <= 0.0:
            return 0.01
        return min(1.0, mean_dt / max(tau, 1e-3))

    def _apply_midi_smoothing(self, norm: float) -> float:
        if norm <= 0.0:
            return 0.0
        if norm >= 1.0:
            return 1.0
        alpha = min(1.0, max(0.0, self.midi_alpha))
        return (1.0 - alpha) * self.midi_norm + alpha * norm

    def _apply_max_hold(self, t_ms: int, envelope: float) -> float:
        """Hold the last 1s peak to reduce rapid graph swings."""
        interval = self._max_hold_interval_ms
        if interval <= 0:
            self._last_emitted_envelope = envelope
            return envelope
        if self._window_start_ms is None:
            self._window_start_ms = t_ms
            self._window_peak_envelope = envelope
            self._last_emitted_envelope = envelope
            return envelope
        if (t_ms - self._window_start_ms) >= interval:
            emitted = self._window_peak_envelope
            self._last_emitted_envelope = emitted
            self._window_start_ms = t_ms
            self._window_peak_envelope = envelope
            return emitted
        self._window_peak_envelope = max(self._window_peak_envelope, envelope)
        return self._last_emitted_envelope



def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    vals = sorted(float(v) for v in values)
    if len(vals) == 1:
        return vals[0]
    clip = min(100.0, max(0.0, pct)) / 100.0
    pos = clip * (len(vals) - 1)
    lo = int(pos)
    hi = min(len(vals) - 1, lo + 1)
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac