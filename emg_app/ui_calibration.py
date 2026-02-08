"""UI mixin for calibration workflow."""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

from .constants import PINS
from .models import DeviceConfig, TargetKey
from .ui_plots import LivePlotWidget


class CalibrationTabMixin:
	"""Handles calibration tab widgets and actions."""

	def _init_calibration_state(self) -> None:
		self.target_var = tk.StringVar(value="")
		self.target_combo: ttk.Combobox | None = None
		self.cal_status: ttk.Label | None = None
		self.cal_tree: ttk.Treeview | None = None
		self.cal_plot: LivePlotWidget | None = None

	def _build_calibration_tab(self) -> None:
		top = ttk.Frame(self.tab_calibration)
		top.pack(fill="x", padx=10, pady=8)

		ttk.Label(top, text="Target:").pack(side="left")

		self.target_combo = ttk.Combobox(top, textvariable=self.target_var, width=60, state="readonly")
		self.target_combo.pack(side="left", padx=6)
		ttk.Button(top, text="Refresh Targets", command=self._refresh_targets).pack(side="left", padx=6)

		ttk.Button(top, text="Calibrate Selected", command=self._calibrate_selected_target).pack(side="left", padx=6)
		ttk.Button(top, text="Recalibrate Selected", command=self._calibrate_selected_target).pack(side="left")
		tk.Button(top, text="Export Calibrations...", command=self._export_calibration_snapshot).pack(side="left", padx=6)

		self.cal_status = ttk.Label(self.tab_calibration, text="Calibration status: -")
		self.cal_status.pack(anchor="w", padx=10, pady=6)

		cols = ("target", "muscle", "body_part", "baseline", "mvc", "last_%mvc")
		self.cal_tree = ttk.Treeview(self.tab_calibration, columns=cols, show="headings", height=18)
		for col in cols:
			self.cal_tree.heading(col, text=col)
			self.cal_tree.column(col, width=160 if col != "target" else 260)
		self.cal_tree.pack(fill="both", expand=True, padx=10, pady=8)

		plot_frame = ttk.Labelframe(self.tab_calibration, text="Live Signal Preview")
		plot_frame.pack(fill="x", expand=False, padx=10, pady=(0, 10))
		self.cal_plot = LivePlotWidget(plot_frame, title="Calibration Signal", window_seconds=10.0)

		self._refresh_targets()

	def _refresh_targets(self) -> None:
		targets = []
		for device in self.device_configs:
			for pin in PINS:
				if device.channels[pin].enabled:
					muscle = device.channels[pin].muscle or "(unnamed muscle)"
					targets.append(
						f"{device.port} | {pin} | {muscle} | {device.body_part} | {device.device_type}"
					)
		if self.target_combo:
			self.target_combo["values"] = targets
		if targets and not self.target_var.get():
			self.target_var.set(targets[0])
		if hasattr(self, "_refresh_processing_targets"):
			self._refresh_processing_targets()

	def _parse_target_selection(self) -> Optional[Tuple[DeviceConfig, str]]:
		selection = self.target_var.get().strip()
		if not selection:
			return None
		parts = [p.strip() for p in selection.split("|")]
		if len(parts) < 2:
			return None
		port = parts[0]
		pin = parts[1]
		device = next((cfg for cfg in self.device_configs if cfg.port == port), None)
		if not device:
			return None
		return device, pin

	def _calibrate_selected_target(self) -> None:
		parsed = self._parse_target_selection()
		if not parsed:
			messagebox.showerror("No Target", "Select a target in the Calibration tab.")
			return
		device, pin = parsed

		if device.port not in self.dev_mgr.streams:
			messagebox.showwarning("Not Connected", "Device is not connected. Connecting now...")
			self.dev_mgr.start_device(device, self.event_q)
			if self.cal_status:
				self.cal_status.config(text="Calibration status: connecting / waiting for samples...")
			self.update()
			time.sleep(0.8)

		if device.port not in self.dev_mgr.streams:
			messagebox.showerror("Connection Failed", "Could not connect to device for calibration.")
			return

		threading.Thread(target=self.calibration.run_calibration, args=(device, pin), daemon=True).start()

	def _update_live_views(self) -> None:
		if not self.cal_tree:
			return
		for row in self.cal_tree.get_children():
			self.cal_tree.delete(row)

		for tgt, cal in self.calibration.calibrations.items():
			stream = self.dev_mgr.streams.get(tgt.port)
			last_pct = "-"
			if stream and len(stream.envelope[tgt.pin]) > 0:
				_, env_val = stream.envelope[tgt.pin][-1]
				last_pct = f"{self.calibration.compute_percent_mvc(tgt, float(env_val)):.1f}"
			self.cal_tree.insert(
				"",
				tk.END,
				values=(
					tgt.to_str(),
					cal.muscle,
					cal.body_part,
					f"{cal.baseline:.2f}",
					f"{cal.mvc:.2f}",
					last_pct,
				),
			)

		self.after(250, self._update_live_views)
