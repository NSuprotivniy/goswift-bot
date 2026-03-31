import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .locations import (
    DEFAULT_LOCATION_KEYS,
    format_location_titles,
    location_keys_from_legacy_checkpoint,
    normalize_location_keys,
)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError as exc:
        raise RuntimeError(f"Date must be in YYYY-MM-DD format, got: {s!r}") from exc


def _get_runtime_config_path() -> Path:
    """Path to runtime config file (persists Telegram-driven settings)."""
    return Path(__file__).resolve().parent.parent.parent / "runtime_config.json"


def _load_runtime_config() -> dict:
    path = _get_runtime_config_path()
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def _save_runtime_config(data: dict) -> None:
    path = _get_runtime_config_path()
    path.write_text(json.dumps(data, indent=2))


def _parse_env_locations(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    keys = [part.strip() for part in raw.split(",")]
    return normalize_location_keys(keys)


def _load_runtime_date_range() -> tuple[date | None, date | None]:
    data = _load_runtime_config()
    try:
        first = _parse_date(data.get("date_first"))
        last = _parse_date(data.get("date_last"))
    except RuntimeError:
        return None, None
    return first, last


def _load_runtime_locations() -> list[str] | None:
    data = _load_runtime_config()
    raw_locations = data.get("locations")
    if raw_locations is None:
        return None
    if not isinstance(raw_locations, list):
        return None
    try:
        return normalize_location_keys(raw_locations)
    except RuntimeError:
        return None


def save_runtime_date_range(date_first: date, date_last: date) -> None:
    """Persist date range to runtime config file."""
    data = _load_runtime_config()
    data["date_first"] = date_first.isoformat()
    data["date_last"] = date_last.isoformat()
    _save_runtime_config(data)


def save_runtime_locations(location_keys: list[str]) -> list[str]:
    """Persist active locations to runtime config file and return normalized keys."""
    normalized = normalize_location_keys(location_keys)
    data = _load_runtime_config()
    data["locations"] = normalized
    _save_runtime_config(data)
    return normalized


@dataclass
class Config:
    telegram_bot_token: str
    telegram_owner_chat_id: int

    goswift_base_url: str
    goswift_cookie: str | None

    goswift_locations: list[str]
    goswift_checkpoint_id: str | None
    goswift_direction: str | None
    goswift_category: str | None

    goswift_date_first: date | None
    goswift_date_last: date | None
    check_interval: timedelta

    @property
    def date_range_ok(self) -> bool:
        """True if both dates are set and first <= last."""
        if self.goswift_date_first is None or self.goswift_date_last is None:
            return False
        return self.goswift_date_first <= self.goswift_date_last

    def iter_dates(self) -> "list[date]":
        """Yield all dates from first to last (inclusive)."""
        if not self.date_range_ok:
            return []
        dates: list[date] = []
        d = self.goswift_date_first
        while d <= self.goswift_date_last:
            dates.append(d)
            d += timedelta(days=1)
        return dates

    def set_locations(self, location_keys: list[str]) -> None:
        self.goswift_locations = normalize_location_keys(location_keys)

    @property
    def active_locations_text(self) -> str:
        return format_location_titles(self.goswift_locations)

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        owner_chat_id_raw = os.getenv("TELEGRAM_OWNER_CHAT_ID")
        goswift_cookie = os.getenv("GOSWIFT_COOKIE") or None

        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
        if not owner_chat_id_raw:
            raise RuntimeError("TELEGRAM_OWNER_CHAT_ID is not set")

        try:
            owner_chat_id = int(owner_chat_id_raw)
        except ValueError as exc:
            raise RuntimeError("TELEGRAM_OWNER_CHAT_ID must be an integer") from exc

        base_url = os.getenv("GOSWIFT_BASE_URL", "https://www.eestipiir.ee")

        checkpoint_id = os.getenv("GOSWIFT_CHECKPOINT_ID")
        direction = os.getenv("GOSWIFT_DIRECTION")
        category = os.getenv("GOSWIFT_CATEGORY", "B")

        env_locations_raw = os.getenv("GOSWIFT_LOCATIONS")
        if env_locations_raw:
            locations = _parse_env_locations(env_locations_raw)
        elif checkpoint_id:
            locations = location_keys_from_legacy_checkpoint(checkpoint_id)
        else:
            locations = list(DEFAULT_LOCATION_KEYS)

        # Date range: env vars or runtime config, with fallbacks
        date_first: date | None = None
        date_last: date | None = None

        env_first = os.getenv("GOSWIFT_DATE_FIRST")
        env_last = os.getenv("GOSWIFT_DATE_LAST")
        env_check = os.getenv("GOSWIFT_CHECK_DATE")  # legacy single date

        if env_first:
            date_first = _parse_date(env_first)
        if env_last:
            date_last = _parse_date(env_last)

        if date_first is not None and date_last is None:
            date_last = date_first
        elif date_first is None and date_last is not None:
            date_first = date_last

        if date_first is None and env_check:
            d = _parse_date(env_check)
            date_first = date_last = d

        rt_first, rt_last = _load_runtime_date_range()
        if rt_first is not None and rt_last is not None:
            date_first, date_last = rt_first, rt_last

        rt_locations = _load_runtime_locations()
        if rt_locations is not None:
            locations = rt_locations

        if date_first is None:
            today = date.today()
            next_month = 1 if today.month == 12 else today.month + 1
            next_year = today.year + 1 if today.month == 12 else today.year
            date_first = date_last = date(next_year, next_month, 1)

        interval_minutes_raw = os.getenv("CHECK_INTERVAL_MINUTES", "10")
        try:
            interval_minutes = int(interval_minutes_raw)
        except ValueError as exc:
            raise RuntimeError("CHECK_INTERVAL_MINUTES must be an integer") from exc

        return cls(
            telegram_bot_token=token,
            telegram_owner_chat_id=owner_chat_id,
            goswift_base_url=base_url.rstrip("/"),
            goswift_cookie=goswift_cookie,
            goswift_locations=normalize_location_keys(locations),
            goswift_checkpoint_id=checkpoint_id,
            goswift_direction=direction,
            goswift_category=category,
            goswift_date_first=date_first,
            goswift_date_last=date_last,
            check_interval=timedelta(minutes=interval_minutes),
        )
