"""Unit tests for the serial I/O helpers."""

from __future__ import annotations

import queue

import pytest

import emg_app.serial_io as serial_io
from emg_app.calibration import CalibrationController
from emg_app.constants import PINS
from emg_app.models import ChannelConfig, DeviceConfig, TargetKey


@pytest.fixture
def device_config() -> DeviceConfig:
    channels = {pin: ChannelConfig(enabled=True, muscle="", notes="") for pin in PINS}
    return DeviceConfig(
        device_type="Arduino Uno",
        port="COM_TEST",
        baud=115200,
        body_part="test",
        channels=channels,
    )


def test_compute_rms_handles_basic_values():
    assert serial_io.compute_rms([3.0, 4.0]) == pytest.approx(3.5355339)
    assert serial_io.compute_rms([]) == 0.0


def test_stream_state_estimates_sampling_rate():
    state = serial_io.EMGStreamState(maxlen=64)
    state.update_fs_estimate(0)
    state.update_fs_estimate(10)
    state.update_fs_estimate(20)
    assert 90.0 <= state.fs_est_hz <= 110.0


def test_serial_worker_processes_samples(monkeypatch, device_config):
    class FakeSerial:
        def __init__(self, lines):
            self.lines = [line.encode("utf-8") for line in lines]
            self.is_open = True

        def reset_input_buffer(self):
            return None

        def readline(self):
            if self.lines:
                return self.lines.pop(0)
            raise RuntimeError("EOF")

        def close(self):
            self.is_open = False

    lines = [
        "t_ms," + ",".join(PINS),
    ]
    for idx in range(1, 60):  # plenty of rows to populate RMS window
        cols = [str(idx * 10)] + [str(100 + idx) for _ in PINS]
        lines.append(",".join(cols))

    monkeypatch.setattr(serial_io.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serial_io, "RMS_WINDOW_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(
        serial_io.serial,
        "Serial",
        lambda *args, **kwargs: FakeSerial(lines.copy()),
    )

    stream = serial_io.EMGStreamState(maxlen=256)
    event_q = queue.Queue()
    worker = serial_io.SerialDeviceWorker(cfg=device_config, stream_state=stream, event_q=event_q)
    worker.run()  # run synchronously for deterministic assertions

    assert len(stream.raw[PINS[0]]) >= 40
    assert len(stream.envelope[PINS[0]]) >= 1

    events = []
    while True:
        try:
            events.append(event_q.get_nowait())
        except queue.Empty:
            break

    assert events[0][0] == "device_status" and events[0][2] is True
    assert any(ev[0] == "sample" for ev in events)
    assert events[-1][0] == "device_status" and events[-1][2] is False


def test_serial_worker_handles_single_value_lines(monkeypatch, device_config):
    class FakeSerial:
        def __init__(self, lines):
            self.lines = [line.encode("utf-8") for line in lines]
            self.is_open = True

        def reset_input_buffer(self):
            return None

        def readline(self):
            if self.lines:
                return self.lines.pop(0)
            raise RuntimeError("EOF")

        def close(self):
            self.is_open = False

    lines = ["0.100\n", "0.200\n", "512\n", "1.5\n"]

    monkeypatch.setattr(serial_io.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serial_io.serial, "Serial", lambda *args, **kwargs: FakeSerial(lines.copy()))

    stream = serial_io.EMGStreamState(maxlen=64)
    event_q = queue.Queue()
    worker = serial_io.SerialDeviceWorker(cfg=device_config, stream_state=stream, event_q=event_q)
    worker.run()

    first_pin = PINS[0]
    assert len(stream.raw[first_pin]) == len(lines)
    # Integer ADC input should convert to volts (~2.5 V for 512)
    last_t, last_v = stream.raw[first_pin][-2]
    assert last_v == pytest.approx(512 * (serial_io.DEFAULT_VREF_VOLTS / 1023.0))
    assert last_t > 0


def test_device_manager_start_stop(monkeypatch, device_config):
    started_workers = {}

    class FakeWorker:
        def __init__(self, cfg, stream_state, event_q):
            self.cfg = cfg
            self.stream_state = stream_state
            self.event_q = event_q
            self.started = False
            self.stopped = False
            started_workers[cfg.port] = self

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(serial_io, "SerialDeviceWorker", FakeWorker)

    mgr = serial_io.DeviceManager()
    mgr.start_device(device_config, queue.Queue())

    worker = started_workers[device_config.port]
    assert worker.started is True
    assert device_config.port in mgr.workers
    assert device_config.port in mgr.streams
    assert mgr.status[device_config.port] == (False, "starting")

    mgr.stop_device(device_config.port)
    assert worker.stopped is True
    assert device_config.port not in mgr.workers
    assert device_config.port not in mgr.streams
    assert device_config.port not in mgr.status


def test_device_manager_stop_all(monkeypatch, device_config):
    ports = [device_config.port, "COM_OTHER"]

    class FakeWorker:
        def __init__(self, cfg, stream_state, event_q):
            self.cfg = cfg
            self.stream_state = stream_state
            self.event_q = event_q
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(serial_io, "SerialDeviceWorker", FakeWorker)

    mgr = serial_io.DeviceManager()
    second_cfg = DeviceConfig(
        device_type="Arduino Uno",
        port=ports[1],
        baud=115200,
        body_part="test",
        channels=device_config.channels,
    )

    mgr.start_device(device_config, queue.Queue())
    mgr.start_device(second_cfg, queue.Queue())

    mgr.stop_all()

    assert mgr.workers == {}
    assert mgr.streams == {}
    assert mgr.status == {}


def test_worker_and_calibration_pipeline(monkeypatch, device_config):
    class FakeSerial:
        def __init__(self, lines):
            self.lines = [line.encode("utf-8") for line in lines]
            self.is_open = True

        def reset_input_buffer(self):
            return None

        def readline(self):
            if self.lines:
                return self.lines.pop(0)
            raise RuntimeError("EOF")

        def close(self):
            self.is_open = False

    header = "t_ms," + ",".join(PINS)
    lines = [header]
    for idx in range(1, 160):
        amplitude = 40 if idx < 80 else 400
        cols = [str(idx * 5)] + [str(amplitude) for _ in PINS]
        lines.append(",".join(cols))

    monkeypatch.setattr(serial_io.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(serial_io, "RMS_WINDOW_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(serial_io.serial, "Serial", lambda *args, **kwargs: FakeSerial(lines.copy()))

    stream = serial_io.EMGStreamState(maxlen=512)
    event_q = queue.Queue()
    worker = serial_io.SerialDeviceWorker(cfg=device_config, stream_state=stream, event_q=event_q)
    worker.run()

    dev_mgr = DummyDeviceManager()
    dev_mgr.streams[device_config.port] = stream
    controller = CalibrationController(dev_mgr, queue.Queue())

    call_counter = {"n": 0}

    def fake_collect(_self, _stream, pin, _seconds):
        call_counter["n"] += 1
        env_vals = [val for (_, val) in stream.envelope[pin]]
        if call_counter["n"] == 1:
            return env_vals[:60]
        return env_vals[-60:]

    controller._collect_envelope = fake_collect.__get__(controller, CalibrationController)  # type: ignore[attr-defined]

    controller.run_calibration(device_config, "A0")

    tgt = TargetKey(port=device_config.port, pin="A0")
    assert tgt in controller.calibrations


class DummyDeviceManager:
    def __init__(self):
        self.streams = {}