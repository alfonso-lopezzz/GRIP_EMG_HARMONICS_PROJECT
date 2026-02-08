"""Standalone EMG processing UI with live plots and MIDI output."""

from __future__ import annotations

import sys
import time
import threading
from collections import deque
from typing import Deque, List, Optional, Tuple

import pyqtgraph as pg
import serial
from PySide6 import QtCore, QtWidgets
from serial.tools import list_ports

from .processing_core import (
    CAPTURE_SECONDS,
    EMGProcessor,
    MidiController,
    MIDI_CC_NUMBER,
    MIDI_CHANNEL,
    MIDI_MAX_RATE_HZ,
    RAW_BUFFER_SECONDS,
)


# ----------------------------- User settings ----------------------------- #
PORT_OVERRIDE: Optional[str] = None  # e.g. "COM3" or "/dev/ttyACM0"
BAUD_RATE = 115200
PLOT_REFRESH_MS = 40  # ~25 FPS


class SerialReader(QtCore.QThread):
    sample_received = QtCore.Signal(int, int)  # t_ms, raw_value
    status_changed = QtCore.Signal(str)

    def __init__(self, port: Optional[str], baud: int):
        super().__init__()
        self._explicit_port = port
        self._baud = baud
        self._ser: Optional[serial.Serial] = None
        self._stopping = threading.Event()

    def stop(self) -> None:
        self._stopping.set()
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass

    def _detect_port(self) -> Optional[str]:
        if self._explicit_port:
            return self._explicit_port
        ports = list(list_ports.comports())
        if not ports:
            return None
        for candidate in ports:
            desc = (candidate.description or "").lower()
            if "arduino" in desc or "ch340" in desc:
                return candidate.device
        return ports[0].device

    def run(self) -> None:  # type: ignore[override]
        port_name = self._detect_port()
        if not port_name:
            self.status_changed.emit("No serial ports detected")
            return
        self.status_changed.emit(f"Opening {port_name}...")
        try:
            self._ser = serial.Serial(port_name, self._baud, timeout=1)
            self.status_changed.emit(f"Connected to {port_name}")
        except Exception as exc:
            self.status_changed.emit(f"Serial open error: {exc}")
            return

        while not self._stopping.is_set():
            try:
                line = self._ser.readline()
            except serial.SerialException as exc:
                self.status_changed.emit(f"Serial read error: {exc}")
                break
            if not line:
                continue
            try:
                decoded = line.decode("utf-8", errors="ignore").strip()
            except UnicodeDecodeError:
                continue
            if not decoded:
                continue
            parts = decoded.split(",")
            if len(parts) != 2:
                continue
            try:
                t_ms = int(parts[0])
                raw = int(parts[1])
            except ValueError:
                continue
            self.sample_received.emit(t_ms, raw)

        self.status_changed.emit("Serial worker stopped")
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass


class ProcessingWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EMG Processing → MIDI")
        self.resize(1200, 700)

        self.processor = EMGProcessor()
        self.raw_buffer: Deque[Tuple[float, float]] = deque(maxlen=int(RAW_BUFFER_SECONDS * 2000))
        self.proc_buffer: Deque[Tuple[float, float]] = deque(maxlen=int(RAW_BUFFER_SECONDS * 2000))
        self.capture_buffer: Deque[float] = deque(maxlen=4096)
        self.serial_thread = SerialReader(PORT_OVERRIDE, BAUD_RATE)
        self.serial_thread.sample_received.connect(self.on_sample)
        self.serial_thread.status_changed.connect(self.on_status)

        self.midi = MidiController(
            cc_number=MIDI_CC_NUMBER,
            channel=MIDI_CHANNEL,
            max_rate_hz=MIDI_MAX_RATE_HZ,
        )

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        controls = QtWidgets.QHBoxLayout()
        layout.addLayout(controls)

        self.status_label = QtWidgets.QLabel("Serial: idle")
        controls.addWidget(self.status_label)

        controls.addStretch(1)

        self.port_label = QtWidgets.QLabel(f"Port: {PORT_OVERRIDE or 'auto'}")
        controls.addWidget(self.port_label)

        midi_controls = QtWidgets.QHBoxLayout()
        layout.addLayout(midi_controls)

        midi_controls.addWidget(QtWidgets.QLabel("MIDI Output:"))
        self.midi_combo = QtWidgets.QComboBox()
        midi_controls.addWidget(self.midi_combo)
        self.refresh_midi_ports()
        refresh_btn = QtWidgets.QPushButton("Refresh Ports")
        refresh_btn.clicked.connect(self.refresh_midi_ports)
        midi_controls.addWidget(refresh_btn)

        connect_btn = QtWidgets.QPushButton("Connect MIDI")
        connect_btn.clicked.connect(self.open_selected_midi_port)
        midi_controls.addWidget(connect_btn)
        midi_controls.addStretch(1)

        calib_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(calib_layout)

        self.rest_btn = QtWidgets.QPushButton("Set Rest (10th %)")
        self.rest_btn.clicked.connect(self.capture_rest)
        calib_layout.addWidget(self.rest_btn)

        self.max_btn = QtWidgets.QPushButton("Set Max (90th %)")
        self.max_btn.clicked.connect(self.capture_max)
        calib_layout.addWidget(self.max_btn)

        calib_layout.addWidget(QtWidgets.QLabel("Rest:"))
        self.rest_value_lbl = QtWidgets.QLabel(f"{self.processor.rest_min:.1f}")
        calib_layout.addWidget(self.rest_value_lbl)
        calib_layout.addWidget(QtWidgets.QLabel("Max:"))
        self.max_value_lbl = QtWidgets.QLabel(f"{self.processor.max_contraction:.1f}")
        calib_layout.addWidget(self.max_value_lbl)
        calib_layout.addStretch(1)

        plots_layout = QtWidgets.QHBoxLayout()
        layout.addLayout(plots_layout, stretch=1)

        self.raw_plot = pg.PlotWidget(title="Raw A0 (0-1023)")
        self.raw_plot.setYRange(0, 1023)
        self.raw_curve = self.raw_plot.plot(pen=pg.mkPen('#1f77b4', width=2))
        plots_layout.addWidget(self.raw_plot)

        self.proc_plot = pg.PlotWidget(title="Processed / MIDI (0-127)")
        self.proc_plot.setYRange(0, 127)
        self.proc_curve = self.proc_plot.plot(pen=pg.mkPen('#d62728', width=2))
        plots_layout.addWidget(self.proc_plot)

        readouts = QtWidgets.QHBoxLayout()
        layout.addLayout(readouts)

        self.raw_lbl = QtWidgets.QLabel("Raw: -")
        readouts.addWidget(self.raw_lbl)
        self.proc_lbl = QtWidgets.QLabel("Envelope: -")
        readouts.addWidget(self.proc_lbl)
        self.midi_lbl = QtWidgets.QLabel("MIDI: -")
        readouts.addWidget(self.midi_lbl)
        self.fs_lbl = QtWidgets.QLabel("Fs: - Hz")
        readouts.addWidget(self.fs_lbl)
        readouts.addStretch(1)

        self.plot_timer = QtCore.QTimer()
        self.plot_timer.setInterval(PLOT_REFRESH_MS)
        self.plot_timer.timeout.connect(self.update_plots)

        self.start_time = time.time()
        self.serial_thread.start()
        self.plot_timer.start()

    # ------------------------- MIDI helpers ------------------------- #
    def refresh_midi_ports(self) -> None:
        current = self.midi_combo.currentText()
        self.midi_combo.clear()
        try:
            for name in self.midi.list_ports():
                self.midi_combo.addItem(name)
        except Exception as exc:
            self.statusBar().showMessage(str(exc), 5000)
        if current:
            idx = self.midi_combo.findText(current)
            if idx >= 0:
                self.midi_combo.setCurrentIndex(idx)

    def open_selected_midi_port(self) -> None:
        name = self.midi_combo.currentText()
        if not name:
            return
        try:
            self.midi.open(name)
            self.statusBar().showMessage(f"MIDI connected: {name}", 5000)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "MIDI error", str(exc))

    def send_midi(self, value: int) -> None:
        try:
            if not self.midi.send(value):
                return
        except Exception as exc:
            self.statusBar().showMessage(f"MIDI send failed: {exc}", 3000)

    # ------------------------- Serial / processing ------------------------- #
    @QtCore.Slot(int, int)
    def on_sample(self, t_ms: int, raw: int) -> None:
        raw_val, envelope, midi_val = self.processor.process(t_ms, raw)
        now = (time.time() - self.start_time)
        self.raw_buffer.append((now, raw_val))
        self.proc_buffer.append((now, midi_val))
        self.capture_buffer.append(envelope)
        if len(self.capture_buffer) > int(CAPTURE_SECONDS * 2000):
            self.capture_buffer.popleft()
        self.raw_lbl.setText(f"Raw: {raw_val:.1f}")
        self.proc_lbl.setText(f"Envelope: {envelope:.2f}")
        self.midi_lbl.setText(f"MIDI: {int(midi_val):3d}")
        if self.processor.sample_rate_hz > 0:
            self.fs_lbl.setText(f"Fs: {self.processor.sample_rate_hz:.1f} Hz")
        self.send_midi(int(midi_val))

    @QtCore.Slot(str)
    def on_status(self, msg: str) -> None:
        self.status_label.setText(f"Serial: {msg}")

    def update_plots(self) -> None:
        raw_times, raw_vals = self._windowed(self.raw_buffer)
        proc_times, proc_vals = self._windowed(self.proc_buffer)
        if raw_times:
            self.raw_curve.setData(raw_times, raw_vals)
            self.raw_plot.setXRange(max(0, raw_times[-1] - RAW_BUFFER_SECONDS), raw_times[-1])
        if proc_times:
            self.proc_curve.setData(proc_times, proc_vals)
            self.proc_plot.setXRange(max(0, proc_times[-1] - RAW_BUFFER_SECONDS), proc_times[-1])

    def _windowed(self, buf: Deque[Tuple[float, float]]) -> Tuple[List[float], List[float]]:
        if not buf:
            return [], []
        latest_time = buf[-1][0]
        start = latest_time - RAW_BUFFER_SECONDS
        times = [t for t, _ in buf if t >= start]
        vals = [v for t, v in buf if t >= start]
        return times, vals

    # ------------------------- Calibration buttons ------------------------- #
    def _capture_samples(self) -> List[float]:
        return list(self.capture_buffer)

    def capture_rest(self) -> None:
        samples = self._capture_samples()
        if not samples:
            QtWidgets.QMessageBox.information(self, "Calibration", "No samples captured yet.")
            return
        self.processor.set_rest(samples)
        self.rest_value_lbl.setText(f"{self.processor.rest_min:.1f}")

    def capture_max(self) -> None:
        samples = self._capture_samples()
        if not samples:
            QtWidgets.QMessageBox.information(self, "Calibration", "No samples captured yet.")
            return
        self.processor.set_max(samples)
        self.max_value_lbl.setText(f"{self.processor.max_contraction:.1f}")

    # ------------------------- Cleanup ------------------------- #
    def closeEvent(self, event):  # type: ignore[override]
        self.plot_timer.stop()
        self.serial_thread.stop()
        self.serial_thread.wait(1000)
        try:
            self.midi.close()
        except Exception:
            pass
        super().closeEvent(event)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    window = ProcessingWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


HOW_TO_RUN = """
How to run
==========
1. Install dependencies:
   pip install pyserial pyqtgraph PySide6 mido python-rtmidi numpy
2. Set the serial parameters at the top of processing_page.py:
   - PORT_OVERRIDE = "COM3" (Windows) or "/dev/ttyACM0" (Linux/macOS) if auto-detect is unsuitable.
   - BAUD_RATE = 115200 to match your Arduino sketch.
3. Configure a loopMIDI (or other virtual) output port, then use the MIDI dropdown in the UI to select it and press "Connect MIDI".
4. Ensure the Arduino emits lines formatted as: t_ms,A0 (example: 12345,512) at steady intervals.
5. Run the app:
   python processing_page.py
6. Use "Set Rest" while relaxed and "Set Max" during maximum contraction; watch the raw and processed plots and verify MIDI CC messages are delivered to the chosen port.
"""
