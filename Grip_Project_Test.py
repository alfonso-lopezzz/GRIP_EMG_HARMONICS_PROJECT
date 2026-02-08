"""Live Arduino voltage plotter proof of concept."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import serial
from serial.tools import list_ports


# User-tunable settings
PORT_OVERRIDE: Optional[str] = None  # e.g., "COM3" on Windows or "/dev/ttyACM0" on Linux
BAUD_RATE = 115200
VREF_VOLTS = 5.0
WINDOW_SECONDS = 10.0
REFRESH_INTERVAL_SEC = 0.05  # 20 FPS


def detect_serial_port(port_hint: Optional[str]) -> Optional[str]:
	"""Return an explicit port if provided, else try to auto-detect."""
	if port_hint:
		return port_hint

	ports = list(list_ports.comports())
	if not ports:
		return None

	for candidate in ports:
		descriptor = (candidate.description or "").lower()
		if any(keyword in descriptor for keyword in ("arduino", "ch340", "ttyacm", "ttyusb")):
			return candidate.device

	return ports[0].device


def parse_voltage(line: str) -> Optional[float]:
	"""Convert a line of serial text into volts (Option A or B)."""
	cleaned = line.strip()
	if not cleaned:
		return None

	try:
		value = float(cleaned)
	except ValueError:
		return None

	if ("." not in cleaned) and ("e" not in cleaned.lower()) and 0.0 <= value <= 1023.0:
		return value * (VREF_VOLTS / 1023.0)
	return value


def serial_reader(
	ser: serial.Serial,
	buffer: Deque[Tuple[float, float]],
	lock: threading.Lock,
	stop_event: threading.Event,
) -> None:
	"""Background loop that pulls lines from serial and adds them to the buffer."""
	print("Connected. Receiving data...")
	while not stop_event.is_set():
		try:
			line_bytes = ser.readline()
		except serial.SerialException:
			print("Serial read error; stopping reader.")
			stop_event.set()
			break

		if not line_bytes:
			continue

		try:
			decoded = line_bytes.decode("utf-8", errors="ignore")
		except UnicodeDecodeError:
			continue

		voltage = parse_voltage(decoded)
		if voltage is None:
			continue

		timestamp = time.time()
		with lock:
			buffer.append((timestamp, voltage))
			cutoff = timestamp - WINDOW_SECONDS
			while buffer and buffer[0][0] < cutoff:
				buffer.popleft()


def configure_axes(ax: plt.Axes, port_name: str, baud: int) -> None:
	"""Apply consistent labeling and title."""
	ax.set_title(f"Live Voltage — {port_name} @ {baud} bps")
	ax.set_xlabel("Time (s)")
	ax.set_ylabel("Voltage (V)")
	ax.grid(True, linestyle="--", alpha=0.3)


def main() -> None:
	port_name = detect_serial_port(PORT_OVERRIDE)
	if not port_name:
		print("No serial ports found. Set PORT_OVERRIDE to your Arduino port.")
		return

	try:
		ser = serial.Serial(port_name, BAUD_RATE, timeout=1)
	except serial.SerialException as exc:
		print(f"Failed to open {port_name}: {exc}")
		return

	data_buffer: Deque[Tuple[float, float]] = deque()
	data_lock = threading.Lock()
	stop_event = threading.Event()
	start_time = time.time()

	reader_thread = threading.Thread(
		target=serial_reader,
		args=(ser, data_buffer, data_lock, stop_event),
		daemon=True,
	)
	reader_thread.start()

	fig, ax = plt.subplots()
	configure_axes(ax, port_name, BAUD_RATE)
	(line_handle,) = ax.plot([], [], lw=1.5)

	def update_plot(_frame: int):
		with data_lock:
			if not data_buffer:
				return line_handle,

			timestamps, voltages = zip(*data_buffer)

		elapsed = [t - start_time for t in timestamps]
		line_handle.set_data(elapsed, voltages)

		now_elapsed = time.time() - start_time
		ax.set_xlim(max(0.0, now_elapsed - WINDOW_SECONDS), max(WINDOW_SECONDS, now_elapsed))

		v_min = min(voltages)
		v_max = max(voltages)
		span = max(0.1, v_max - v_min)
		padding = span * 0.1
		ax.set_ylim(v_min - padding, v_max + padding)
		return line_handle,

	def handle_close(_event):
		stop_event.set()

	fig.canvas.mpl_connect("close_event", handle_close)

	try:
		animation = FuncAnimation(
			fig,
			update_plot,
			interval=int(REFRESH_INTERVAL_SEC * 1000),
			blit=True,
		)
		plt.show()
	finally:
		stop_event.set()
		reader_thread.join(timeout=2.0)
		ser.close()


if __name__ == "__main__":
	main()


HOW_TO_RUN = """
How to run
==========
1. Install dependencies:
   pip install pyserial matplotlib
2. Set the serial parameters near the top of this file:
   - PORT_OVERRIDE = "COM3" (Windows) or "/dev/ttyACM0" (Linux/macOS) if auto-detect fails.
   - BAUD_RATE = 115200 unless your Arduino sketch uses a different value.
3. Ensure your Arduino sketch prints one floating-point voltage per line OR an integer ADC value (0-1023).
   Example serial output:
	 0.512\n
	 0.498\n
	 0.505\n
   or, for ADC counts:
	 512\n
	 498\n
	 505\n
4. Run the script:
   python Grip_Project_Test.py
"""
