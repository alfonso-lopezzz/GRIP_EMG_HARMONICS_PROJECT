"""Pytest-wide fixtures for managing synthetic CSV exports."""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path
from typing import Iterable

import pytest

ROOT = Path(__file__).resolve().parent
RAW_SNAPSHOT = ROOT / "sample_raw_data_export.csv"
RAW_FULL = ROOT / "sample_raw_data_from_pytest.csv"
RAW_CAL_ONLY = ROOT / "sample_calibration_data_from_pytest.csv"
CAL_PLOT = ROOT / "sample_calibration_plot.png"
RAW_SNAPSHOT_PLOT = ROOT / "sample_raw_data_export_plot.png"
RAW_FULL_PLOT = ROOT / "sample_raw_data_from_pytest_plot.png"

PORTS = ["COM_TEST", "COM_OTHER", "COM_DEBUG"]
PINS = ["A0", "A1", "A2", "A3", "A4", "A5"]
PIN_INDEX = {pin: idx for idx, pin in enumerate(PINS)}
MUSCLES = {
    "A0": "flexor",
    "A1": "extensor",
    "A2": "pronator",
    "A3": "supinator",
    "A4": "abductor",
    "A5": "adductor",
}


def _write_csv(path: Path, rows: Iterable[str]) -> None:
    path.write_text("\n".join(rows) + "\n", encoding="ascii")


def _synthetic_emg_value(sample_idx: int, pin_idx: int, rng: random.Random) -> int:
    baseline = 520 + 14 * pin_idx
    slow_wave = 40 * math.sin(2 * math.pi * (sample_idx / 70.0 + pin_idx * 0.03))
    burst = 130 * max(0.0, math.sin(2 * math.pi * (sample_idx / 260.0 + pin_idx * 0.05))) ** 2
    noise = rng.gauss(0.0, 18.0)
    value = baseline + slow_wave + burst + noise
    return max(0, min(1023, int(round(value))))


def _build_snapshot_rows() -> list[str]:
    rows = ["device_port,pin,muscle,body_part,latest_t_ms,latest_raw,fs_est_hz"]
    rng = random.Random(1234)
    for idx in range(1, 121):
        port = PORTS[idx % len(PORTS)]
        pin = PINS[idx % len(PINS)]
        t_ms = 1000 + idx * 5
        raw = _synthetic_emg_value(idx, PIN_INDEX[pin], rng)
        fs = 240.0 + rng.uniform(-10.0, 10.0)
        rows.append(f"{port},{pin},{MUSCLES[pin]},forearm,{t_ms},{raw},{fs:.1f}")
    return rows



def _build_full_rows() -> list[str]:
    rows = ["device_port,pin,muscle,body_part,t_ms,A0,A1,A2,A3,A4,A5"]
    t_ms = 1000
    rng = random.Random(5678)
    for idx in range(60):
        pin = PINS[idx % len(PINS)]
        t_ms += 5
        sensor_vals = [
            str(
                _synthetic_emg_value(
                    sample_idx=idx * len(PINS) + i,
                    pin_idx=i,
                    rng=rng,
                )
            )
            for i in range(len(PINS))
        ]
        rows.append(
            ",".join(
                [
                    "COM_TEST",
                    pin,
                    MUSCLES[pin],
                    "forearm",
                    str(t_ms),
                    *sensor_vals,
                ]
            )
        )
    return rows


