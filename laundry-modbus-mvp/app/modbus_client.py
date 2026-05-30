from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModbusSettings:
	mode: str = "simulator"
	serial_port: str = "/dev/ttyUSB0"
	baudrate: int = 9600
	parity: str = "N"
	stopbits: int = 1
	timeout_seconds: float = 1.0
	gateway_host: str = "127.0.0.1"
	gateway_port: int = 502


@dataclass
class MachineTelemetry:
	status: str
	program_number: int | None
	remaining_minutes: int
	error_code: int
	error_message: str | None
	door_state: str
	raw_registers: dict[str, int]


class LaundryModbusClient(Protocol):
	def read_status(self, slave_address: int) -> MachineTelemetry: ...
	def start_program(self, slave_address: int, program_number: int, credit_cents: int) -> None: ...
	def stop_machine(self, slave_address: int) -> None: ...
	def reset_error(self, slave_address: int) -> None: ...
	def set_program(self, slave_address: int, program_number: int) -> None: ...
	def send_credit(self, slave_address: int, amount_cents: int) -> None: ...


class PymodbusLaundryClient:
	STATUS_REGISTER = 0x0000
	PROGRAM_REGISTER = 0x0001
	REMAINING_TIME_REGISTER = 0x0002
	ERROR_REGISTER = 0x0003
	DOOR_REGISTER = 0x0004
	COMMAND_REGISTER = 0x0100
	CREDIT_REGISTER = 0x0101

	COMMAND_START = 1
	COMMAND_STOP = 2
	COMMAND_RESET = 3

	def __init__(self, settings: ModbusSettings) -> None:
		if importlib.util.find_spec("pymodbus") is None:
			raise RuntimeError("Install pymodbus or run with MODBUS_MODE=simulator")
		self.settings = settings
		serial_module = importlib.import_module("pymodbus.client")
		client_class = getattr(serial_module, "ModbusSerialClient")
		self.client = client_class(
			port=settings.serial_port,
			baudrate=settings.baudrate,
			parity=settings.parity,
			stopbits=settings.stopbits,
			timeout=settings.timeout_seconds,
		)
		if not self.client.connect():
			raise RuntimeError(f"Could not connect to Modbus RTU adapter on {settings.serial_port}")

	def read_status(self, slave_address: int) -> MachineTelemetry:
		result = self.client.read_holding_registers(address=self.STATUS_REGISTER, count=5, slave=slave_address)
		if result.isError():
			raise TimeoutError(f"Modbus read failed for slave {slave_address}")
		registers = result.registers
		status_map = {0: "free", 1: "running", 2: "finished", 3: "error"}
		door_map = {0: "closed", 1: "open"}
		return MachineTelemetry(
			status=status_map.get(registers[0], "unknown"),
			program_number=registers[1],
			remaining_minutes=registers[2],
			error_code=registers[3],
			error_message=None if registers[3] == 0 else f"Controller error {registers[3]}",
			door_state=door_map.get(registers[4], "unknown"),
			raw_registers={
				"status": registers[0],
				"program": registers[1],
				"remaining_time": registers[2],
				"error": registers[3],
				"door": registers[4],
			},
		)

	def start_program(self, slave_address: int, program_number: int, credit_cents: int) -> None:
		self.set_program(slave_address, program_number)
		self.send_credit(slave_address, credit_cents)
		self._write_register(slave_address, self.COMMAND_REGISTER, self.COMMAND_START)

	def stop_machine(self, slave_address: int) -> None:
		self._write_register(slave_address, self.COMMAND_REGISTER, self.COMMAND_STOP)

	def reset_error(self, slave_address: int) -> None:
		self._write_register(slave_address, self.COMMAND_REGISTER, self.COMMAND_RESET)

	def set_program(self, slave_address: int, program_number: int) -> None:
		self._write_register(slave_address, self.PROGRAM_REGISTER, program_number)

	def send_credit(self, slave_address: int, amount_cents: int) -> None:
		self._write_register(slave_address, self.CREDIT_REGISTER, amount_cents)

	def _write_register(self, slave_address: int, register: int, value: int) -> None:
		result = self.client.write_register(address=register, value=value, slave=slave_address)
		if result.isError():
			raise TimeoutError(f"Modbus write failed for slave {slave_address}, register {register}")
