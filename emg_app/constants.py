"""Centralized application constants for the EMG app."""

DEVICE_TYPES = ["ESP32", "Arduino Uno", "Arduino Nano"]
BODY_PARTS = ["upper limb", "lower limb", "trunk/core", "head/neck", "custom"]
PINS = ["A0", "A1", "A2", "A3", "A4", "A5"]

CONNECTION_TYPES = ["Serial (USB/Wired)", "Bluetooth Classic (SPP)", "Bluetooth LE (BLE)"]
BLE_UART_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"   # Nordic UART
BLE_UART_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # TX (notify)
BLE_UART_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # RX (write)

DEFAULT_BAUD = 115200
DEFAULT_VREF_VOLTS = 5.0
RAW_BUFFER_SECONDS = 12.0
CAPTURE_SECONDS = 1.5

CAL_BASELINE_SECONDS = 4.0
CAL_MVC_SECONDS = 4.0
RMS_WINDOW_SECONDS = 0.200  # seconds
EMA_ALPHA = 0.2

EPS = 1e-6

CAL_LOG_PATH = "calibration_log.json"
