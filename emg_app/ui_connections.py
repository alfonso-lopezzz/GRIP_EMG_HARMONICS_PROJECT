"""UI mixin that encapsulates the Connections tab."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from serial.tools import list_ports

from .constants import (
	BLE_UART_SERVICE_UUID,
	BODY_PARTS,
	CONNECTION_TYPES,
	DEFAULT_BAUD,
	DEVICE_TYPES,
	PINS,
)
from .models import ChannelConfig, DeviceConfig
from .serial_io import HAS_BLEAK

if HAS_BLEAK:
	from bleak import BleakScanner


class ConnectionsMixin:
	"""Adds device management UI and persistence helpers."""

	def _init_connections_state(self) -> None:
		self.device_configs: List[DeviceConfig] = []
		self.available_ports: List[str] = []
		self.available_ble_devices: List[Tuple[str, str]] = []  # (name, address)
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

	def _scan_ble_devices_bg(self, callback) -> None:
		"""Scan for BLE devices in a background thread, then call *callback* on the main thread."""
		if not HAS_BLEAK:
			return

		def _worker():
			try:
				loop = asyncio.new_event_loop()
				devices = loop.run_until_complete(BleakScanner.discover(timeout=5.0))
				results = [(d.name or "Unknown", d.address) for d in devices]
			except Exception:
				results = []
			self.available_ble_devices = results
			# Schedule callback on the Tk main thread
			try:
				self.after(0, callback)
			except Exception:
				pass

		threading.Thread(target=_worker, daemon=True).start()

	def _add_device_dialog(self) -> None:
		win = tk.Toplevel(self)
		win.title("Add Device")
		win.geometry("580x530")
		win.transient(self)
		win.grab_set()

		device_type_var = tk.StringVar(value=DEVICE_TYPES[0])
		conn_type_var = tk.StringVar(value=CONNECTION_TYPES[0])
		port_var = tk.StringVar(value=self.available_ports[0] if self.available_ports else "")
		manual_port_var = tk.StringVar(value="")
		body_part_var = tk.StringVar(value=BODY_PARTS[0])
		baud_var = tk.IntVar(value=DEFAULT_BAUD)
		ble_device_var = tk.StringVar(value="")
		ble_manual_addr_var = tk.StringVar(value="")
		ble_service_uuid_var = tk.StringVar(value=BLE_UART_SERVICE_UUID)

		frm = ttk.Frame(win)
		frm.pack(fill="both", expand=True, padx=12, pady=12)

		# Row 0: Device type
		ttk.Label(frm, text="Device Type:").grid(row=0, column=0, sticky="w")
		ttk.Combobox(frm, values=DEVICE_TYPES, textvariable=device_type_var, state="readonly").grid(
			row=0, column=1, sticky="ew"
		)

		# Row 1: Connection type
		ttk.Label(frm, text="Connection Type:").grid(row=1, column=0, sticky="w")
		conn_combo = ttk.Combobox(frm, values=CONNECTION_TYPES, textvariable=conn_type_var, state="readonly")
		conn_combo.grid(row=1, column=1, sticky="ew")

		# Row 2: Serial port (for Serial / BT Classic)
		serial_port_lbl = ttk.Label(frm, text="Serial Port:")
		serial_port_lbl.grid(row=2, column=0, sticky="w")
		serial_port_combo = ttk.Combobox(frm, values=self.available_ports, textvariable=port_var, state="readonly")
		serial_port_combo.grid(row=2, column=1, sticky="ew")

		# Row 3: Manual port entry
		manual_lbl = ttk.Label(frm, text="Manual Port Entry (optional):")
		manual_lbl.grid(row=3, column=0, sticky="w")
		manual_entry = ttk.Entry(frm, textvariable=manual_port_var)
		manual_entry.grid(row=3, column=1, sticky="ew")

		# Row 4: Baud
		baud_lbl = ttk.Label(frm, text="Baud:")
		baud_lbl.grid(row=4, column=0, sticky="w")
		baud_entry = ttk.Entry(frm, textvariable=baud_var)
		baud_entry.grid(row=4, column=1, sticky="ew")

		# Row 5: BLE device combo (for BLE)
		ble_dev_lbl = ttk.Label(frm, text="BLE Device:")
		ble_dev_lbl.grid(row=5, column=0, sticky="w")
		ble_dev_combo = ttk.Combobox(frm, textvariable=ble_device_var, state="readonly", width=40)
		ble_dev_combo.grid(row=5, column=1, sticky="ew")

		# Row 6: BLE scan / refresh
		ble_scan_frame = ttk.Frame(frm)
		ble_scan_frame.grid(row=6, column=0, columnspan=2, sticky="w")
		ble_scan_btn = ttk.Button(ble_scan_frame, text="Scan for BLE Devices", command=lambda: self._dialog_ble_scan(ble_dev_combo, ble_scan_status))
		ble_scan_btn.pack(side="left")
		ble_scan_status = ttk.Label(ble_scan_frame, text="")
		ble_scan_status.pack(side="left", padx=8)

		# Row 7: Manual BLE address
		ble_addr_lbl = ttk.Label(frm, text="Manual BLE Address:")
		ble_addr_lbl.grid(row=7, column=0, sticky="w")
		ble_addr_entry = ttk.Entry(frm, textvariable=ble_manual_addr_var)
		ble_addr_entry.grid(row=7, column=1, sticky="ew")

		# Row 8: BLE service UUID
		ble_uuid_lbl = ttk.Label(frm, text="BLE UART Service UUID:")
		ble_uuid_lbl.grid(row=8, column=0, sticky="w")
		ble_uuid_entry = ttk.Entry(frm, textvariable=ble_service_uuid_var)
		ble_uuid_entry.grid(row=8, column=1, sticky="ew")

		# BLE not-installed notice
		ble_notice_lbl = ttk.Label(frm, text="Install 'bleak' for BLE support.", foreground="gray")
		ble_notice_lbl.grid(row=9, column=0, columnspan=2, sticky="w")

		# Row 10: Body part
		ttk.Label(frm, text="Body Part:").grid(row=10, column=0, sticky="w")
		ttk.Combobox(frm, values=BODY_PARTS, textvariable=body_part_var, state="readonly").grid(
			row=10, column=1, sticky="ew"
		)

		# Row 11: Channels header
		ttk.Label(frm, text="Channel Mapping (A0-A5): enable/muscle/notes").grid(
			row=11, column=0, columnspan=2, sticky="w", pady=(10, 2)
		)

		chan_rows: Dict[str, Dict[str, tk.Variable]] = {}
		table = ttk.Frame(frm)
		table.grid(row=12, column=0, columnspan=2, sticky="nsew")

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

		# -- Visibility helpers for serial vs BLE fields --
		serial_widgets = [serial_port_lbl, serial_port_combo, manual_lbl, manual_entry, baud_lbl, baud_entry]
		ble_widgets = [ble_dev_lbl, ble_dev_combo, ble_scan_frame, ble_addr_lbl, ble_addr_entry, ble_uuid_lbl, ble_uuid_entry]

		def _toggle_fields(*_args) -> None:
			conn = conn_type_var.get()
			is_ble = (conn == "Bluetooth LE (BLE)")
			for w in serial_widgets:
				if is_ble:
					w.grid_remove()
				else:
					w.grid()
			for w in ble_widgets:
				if is_ble:
					w.grid()
				else:
					w.grid_remove()
			# Show notice only when BLE is selected and bleak is missing
			if is_ble and not HAS_BLEAK:
				ble_notice_lbl.grid()
			else:
				ble_notice_lbl.grid_remove()

		conn_type_var.trace_add("write", _toggle_fields)
		_toggle_fields()  # apply initial state

		def on_add() -> None:
			conn = conn_type_var.get()
			is_ble = (conn == "Bluetooth LE (BLE)")

			if is_ble:
				# Resolve BLE address from combo or manual entry
				ble_sel = ble_device_var.get().strip()
				ble_addr = ble_manual_addr_var.get().strip()
				if ble_addr:
					port = ble_addr
				elif ble_sel:
					# Parse "Name (AA:BB:CC:DD:EE:FF)" format
					if "(" in ble_sel and ")" in ble_sel:
						port = ble_sel.rsplit("(", 1)[1].rstrip(")")
					else:
						port = ble_sel
				else:
					messagebox.showerror("Missing BLE Device", "Select a BLE device or enter a manual address.")
					return
				ble_uuid = ble_service_uuid_var.get().strip()
			else:
				port = manual_port_var.get().strip() or port_var.get().strip()
				ble_uuid = ""
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
				connection_type=conn,
				ble_service_uuid=ble_uuid if is_ble else "",
			)
			self.device_configs.append(cfg)
			self._refresh_device_list()
			win.destroy()

		ttk.Button(frm, text="Add Device", command=on_add).grid(row=13, column=0, columnspan=2, pady=12)

	def _dialog_ble_scan(self, combo: ttk.Combobox, status_lbl: ttk.Label) -> None:
		"""Trigger a BLE scan and populate *combo* with results."""
		if not HAS_BLEAK:
			status_lbl.config(text="bleak not installed")
			return
		status_lbl.config(text="Scanning...")

		def _on_done():
			values = [f"{name} ({addr})" for name, addr in self.available_ble_devices]
			combo["values"] = values
			if values:
				combo.current(0)
			status_lbl.config(text=f"Found {len(values)} device(s)")

		self._scan_ble_devices_bg(_on_done)

	def _refresh_device_list(self) -> None:
		if not self.device_list:
			return
		self.device_list.delete(0, tk.END)
		for i, device in enumerate(self.device_configs):
			conn_tag = ""
			if getattr(device, "connection_type", "") == "Bluetooth LE (BLE)":
				conn_tag = " [BLE]"
			elif getattr(device, "connection_type", "") == "Bluetooth Classic (SPP)":
				conn_tag = " [BT]"
			self.device_list.insert(tk.END, f"[{i}] {device.device_type} @ {device.port}{conn_tag} ({device.body_part})")
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
		conn_type_var = tk.StringVar(value=getattr(device, "connection_type", "Serial (USB/Wired)"))
		port_var = tk.StringVar(value=device.port)
		baud_var = tk.IntVar(value=device.baud)
		body_part_var = tk.StringVar(value=device.body_part)

		ttk.Label(frm, text="Device Type:").grid(row=0, column=0, sticky="w")
		ttk.Combobox(frm, values=DEVICE_TYPES, textvariable=device_type_var, state="readonly").grid(
			row=0, column=1, sticky="ew"
		)

		ttk.Label(frm, text="Connection Type:").grid(row=1, column=0, sticky="w")
		ttk.Label(frm, textvariable=conn_type_var).grid(row=1, column=1, sticky="w")

		ttk.Label(frm, text="Port / Address:").grid(row=2, column=0, sticky="w")
		ttk.Entry(frm, textvariable=port_var).grid(row=2, column=1, sticky="ew")

		ttk.Label(frm, text="Baud:").grid(row=3, column=0, sticky="w")
		ttk.Entry(frm, textvariable=baud_var).grid(row=3, column=1, sticky="ew")

		ttk.Label(frm, text="Body Part:").grid(row=4, column=0, sticky="w")
		ttk.Combobox(frm, values=BODY_PARTS, textvariable=body_part_var, state="readonly").grid(
			row=4, column=1, sticky="ew"
		)

		ttk.Label(frm, text="Channel Mapping (A0-A5)").grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 2))

		table = ttk.Frame(frm)
		table.grid(row=6, column=0, columnspan=2, sticky="nsew")

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

		ttk.Button(frm, text="Save Edits", command=on_save_edits).grid(row=7, column=0, columnspan=2, pady=12)

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
					"connection_type": getattr(device, "connection_type", "Serial (USB/Wired)"),
					"ble_service_uuid": getattr(device, "ble_service_uuid", ""),
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
						connection_type=str(record.get("connection_type", "Serial (USB/Wired)")),
						ble_service_uuid=str(record.get("ble_service_uuid", "")),
					)
				)
			self.dev_mgr.stop_all()
			self.device_configs = loaded
			self._refresh_device_list()
			self._render_device_details()
			messagebox.showinfo("Loaded", f"Loaded preset:\n{path}")
		except Exception as exc:
			messagebox.showerror("Load Failed", f"Could not load preset:\n{exc}")
