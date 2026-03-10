"""Serial and BLE helpers that communicate with the EMG hardware."""

from __future__ import annotations

import asyncio
import math
import queue
import statistics
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import serial

from .constants import (
	BLE_UART_SERVICE_UUID,
	BLE_UART_TX_CHAR_UUID,
	DEFAULT_VREF_VOLTS,
	PINS,
	RMS_WINDOW_SECONDS,
)

try:
	import bleak
	from bleak import BleakClient, BleakScanner
	HAS_BLEAK = True
except ImportError:  # pragma: no cover
	HAS_BLEAK = False
	bleak = None  # type: ignore[assignment]
	BleakClient = None  # type: ignore[assignment,misc]
	BleakScanner = None  # type: ignore[assignment,misc]

STREAM_MAXLEN = 20000  # generous cushion for 10+ seconds at high Fs
from .models import DeviceConfig


def compute_rms(values: List[float]) -> float:
	if not values:
		return 0.0
	return math.sqrt(sum(v * v for v in values) / len(values))


PIN_INDEX = {pin: idx for idx, pin in enumerate(PINS)}


class EMGStreamState:
	"""Holds rolling buffers for raw samples and RMS envelopes."""

	def __init__(self, maxlen: int = STREAM_MAXLEN):
		self.maxlen = maxlen
		self.raw: Dict[str, Deque[Tuple[int, float]]] = {
			p: deque(maxlen=maxlen) for p in PINS
		}
		self.envelope: Dict[str, Deque[Tuple[int, float]]] = {
			p: deque(maxlen=maxlen) for p in PINS
		}
		self.fs_est_hz: float = 0.0
		self._last_t_ms: Optional[int] = None
		self._dt_ms_window: Deque[int] = deque(maxlen=200)

	def update_fs_estimate(self, t_ms: int) -> None:
		if self._last_t_ms is not None:
			dt = t_ms - self._last_t_ms
			if dt > 0:
				self._dt_ms_window.append(dt)
				med_dt = statistics.median(self._dt_ms_window) if self._dt_ms_window else dt
				self.fs_est_hz = 1000.0 / med_dt
		self._last_t_ms = t_ms


class _PayloadParserMixin:
	"""Shared CSV / single-value parsing logic for Serial and BLE workers."""

	stream_state: EMGStreamState
	event_q: queue.Queue
	_active_pins: List[str]

	def _handle_csv_payload(self, parts: List[str]) -> Optional[int]:
		if len(parts) < 2:
			return None
		try:
			t_ms = int(parts[0])
		except ValueError:
			return None
		self.stream_state.update_fs_estimate(t_ms)
		updated_pins: List[str] = []
		for pin in self._active_pins:
			idx = 1 + PIN_INDEX[pin]
			if idx >= len(parts):
				continue
			try:
				value = float(parts[idx])
			except ValueError:
				continue
			self.stream_state.raw[pin].append((t_ms, value))
			updated_pins.append(pin)
		if not updated_pins:
			return None
		self._update_envelopes(t_ms, updated_pins)
		return t_ms

	def _handle_single_value_payload(self, payload: str) -> Optional[int]:
		value = self._parse_single_value(payload)
		if value is None:
			return None
		t_ms = int(time.time() * 1000)
		self.stream_state.update_fs_estimate(t_ms)
		for pin in self._active_pins:
			self.stream_state.raw[pin].append((t_ms, value))
		self._update_envelopes(t_ms, self._active_pins)
		return t_ms

	@staticmethod
	def _parse_single_value(payload: str) -> Optional[float]:
		cleaned = payload.strip()
		if not cleaned:
			return None
		try:
			value = float(cleaned)
		except ValueError:
			return None
		if ("." not in cleaned) and ("e" not in cleaned.lower()) and 0.0 <= value <= 1023.0:
			return value * (DEFAULT_VREF_VOLTS / 1023.0)
		return value

	def _update_envelopes(self, t_ms: int, pins: List[str]) -> None:
		fs = self.stream_state.fs_est_hz
		if fs <= 0:
			return
		win_n = max(5, int(fs * RMS_WINDOW_SECONDS))
		for pin in pins:
			buf = self.stream_state.raw[pin]
			if len(buf) < win_n:
				continue
			tail = list(buf)[-win_n:]
			rect = [abs(float(v)) for (_, v) in tail]
			rms = compute_rms(rect)
			self.stream_state.envelope[pin].append((t_ms, rms))

	def _parse_line(self, line_text: str, port: str) -> None:
		"""Parse a single text line and push results into stream state."""
		s = line_text.strip()
		if not s:
			return
		if s.lower().startswith("t_ms"):
			return
		parts = s.split(",") if "," in s else None
		if parts:
			t_ms = self._handle_csv_payload(parts)
		else:
			t_ms = self._handle_single_value_payload(s)
		if t_ms is not None:
			self.event_q.put(("sample", port, t_ms))


