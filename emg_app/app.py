"""Tk application wiring for the EMG tool."""

from __future__ import annotations

import csv
import queue
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .calibration import CalibrationController
from .serial_io import DeviceManager
from .ui_calibration import CalibrationTabMixin
from .ui_connections import ConnectionsMixin
from .ui_plots import LivePlotWidget
from .ui_processing import ProcessingTabMixin


class EMGApp(tk.Tk, ConnectionsMixin, CalibrationTabMixin, ProcessingTabMixin):
	def __init__(self):
		super().__init__()
		self.title("EMG Multi-Device GUI (Connections + Calibration)")
		self.geometry("1200x760")

		self.event_q = queue.Queue()
		self.dev_mgr = DeviceManager()
		self.calibration = CalibrationController(self.dev_mgr, self.event_q)
		self.raw_tree: ttk.Treeview | None = None
		self.raw_plot: LivePlotWidget | None = None
		self._raw_log_last_sec: dict[str, int] = {}

		self._init_connections_state()
		self._init_calibration_state()
		self._init_processing_state()

		self._build_ui()

		self.after(100, self._poll_events)
		self.after(200, self._update_live_views)
		self.after(1000, self._update_raw_data_table)
		self.after(150, self._update_live_plots)

	def _build_ui(self) -> None:
		self.notebook = ttk.Notebook(self)
		self.notebook.pack(fill="both", expand=True)

		self.tab_connections = ttk.Frame(self.notebook)
		self.tab_calibration = ttk.Frame(self.notebook)
		self.tab_raw_data = ttk.Frame(self.notebook)
		self.tab_processing = ttk.Frame(self.notebook)
		self.tab_plots = ttk.Frame(self.notebook)
		self.tab_midi = ttk.Frame(self.notebook)

		self.notebook.add(self.tab_connections, text="Connections")
		self.notebook.add(self.tab_calibration, text="Calibration")
		self.notebook.add(self.tab_raw_data, text="Raw Data")
		self.notebook.add(self.tab_processing, text="Processing (stub)")
		self.notebook.add(self.tab_plots, text="Plots (stub)")
		self.notebook.add(self.tab_midi, text="MIDI (stub)")

		self._build_connections_tab()
		self._build_calibration_tab()
		self._build_raw_data_tab()
		self._build_processing_tab()
		self._build_stub_tabs()

	def _build_raw_data_tab(self) -> None:
		toolbar = ttk.Frame(self.tab_raw_data)
		toolbar.pack(fill="x", padx=10, pady=(10, 0))
		ttk.Button(toolbar, text="Save Raw Snapshot...", command=self._export_raw_snapshot).pack(side="left")

		columns = (
			"device_port",
			"pin",
			"muscle",
			"body_part",
			"t_ms",
			"raw_value",
			"fs_est",
		)
		container = ttk.Frame(self.tab_raw_data)
		container.pack(fill="both", expand=True, padx=10, pady=10)

		scroll_y = ttk.Scrollbar(container, orient="vertical")
		scroll_y.pack(side="right", fill="y")

		self.raw_tree = ttk.Treeview(
			container,
			columns=columns,
			show="headings",
			height=22,
			yscrollcommand=scroll_y.set,
		)
		self.raw_tree.pack(fill="both", expand=True)
		scroll_y.config(command=self.raw_tree.yview)

		headings = {
			"device_port": "Device Port",
			"pin": "Pin",
			"muscle": "Muscle Name",
			"body_part": "Body Part",
			"t_ms": "Latest t_ms",
			"raw_value": "Latest Raw Value",
			"fs_est": "Estimated Sample Rate (Hz)",
		}
		widths = {
			"device_port": 160,
			"pin": 80,
			"muscle": 200,
			"body_part": 160,
			"t_ms": 140,
			"raw_value": 140,
			"fs_est": 200,
		}
		for col in columns:
			self.raw_tree.heading(col, text=headings[col])
			self.raw_tree.column(col, width=widths[col], anchor="center")

		plot_frame = ttk.Labelframe(self.tab_raw_data, text="Live Signal Preview")
		plot_frame.pack(fill="x", expand=False, padx=10, pady=(0, 10))
		self.raw_plot = LivePlotWidget(plot_frame, title="Raw Data Signal", window_seconds=10.0)

	def _build_stub_tabs(self) -> None:
		ttk.Label(
			self.tab_plots,
			text="Plots / Visualization page will be implemented next.",
		).pack(anchor="w", padx=10, pady=10)

		ttk.Label(
			self.tab_midi,
			text="MIDI / LoopMIDI routing will be implemented next.",
		).pack(anchor="w", padx=10, pady=10)

	def _poll_events(self) -> None:
		try:
			while True:
				event = self.event_q.get_nowait()
				etype = event[0]

				if etype == "device_status":
					_, port, ok, msg = event
					self.dev_mgr.status[port] = (bool(ok), str(msg))
					self._render_device_details()

				elif etype == "cal_status":
					_, msg = event
					if self.cal_status:
						self.cal_status.config(text=f"Calibration status: {msg}")
		except queue.Empty:
			pass

		self.after(80, self._poll_events)

	def on_close(self) -> None:
		self.dev_mgr.stop_all()
		if hasattr(self, "processing_midi"):
			try:
				self.processing_midi.close()
			except Exception:
				pass
		self.destroy()

	def _update_raw_data_table(self) -> None:
		if not self.raw_tree:
			self.after(1000, self._update_raw_data_table)
			return

		enabled_channels = list(self._iter_enabled_channels())
		active_ids = {item["iid"] for item in enabled_channels}
		for iid in list(self._raw_log_last_sec.keys()):
			if iid not in active_ids:
				self._raw_log_last_sec.pop(iid, None)

		for item in enabled_channels:
			port = item["port"]
			pin = item["pin"]
			muscle = item["muscle"]
			body_part = item["body_part"]
			stream = self.dev_mgr.streams.get(port)
			if not stream:
				continue
			buf = stream.raw.get(pin)
			if not buf:
				continue
			t_ms, raw_val = buf[-1]
			second_bucket = int(t_ms // 1000)
			if self._raw_log_last_sec.get(item["iid"]) == second_bucket:
				continue
			self._raw_log_last_sec[item["iid"]] = second_bucket
			fs_display = f"{stream.fs_est_hz:.1f}" if stream.fs_est_hz > 0 else "-"
			values = (
				port,
				pin,
				muscle,
				body_part,
				str(t_ms),
				str(raw_val),
				fs_display,
			)
			self.raw_tree.insert("", 0, values=values)

		self.after(1000, self._update_raw_data_table)

	def _export_raw_snapshot(self) -> None:
		channels = list(self._iter_enabled_channels())
		if not channels:
			messagebox.showinfo("No Channels Enabled", "Enable channels in the Connections tab before exporting.")
			return
		path = filedialog.asksaveasfilename(
			title="Save Raw Snapshot",
			defaultextension=".csv",
			filetypes=[("CSV", "*.csv"), ("All Files", "*.*")],
		)
		if not path:
			return
		with open(path, "w", encoding="utf-8", newline="") as file:
			writer = csv.writer(file)
			writer.writerow([
				"device_port",
				"pin",
				"muscle",
				"body_part",
				"latest_t_ms",
				"latest_raw",
				"fs_est_hz",
			])
			for item in channels:
				port = item["port"]
				pin = item["pin"]
				muscle = item["muscle"]
				body_part = item["body_part"]
				latest_t = "-"
				latest_raw = "-"
				fs_display = "-"
				stream = self.dev_mgr.streams.get(port)
				if stream:
					if stream.fs_est_hz > 0:
						fs_display = f"{stream.fs_est_hz:.1f}"
					buf = stream.raw.get(pin)
					if buf:
						t_ms, raw_val = buf[-1]
						latest_t = str(t_ms)
						latest_raw = str(raw_val)
				writer.writerow((port, pin, muscle, body_part, latest_t, latest_raw, fs_display))
		messagebox.showinfo("Saved", f"Raw snapshot written:\n{path}")

	def _export_calibration_snapshot(self) -> None:
		calibrations = self.calibration.calibrations
		if not calibrations:
			messagebox.showinfo("No Calibrations", "Run at least one calibration before exporting.")
			return
		path = filedialog.asksaveasfilename(
			title="Save Calibration Snapshot",
			defaultextension=".csv",
			filetypes=[("CSV", "*.csv"), ("All Files", "*.*")],
		)
		if not path:
			return
		with open(path, "w", encoding="utf-8", newline="") as file:
			writer = csv.writer(file)
			writer.writerow([
				"device_port",
				"pin",
				"device_type",
				"body_part",
				"muscle",
				"baseline",
				"mvc",
				"ts_unix",
				"last_percent_mvc",
			])
			for tgt, params in calibrations.items():
				last_pct = "-"
				stream = self.dev_mgr.streams.get(tgt.port)
				if stream and stream.envelope[tgt.pin]:
					_, env_val = stream.envelope[tgt.pin][-1]
					last_pct = f"{self.calibration.compute_percent_mvc(tgt, float(env_val)):.1f}"
				writer.writerow(
					[
						tgt.port,
						tgt.pin,
						params.device_type,
						params.body_part,
						params.muscle,
						f"{params.baseline:.6f}",
						f"{params.mvc:.6f}",
						f"{params.ts_unix:.6f}",
						last_pct,
					]
				)
		messagebox.showinfo("Saved", f"Calibration snapshot written:\n{path}")

	def _update_live_plots(self) -> None:
		self._update_raw_plot_widget()
		self._update_calibration_plot_widget()
		self.after(150, self._update_live_plots)

	def _update_raw_plot_widget(self) -> None:
		if not self.raw_plot:
			return
		channel = self._select_plot_channel()
		if not channel:
			self.raw_plot.update([], [], "")
			return
		times, values = self._gather_plot_points(channel["port"], channel["pin"])
		subtitle = f"{channel['port']} {channel['pin']} ({channel['muscle']})"
		self.raw_plot.update(times, values, subtitle=subtitle)

	def _update_calibration_plot_widget(self) -> None:
		if not getattr(self, "cal_plot", None):
			return
		parsed = self._parse_target_selection()
		if parsed:
			device, pin = parsed
			muscle = device.channels[pin].muscle or "(unnamed muscle)"
			channel = {
				"port": device.port,
				"pin": pin,
				"muscle": muscle,
			}
		else:
			channel = self._select_plot_channel()
		if not channel:
			self.cal_plot.update([], [], "")
			return
		times, values = self._gather_plot_points(channel["port"], channel["pin"])
		subtitle = f"{channel['port']} {channel['pin']} ({channel['muscle']})"
		self.cal_plot.update(times, values, subtitle=subtitle)

	def _select_plot_channel(self) -> Optional[dict]:
		for item in self._iter_enabled_channels():
			stream = self.dev_mgr.streams.get(item["port"])
			if not stream:
				continue
			buf = stream.raw.get(item["pin"])
			if buf and len(buf) > 1:
				return item
		return None

	def _gather_plot_points(self, port: str, pin: str, window_seconds: float = 10.0) -> Tuple[List[float], List[float]]:
		stream = self.dev_mgr.streams.get(port)
		if not stream:
			return [], []
		buf = stream.raw.get(pin)
		if not buf:
			return [], []
		data = list(buf)
		if not data:
			return [], []
		latest = data[-1][0]
		window_ms = int(window_seconds * 1000)
		start = max(data[0][0], latest - window_ms)
		filtered = [(t, v) for (t, v) in data if t >= start]
		if not filtered:
			return [], []
		times = [float(t) / 1000.0 for t, _ in filtered]
		values = [float(v) for _, v in filtered]
		return times, values

	def _iter_enabled_channels(self):
		for device in getattr(self, "device_configs", []):
			for pin, channel in device.channels.items():
				if channel.enabled:
					yield {
						"iid": f"{device.port} | {pin}",
						"port": device.port,
						"pin": pin,
						"muscle": channel.muscle or "(unnamed muscle)",
						"body_part": device.body_part,
					}
