"""Centralized application constants for the EMG app."""

DEVICE_TYPES = ["ESP32", "Arduino Uno", "Arduino Nano"]
BODY_PARTS = ["upper limb", "lower limb", "trunk/core", "head/neck", "custom"]
PINS = ["A0", "A1", "A2", "A3", "A4", "A5"]

DEFAULT_BAUD = 115200
DEFAULT_VREF_VOLTS = 5.0

CAL_BASELINE_SECONDS = 4.0
CAL_MVC_SECONDS = 4.0
RMS_WINDOW_SECONDS = 0.200  # seconds
EMA_ALPHA = 0.2

EPS = 1e-6

CAL_LOG_PATH = "calibration_log.json"
