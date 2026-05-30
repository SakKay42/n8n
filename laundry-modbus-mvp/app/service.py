from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Any

from app.database import connect, log_event, row_to_dict, rows_to_dicts, utc_now
from app.modbus_client import LaundryModbusClient, MachineTelemetry


class MachineService:
	def __init__(self, modbus_client: LaundryModbusClient) -> None:
		self.modbus_client = modbus_client
		self.poll_interval = float(os.getenv("POLL_INTERVAL_SECONDS", "2"))
		self._stop = threading.Event()
		self._thread: threading.Thread | None = None

	def start_polling(self) -> None:
		if self._thread and self._thread.is_alive():
			return
		self._thread = threading.Thread(target=self._poll_forever, daemon=True)
		self._thread.start()
		log_event(None, "info", "polling_started", "Polling service started", {"interval_seconds": self.poll_interval})

	def stop_polling(self) -> None:
		self._stop.set()
		if self._thread:
			self._thread.join(timeout=3)

	def list_machines(self) -> list[dict[str, Any]]:
		with connect() as conn:
			return rows_to_dicts(
				conn.execute(
					"""
					SELECT m.*, s.status, s.program_number, s.remaining_minutes, s.error_code,
					       s.error_message, s.door_state, s.last_seen, s.updated_at AS status_updated_at
					FROM machines m
					LEFT JOIN machine_status s ON s.machine_id = m.id
					ORDER BY m.id
					"""
				).fetchall()
			)

	def get_machine(self, machine_id: str) -> dict[str, Any] | None:
		with connect() as conn:
			return row_to_dict(
				conn.execute(
					"""
					SELECT m.*, s.status, s.program_number, s.remaining_minutes, s.error_code,
					       s.error_message, s.door_state, s.last_seen, s.raw_registers
					FROM machines m
					LEFT JOIN machine_status s ON s.machine_id = m.id
					WHERE m.id = ?
					""",
					(machine_id,),
				).fetchone()
			)

	def start_machine(self, machine_id: str, program_number: int | None, payment_reference: str | None = None) -> dict[str, Any]:
		machine = self._machine_or_raise(machine_id)
		self.poll_one(machine)
		status = self.get_machine(machine_id)
		if status is None:
			raise ValueError("Machine not found")
		if status["door_state"] != "closed":
			raise ValueError("Safety check failed: door is not closed")
		if status["status"] == "running":
			raise ValueError("Safety check failed: machine is already running")
		if status["error_code"] and int(status["error_code"]) > 0:
			raise ValueError("Safety check failed: machine has an active error")

		selected_program = program_number or int(machine["default_program"])
		price = self._price_for(machine_id, selected_program)
		transaction_id = str(uuid.uuid4())
		reference = payment_reference or f"sim-{transaction_id}"
		with connect() as conn:
			duplicate = conn.execute("SELECT id FROM transactions WHERE payment_reference = ?", (reference,)).fetchone()
			if duplicate:
				raise ValueError("Payment reference was already used")
			conn.execute(
				"""
				INSERT INTO transactions(id, machine_id, program_number, amount_cents, currency, status, payment_reference, created_at, updated_at)
				VALUES (?, ?, ?, ?, ?, 'authorized', ?, ?, ?)
				""",
				(transaction_id, machine_id, selected_program, price["price_cents"], price["currency"], reference, utc_now(), utc_now()),
			)
		log_event(machine_id, "info", "payment_authorized", "Payment simulated and authorized", {"transaction_id": transaction_id})

		try:
			self.modbus_client.start_program(int(machine["slave_address"]), selected_program, int(price["price_cents"]))
		except Exception as exc:
			with connect() as conn:
				conn.execute("UPDATE transactions SET status = 'failed', updated_at = ? WHERE id = ?", (utc_now(), transaction_id))
			log_event(machine_id, "error", "command_failed", str(exc), {"command": "start_program"})
			raise

		with connect() as conn:
			conn.execute("UPDATE transactions SET status = 'captured', updated_at = ? WHERE id = ?", (utc_now(), transaction_id))
		log_event(machine_id, "info", "command_sent", "Start program command sent", {"program_number": selected_program})
		self.poll_one(machine)
		return {"transaction_id": transaction_id, "status": "captured", "machine_id": machine_id, "program_number": selected_program}

	def stop_machine(self, machine_id: str) -> None:
		machine = self._machine_or_raise(machine_id)
		self.modbus_client.stop_machine(int(machine["slave_address"]))
		log_event(machine_id, "warning", "command_sent", "Stop command sent", {})
		self.poll_one(machine)

	def reset_error(self, machine_id: str) -> None:
		machine = self._machine_or_raise(machine_id)
		self.modbus_client.reset_error(int(machine["slave_address"]))
		with connect() as conn:
			conn.execute("UPDATE errors SET active = 0, cleared_at = ? WHERE machine_id = ? AND active = 1", (utc_now(), machine_id))
		log_event(machine_id, "info", "command_sent", "Reset error command sent", {})
		self.poll_one(machine)

	def set_price(self, machine_id: str, program_number: int, price_cents: int, currency: str) -> dict[str, Any]:
		self._machine_or_raise(machine_id)
		with connect() as conn:
			conn.execute(
				"""
				INSERT INTO prices(machine_id, program_number, price_cents, currency, updated_at)
				VALUES (?, ?, ?, ?, ?)
				ON CONFLICT(machine_id, program_number) DO UPDATE SET price_cents = excluded.price_cents, currency = excluded.currency, updated_at = excluded.updated_at
				""",
				(machine_id, program_number, price_cents, currency, utc_now()),
			)
		log_event(machine_id, "info", "price_updated", "Program price updated", {"program_number": program_number, "price_cents": price_cents})
		return {"machine_id": machine_id, "program_number": program_number, "price_cents": price_cents, "currency": currency}

	def transactions(self) -> list[dict[str, Any]]:
		with connect() as conn:
			return rows_to_dicts(conn.execute("SELECT * FROM transactions ORDER BY created_at DESC LIMIT 200").fetchall())

	def events(self) -> list[dict[str, Any]]:
		with connect() as conn:
			return rows_to_dicts(conn.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT 300").fetchall())

	def poll_one(self, machine: dict[str, Any]) -> None:
		try:
			telemetry = self.modbus_client.read_status(int(machine["slave_address"]))
			self._store_telemetry(machine["id"], telemetry)
		except Exception as exc:
			self._mark_offline(machine["id"], str(exc))

	def _poll_forever(self) -> None:
		while not self._stop.is_set():
			with connect() as conn:
				machines = rows_to_dicts(conn.execute("SELECT * FROM machines WHERE enabled = 1 ORDER BY id").fetchall())
			for machine in machines:
				self.poll_one(machine)
			self._stop.wait(self.poll_interval)

	def _store_telemetry(self, machine_id: str, telemetry: MachineTelemetry) -> None:
		now = utc_now()
		with connect() as conn:
			previous = conn.execute("SELECT error_code FROM machine_status WHERE machine_id = ?", (machine_id,)).fetchone()
			conn.execute(
				"""
				INSERT INTO machine_status(machine_id, status, program_number, remaining_minutes, error_code, error_message, door_state, last_seen, raw_registers, updated_at)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
				ON CONFLICT(machine_id) DO UPDATE SET status = excluded.status, program_number = excluded.program_number,
				remaining_minutes = excluded.remaining_minutes, error_code = excluded.error_code, error_message = excluded.error_message,
				door_state = excluded.door_state, last_seen = excluded.last_seen, raw_registers = excluded.raw_registers, updated_at = excluded.updated_at
				""",
				(
					machine_id,
					telemetry.status,
					telemetry.program_number,
					telemetry.remaining_minutes,
					telemetry.error_code,
					telemetry.error_message,
					telemetry.door_state,
					now,
					json.dumps(telemetry.raw_registers),
					now,
				),
			)
			if telemetry.error_code and (previous is None or previous["error_code"] != telemetry.error_code):
				conn.execute(
					"INSERT INTO errors(machine_id, error_code, message, active, created_at) VALUES (?, ?, ?, 1, ?)",
					(machine_id, telemetry.error_code, telemetry.error_message or "Controller error", now),
				)

	def _mark_offline(self, machine_id: str, reason: str) -> None:
		with connect() as conn:
			conn.execute("UPDATE machine_status SET status = 'offline', updated_at = ? WHERE machine_id = ?", (utc_now(), machine_id))
		log_event(machine_id, "error", "communication_error", "Machine did not respond", {"reason": reason})

	def _machine_or_raise(self, machine_id: str) -> dict[str, Any]:
		machine = self.get_machine(machine_id)
		if machine is None:
			raise ValueError("Machine not found")
		return machine

	def _price_for(self, machine_id: str, program_number: int) -> dict[str, Any]:
		with connect() as conn:
			row = conn.execute(
				"SELECT price_cents, currency FROM prices WHERE machine_id = ? AND program_number = ?",
				(machine_id, program_number),
			).fetchone()
		if row is None:
			raise ValueError("No price configured for selected program")
		return row_to_dict(row) or {}
