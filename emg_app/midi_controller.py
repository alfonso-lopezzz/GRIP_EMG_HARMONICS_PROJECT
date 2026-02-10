"""MIDI CC helpers for EMG processing front-ends."""

from __future__ import annotations

import time
from typing import List, Optional

from .processing_core import MIDI_VALUE_MAX

try:  # Optional dependency guard for runtime MIDI output
    import mido
    from mido import Message
except Exception:  # pragma: no cover - handled at runtime
    mido = None
    Message = None

MIDI_CC_NUMBER = 1
MIDI_CHANNEL = 1
MIDI_MAX_RATE_HZ = 100.0


class MidiController:
    """Thin wrapper over mido for rate-limited CC output."""

    def __init__(
        self,
        cc_number: int = MIDI_CC_NUMBER,
        channel: int = MIDI_CHANNEL,
        max_rate_hz: float = MIDI_MAX_RATE_HZ,
    ) -> None:
        self.cc_number = cc_number
        self.channel = max(1, min(16, channel)) - 1
        self.max_rate_hz = max_rate_hz
        self._last_send_time = 0.0
        self._last_value = -1
        self.port: Optional[object] = None

    def list_ports(self) -> List[str]:
        self._ensure_mido()
        return list(mido.get_output_names())

    def open(self, name: str) -> None:
        self._ensure_mido()
        if self.port:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = mido.open_output(name)

    def close(self) -> None:
        if self.port:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None

    def send(self, value: int) -> bool:
        if not self.port:
            return False
        value = max(0, min(MIDI_VALUE_MAX, int(value)))
        now = time.time()
        if value == self._last_value:
            return False
        if (now - self._last_send_time) < (1.0 / self.max_rate_hz):
            return False
        self._ensure_mido()
        msg = Message("control_change", control=self.cc_number, value=value, channel=self.channel)
        try:
            self.port.send(msg)
        except Exception:
            return False
        self._last_send_time = now
        self._last_value = value
        return True

    def _ensure_mido(self) -> None:
        if mido is None or Message is None:
            raise RuntimeError(
                "MIDI support requires the 'mido' and 'python-rtmidi' packages. Install them to enable this feature."
            )
