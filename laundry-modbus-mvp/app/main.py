from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.database import init_db, seed_defaults
from app.modbus_client import ModbusSettings, PymodbusLaundryClient
from app.service import MachineService
from app.simulator import SimulatorClient


class StartRequest(BaseModel):
	program_number: int | None = Field(default=None, ge=1)
	payment_reference: str | None = None


class PriceRequest(BaseModel):
	program_number: int = Field(ge=1)
	price_cents: int = Field(ge=0)
	currency: str = Field(default="EUR", min_length=3, max_length=3)


def build_modbus_client() -> object:
	settings = ModbusSettings(
		mode=os.getenv("MODBUS_MODE", "simulator"),
		serial_port=os.getenv("MODBUS_SERIAL_PORT", "/dev/ttyUSB0"),
		baudrate=int(os.getenv("MODBUS_BAUDRATE", "9600")),
		parity=os.getenv("MODBUS_PARITY", "N"),
		stopbits=int(os.getenv("MODBUS_STOPBITS", "1")),
		timeout_seconds=float(os.getenv("MODBUS_TIMEOUT_SECONDS", "1")),
	)
	if settings.mode == "rtu":
		return PymodbusLaundryClient(settings)
	return SimulatorClient()


init_db()
seed_defaults()
service = MachineService(build_modbus_client())

app = FastAPI(title="Laundry Modbus MVP", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent.parent / "static"), name="static")


@app.on_event("startup")
def startup() -> None:
	service.start_polling()


@app.on_event("shutdown")
def shutdown() -> None:
	service.stop_polling()


@app.get("/")
def dashboard() -> FileResponse:
	return FileResponse(Path(__file__).resolve().parent.parent / "static" / "index.html")


@app.get("/machines")
def list_machines() -> list[dict]:
	return service.list_machines()


@app.get("/machines/{machine_id}")
def get_machine(machine_id: str) -> dict:
	machine = service.get_machine(machine_id)
	if machine is None:
		raise HTTPException(status_code=404, detail="Machine not found")
	return machine


@app.post("/machines/{machine_id}/start")
def start_machine(machine_id: str, body: StartRequest) -> dict:
	try:
		return service.start_machine(machine_id, body.program_number, body.payment_reference)
	except ValueError as exc:
		raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/machines/{machine_id}/stop")
def stop_machine(machine_id: str) -> dict:
	try:
		service.stop_machine(machine_id)
		return {"status": "ok"}
	except ValueError as exc:
		raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/machines/{machine_id}/reset")
def reset_machine(machine_id: str) -> dict:
	try:
		service.reset_error(machine_id)
		return {"status": "ok"}
	except ValueError as exc:
		raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/machines/{machine_id}/price")
def set_price(machine_id: str, body: PriceRequest) -> dict:
	try:
		return service.set_price(machine_id, body.program_number, body.price_cents, body.currency.upper())
	except ValueError as exc:
		raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/machines/{machine_id}/simulate-payment")
def simulate_payment(machine_id: str, body: StartRequest) -> dict:
	try:
		return service.start_machine(machine_id, body.program_number, body.payment_reference)
	except ValueError as exc:
		raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/transactions")
def transactions() -> list[dict]:
	return service.transactions()


@app.get("/events")
def events() -> list[dict]:
	return service.events()
