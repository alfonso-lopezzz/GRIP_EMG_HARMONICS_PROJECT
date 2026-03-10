"""MIDI tab mixin – dedicated MIDI control center for the EMG Tk app."""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import messagebox, ttk

from .constants import CAPTURE_SECONDS, RAW_BUFFER_SECONDS
from .midi_controller import MidiController, MIDI_CC_NUMBER, MIDI_CHANNEL, MIDI_MAX_RATE_HZ
from .processing_core import EMGProcessor, MIDI_VALUE_MAX
from .ui_plots import LivePlotWidget

_MIDI_PLOT_WINDOW_SEC = 10.0
_MIDI_LOG_MAX = 50
_MIDI_HISTORY_MAX = 20000
_METER_WIDTH = 500
_METER_HEIGHT = 28


class MidiTabMixin:
    """Adds a fully-functional MIDI configuration / monitoring tab."""

    # ------------------------------------------------------------------ #
    #  State initialisation                                                #
    # ------------------------------------------------------------------ #
    def _init_midi_state(self) -> None:
        # --- own MidiController (independent of Processing tab) ---
        self.midi_controller = MidiController(
            cc_number=MIDI_CC_NUMBER,
            channel=MIDI_CHANNEL,
            max_rate_hz=MIDI_MAX_RATE_HZ,
        )

        # Tk variables
        self.midi_enabled_var = tk.BooleanVar(value=True)
        self.midi_cc_var = tk.IntVar(value=MIDI_CC_NUMBER)
        self.midi_channel_var = tk.IntVar(value=MIDI_CHANNEL)
        self.midi_rate_var = tk.DoubleVar(value=MIDI_MAX_RATE_HZ)
        self.midi_target_var = tk.StringVar(value="")
        self._midi_connected = False

        # Counters / buffers
        self.midi_messages_sent: int = 0
        self.midi_log_entries: Deque[Tuple[str, int, float, float]] = deque(maxlen=_MIDI_LOG_MAX)
        self.midi_plot_history: Deque[Tuple[float, float]] = deque(maxlen=_MIDI_HISTORY_MAX)

        # Per-target EMGProcessor cache (same pattern as ProcessingTabMixin)
        self._midi_processors: Dict[str, EMGProcessor] = {}
        self._midi_cal_versions: Dict[str, float] = {}
        self._midi_last_seen_ts: Dict[str, int] = {}
        self._midi_capture_bufs: Dict[str, Deque[float]] = {}

        # Widget refs (assigned in _build)
        self._midi_port_combo: Optional[ttk.Combobox] = None
        self._midi_connect_btn: Optional[ttk.Button] = None
        self._midi_status_lbl: Optional[ttk.Label] = None
        self._midi_target_combo: Optional[ttk.Combobox] = None
        self._midi_meter_canvas: Optional[tk.Canvas] = None
        self._midi_cc_lbl: Optional[ttk.Label] = None
        self._midi_env_lbl: Optional[ttk.Label] = None
        self._midi_raw_lbl: Optional[ttk.Label] = None
        self._midi_fs_lbl: Optional[ttk.Label] = None
        self._midi_sent_lbl: Optional[ttk.Label] = None
        self._midi_log_tree: Optional[ttk.Treeview] = None
        self._midi_plot: Optional[LivePlotWidget] = None

    # ------------------------------------------------------------------ #
    #  UI build                                                            #
    # ------------------------------------------------------------------ #
    def _build_midi_tab(self) -> None:
        outer = ttk.Frame(self.tab_midi)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # ---- A. Connection & configuration ----
        conn_frame = ttk.Labelframe(outer, text="Connection & Configuration")
        conn_frame.pack(fill="x", pady=(0, 6))

        row1 = ttk.Frame(conn_frame)
        row1.pack(fill="x", padx=8, pady=4)

        ttk.Label(row1, text="MIDI Output Port:").pack(side="left")
        self._midi_port_combo = ttk.Combobox(row1, width=36, state="readonly")
        self._midi_port_combo.pack(side="left", padx=4)
        ttk.Button(row1, text="Refresh Ports", command=self._midi_refresh_ports).pack(side="left", padx=2)
        self._midi_connect_btn = ttk.Button(row1, text="Connect", command=self._midi_toggle_connection)
        self._midi_connect_btn.pack(side="left", padx=4)
        self._midi_status_lbl = ttk.Label(row1, text="Disconnected")
        self._midi_status_lbl.pack(side="left", padx=8)

        row2 = ttk.Frame(conn_frame)
        row2.pack(fill="x", padx=8, pady=4)

        ttk.Label(row2, text="CC #:").pack(side="left")
        cc_spin = ttk.Spinbox(
            row2, from_=0, to=127, width=5,
            textvariable=self.midi_cc_var,
            command=self._midi_on_config_change,
        )
        cc_spin.pack(side="left", padx=(2, 10))
        cc_spin.bind("<Return>", lambda _: self._midi_on_config_change())

        ttk.Label(row2, text="Channel:").pack(side="left")
        ch_spin = ttk.Spinbox(
            row2, from_=1, to=16, width=4,
            textvariable=self.midi_channel_var,
            command=self._midi_on_config_change,
        )
        ch_spin.pack(side="left", padx=(2, 10))
        ch_spin.bind("<Return>", lambda _: self._midi_on_config_change())

        ttk.Label(row2, text="Max Rate (Hz):").pack(side="left")
        rate_spin = ttk.Spinbox(
            row2, from_=1, to=1000, width=6,
            textvariable=self.midi_rate_var,
            command=self._midi_on_config_change,
        )
        rate_spin.pack(side="left", padx=(2, 10))
        rate_spin.bind("<Return>", lambda _: self._midi_on_config_change())

        ttk.Checkbutton(
            row2, text="Enable MIDI Output",
            variable=self.midi_enabled_var,
        ).pack(side="left", padx=10)

        # ---- B. Target channel ----
        target_frame = ttk.Frame(outer)
        target_frame.pack(fill="x", pady=(0, 6))
        ttk.Label(target_frame, text="Target Channel:").pack(side="left")
        self._midi_target_combo = ttk.Combobox(
            target_frame, textvariable=self.midi_target_var, width=50, state="readonly",
        )
        self._midi_target_combo.pack(side="left", padx=6)
        ttk.Button(target_frame, text="Refresh", command=self._midi_refresh_targets).pack(side="left")

        # ---- C. Meter & readouts ----
        meter_frame = ttk.Labelframe(outer, text="Live MIDI Meter & Readouts")
        meter_frame.pack(fill="x", pady=(0, 6))

        self._midi_meter_canvas = tk.Canvas(
            meter_frame, width=_METER_WIDTH, height=_METER_HEIGHT,
            bg="#222222", highlightthickness=0,
        )
        self._midi_meter_canvas.pack(padx=8, pady=(6, 2), anchor="w")
        # draw initial empty bar
        self._midi_meter_rect = self._midi_meter_canvas.create_rectangle(
            0, 0, 0, _METER_HEIGHT, fill="#22cc44", outline="",
        )

        readout = ttk.Frame(meter_frame)
        readout.pack(fill="x", padx=8, pady=(2, 6))
        self._midi_cc_lbl = ttk.Label(readout, text="MIDI CC: ---", font=("TkDefaultFont", 14, "bold"))
        self._midi_cc_lbl.pack(side="left", padx=(0, 16))
        self._midi_env_lbl = ttk.Label(readout, text="Envelope: -")
        self._midi_env_lbl.pack(side="left", padx=8)
        self._midi_raw_lbl = ttk.Label(readout, text="Raw: -")
        self._midi_raw_lbl.pack(side="left", padx=8)
        self._midi_fs_lbl = ttk.Label(readout, text="Fs: - Hz")
        self._midi_fs_lbl.pack(side="left", padx=8)
        self._midi_sent_lbl = ttk.Label(readout, text="Messages Sent: 0")
        self._midi_sent_lbl.pack(side="left", padx=8)

        # ---- D. Activity log ----
        log_frame = ttk.Labelframe(outer, text="MIDI Activity Log")
        log_frame.pack(fill="x", pady=(0, 6))

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Button(log_toolbar, text="Clear Log", command=self._midi_clear_log).pack(side="left")

        log_cols = ("time", "cc_value", "envelope", "raw")
        self._midi_log_tree = ttk.Treeview(
            log_frame, columns=log_cols, show="headings", height=6,
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self._midi_log_tree.yview)
        self._midi_log_tree.configure(yscrollcommand=log_scroll.set)
        headings = {"time": "Time", "cc_value": "CC Value", "envelope": "Envelope", "raw": "Raw"}
        widths = {"time": 100, "cc_value": 80, "envelope": 100, "raw": 100}
        for col in log_cols:
            self._midi_log_tree.heading(col, text=headings[col])
            self._midi_log_tree.column(col, width=widths[col], anchor="center")
        self._midi_log_tree.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=(2, 6))
        log_scroll.pack(side="left", fill="y", pady=(2, 6), padx=(0, 8))

        # ---- E. Live plot ----
        plot_frame = ttk.Labelframe(outer, text="MIDI CC Output")
        plot_frame.pack(fill="both", expand=True, pady=(0, 0))
        self._midi_plot = LivePlotWidget(
            plot_frame,
            title="MIDI CC Output",
            window_seconds=_MIDI_PLOT_WINDOW_SEC,
        )

        # Populate combos & start periodic update
        self._midi_refresh_ports()
        self._midi_refresh_targets()
        self.after(120, self._update_midi_tab)

    # ------------------------------------------------------------------ #
    #  Port helpers                                                        #
    # ------------------------------------------------------------------ #
    def _midi_refresh_ports(self) -> None:
        if not self._midi_port_combo:
            return
        current = self._midi_port_combo.get()
        try:
            ports = self.midi_controller.list_ports()
        except Exception as exc:
            ports = []
            if self._midi_status_lbl:
                self._midi_status_lbl.config(text=str(exc))
        self._midi_port_combo["values"] = ports
        if current in ports:
            self._midi_port_combo.set(current)
        elif ports:
            self._midi_port_combo.current(0)

    def _midi_toggle_connection(self) -> None:
        if self._midi_connected:
            # disconnect
            try:
                self.midi_controller.close()
            except Exception:
                pass
            self._midi_connected = False
            if self._midi_connect_btn:
                self._midi_connect_btn.config(text="Connect")
            if self._midi_status_lbl:
                self._midi_status_lbl.config(text="Disconnected")
            return

        if not self._midi_port_combo:
            return
        name = self._midi_port_combo.get()
        if not name:
            messagebox.showwarning("MIDI", "Select a MIDI output port first.")
            return
        try:
            self.midi_controller.open(name)
            self._midi_connected = True
            if self._midi_connect_btn:
                self._midi_connect_btn.config(text="Disconnect")
            if self._midi_status_lbl:
                self._midi_status_lbl.config(text=f"Connected: {name}")
        except Exception as exc:
            messagebox.showerror("MIDI Error", str(exc))

    # ------------------------------------------------------------------ #
    #  Config callback                                                     #
    # ------------------------------------------------------------------ #
    def _midi_on_config_change(self) -> None:
        try:
            self.midi_controller.cc_number = int(self.midi_cc_var.get())
        except (ValueError, tk.TclError):
            pass
        try:
            ch = int(self.midi_channel_var.get())
            self.midi_controller.channel = max(1, min(16, ch)) - 1
        except (ValueError, tk.TclError):
            pass
        try:
            self.midi_controller.max_rate_hz = float(self.midi_rate_var.get())
        except (ValueError, tk.TclError):
            pass

    # ------------------------------------------------------------------ #
    #  Target helpers                                                      #
    # ------------------------------------------------------------------ #
    def _midi_refresh_targets(self) -> None:
        values: List[str] = []
        for item in self._iter_enabled_channels():
            label = f"{item['port']} | {item['pin']} | {item['muscle']}"
            values.append(label)
        if self._midi_target_combo:
            self._midi_target_combo["values"] = values
        if values and not self.midi_target_var.get():
            self.midi_target_var.set(values[0])

    def _midi_parse_target(self) -> Optional[Tuple[str, str, str]]:
        sel = self.midi_target_var.get().strip()
        if not sel:
            return None
        parts = [p.strip() for p in sel.split("|")]
        if len(parts) < 2:
            return None
        port, pin = parts[0], parts[1]
        muscle = parts[2] if len(parts) > 2 else ""
        return port, pin, muscle

    # ------------------------------------------------------------------ #
    #  Calibration sync (mirrors ProcessingTabMixin logic)                 #
    # ------------------------------------------------------------------ #
    def _midi_maybe_apply_calibration(
        self, key: str, port: str, pin: str, processor: EMGProcessor,
    ) -> None:
        cal_ctrl = getattr(self, "calibration", None)
        if not cal_ctrl:
            return
        params = None
        for tgt, cal in cal_ctrl.calibrations.items():
            if tgt.port == port and tgt.pin == pin:
                params = cal
                break
        if not params:
            return
        last = self._midi_cal_versions.get(key)
        if (
            last == params.ts_unix
            and processor.rest_min == params.baseline
            and processor.max_contraction == params.mvc
        ):
            return
        processor.rest_min = params.baseline
        processor.max_contraction = params.mvc
        self._midi_cal_versions[key] = params.ts_unix

    # ------------------------------------------------------------------ #
    #  Log helpers                                                         #
    # ------------------------------------------------------------------ #
    def _midi_clear_log(self) -> None:
        self.midi_log_entries.clear()
        if self._midi_log_tree:
            for item in self._midi_log_tree.get_children():
                self._midi_log_tree.delete(item)

    def _midi_render_log(self) -> None:
        tree = self._midi_log_tree
        if not tree:
            return
        for item in tree.get_children():
            tree.delete(item)
        for ts_str, cc, env, raw in self.midi_log_entries:
            tree.insert("", 0, values=(ts_str, cc, f"{env:.2f}", f"{raw:.1f}"))

    # ------------------------------------------------------------------ #
    #  Meter drawing                                                       #
    # ------------------------------------------------------------------ #
    def _midi_update_meter(self, value: int) -> None:
        canvas = self._midi_meter_canvas
        if not canvas:
            return
        clamped = max(0, min(MIDI_VALUE_MAX, value))
        frac = clamped / float(MIDI_VALUE_MAX)
        bar_w = int(frac * _METER_WIDTH)

        if clamped <= 40:
            color = "#22cc44"
        elif clamped <= 90:
            color = "#ddcc22"
        else:
            color = "#dd3333"

        canvas.coords(self._midi_meter_rect, 0, 0, bar_w, _METER_HEIGHT)
        canvas.itemconfig(self._midi_meter_rect, fill=color)

    # ------------------------------------------------------------------ #
    #  Periodic update                                                     #
    # ------------------------------------------------------------------ #
    def _update_midi_tab(self) -> None:
        parsed = self._midi_parse_target()
        if not parsed:
            # No target – reset displays and reschedule
            self._midi_update_meter(0)
            if self._midi_cc_lbl:
                self._midi_cc_lbl.config(text="MIDI CC: ---")
            self.after(120, self._update_midi_tab)
            return

        port, pin, muscle = parsed
        stream = self.dev_mgr.streams.get(port)
        if not stream:
            self.after(120, self._update_midi_tab)
            return
        buf = stream.raw.get(pin)
        if not buf:
            self.after(120, self._update_midi_tab)
            return

        key = f"{port}|{pin}"
        processor = self._midi_processors.setdefault(key, EMGProcessor())
        self._midi_maybe_apply_calibration(key, port, pin, processor)
        capture = self._midi_capture_bufs.setdefault(
            key, deque(maxlen=int(CAPTURE_SECONDS * 2000)),
        )

        data = list(buf)
        last_seen = self._midi_last_seen_ts.get(key)
        start_idx = 0
        if last_seen is not None:
            for idx, (st, _) in enumerate(data):
                if st > last_seen:
                    start_idx = idx
                    break
            else:
                start_idx = len(data)
        new_samples = data[start_idx:]
        if new_samples:
            self._midi_last_seen_ts[key] = int(new_samples[-1][0])

        last_raw: Optional[float] = None
        last_env: Optional[float] = None
        last_midi: Optional[int] = None

        for t_ms, raw_val in new_samples:
            raw_v, env, midi_cc = processor.process(int(t_ms), float(raw_val))
            capture.append(env)
            last_raw = raw_v
            last_env = env
            last_midi = midi_cc

            # Record plot history
            self.midi_plot_history.append((float(t_ms) / 1000.0, float(midi_cc)))

            # Send MIDI if enabled
            if self.midi_enabled_var.get() and self._midi_connected:
                try:
                    sent = self.midi_controller.send(midi_cc)
                except Exception:
                    sent = False
                if sent:
                    self.midi_messages_sent += 1
                    ts_str = time.strftime("%H:%M:%S", time.localtime())
                    self.midi_log_entries.append((ts_str, midi_cc, env, raw_v))

        # --- Update UI ---
        if last_midi is not None:
            self._midi_update_meter(last_midi)
            if self._midi_cc_lbl:
                self._midi_cc_lbl.config(text=f"MIDI CC: {last_midi:3d}")
        if last_env is not None and self._midi_env_lbl:
            self._midi_env_lbl.config(text=f"Envelope: {last_env:.2f}")
        if last_raw is not None and self._midi_raw_lbl:
            self._midi_raw_lbl.config(text=f"Raw: {last_raw:.1f}")
        if processor.sample_rate_hz > 0 and self._midi_fs_lbl:
            self._midi_fs_lbl.config(text=f"Fs: {processor.sample_rate_hz:.1f} Hz")
        if self._midi_sent_lbl:
            self._midi_sent_lbl.config(text=f"Messages Sent: {self.midi_messages_sent}")

        # Log table (only re-render when there are new entries to avoid flicker)
        if new_samples:
            self._midi_render_log()

        # Plot
        if self._midi_plot:
            times, values = self._midi_windowed_plot()
            subtitle = f"{port} {pin} ({muscle or 'muscle'})"
            midi_ticks = [0.0, 25.0, 50.0, 75.0, 100.0, float(MIDI_VALUE_MAX)]
            self._midi_plot.update(
                times, values, subtitle,
                y_limits=(0.0, float(MIDI_VALUE_MAX)),
                y_ticks=midi_ticks,
            )

        self.after(120, self._update_midi_tab)

    # ------------------------------------------------------------------ #
    #  Plot data helper                                                    #
    # ------------------------------------------------------------------ #
    def _midi_windowed_plot(self) -> Tuple[List[float], List[float]]:
        if not self.midi_plot_history:
            return [], []
        data = list(self.midi_plot_history)
        latest = data[-1][0]
        start = latest - _MIDI_PLOT_WINDOW_SEC
        filtered = [(t, v) for t, v in data if t >= start]
        if not filtered:
            return [], []
        return [t for t, _ in filtered], [v for _, v in filtered]