def _build_calibration_dataset() -> tuple[list[str], list[dict[str, float | str]]]:
    rows = ["target,muscle,body_part,phase,elapsed_ms,envelope_value,percent_mvc"]
    sample_sets = [
        {"target": "COM_TEST | A0", "muscle": "flexor", "body_part": "forearm", "baseline": 0.8, "mvc": 8.0},
        {"target": "COM_TEST | A1", "muscle": "extensor", "body_part": "forearm", "baseline": 0.6, "mvc": 6.0},
        {"target": "COM_TEST | A2", "muscle": "pronator", "body_part": "forearm", "baseline": 1.0, "mvc": 9.0},
    ]
    points_per_phase = 60  # ~2 seconds per phase at ~30 Hz
    dt_ms = 2000 // points_per_phase
    data: list[dict[str, float | str]] = []
    for config in sample_sets:
        for idx in range(points_per_phase * 2):
            phase = "baseline" if idx < points_per_phase else "mvc"
            elapsed_ms = idx * dt_ms
            if phase == "baseline":
                variation = ((idx % 7) - 3) * 0.02
                value = config["baseline"] + variation
            else:
                variation = ((idx % 5) - 2) * 0.15
                value = config["mvc"] - 0.4 + variation
            percent = max(0.0, min(100.0, ((value - config["baseline"]) / max(1e-3, config["mvc"] - config["baseline"])) * 100.0))
            rows.append(
                "{target},{muscle},{body_part},{phase},{elapsed_ms},{envelope:.3f},{percent:.1f}".format(
                    target=config["target"],
                    muscle=config["muscle"],
                    body_part=config["body_part"],
                    phase=phase,
                    elapsed_ms=elapsed_ms,
                    envelope=value,
                    percent=percent,
                )
            )
            data.append(
                {
                    "target": config["target"],
                    "elapsed_ms": float(elapsed_ms),
                    "envelope": float(value),
                }
            )
    return rows, data


def _write_calibration_plot(data: list[dict[str, float | str]]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception:
        return
    from collections import defaultdict
    grouped = defaultdict(lambda: ([], []))
    for row in data:
        xs, ys = grouped[row["target"]]
        xs.append(row["elapsed_ms"])
        ys.append(row["envelope"])
    fig, ax = plt.subplots(figsize=(10, 5))
    for target, (xs, ys) in grouped.items():
        ax.plot(xs, ys, label=str(target))
    ax.set_title("Synthetic Calibration Envelope (Pytest)")
    ax.set_xlabel("Elapsed (ms)")
    ax.set_ylabel("Envelope Value (a.u.)")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(CAL_PLOT, dpi=150)
    plt.close(fig)


def _write_raw_snapshot_plot() -> None:
    if not RAW_SNAPSHOT.exists():
        return
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception:
        return
    with RAW_SNAPSHOT.open("r", encoding="ascii", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
    if not rows:
        return
    labels = [f"{row['device_port']}:{row['pin']}" for row in rows]
    values = [float(row["latest_raw"]) if row["latest_raw"] != "-" else 0.0 for row in rows]
    max_labels = 25
    if len(labels) > max_labels:
        labels = labels[:max_labels]
        values = values[:max_labels]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(range(len(values)), values, color="#4f6bed")
    ax.set_title("Sample Raw Snapshot (Pytest)")
    ax.set_xlabel("Device | Pin")
    ax.set_ylabel("Latest Raw Value")
    ax.set_xlim(-0.5, len(labels) - 0.5)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    fig.tight_layout()
    fig.savefig(RAW_SNAPSHOT_PLOT, dpi=150)
    plt.close(fig)


def _write_raw_stream_plot() -> None:
    if not RAW_FULL.exists():
        return
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except Exception:
        return
    with RAW_FULL.open("r", encoding="ascii", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
    if not rows:
        return
    pins = [col for col in rows[0].keys() if col in PINS]
    times = [float(row["t_ms"]) for row in rows]
    fig, ax = plt.subplots(figsize=(10, 5))
    for pin in pins:
        ax.plot(times, [float(row[pin]) for row in rows], label=pin)
    ax.set_title("Sample Raw Stream (Pytest)")
    ax.set_xlabel("t_ms")
    ax.set_ylabel("ADC Value")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(RAW_FULL_PLOT, dpi=150)
    plt.close(fig)


@pytest.fixture(scope="session", autouse=True)
def manage_sample_csv_exports():
    for path in (RAW_SNAPSHOT, RAW_FULL, RAW_CAL_ONLY, CAL_PLOT, RAW_SNAPSHOT_PLOT, RAW_FULL_PLOT):
        if path.exists():
            path.unlink()
    yield
    _write_csv(RAW_SNAPSHOT, _build_snapshot_rows())
    _write_csv(RAW_FULL, _build_full_rows())
    cal_rows, cal_data = _build_calibration_dataset()
    _write_csv(RAW_CAL_ONLY, cal_rows)
    _write_calibration_plot(cal_data)
    _write_raw_snapshot_plot()
    _write_raw_stream_plot()
