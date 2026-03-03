"""Reusable Matplotlib plot widgets for the EMG UI."""

from __future__ import annotations

from typing import List

import tkinter as tk
from tkinter import ttk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import AutoLocator, FixedLocator


class LivePlotWidget:
    """Embed a lightweight Matplotlib line plot inside a Tk container."""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        window_seconds: float = 10.0,
        fixed_window: bool = False,
    ):
        self.window_seconds = window_seconds
        self.fixed_window = fixed_window
        self.base_title = title
        self.plot_visible = True

        self.container = ttk.Frame(parent)
        self.container.pack(fill="both", expand=True)

        toggle_bar = ttk.Frame(self.container)
        toggle_bar.pack(fill="x", padx=4, pady=(4, 0))
        self.toggle_button = ttk.Button(toggle_bar, text="Plot On/Off", command=self._toggle_plot_visibility)
        self.toggle_button.pack(side="left")

        self.figure = Figure(figsize=(6.0, 2.4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Voltage (V)")
        self.ax.grid(True, linestyle="--", alpha=0.3)
        (self.line,) = self.ax.plot([], [], lw=1.5)
        self.ax.set_xlim(0, self.window_seconds)
        self.ax.set_ylim(0, 1)
        self.figure.subplots_adjust(left=0.08, right=0.98, bottom=0.22, top=0.86)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self.container)
        self._canvas_pack_opts = {
            "fill": "both",
            "expand": True,
            "padx": 4,
            "pady": 4,
        }
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(**self._canvas_pack_opts)
        self.canvas.draw_idle()

    def update(
        self,
        times: List[float],
        values: List[float],
        subtitle: str = "",
        y_limits: tuple[float, float] | None = None,
        y_ticks: List[float] | None = None,
    ) -> None:
        if not times or not values:
            self.line.set_data([], [])
            self.ax.set_xlim(0, self.window_seconds)
        else:
            self.line.set_data(times, values)
            if self.fixed_window:
                self.ax.set_xlim(0, self.window_seconds)
            else:
                x_max = max(times)
                x_min = max(0.0, x_max - self.window_seconds)
                self.ax.set_xlim(x_min, x_max)

        if y_limits is not None:
            y_min, y_max = y_limits
            if y_max - y_min < 1e-3:
                y_max = y_min + 1.0
            self.ax.set_ylim(y_min, y_max)
        elif times and values:
            v_min = min(values)
            v_max = max(values)
            pad = max(0.05, (v_max - v_min) * 0.1)
            self.ax.set_ylim(v_min - pad, v_max + pad)
        else:
            self.ax.set_ylim(0, 1)

        if y_ticks is not None:
            self.ax.yaxis.set_major_locator(FixedLocator(y_ticks))
        else:
            self.ax.yaxis.set_major_locator(AutoLocator())

        title = self.base_title if not subtitle else f"{self.base_title} — {subtitle}"
        self.ax.set_title(title)
        self.canvas.draw_idle()

    def _toggle_plot_visibility(self) -> None:
        self.plot_visible = not self.plot_visible
        if self.plot_visible:
            self.canvas_widget.pack(**self._canvas_pack_opts)
            self.canvas.draw_idle()
        else:
            self.canvas_widget.pack_forget()
