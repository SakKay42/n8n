# Laundry Modbus RTU MVP

This directory contains a standalone MVP for controlling washer and dryer controllers over RS-485 / Modbus RTU. It is intentionally small so operators can test the dashboard in simulator mode first, then switch the same API to a USB-RS485 adapter.

## What is included

- FastAPI backend with REST endpoints for machines, commands, prices, transactions, and technical events.
- SQLite persistence for MVP tables: `machines`, `machine_status`, `programs`, `prices`, `transactions`, `events`, `errors`, and `settings`.
- Polling service that reads every enabled slave every 1-3 seconds and marks non-responding machines as `offline`.
- Command service for start, stop, reset error, set price, and simulated payment.
- Safety logic that blocks starts when the door is open, the machine is already running, an active error exists, or a payment reference is reused.
- Browser dashboard with machine tiles, remaining time, error display, simulated payment, stop, reset, transaction history, and event log.
- Simulator mode for development without RS-485 hardware.

## Quick start in simulator mode

```bash
cd laundry-modbus-mvp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 and click **Simulate payment** on a free machine.

## Docker

```bash
cd laundry-modbus-mvp
docker compose up --build
```

If you are running without a USB-RS485 adapter, remove the `devices` block from `docker-compose.yml` or keep `MODBUS_MODE=simulator` and ignore the device mapping if Docker allows it on your host.

## API

- `GET /machines` — list all machines and their current status.
- `GET /machines/{id}` — read one machine.
- `POST /machines/{id}/start` — start after an already-authorized payment.
- `POST /machines/{id}/simulate-payment` — MVP payment simulation and start.
- `POST /machines/{id}/stop` — stop machine.
- `POST /machines/{id}/reset` — reset active controller error.
- `POST /machines/{id}/price` — upsert program price.
- `GET /transactions` — payment/start history.
- `GET /events` — technical command and communication log.

Example start payload:

```json
{
  "program_number": 1,
  "payment_reference": "terminal-unique-payment-id"
}
```

## RS-485 / Modbus RTU connection

1. Wire the master terminal or backend host through a USB-RS485 adapter, or through an RS485-Ethernet gateway that exposes a serial device on the backend host.
2. Connect A/B lines consistently across all controllers, add termination at the ends of the bus, and ensure every machine has a unique Modbus slave address.
3. Set serial parameters from the controller documentation in `.env`: `MODBUS_BAUDRATE`, `MODBUS_PARITY`, `MODBUS_STOPBITS`, and `MODBUS_TIMEOUT_SECONDS`.
4. Switch `.env` to `MODBUS_MODE=rtu` and set `MODBUS_SERIAL_PORT=/dev/ttyUSB0` or the gateway-provided serial path.
5. Update the register map in `app/modbus_client.py` after confirming addresses in `SX17600XA Touch screen networking protocol-20231020.xlsx` and `SX27600XA External network communication protocol.xlsx`.

The current code uses a conservative placeholder register map because the Excel protocol files are not committed in this repository. Replace the constants in `PymodbusLaundryClient` with the exact holding/input registers and command values from the supplier documents before connecting to production hardware.

## Machine configuration

See `config/machines.example.yml` for the intended machine and program shape. The MVP seeds three default machines into SQLite at startup so the dashboard works immediately. For production, load this configuration during deployment or insert/update rows directly in SQLite.

## Safety notes

- Do not treat simulator mode as hardware validation.
- Test every command against one isolated controller before putting multiple machines on the bus.
- Keep unique payment references from a terminal; duplicate references are rejected to avoid repeated capture/start attempts.
- Log review is available in `/events`; every command and communication error is written to the `events` table.
