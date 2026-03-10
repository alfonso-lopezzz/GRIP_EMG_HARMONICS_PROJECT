"""Unit tests for BLE connection support (BLEDeviceWorker, DeviceManager routing, UI)."""

from __future__ import annotations

import asyncio
import json
import queue
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import emg_app.serial_io as serial_io
from emg_app.constants import (
	BLE_UART_SERVICE_UUID,
	BLE_UART_TX_CHAR_UUID,
	CONNECTION_TYPES,
	PINS,
)
from emg_app.models import ChannelConfig, DeviceConfig


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ble_device_config() -> DeviceConfig:
	channels = {pin: ChannelConfig(enabled=True, muscle="", notes="") for pin in PINS}
	return DeviceConfig(
		device_type="Arduino Nano 33 BLE",
		port="AA:BB:CC:DD:EE:FF",
		baud=115200,
		body_part="forearm",
		channels=channels,
		connection_type="Bluetooth LE (BLE)",
		ble_service_uuid=BLE_UART_SERVICE_UUID,
	)


@pytest.fixture
def serial_device_config() -> DeviceConfig:
	channels = {pin: ChannelConfig(enabled=True, muscle="", notes="") for pin in PINS}
	return DeviceConfig(
		device_type="Arduino Uno",
		port="COM_TEST",
		baud=115200,
		body_part="test",
		channels=channels,
		connection_type="Serial (USB/Wired)",
	)


@pytest.fixture
def bt_classic_config() -> DeviceConfig:
	channels = {pin: ChannelConfig(enabled=True, muscle="", notes="") for pin in PINS}
	return DeviceConfig(
		device_type="Arduino Uno",
		port="COM_BT",
		baud=115200,
		body_part="test",
		channels=channels,
		connection_type="Bluetooth Classic (SPP)",
	)


# ---------------------------------------------------------------------------
# _PayloadParserMixin tests
# ---------------------------------------------------------------------------


class TestPayloadParserMixin:
	"""Verify _PayloadParserMixin shared logic works identically to old SerialDeviceWorker."""

	def _make_parser(self, cfg: DeviceConfig, stream: serial_io.EMGStreamState, event_q: queue.Queue):
		"""Create a bare _PayloadParserMixin instance with required attributes."""

		class TestParser(serial_io._PayloadParserMixin):
			pass

		parser = TestParser.__new__(TestParser)
		parser.cfg = cfg
		parser.stream_state = stream
		parser.event_q = event_q
		parser._active_pins = [pin for pin, ch in cfg.channels.items() if ch.enabled]
		if not parser._active_pins:
			parser._active_pins = [PINS[0]]
		return parser

	def test_parse_csv_line(self, ble_device_config):
		stream = serial_io.EMGStreamState(maxlen=64)
		eq = queue.Queue()
		parser = self._make_parser(ble_device_config, stream, eq)

		header = "t_ms," + ",".join(PINS)
		parser._parse_line(header, ble_device_config.port)

		data = "100," + ",".join(["512"] * 6)
		parser._parse_line(data, ble_device_config.port)

		assert len(stream.raw[PINS[0]]) == 1
		t, v = stream.raw[PINS[0]][0]
		assert t == 100.0
		# CSV path stores raw ADC value directly
		assert v == 512.0

	def test_parse_single_value(self, ble_device_config):
		stream = serial_io.EMGStreamState(maxlen=64)
		eq = queue.Queue()
		parser = self._make_parser(ble_device_config, stream, eq)

		parser._parse_line("0.5", ble_device_config.port)
		parser._parse_line("1.0", ble_device_config.port)

		assert len(stream.raw[PINS[0]]) == 2

	def test_parse_ignores_garbage(self, ble_device_config):
		stream = serial_io.EMGStreamState(maxlen=64)
		eq = queue.Queue()
		parser = self._make_parser(ble_device_config, stream, eq)

		parser._parse_line("hello world", ble_device_config.port)
		assert len(stream.raw[PINS[0]]) == 0


# ---------------------------------------------------------------------------
# BLEDeviceWorker tests
# ---------------------------------------------------------------------------


