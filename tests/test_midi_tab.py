"""Tests for the MIDI tab (MidiTabMixin) without hardware."""

from __future__ import annotations

import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from emg_app import app as app_mod
from emg_app.constants import PINS
from emg_app.models import CalibrationParams, ChannelConfig, DeviceConfig, TargetKey
from emg_app.processing_core import MIDI_VALUE_MAX


# ---- Helpers (reuse FakeStream / stubs from test_app_ui) ----

class FakeStream(SimpleNamespace):
    def __init__(self, fs_est_hz: float = 0.0):
        super().__init__(
            raw={pin: deque(maxlen=2000) for pin in PINS},
            envelope={pin: deque(maxlen=2000) for pin in PINS},
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
    def __init__(self, device_manager, event_q, log_path=None):
        self.dev_mgr = device_manager
        self.event_q = event_q
        self.log_path = log_path
        self.calibrations = {}
        self.runs = []

    def run_calibration(self, device, pin):
        self.runs.append((device.port, pin))


def _make_device(port: str = "COM_TEST") -> DeviceConfig:
    channels = {pin: ChannelConfig(enabled=False, muscle="", notes="") for pin in PINS}
    channels["A0"].enabled = True
    channels["A0"].muscle = "flexor"
    return DeviceConfig(
        device_type="Arduino Uno",
        port=port,
        baud=115200,
        body_part="forearm",
        channels=channels,
    )


# ---- Fixtures ----

@pytest.fixture(scope="module")
def midi_app():
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
def _reset(midi_app):
    midi_app.device_configs = []
    midi_app.dev_mgr.streams.clear()
    midi_app.dev_mgr.status.clear()
    midi_app.calibration.calibrations.clear()
    midi_app.midi_messages_sent = 0
    midi_app.midi_log_entries.clear()
    midi_app.midi_plot_history.clear()
    midi_app._midi_processors.clear()
    midi_app._midi_cal_versions.clear()
    midi_app._midi_last_seen_ts.clear()
    midi_app._midi_capture_bufs.clear()
    midi_app.midi_target_var.set("")
    midi_app._midi_connected = False


# ---- Tests ----

class TestMidiTabUI:
    """Verify UI widgets were created and are accessible."""

    def test_midi_tab_exists(self, midi_app):
        tabs = midi_app.notebook.tabs()
        tab_texts = [midi_app.notebook.tab(t, "text") for t in tabs]
        assert "MIDI" in tab_texts

    def test_port_combo_exists(self, midi_app):
        assert midi_app._midi_port_combo is not None

    def test_connect_button_exists(self, midi_app):
        assert midi_app._midi_connect_btn is not None
        assert midi_app._midi_connect_btn.cget("text") == "Connect"

    def test_status_label_defaults(self, midi_app):
        assert midi_app._midi_status_lbl is not None
        text = midi_app._midi_status_lbl.cget("text")
        # Either "Disconnected" (mido available) or a dependency hint (mido missing)
        assert "Disconnected" in text or "mido" in text.lower()

    def test_meter_canvas_exists(self, midi_app):
        assert midi_app._midi_meter_canvas is not None

    def test_log_tree_exists(self, midi_app):
        assert midi_app._midi_log_tree is not None

    def test_plot_widget_exists(self, midi_app):
        assert midi_app._midi_plot is not None

    def test_readout_labels_exist(self, midi_app):
        assert midi_app._midi_cc_lbl is not None
        assert midi_app._midi_env_lbl is not None
        assert midi_app._midi_raw_lbl is not None
        assert midi_app._midi_fs_lbl is not None
        assert midi_app._midi_sent_lbl is not None


class TestMidiTabTargets:
    """Target channel refresh and parsing."""

    def test_refresh_targets_populates_combo(self, midi_app):
        cfg = _make_device()
        midi_app.device_configs = [cfg]
        midi_app._midi_refresh_targets()
        values = list(midi_app._midi_target_combo["values"])
        assert len(values) == 1
        assert "A0" in values[0]
        assert "flexor" in values[0]

    def test_refresh_targets_empty_when_no_devices(self, midi_app):
        midi_app._midi_refresh_targets()
        values = list(midi_app._midi_target_combo["values"])
        assert len(values) == 0

    def test_parse_target_returns_none_when_empty(self, midi_app):
        midi_app.midi_target_var.set("")
        assert midi_app._midi_parse_target() is None

    def test_parse_target_returns_correct_parts(self, midi_app):
        midi_app.midi_target_var.set("COM_TEST | A0 | flexor")
        result = midi_app._midi_parse_target()
        assert result == ("COM_TEST", "A0", "flexor")


class TestMidiTabConfig:
    """Configuration spinbox callbacks."""

    def test_cc_number_update(self, midi_app):
        midi_app.midi_cc_var.set(42)
        midi_app._midi_on_config_change()
        assert midi_app.midi_controller.cc_number == 42

    def test_channel_update(self, midi_app):
        midi_app.midi_channel_var.set(10)
        midi_app._midi_on_config_change()
        assert midi_app.midi_controller.channel == 9  # internal 0-indexed

    def test_rate_update(self, midi_app):
        midi_app.midi_rate_var.set(200.0)
        midi_app._midi_on_config_change()
        assert midi_app.midi_controller.max_rate_hz == 200.0


class TestMidiTabProcessing:
    """Processing pipeline with simulated data."""

    def test_update_processes_samples_and_updates_labels(self, midi_app):
        cfg = _make_device()
        midi_app.device_configs = [cfg]
        midi_app._midi_refresh_targets()
        midi_app.midi_target_var.set("COM_TEST | A0 | flexor")

        stream = FakeStream(fs_est_hz=500.0)
        # Inject 50 synthetic samples
        for i in range(50):
            t_ms = 1000 + i * 4
            raw = 512 + (i * 3)  # ramp up
            stream.raw["A0"].append((t_ms, raw))
        midi_app.dev_mgr.streams["COM_TEST"] = stream

        # Run one update cycle (without scheduling the next)
        midi_app._update_midi_tab.__wrapped__(midi_app) if hasattr(midi_app._update_midi_tab, '__wrapped__') else midi_app._update_midi_tab()

        # Check that readouts updated
        cc_text = midi_app._midi_cc_lbl.cget("text")
        assert "MIDI CC:" in cc_text
        assert "---" not in cc_text  # should have a numeric value now

        env_text = midi_app._midi_env_lbl.cget("text")
        assert "Envelope:" in env_text

        raw_text = midi_app._midi_raw_lbl.cget("text")
        assert "Raw:" in raw_text

    def test_update_populates_plot_history(self, midi_app):
        cfg = _make_device()
        midi_app.device_configs = [cfg]
        midi_app._midi_refresh_targets()
        midi_app.midi_target_var.set("COM_TEST | A0 | flexor")

        stream = FakeStream(fs_est_hz=500.0)
        for i in range(20):
            stream.raw["A0"].append((1000 + i * 4, 500 + i))
        midi_app.dev_mgr.streams["COM_TEST"] = stream

        midi_app._update_midi_tab()

        assert len(midi_app.midi_plot_history) > 0, "plot history should have data"

    def test_no_crash_with_no_target(self, midi_app):
        """Should handle gracefully when no target is selected."""
        midi_app.midi_target_var.set("")
        # Should not raise
        midi_app._update_midi_tab()

    def test_no_crash_with_no_stream(self, midi_app):
        """Should handle gracefully when device is not connected."""
        midi_app.midi_target_var.set("COM_NONE | A0 | flexor")
        midi_app._update_midi_tab()

    def test_calibration_applied_to_processor(self, midi_app):
        cfg = _make_device()
        midi_app.device_configs = [cfg]
        midi_app._midi_refresh_targets()
        midi_app.midi_target_var.set("COM_TEST | A0 | flexor")

        tgt = TargetKey(port="COM_TEST", pin="A0")
        cal = CalibrationParams(
            baseline=100.0,
            mvc=800.0,
            ts_unix=time.time(),
            body_part="forearm",
            muscle="flexor",
            device_type="Arduino Uno",
            port="COM_TEST",
            pin="A0",
        )
        midi_app.calibration.calibrations[tgt] = cal

        stream = FakeStream(fs_est_hz=500.0)
        for i in range(10):
            stream.raw["A0"].append((1000 + i * 4, 500))
        midi_app.dev_mgr.streams["COM_TEST"] = stream

        midi_app._update_midi_tab()

        key = "COM_TEST|A0"
        processor = midi_app._midi_processors.get(key)
        assert processor is not None
        assert processor.rest_min == 100.0
        assert processor.max_contraction == 800.0


class TestMidiTabConnection:
    """Connection toggle logic (without actual MIDI hardware)."""

    def test_connect_without_port_shows_warning(self, midi_app, monkeypatch):
        shown = []
        monkeypatch.setattr("emg_app.ui_midi.messagebox.showwarning", lambda *a, **kw: shown.append(a))
        midi_app._midi_port_combo.set("")
        midi_app._midi_toggle_connection()
        assert len(shown) == 1
        assert not midi_app._midi_connected

    def test_disconnect_toggles_state(self, midi_app):
        midi_app._midi_connected = True
        midi_app.midi_controller.port = MagicMock()
        midi_app._midi_toggle_connection()  # should disconnect
        assert not midi_app._midi_connected
        assert midi_app._midi_connect_btn.cget("text") == "Connect"
        assert "Disconnected" in midi_app._midi_status_lbl.cget("text")


class TestMidiTabLog:
    """Activity log behavior."""

    def test_clear_log_empties_entries(self, midi_app):
        midi_app.midi_log_entries.append(("12:00:00", 64, 12.3, 512.0))
        midi_app._midi_render_log()
        assert len(midi_app._midi_log_tree.get_children()) == 1

        midi_app._midi_clear_log()
        assert len(midi_app.midi_log_entries) == 0
        assert len(midi_app._midi_log_tree.get_children()) == 0

    def test_log_entries_render_newest_first(self, midi_app):
        midi_app.midi_log_entries.append(("12:00:00", 30, 5.0, 400.0))
        midi_app.midi_log_entries.append(("12:00:01", 90, 25.0, 700.0))
        midi_app._midi_render_log()
        children = midi_app._midi_log_tree.get_children()
        assert len(children) == 2
        first_row = midi_app._midi_log_tree.item(children[0], "values")
        assert str(first_row[1]) == "90"  # newest on top


class TestMidiTabMeter:
    """Meter visual update."""

    def test_meter_updates_without_error(self, midi_app):
        midi_app._midi_update_meter(0)
        midi_app._midi_update_meter(40)
        midi_app._midi_update_meter(90)
        midi_app._midi_update_meter(127)
        # No assertions needed beyond "doesn't crash"; canvas state
        # is verified visually.

    def test_meter_clamps_out_of_range(self, midi_app):
        # Should not raise for values outside 0-127
        midi_app._midi_update_meter(-10)
        midi_app._midi_update_meter(200)
