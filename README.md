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

## Running locally (without Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' example.env | xargs)
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

## Testing

System tests are written with `pytest`.

- Run non-live system tests: `pytest -m "system and not live" -v`
- Run live GoSwift tests manually: `pytest -m "system and live" -v -s`

See `TESTING.md` for the full scenario matrix, required environment variables,
and live-test caveats.
