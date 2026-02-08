"""UI-level tests that exercise EMGApp mixin behaviors without hardware."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import pytest

from emg_app import app as app_mod
from emg_app.constants import PINS
from emg_app.models import ChannelConfig, DeviceConfig
import emg_app.ui_calibration as ui_calibration


class FakeStream(SimpleNamespace):
    def __init__(self, fs_est_hz: float = 0.0):
        super().__init__(
            raw={pin: deque(maxlen=32) for pin in PINS},
            envelope={pin: deque(maxlen=32) for pin in PINS},
            fs_est_hz=fs_est_hz,
        )


class StubDeviceManager:
    def __init__(self):
        self.streams = {}
        self.status = {}
        self.started_ports: list[str] = []
        self.stopped_ports: list[str] = []

    def start_device(self, cfg, event_q):
        self.started_ports.append(cfg.port)
        self.streams.setdefault(cfg.port, FakeStream(fs_est_hz=512.0))
        self.status[cfg.port] = (True, "connected")

    def stop_device(self, port: str):
        self.stopped_ports.append(port)
        self.streams.pop(port, None)
        self.status.pop(port, None)

    def stop_all(self):
        for port in list(self.streams.keys()):
            self.stop_device(port)


class StubCalibrationController:
    def __init__(self, device_manager, event_q, log_path: str | None = None):
        self.dev_mgr = device_manager
        self.event_q = event_q
        self.log_path = log_path
        self.calibrations = {}
        self.runs: list[tuple[str, str]] = []

    def run_calibration(self, device, pin):
        self.runs.append((device.port, pin))


@pytest.fixture(scope="module")
def test_app():
    patcher = pytest.MonkeyPatch()
    patcher.setattr(app_mod, "DeviceManager", StubDeviceManager)
    patcher.setattr(app_mod, "CalibrationController", StubCalibrationController)
    application = app_mod.EMGApp()
    application.withdraw()
    yield application
    application.on_close()
    try:
        application.destroy()
    except Exception:
        pass
    patcher.undo()


@pytest.fixture(autouse=True)
def _reset_app_state(test_app):
    test_app.device_configs = []
    test_app.dev_mgr.streams.clear()
    test_app.dev_mgr.status.clear()
    test_app.calibration.calibrations.clear()
    test_app.calibration.runs.clear()
    if test_app.raw_tree:
        for iid in test_app.raw_tree.get_children(""):
            test_app.raw_tree.delete(iid)
    if test_app.target_combo:
        test_app.target_combo["values"] = ()
        test_app.target_var.set("")


def _make_device(port: str = "COM_TEST") -> DeviceConfig:
    channels = {pin: ChannelConfig(enabled=False, muscle="", notes="") for pin in PINS}
    channels["A0"].enabled = True
    channels["A0"].muscle = "flexor"
    channels["A2"].enabled = True
    channels["A2"].muscle = "extensor"
    return DeviceConfig(
        device_type="Arduino Uno",
        port=port,
        baud=115200,
        body_part="forearm",
        channels=channels,
    )


def test_refresh_targets_reflects_enabled_channels(test_app):
    cfg = _make_device()
    cfg.channels["A2"].enabled = False
    test_app.device_configs = [cfg]
    test_app._refresh_targets()
    values = list(test_app.target_combo["values"])
    assert len(values) == 1
    assert "A0" in values[0]


def test_raw_data_table_updates_rows(test_app):
    cfg = _make_device()
    test_app.device_configs = [cfg]
    stream = FakeStream(fs_est_hz=256.0)
    stream.raw["A0"].append((1234, 789))
    test_app.dev_mgr.streams[cfg.port] = stream
    test_app._update_raw_data_table()
    iid = f"{cfg.port} | A0"
    row = test_app.raw_tree.item(iid)
    values = row["values"]
    assert values[0] == cfg.port
    assert values[1] == "A0"
    assert str(values[4]) == "1234"
    assert str(values[5]) == "789"
    assert str(values[6]) == "256.0"


def test_raw_data_table_removes_disabled_channels(test_app):
    cfg = _make_device()
    test_app.device_configs = [cfg]
    stream = FakeStream(fs_est_hz=300.0)
    stream.raw["A0"].append((50, 100))
    test_app.dev_mgr.streams[cfg.port] = stream
    test_app._update_raw_data_table()
    iid = f"{cfg.port} | A0"
    assert iid in test_app.raw_tree.get_children("")
    cfg.channels["A0"].enabled = False
    test_app._update_raw_data_table()
    assert iid not in test_app.raw_tree.get_children("")


def test_calibrate_selected_target_runs_controller(monkeypatch, test_app):
    cfg = _make_device()
    test_app.device_configs = [cfg]
    test_app._refresh_targets()
    selection = test_app.target_combo["values"][0]
    test_app.target_var.set(selection)

    monkeypatch.setattr(ui_calibration.time, "sleep", lambda *_args, **_kwargs: None)

    class ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(ui_calibration.threading, "Thread", ImmediateThread)

    test_app.dev_mgr.streams.pop(cfg.port, None)
    test_app._calibrate_selected_target()

    assert cfg.port in test_app.dev_mgr.started_ports
    assert test_app.calibration.runs == [(cfg.port, "A0")]


def test_poll_events_updates_status(test_app):
    test_app.event_q.put(("device_status", "COM_EVT", True, "connected"))
    test_app.event_q.put(("cal_status", "done"))
    test_app._poll_events()
    assert test_app.dev_mgr.status["COM_EVT"] == (True, "connected")
    assert test_app.cal_status.cget("text") == "Calibration status: done"