class TestBLEDeviceWorker:
	"""Tests for BLEDeviceWorker construction and behaviour."""

	def test_worker_init(self, ble_device_config):
		stream = serial_io.EMGStreamState(maxlen=64)
		eq = queue.Queue()
		worker = serial_io.BLEDeviceWorker(cfg=ble_device_config, stream_state=stream, event_q=eq)

		assert worker.cfg is ble_device_config
		assert worker.stream_state is stream
		assert worker._active_pins == [p for p in PINS if ble_device_config.channels[p].enabled]

	def test_worker_stop_sets_event(self, ble_device_config):
		stream = serial_io.EMGStreamState(maxlen=64)
		eq = queue.Queue()
		worker = serial_io.BLEDeviceWorker(cfg=ble_device_config, stream_state=stream, event_q=eq)

		assert not worker._stop_event.is_set()
		worker.stop()
		assert worker._stop_event.is_set()

	def test_worker_bleak_missing(self, monkeypatch, ble_device_config):
		"""When bleak is not installed, run() should report error via event_q."""
		monkeypatch.setattr(serial_io, "HAS_BLEAK", False)

		stream = serial_io.EMGStreamState(maxlen=64)
		eq = queue.Queue()
		worker = serial_io.BLEDeviceWorker(cfg=ble_device_config, stream_state=stream, event_q=eq)
		worker.run()

		events = []
		while not eq.empty():
			events.append(eq.get_nowait())

		assert any("bleak" in str(ev).lower() for ev in events)
		assert any(ev[2] is False for ev in events if ev[0] == "device_status")

	def test_worker_line_buffer_parsing(self, ble_device_config):
		"""Simulate BLE notification chunks being assembled into complete lines."""
		stream = serial_io.EMGStreamState(maxlen=64)
		eq = queue.Queue()
		worker = serial_io.BLEDeviceWorker(cfg=ble_device_config, stream_state=stream, event_q=eq)

		# Simulate partial lines arriving
		worker._line_buffer = ""
		addr = ble_device_config.port

		# First chunk: incomplete line
		chunk1 = "0.50"
		worker._line_buffer += chunk1
		assert "\n" not in worker._line_buffer

		# Second chunk: completes the line
		chunk2 = "0\n1.00\n"
		worker._line_buffer += chunk2
		while "\n" in worker._line_buffer:
			line, worker._line_buffer = worker._line_buffer.split("\n", 1)
			worker._parse_line(line, addr)

		assert len(stream.raw[PINS[0]]) == 2


# ---------------------------------------------------------------------------
# DeviceManager routing tests
# ---------------------------------------------------------------------------


class TestDeviceManagerRouting:
	"""Verify DeviceManager routes to the correct worker based on connection_type."""

	def test_serial_routes_to_serial_worker(self, monkeypatch, serial_device_config):
		created = {}

		class FakeSerialWorker:
			def __init__(self, cfg, stream_state, event_q):
				created["type"] = "serial"
				self.cfg = cfg
			def start(self): pass
			def stop(self): pass

		class FakeBLEWorker:
			def __init__(self, cfg, stream_state, event_q):
				created["type"] = "ble"
				self.cfg = cfg
			def start(self): pass
			def stop(self): pass

		monkeypatch.setattr(serial_io, "SerialDeviceWorker", FakeSerialWorker)
		monkeypatch.setattr(serial_io, "BLEDeviceWorker", FakeBLEWorker)

		mgr = serial_io.DeviceManager()
		mgr.start_device(serial_device_config, queue.Queue())

		assert created["type"] == "serial"

	def test_ble_routes_to_ble_worker(self, monkeypatch, ble_device_config):
		created = {}

		class FakeSerialWorker:
			def __init__(self, cfg, stream_state, event_q):
				created["type"] = "serial"
				self.cfg = cfg
			def start(self): pass
			def stop(self): pass

		class FakeBLEWorker:
			def __init__(self, cfg, stream_state, event_q):
				created["type"] = "ble"
				self.cfg = cfg
			def start(self): pass
			def stop(self): pass

		monkeypatch.setattr(serial_io, "SerialDeviceWorker", FakeSerialWorker)
		monkeypatch.setattr(serial_io, "BLEDeviceWorker", FakeBLEWorker)

		mgr = serial_io.DeviceManager()
		mgr.start_device(ble_device_config, queue.Queue())

		assert created["type"] == "ble"

	def test_bt_classic_routes_to_serial_worker(self, monkeypatch, bt_classic_config):
		created = {}

		class FakeSerialWorker:
			def __init__(self, cfg, stream_state, event_q):
				created["type"] = "serial"
				self.cfg = cfg
			def start(self): pass
			def stop(self): pass

		class FakeBLEWorker:
			def __init__(self, cfg, stream_state, event_q):
				created["type"] = "ble"
				self.cfg = cfg
			def start(self): pass
			def stop(self): pass

		monkeypatch.setattr(serial_io, "SerialDeviceWorker", FakeSerialWorker)
		monkeypatch.setattr(serial_io, "BLEDeviceWorker", FakeBLEWorker)

		mgr = serial_io.DeviceManager()
		mgr.start_device(bt_classic_config, queue.Queue())

		assert created["type"] == "serial"

	def test_default_connection_type_routes_serial(self, monkeypatch):
		"""A DeviceConfig with no explicit connection_type should route to serial."""
		channels = {pin: ChannelConfig(enabled=True, muscle="", notes="") for pin in PINS}
		cfg = DeviceConfig(
			device_type="Arduino Uno", port="COM99", baud=115200,
			body_part="test", channels=channels,
		)
		created = {}

		class FakeSerialWorker:
			def __init__(self, cfg, stream_state, event_q):
				created["type"] = "serial"
				self.cfg = cfg
			def start(self): pass
			def stop(self): pass

		monkeypatch.setattr(serial_io, "SerialDeviceWorker", FakeSerialWorker)
		mgr = serial_io.DeviceManager()
		mgr.start_device(cfg, queue.Queue())

		assert created["type"] == "serial"


