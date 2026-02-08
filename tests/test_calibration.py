"""Tests for the calibration controller logic."""

from __future__ import annotations

import json
import queue
from collections import defaultdict, deque
import threading
import time

import pytest

import emg_app.calibration as calibration_mod
from emg_app.calibration import CalibrationController
from emg_app.constants import CAL_BASELINE_SECONDS, CAL_MVC_SECONDS, PINS
from emg_app.models import CalibrationParams, ChannelConfig, DeviceConfig, TargetKey


@pytest.fixture
def device_config() -> DeviceConfig:
    channels = {pin: ChannelConfig(enabled=True, muscle="flexor", notes="") for pin in PINS}
    return DeviceConfig(
        device_type="Arduino Uno",
        port="COM_TEST",
        baud=115200,
        body_part="forearm",
        channels=channels,
    )


class DummyStream:
    def __init__(self):
        self.envelope = defaultdict(list)
        self.raw = defaultdict(deque)


class DummyDeviceManager:
    def __init__(self):
        self.streams = {}


def bind_collect(controller, baseline_vals, mvc_vals):
    call_count = {"n": 0}

    def fake_collect(_self, _stream, _pin, _seconds):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return baseline_vals
        if call_count["n"] == 2:
            return mvc_vals
        return []

    controller._collect_envelope = fake_collect.__get__(controller, CalibrationController)  # type: ignore[attr-defined]


def test_run_calibration_success(monkeypatch, tmp_path, device_config):
    event_q = queue.Queue()
    dev_mgr = DummyDeviceManager()
    dev_mgr.streams[device_config.port] = DummyStream()

    controller = CalibrationController(dev_mgr, event_q, log_path=str(tmp_path / "cal_log.json"))
    bind_collect(controller, baseline_vals=[0.8] * 50, mvc_vals=[8.0] * 120)
    monkeypatch.setattr(calibration_mod.time, "time", lambda: 1234.0)

    controller.run_calibration(device_config, "A0")

    assert len(controller.calibrations) == 1
    tgt = TargetKey(port=device_config.port, pin="A0")
    assert tgt in controller.calibrations

    with open(tmp_path / "cal_log.json", "r", encoding="utf-8") as file:
        payload = json.load(file)
    assert len(payload["calibrations"]) == 1
    assert payload["calibrations"][0]["baseline"] == pytest.approx(0.8)

    statuses = []
    while not event_q.empty():
        statuses.append(event_q.get())
    assert any("Calibration complete" in msg for _, msg in statuses)


def test_run_calibration_tolerates_noisy_baseline(monkeypatch, tmp_path, device_config):
    event_q = queue.Queue()
    dev_mgr = DummyDeviceManager()
    dev_mgr.streams[device_config.port] = DummyStream()

    controller = CalibrationController(dev_mgr, event_q, log_path=str(tmp_path / "cal_log.json"))
    noisy_baseline = ([0.0] * 40) + ([2.0] * 40)
    mvc_vals = [8.0] * 120
    bind_collect(controller, baseline_vals=noisy_baseline, mvc_vals=mvc_vals)
    monkeypatch.setattr(calibration_mod.time, "time", lambda: 4321.0)

    controller.run_calibration(device_config, "A0")

    tgt = TargetKey(port=device_config.port, pin="A0")
    assert tgt in controller.calibrations

    statuses = []
    while not event_q.empty():
        statuses.append(event_q.get())
    assert any("Calibration complete" in msg for _, msg in statuses)


def test_run_calibration_failure_emits_status(monkeypatch, device_config):
    event_q = queue.Queue()
    dev_mgr = DummyDeviceManager()
    dev_mgr.streams[device_config.port] = DummyStream()

    controller = CalibrationController(dev_mgr, event_q)
    bind_collect(controller, baseline_vals=[0.5] * 25, mvc_vals=[0.6] * 10)

    controller.run_calibration(device_config, "A0")

    assert controller.calibrations == {}
    statuses = []
    while not event_q.empty():
        statuses.append(event_q.get())
    assert any("insufficient MVC" in msg for _, msg in statuses)


def test_compute_percent_mvc_applies_smoothing(device_config):
    event_q = queue.Queue()
    dev_mgr = DummyDeviceManager()
    controller = CalibrationController(dev_mgr, event_q)

    tgt = TargetKey(port=device_config.port, pin="A0")
    controller.calibrations[tgt] = CalibrationParams(
        baseline=0.0,
        mvc=100.0,
        ts_unix=0.0,
        body_part="forearm",
        muscle="test",
        device_type="Arduino Uno",
        port=device_config.port,
        pin="A0",
    )

    first = controller.compute_percent_mvc(tgt, 50.0)
    second = controller.compute_percent_mvc(tgt, 100.0)

    assert first == pytest.approx(50.0)
    assert second < 100.0  # EMA smoothing should dampen the step change


def test_collect_envelope_falls_back_to_raw(device_config):
    event_q = queue.Queue()
    dev_mgr = DummyDeviceManager()
    stream = DummyStream()
    dev_mgr.streams[device_config.port] = stream

    controller = CalibrationController(dev_mgr, event_q)

    def producer():
        t_ms = 0
        for _ in range(60):
            t_ms += 5
            stream.raw["A0"].append((t_ms, 0.5))
            time.sleep(0.002)

    thread = threading.Thread(target=producer)
    thread.start()
    values = controller._collect_envelope(stream, "A0", seconds=0.2)
    thread.join()

    assert len(values) > 0