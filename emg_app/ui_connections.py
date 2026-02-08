"""UI mixin that encapsulates the Connections tab."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from serial.tools import list_ports

from .constants import BODY_PARTS, DEVICE_TYPES, DEFAULT_BAUD, PINS
from .models import ChannelConfig, DeviceConfig


class ConnectionsMixin:
	"""Adds device management UI and persistence helpers."""

	def _init_connections_state(self) -> None:
		self.device_configs: List[DeviceConfig] = []
		self.available_ports: List[str] = []
		self.ports_var = tk.StringVar(value="")
		self.device_list: tk.Listbox | None = None
		self.details: ttk.Frame | None = None
		self.details_container: ttk.Frame | None = None
		self.device_status_lbl: ttk.Label | None = None

	# ---------------- UI BUILD ----------------
	def _build_connections_tab(self) -> None:
		top = ttk.Frame(self.tab_connections)
		top.pack(fill="x", padx=10, pady=8)

		ttk.Button(top, text="Refresh Ports", command=self._refresh_ports).pack(side="left")
		ttk.Button(top, text="Add Device", command=self._add_device_dialog).pack(side="left", padx=8)
		ttk.Button(top, text="Save Preset...", command=self._save_connections_preset).pack(side="left", padx=8)
		ttk.Button(top, text="Load Preset...", command=self._load_connections_preset).pack(side="left")

		self._refresh_ports()

		mid = ttk.Frame(self.tab_connections)
		mid.pack(fill="both", expand=True, padx=10, pady=8)

		left = ttk.Frame(mid)
		left.pack(side="left", fill="y")

		ttk.Label(left, text="Configured Devices").pack(anchor="w")

		self.device_list = tk.Listbox(left, height=20, width=40)
		self.device_list.pack(fill="y", expand=False)
		self.device_list.bind("<<ListboxSelect>>", lambda _e: self._render_device_details())

		btns = ttk.Frame(left)
		btns.pack(fill="x", pady=6)
		ttk.Button(btns, text="Connect", command=self._connect_selected_device).pack(side="left")
		ttk.Button(btns, text="Disconnect", command=self._disconnect_selected_device).pack(side="left", padx=6)
		ttk.Button(btns, text="Remove", command=self._remove_selected_device).pack(side="left")

		self.device_status_lbl = ttk.Label(left, text="Status: -")
		self.device_status_lbl.pack(anchor="w", pady=6)

		self.details = ttk.Frame(mid)
		self.details.pack(side="left", fill="both", expand=True, padx=12)

		ttk.Label(self.details, text="Device Details").pack(anchor="w")

		self.details_container = ttk.Frame(self.details)
		self.details_container.pack(fill="both", expand=True)

		self._render_device_details()

	# ---------------- Connections: helpers ----------------
	def _refresh_ports(self) -> None:
		ports = [p.device for p in list_ports.comports()]
		self.available_ports = ports

	def _add_device_dialog(self) -> None:
		win = tk.Toplevel(self)
		win.title("Add Device")
		win.geometry("520x430")
		win.transient(self)
		win.grab_set()

		device_type_var = tk.StringVar(value=DEVICE_TYPES[0])
		port_var = tk.StringVar(value=self.available_ports[0] if self.available_ports else "")
		manual_port_var = tk.StringVar(value="")
		body_part_var = tk.StringVar(value=BODY_PARTS[0])
		baud_var = tk.IntVar(value=DEFAULT_BAUD)

		frm = ttk.Frame(win)
		frm.pack(fill="both", expand=True, padx=12, pady=12)

		ttk.Label(frm, text="Device Type:").grid(row=0, column=0, sticky="w")
		ttk.Combobox(frm, values=DEVICE_TYPES, textvariable=device_type_var, state="readonly").grid(
			row=0, column=1, sticky="ew"
		)

		ttk.Label(frm, text="Serial Port:").grid(row=1, column=0, sticky="w")
		ttk.Combobox(frm, values=self.available_ports, textvariable=port_var, state="readonly").grid(
			row=1, column=1, sticky="ew"
		)

		ttk.Label(frm, text="Manual Port Entry (optional):").grid(row=2, column=0, sticky="w")
		ttk.Entry(frm, textvariable=manual_port_var).grid(row=2, column=1, sticky="ew")

		ttk.Label(frm, text="Baud:").grid(row=3, column=0, sticky="w")
		ttk.Entry(frm, textvariable=baud_var).grid(row=3, column=1, sticky="ew")

		ttk.Label(frm, text="Body Part:").grid(row=4, column=0, sticky="w")
		ttk.Combobox(frm, values=BODY_PARTS, textvariable=body_part_var, state="readonly").grid(
			row=4, column=1, sticky="ew"
		)

		ttk.Label(frm, text="Channel Mapping (A0-A5): enable/muscle/notes").grid(
			row=5, column=0, columnspan=2, sticky="w", pady=(10, 2)
		)

		chan_rows: Dict[str, Dict[str, tk.Variable]] = {}
		table = ttk.Frame(frm)
		table.grid(row=6, column=0, columnspan=2, sticky="nsew")

		ttk.Label(table, text="Pin").grid(row=0, column=0, sticky="w")
		ttk.Label(table, text="Enable").grid(row=0, column=1, sticky="w")
		ttk.Label(table, text="Muscle").grid(row=0, column=2, sticky="w")
		ttk.Label(table, text="Notes").grid(row=0, column=3, sticky="w")

		for r, pin in enumerate(PINS, start=1):
			enabled_var = tk.BooleanVar(value=False)
			muscle_var = tk.StringVar(value="")
			notes_var = tk.StringVar(value="")
			ttk.Label(table, text=pin).grid(row=r, column=0, sticky="w")
			ttk.Checkbutton(table, variable=enabled_var).grid(row=r, column=1, sticky="w")
			ttk.Entry(table, textvariable=muscle_var, width=18).grid(row=r, column=2, sticky="ew")
			ttk.Entry(table, textvariable=notes_var, width=24).grid(row=r, column=3, sticky="ew")
			chan_rows[pin] = {"enabled": enabled_var, "muscle": muscle_var, "notes": notes_var}

		frm.columnconfigure(1, weight=1)
		table.columnconfigure(2, weight=1)
		table.columnconfigure(3, weight=2)

		def on_add() -> None:
			port = manual_port_var.get().strip() or port_var.get().strip()
			if not port:
				messagebox.showerror("Missing Port", "Select a port or enter a manual port.")
				return

			channels = {}
			for pin in PINS:
				channels[pin] = ChannelConfig(
					enabled=bool(chan_rows[pin]["enabled"].get()),
					muscle=str(chan_rows[pin]["muscle"].get()).strip(),
					notes=str(chan_rows[pin]["notes"].get()).strip(),
				)

			cfg = DeviceConfig(
				device_type=device_type_var.get(),
				port=port,
				baud=int(baud_var.get()),
				body_part=body_part_var.get(),
				channels=channels,
			)
			self.device_configs.append(cfg)
			self._refresh_device_list()
			win.destroy()

		ttk.Button(frm, text="Add Device", command=on_add).grid(row=7, column=0, columnspan=2, pady=12)

	def _refresh_device_list(self) -> None:
		if not self.device_list:
			return
		self.device_list.delete(0, tk.END)
		for i, device in enumerate(self.device_configs):
			self.device_list.insert(tk.END, f"[{i}] {device.device_type} @ {device.port} ({device.body_part})")
		self._refresh_targets()

	def _get_selected_device_index(self) -> Optional[int]:
		if not self.device_list:
			return None
		sel = self.device_list.curselection()
		if not sel:
			return None
		return int(sel[0])

	def _render_device_details(self) -> None:
		if not self.details_container:
			return
		for child in self.details_container.winfo_children():
			child.destroy()

		idx = self._get_selected_device_index()
		if idx is None:
			ttk.Label(self.details_container, text="Select a device to view/edit details.").pack(anchor="w")
			return

		device = self.device_configs[idx]
		status = self.dev_mgr.status.get(device.port, (False, "not connected"))
		if self.device_status_lbl:
			self.device_status_lbl.config(text=f"Status: {status[1]}")

		frm = ttk.Frame(self.details_container)
		frm.pack(fill="both", expand=True)

		device_type_var = tk.StringVar(value=device.device_type)
		port_var = tk.StringVar(value=device.port)
		baud_var = tk.IntVar(value=device.baud)
		body_part_var = tk.StringVar(value=device.body_part)

		ttk.Label(frm, text="Device Type:").grid(row=0, column=0, sticky="w")
		ttk.Combobox(frm, values=DEVICE_TYPES, textvariable=device_type_var, state="readonly").grid(
			row=0, column=1, sticky="ew"
		)

		ttk.Label(frm, text="Port:").grid(row=1, column=0, sticky="w")
		ttk.Entry(frm, textvariable=port_var).grid(row=1, column=1, sticky="ew")

		ttk.Label(frm, text="Baud:").grid(row=2, column=0, sticky="w")
		ttk.Entry(frm, textvariable=baud_var).grid(row=2, column=1, sticky="ew")

		ttk.Label(frm, text="Body Part:").grid(row=3, column=0, sticky="w")
		ttk.Combobox(frm, values=BODY_PARTS, textvariable=body_part_var, state="readonly").grid(
			row=3, column=1, sticky="ew"
		)

		ttk.Label(frm, text="Channel Mapping (A0-A5)").grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 2))

		table = ttk.Frame(frm)
		table.grid(row=5, column=0, columnspan=2, sticky="nsew")

		ttk.Label(table, text="Pin").grid(row=0, column=0, sticky="w")
		ttk.Label(table, text="Enable").grid(row=0, column=1, sticky="w")
		ttk.Label(table, text="Muscle").grid(row=0, column=2, sticky="w")
		ttk.Label(table, text="Notes").grid(row=0, column=3, sticky="w")

		chan_vars: Dict[str, Dict[str, tk.Variable]] = {}
		for r, pin in enumerate(PINS, start=1):
			enabled_var = tk.BooleanVar(value=device.channels[pin].enabled)
			muscle_var = tk.StringVar(value=device.channels[pin].muscle)
			notes_var = tk.StringVar(value=device.channels[pin].notes)
			ttk.Label(table, text=pin).grid(row=r, column=0, sticky="w")
			ttk.Checkbutton(table, variable=enabled_var).grid(row=r, column=1, sticky="w")
			ttk.Entry(table, textvariable=muscle_var, width=18).grid(row=r, column=2, sticky="ew")
			ttk.Entry(table, textvariable=notes_var, width=24).grid(row=r, column=3, sticky="ew")
			chan_vars[pin] = {"enabled": enabled_var, "muscle": muscle_var, "notes": notes_var}

		frm.columnconfigure(1, weight=1)
		table.columnconfigure(2, weight=1)
		table.columnconfigure(3, weight=2)

		def on_save_edits() -> None:
			device.device_type = device_type_var.get()
			device.port = port_var.get().strip()
			device.baud = int(baud_var.get())
			device.body_part = body_part_var.get()

			for pin in PINS:
				device.channels[pin].enabled = bool(chan_vars[pin]["enabled"].get())
				device.channels[pin].muscle = str(chan_vars[pin]["muscle"].get()).strip()
				device.channels[pin].notes = str(chan_vars[pin]["notes"].get()).strip()

			self._refresh_device_list()
			messagebox.showinfo("Saved", "Device configuration updated.")
			self._render_device_details()

		ttk.Button(frm, text="Save Edits", command=on_save_edits).grid(row=6, column=0, columnspan=2, pady=12)

	def _connect_selected_device(self) -> None:
		idx = self._get_selected_device_index()
		if idx is None:
			return
		cfg = self.device_configs[idx]
		self.dev_mgr.start_device(cfg, self.event_q)
		self._refresh_targets()

	def _disconnect_selected_device(self) -> None:
		idx = self._get_selected_device_index()
		if idx is None:
			return
		cfg = self.device_configs[idx]
		self.dev_mgr.stop_device(cfg.port)
		self._refresh_targets()

	def _remove_selected_device(self) -> None:
		idx = self._get_selected_device_index()
		if idx is None:
			return
		cfg = self.device_configs[idx]
		self.dev_mgr.stop_device(cfg.port)
		self.device_configs.pop(idx)
		self._refresh_device_list()
		self._render_device_details()

	def _save_connections_preset(self) -> None:
		path = filedialog.asksaveasfilename(
			title="Save Connections Preset",
			defaultextension=".json",
			filetypes=[("JSON", "*.json")],
		)
		if not path:
			return

		payload = []
		for device in self.device_configs:
			payload.append(
				{
					"device_type": device.device_type,
					"port": device.port,
					"baud": device.baud,
					"body_part": device.body_part,
					"channels": {pin: asdict(device.channels[pin]) for pin in PINS},
				}
			)

		with open(path, "w", encoding="utf-8") as file:
			json.dump({"devices": payload}, file, indent=2)

		messagebox.showinfo("Saved", f"Connections preset saved:\n{path}")

	def _load_connections_preset(self) -> None:
		path = filedialog.askopenfilename(
			title="Load Connections Preset",
			filetypes=[("JSON", "*.json")],
		)
		if not path:
			return

		try:
			with open(path, "r", encoding="utf-8") as file:
				data = json.load(file)
			devices = data.get("devices", [])
			loaded: List[DeviceConfig] = []
			for record in devices:
				ch = {}
				ch_raw = record.get("channels", {})
				for pin in PINS:
					c = ch_raw.get(pin, {})
					ch[pin] = ChannelConfig(
						enabled=bool(c.get("enabled", False)),
						muscle=str(c.get("muscle", "")),
						notes=str(c.get("notes", "")),
					)
				loaded.append(
					DeviceConfig(
						device_type=str(record.get("device_type", DEVICE_TYPES[0])),
						port=str(record.get("port", "")),
						baud=int(record.get("baud", DEFAULT_BAUD)),
						body_part=str(record.get("body_part", BODY_PARTS[0])),
						channels=ch,
					)
				)
			self.dev_mgr.stop_all()
			self.device_configs = loaded
			self._refresh_device_list()
			self._render_device_details()
			messagebox.showinfo("Loaded", f"Loaded preset:\n{path}")
		except Exception as exc:
			messagebox.showerror("Load Failed", f"Could not load preset:\n{exc}")