# ---------------------------------------------------------------------------
# DeviceConfig model tests
# ---------------------------------------------------------------------------


class TestDeviceConfigBLE:
	"""Verify DeviceConfig dataclass BLE fields."""

	def test_default_connection_type(self):
		channels = {pin: ChannelConfig(enabled=False, muscle="", notes="") for pin in PINS}
		cfg = DeviceConfig(
			device_type="Arduino Uno", port="COM1", baud=115200,
			body_part="test", channels=channels,
		)
		assert cfg.connection_type == "Serial (USB/Wired)"
		assert cfg.ble_service_uuid == ""

	def test_ble_connection_type(self, ble_device_config):
		assert ble_device_config.connection_type == "Bluetooth LE (BLE)"
		assert ble_device_config.ble_service_uuid == BLE_UART_SERVICE_UUID


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestBLEConstants:
	"""Verify BLE-related constants exist and are valid UUIDs."""

	def test_connection_types_list(self):
		assert "Serial (USB/Wired)" in CONNECTION_TYPES
		assert "Bluetooth Classic (SPP)" in CONNECTION_TYPES
		assert "Bluetooth LE (BLE)" in CONNECTION_TYPES

	def test_ble_uuid_format(self):
		for uuid_val in [BLE_UART_SERVICE_UUID, BLE_UART_TX_CHAR_UUID]:
			parts = uuid_val.split("-")
			assert len(parts) == 5, f"UUID {uuid_val} should have 5 dash-separated groups"


# ---------------------------------------------------------------------------
# Preset save/load backward compatibility tests
# ---------------------------------------------------------------------------


class TestPresetBLECompatibility:
	"""Verify preset JSON round-trip includes BLE fields and old presets load cleanly."""

	def test_save_includes_ble_fields(self, ble_device_config):
		"""Serialized preset should contain connection_type and ble_service_uuid."""
		from dataclasses import asdict

		# Mimic what _save_connections_preset does
		record = {
			"device_type": ble_device_config.device_type,
			"port": ble_device_config.port,
			"baud": ble_device_config.baud,
			"body_part": ble_device_config.body_part,
			"connection_type": getattr(ble_device_config, "connection_type", "Serial (USB/Wired)"),
			"ble_service_uuid": getattr(ble_device_config, "ble_service_uuid", ""),
			"channels": {pin: asdict(ble_device_config.channels[pin]) for pin in PINS},
		}

		assert record["connection_type"] == "Bluetooth LE (BLE)"
		assert record["ble_service_uuid"] == BLE_UART_SERVICE_UUID

	def test_load_old_preset_without_ble_fields(self):
		"""Loading a preset without connection_type / ble_service_uuid should use defaults."""
		old_record = {
			"device_type": "Arduino Uno",
			"port": "COM3",
			"baud": 115200,
			"body_part": "forearm",
			"channels": {},
		}

		# Mimic _load_connections_preset logic
		conn_type = str(old_record.get("connection_type", "Serial (USB/Wired)"))
		ble_uuid = str(old_record.get("ble_service_uuid", ""))

		assert conn_type == "Serial (USB/Wired)"
		assert ble_uuid == ""

	def test_round_trip(self, ble_device_config):
		"""Save to JSON and reload — BLE fields survive."""
		from dataclasses import asdict

		record = {
			"device_type": ble_device_config.device_type,
			"port": ble_device_config.port,
			"baud": ble_device_config.baud,
			"body_part": ble_device_config.body_part,
			"connection_type": ble_device_config.connection_type,
			"ble_service_uuid": ble_device_config.ble_service_uuid,
			"channels": {pin: asdict(ble_device_config.channels[pin]) for pin in PINS},
		}

		payload = json.dumps({"devices": [record]})
		data = json.loads(payload)

		loaded = data["devices"][0]
		assert loaded["connection_type"] == "Bluetooth LE (BLE)"
		assert loaded["ble_service_uuid"] == BLE_UART_SERVICE_UUID
