# GoSwift border queue Telegram bot

This service periodically checks the GoSwift border queue website for available
timeslots and sends you Telegram notifications when new slots appear.

It is implemented as a Python application using `python-telegram-bot` and is
intended to run as a containerised service.

By default, the bot checks both supported locations:
- `Koidula`
- `Luhamaa`

You can switch between `Koidula`, `Luhamaa`, or `Both` at runtime via the
Telegram `/locations` command. The selection is persisted in `runtime_config.json`
and survives restarts.

## Configuration

The bot is configured via environment variables. Copy `example.env` to `.env`
and fill in the values:

- `TELEGRAM_BOT_TOKEN`: Bot token from BotFather.
- `TELEGRAM_OWNER_CHAT_ID`: Your personal chat ID; only this chat can use the bot.
- `LOG_LEVEL`: Logging verbosity. Supported values: `DEBUG`, `INFO`,
  `WARNING`, `ERROR`. Default is `INFO`.
- `LOGS_MAX_GB`: Maximum total size of the `logs` directory. Default is `5`.
- `LOG_CHUNK_MB`: Maximum size of a single active log chunk before rotation.
  Default is `1024`.
- `GOSWIFT_BASE_URL`: Base URL of the GoSwift portal (default is
  `https://www.eestipiir.ee`).
- `GOSWIFT_COOKIE`: Optional session cookie string copied from your browser.
  The bot now tries to bootstrap a fresh HTTP session automatically and only
  needs this as a fallback if the portal rejects anonymous session startup.
- `GOSWIFT_LOCATIONS`: Comma-separated active locations. Supported values:
  `koidula`, `luhamaa`. Default is `koidula,luhamaa`.
- `GOSWIFT_CHECKPOINT_ID`: Legacy fallback for a single location. Also accepts
  `2` or `3`.
- `GOSWIFT_DIRECTION`: Optional direction string (e.g. `EE-RU`).
- `GOSWIFT_CATEGORY`: Vehicle category, default is `B`.
- `CHECK_INTERVAL_MINUTES`: Interval between checks, default is `10`.
- `GOSWIFT_DATE_FIRST` / `GOSWIFT_DATE_LAST`: Optional inclusive date range.

## Runtime configuration

The bot stores Telegram-driven settings in `runtime_config.json`. The file can
contain:

```json
{
  "date_first": "2026-04-01",
  "date_last": "2026-04-10",
  "locations": ["koidula", "luhamaa"]
}
```

If runtime settings are present, they override environment values for dates and
locations.

## Logging

The bot writes logs both to stdout and to per-session log files in `./logs`.
Each process start creates a session whose active file is split into chunk files
named like `goswift-bot-YYYYMMDD-HHMMSS-ffffff.chunk0001.log`.

Use `LOG_LEVEL=DEBUG` when you want a detailed trace for manual testing. In
that mode the bot logs the full operator flow, GoSwift request/response
details, parser steps, runtime state changes, and outgoing Telegram messages.
By design this debug log may contain sensitive runtime data, including raw
payloads and session-related values, so use it only for controlled local runs.

Log retention is controlled by `LOGS_MAX_GB` and `LOG_CHUNK_MB`:
- active logs rotate into new chunks when they reach `LOG_CHUNK_MB`;
- when the whole `logs` directory exceeds `LOGS_MAX_GB`, the oldest closed
  `.log` chunks are compressed into `.log.gz`;
- if everything old is already archived and the directory is still too large,
  the oldest `.log.gz` archives are deleted;
- the active current chunk is never archived or deleted while the process is running.

If the disk is full or file logging cannot continue, the service stays alive and
continues logging to stdout/stderr only.

When you run the service with `docker compose`, the local `./logs` directory is
mounted into the container as `/app/logs`, so log files remain available on the
host after container restarts or recreation.

## Running locally (without Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' example.env | xargs)
export LOG_LEVEL=DEBUG
PYTHONPATH=src python3 -m goswift_bot.main
```

## Docker

Build and run the container:

```bash
docker build -t goswift-bot .
docker run --env-file=./.env --restart unless-stopped goswift-bot
```

Or use `docker-compose`:

```bash
docker compose up -d
```

Session log files will appear in `./logs`.

## Testing

System tests are written with `pytest`.

- Run non-live system tests: `pytest -m "system and not live" -v`
- Run live GoSwift tests manually: `pytest -m "system and live" -v -s`

See `TESTING.md` for the full scenario matrix, required environment variables,
and live-test caveats.
