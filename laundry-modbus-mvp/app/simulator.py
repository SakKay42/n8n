from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.modbus_client import LaundryModbusClient, MachineTelemetry


@dataclass
class SimulatedMachine:
	program_number: int = 1
	status: str = "free"
	remaining_minutes: int = 0
	error_code: int = 0
	error_message: str | None = None
	door_state: str = "closed"
	finish_at: datetime | None = None
	credit_cents: int = 0


class SimulatorClient(LaundryModbusClient):
	def __init__(self) -> None:
		self.machines: dict[int, SimulatedMachine] = {}

	def _machine(self, slave_address: int) -> SimulatedMachine:
		return self.machines.setdefault(slave_address, SimulatedMachine())

	def read_status(self, slave_address: int) -> MachineTelemetry:
		machine = self._machine(slave_address)
		self._tick(machine)
		status_register = {"free": 0, "running": 1, "finished": 2, "error": 3}.get(machine.status, 99)
		door_register = 1 if machine.door_state == "open" else 0
		return MachineTelemetry(
			status=machine.status,
			program_number=machine.program_number,
			remaining_minutes=machine.remaining_minutes,
			error_code=machine.error_code,
			error_message=machine.error_message,
			door_state=machine.door_state,
			raw_registers={
				"status": status_register,
				"program": machine.program_number,
				"remaining_time": machine.remaining_minutes,
				"error": machine.error_code,
				"door": door_register,
			},
		)

	def start_program(self, slave_address: int, program_number: int, credit_cents: int) -> None:
		machine = self._machine(slave_address)
		self._tick(machine)
		if machine.door_state != "closed":
			raise ValueError("Door is open")
		if machine.error_code:
			raise ValueError("Machine has an active error")
		if machine.status == "running":
			raise ValueError("Machine is already running")
		self.set_program(slave_address, program_number)
		self.send_credit(slave_address, credit_cents)
		duration = 2 if program_number == 2 else 3
		machine.status = "running"
		machine.remaining_minutes = duration
		machine.finish_at = datetime.now(UTC) + timedelta(minutes=duration)

	def stop_machine(self, slave_address: int) -> None:
		machine = self._machine(slave_address)
		machine.status = "free"
		machine.remaining_minutes = 0
		machine.finish_at = None

	def reset_error(self, slave_address: int) -> None:
		machine = self._machine(slave_address)
		machine.error_code = 0
		machine.error_message = None
		if machine.status == "error":
			machine.status = "free"

	def set_program(self, slave_address: int, program_number: int) -> None:
		self._machine(slave_address).program_number = program_number

	def send_credit(self, slave_address: int, amount_cents: int) -> None:
		self._machine(slave_address).credit_cents = amount_cents

	def inject_error(self, slave_address: int, error_code: int, message: str) -> None:
		machine = self._machine(slave_address)
		machine.status = "error"
		machine.error_code = error_code
		machine.error_message = message
		machine.finish_at = None

	def _tick(self, machine: SimulatedMachine) -> None:
		if machine.status != "running" or machine.finish_at is None:
			return
		now = datetime.now(UTC)
		remaining = machine.finish_at - now
		if remaining.total_seconds() <= 0:
			machine.status = "finished"
			machine.remaining_minutes = 0
			machine.finish_at = None
		else:
			machine.remaining_minutes = max(1, int(remaining.total_seconds() // 60) + 1)
