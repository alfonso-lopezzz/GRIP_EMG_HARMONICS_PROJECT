"""Reusable Matplotlib plot widgets for the EMG UI."""

from __future__ import annotations

from typing import List

import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


class LivePlotWidget:
    """Embed a lightweight Matplotlib line plot inside a Tk container."""

    def __init__(self, parent: tk.Widget, title: str, window_seconds: float = 10.0):
        self.window_seconds = window_seconds
        self.base_title = title
        self.figure = Figure(figsize=(6.0, 2.4), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Voltage (V)")
        self.ax.grid(True, linestyle="--", alpha=0.3)
        (self.line,) = self.ax.plot([], [], lw=1.5)
        self.ax.set_xlim(0, self.window_seconds)
        self.ax.set_ylim(0, 1)
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.draw_idle()
        self.canvas.get_tk_widget().pack(fill="x", expand=True, padx=4, pady=4)

    def update(
        self,
        times: List[float],
        values: List[float],
        subtitle: str = "",
        y_limits: tuple[float, float] | None = None,
    ) -> None:
        if not times or not values:
            self.line.set_data([], [])
            self.ax.set_xlim(0, self.window_seconds)
        else:
            self.line.set_data(times, values)
            x_min = min(times)
            x_max = max(times)
            if x_max - x_min < 0.5:
                x_max = max(self.window_seconds, x_max + 0.5)
            self.ax.set_xlim(max(0.0, x_min), x_max)

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

        title = self.base_title if not subtitle else f"{self.base_title} — {subtitle}"
        self.ax.set_title(title)
        self.canvas.draw_idle()