class SerialDeviceWorker(_PayloadParserMixin, threading.Thread):
	"""Reads CSV lines from serial and updates the shared stream state."""

	def __init__(self, cfg: DeviceConfig, stream_state: EMGStreamState, event_q: queue.Queue):
		threading.Thread.__init__(self, daemon=True)
		self.cfg = cfg
		self.stream_state = stream_state
		self.event_q = event_q
		self._stop = threading.Event()
		self._ser: Optional[serial.Serial] = None
		self._active_pins = [pin for pin, ch in cfg.channels.items() if ch.enabled]
		if not self._active_pins:
			self._active_pins = [PINS[0]]

	def stop(self) -> None:
		self._stop.set()
		try:
			if self._ser and self._ser.is_open:
				self._ser.close()
		except Exception:
			pass

	def run(self) -> None:  # noqa: D401 (thread loop)
		"""Main thread loop."""

		port = self.cfg.port
		try:
			self._ser = serial.Serial(port, self.cfg.baud, timeout=1)
			time.sleep(2.0)
			try:
				self._ser.reset_input_buffer()
			except Exception:
				pass
			self.event_q.put(("device_status", port, True, "connected"))
		except Exception as exc:
			self.event_q.put(("device_status", port, False, f"open error: {exc}"))
			return

		while not self._stop.is_set():
			try:
				line = self._ser.readline()
				if not line:
					continue
				s = line.decode("utf-8", errors="replace").strip()
				self._parse_line(s, port)
			except Exception as exc:  # assume serial failure
				self.event_q.put(("device_status", port, False, f"read error: {exc}"))
				break

		try:
			if self._ser and self._ser.is_open:
				self._ser.close()
		except Exception:
			pass
		self.event_q.put(("device_status", port, False, "disconnected"))


class BLEDeviceWorker(_PayloadParserMixin, threading.Thread):
	"""Connects to a BLE UART peripheral and streams data into EMGStreamState."""

	def __init__(self, cfg: DeviceConfig, stream_state: EMGStreamState, event_q: queue.Queue):
		threading.Thread.__init__(self, daemon=True)
		self.cfg = cfg
		self.stream_state = stream_state
		self.event_q = event_q
		self._stop_event = threading.Event()
		self._active_pins = [pin for pin, ch in cfg.channels.items() if ch.enabled]
		if not self._active_pins:
			self._active_pins = [PINS[0]]
		self._line_buffer = ""

	def stop(self) -> None:
		self._stop_event.set()

	def run(self) -> None:
		"""Run BLE client inside a dedicated asyncio event loop."""
		if not HAS_BLEAK:
			self.event_q.put((
				"device_status", self.cfg.port, False,
				"BLE requires the 'bleak' package. Install it to enable BLE support.",
			))
			return
		try:
			loop = asyncio.new_event_loop()
			loop.run_until_complete(self._ble_main())
		except Exception as exc:
			self.event_q.put(("device_status", self.cfg.port, False, f"BLE error: {exc}"))
		finally:
			self.event_q.put(("device_status", self.cfg.port, False, "disconnected"))

	async def _ble_main(self) -> None:
		address = self.cfg.port
		tx_char_uuid = BLE_UART_TX_CHAR_UUID

		self.event_q.put(("device_status", address, False, f"BLE connecting to {address}..."))
		try:
			client = BleakClient(address)
			await client.connect(timeout=10.0)
		except Exception as exc:
			self.event_q.put(("device_status", address, False, f"BLE connect failed: {exc}"))
			return

		if not client.is_connected:
			self.event_q.put(("device_status", address, False, "BLE connection failed"))
			return

		self.event_q.put(("device_status", address, True, f"BLE connected: {address}"))

		def _on_notify(_sender, data: bytearray) -> None:
			try:
				text = data.decode("utf-8", errors="replace")
			except Exception:
				return
			self._line_buffer += text
			while "\n" in self._line_buffer:
				line, self._line_buffer = self._line_buffer.split("\n", 1)
				self._parse_line(line, address)

		try:
			await client.start_notify(tx_char_uuid, _on_notify)
		except Exception as exc:
			self.event_q.put(("device_status", address, False, f"BLE notify error: {exc}"))
			await client.disconnect()
			return

		# Stay alive until stop requested
		while not self._stop_event.is_set():
			await asyncio.sleep(0.1)
			if not client.is_connected:
				self.event_q.put(("device_status", address, False, "BLE device disconnected"))
				break

		try:
			await client.stop_notify(tx_char_uuid)
		except Exception:
			pass
		try:
			await client.disconnect()
		except Exception:
			pass


class DeviceManager:
	"""Tracks workers per serial port / BLE address and exposes stream states."""

	def __init__(self):
		self.streams: Dict[str, EMGStreamState] = {}
		self.workers: Dict[str, threading.Thread] = {}  # SerialDeviceWorker | BLEDeviceWorker
		self.status: Dict[str, Tuple[bool, str]] = {}

	def start_device(self, cfg: DeviceConfig, event_q: queue.Queue) -> None:
		if cfg.port in self.workers:
			return
		stream = EMGStreamState(maxlen=STREAM_MAXLEN)
		self.streams[cfg.port] = stream

		conn = getattr(cfg, "connection_type", "Serial (USB/Wired)")
		if conn == "Bluetooth LE (BLE)":
			worker: threading.Thread = BLEDeviceWorker(cfg=cfg, stream_state=stream, event_q=event_q)
		else:
			# Serial (USB/Wired) and Bluetooth Classic (SPP) both use COM ports
			worker = SerialDeviceWorker(cfg=cfg, stream_state=stream, event_q=event_q)

		self.workers[cfg.port] = worker
		self.status[cfg.port] = (False, "starting")
		worker.start()

	def stop_device(self, port: str) -> None:
		worker = self.workers.get(port)
		if worker:
			worker.stop()
		self.workers.pop(port, None)
		self.streams.pop(port, None)
		self.status.pop(port, None)

	def stop_all(self) -> None:
		for port in list(self.workers.keys()):
			self.stop_device(port)
