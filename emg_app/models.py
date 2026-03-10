"""Dataclasses shared across the EMG application."""

from dataclasses import dataclass
from typing import Dict


@dataclass
class ChannelConfig:
	enabled: bool = False
	muscle: str = ""
	notes: str = ""


@dataclass
class DeviceConfig:
	device_type: str
	port: str
	baud: int
	body_part: str
	channels: Dict[str, ChannelConfig]  # pin -> ChannelConfig
	connection_type: str = "Serial (USB/Wired)"   # Serial, BT Classic, or BLE
	ble_service_uuid: str = ""                     # optional custom BLE UART service UUID


@dataclass(frozen=True)
class TargetKey:
	port: str
	pin: str  # "A0" etc.

	def to_str(self) -> str:
		return f"{self.port} | {self.pin}"


@dataclass
class CalibrationParams:
	baseline: float
	mvc: float
	ts_unix: float
	body_part: str
	muscle: str
	device_type: str
	port: str
	pin: str
