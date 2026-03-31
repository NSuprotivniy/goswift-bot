from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from goswift_bot.config import Config
from goswift_bot.goswift_client import GoSwiftClient
from goswift_bot.models import LastRunInfo, Slot
from goswift_bot.slot_filter import SlotFilter


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


def load_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE env file without mutating process environment."""
    data: dict[str, str] = {}
    if not path.exists():
        return data

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def live_env_snapshot() -> dict[str, str]:
    """Merge process env with optional local .env defaults for manual live runs."""
    merged = load_env_file(Path(os.getenv("GOSWIFT_LIVE_ENV_FILE", DEFAULT_ENV_FILE)))
    merged.update({key: value for key, value in os.environ.items() if value})
    merged.setdefault("TELEGRAM_BOT_TOKEN", "system-test-token")
    merged.setdefault("TELEGRAM_OWNER_CHAT_ID", "123456789")
    merged.setdefault("CHECK_INTERVAL_MINUTES", "10")
    return merged


def default_test_env() -> dict[str, str]:
    """Synthetic env for non-live tests so they stay deterministic and offline."""
    return {
        "TELEGRAM_BOT_TOKEN": "system-test-token",
        "TELEGRAM_OWNER_CHAT_ID": "123456789",
        "GOSWIFT_BASE_URL": "https://www.eestipiir.ee",
        "GOSWIFT_LOCATIONS": "koidula,luhamaa",
        "GOSWIFT_CATEGORY": "B",
        "CHECK_INTERVAL_MINUTES": "10",
        "LOG_LEVEL": "INFO",
        "LOGS_MAX_GB": "5",
        "LOG_CHUNK_MB": "1024",
    }


def require_live_env() -> dict[str, str]:
    """Return live env for manual runs.

    GOSWIFT_COOKIE is optional because the client can now bootstrap a fresh
    session automatically. When the live site still requires a browser-seeded
    session, operators can provide the cookie as a fallback.
    """
    env = live_env_snapshot()
    missing = [key for key in ("GOSWIFT_BASE_URL",) if not env.get(key)]
    if missing:
        pytest.skip(
            "Live GoSwift tests require env values: "
            + ", ".join(missing)
            + ". Set them in the shell or in .env."
        )
    return env


def apply_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    """Replace relevant process env for deterministic config loading."""
    managed_keys = {
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OWNER_CHAT_ID",
        "GOSWIFT_BASE_URL",
        "GOSWIFT_COOKIE",
        "GOSWIFT_LOCATIONS",
        "GOSWIFT_CHECKPOINT_ID",
        "GOSWIFT_DIRECTION",
        "GOSWIFT_CATEGORY",
        "GOSWIFT_DATE_FIRST",
        "GOSWIFT_DATE_LAST",
        "GOSWIFT_CHECK_DATE",
        "CHECK_INTERVAL_MINUTES",
        "LOG_LEVEL",
        "LOGS_MAX_GB",
        "LOG_CHUNK_MB",
    }
    for key in managed_keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        if key in managed_keys and value is not None:
            monkeypatch.setenv(key, value)


@dataclass
class SpyCall:
    """Recorded outbound Telegram operation."""

    method: str
    payload: dict[str, Any]


@dataclass
class SpyBot:
    """Collects messages that the bot would have sent to Telegram."""

    calls: list[SpyCall] = field(default_factory=list)

    async def send_message(self, **kwargs: Any) -> SpyCall:
        call = SpyCall(method="send_message", payload=kwargs)
        self.calls.append(call)
        return call


@dataclass
class SpyMessage:
    """Minimal Telegram message double used by command handlers."""

    chat_id: int
    replies: list[SpyCall] = field(default_factory=list)

    async def reply_text(self, text: str, **kwargs: Any) -> SpyCall:
        call = SpyCall(
            method="reply_text",
            payload={"chat_id": self.chat_id, "text": text, **kwargs},
        )
        self.replies.append(call)
        return call


@dataclass
class SpyCallbackQuery:
    """Minimal callback query double for inline keyboard tests."""

    data: str
    message: SpyMessage
    answered: list[str | None] = field(default_factory=list)
    edits: list[SpyCall] = field(default_factory=list)

    async def answer(self, text: str | None = None) -> None:
        self.answered.append(text)

    async def edit_message_text(self, **kwargs: Any) -> SpyCall:
        call = SpyCall(method="edit_message_text", payload=kwargs)
        self.edits.append(call)
        return call


def make_context(
    config: Config,
    client: GoSwiftClient | None = None,
    slot_filter: SlotFilter | None = None,
    last_run: LastRunInfo | None = None,
    bot: SpyBot | None = None,
    args: list[str] | None = None,
) -> tuple[SimpleNamespace, SpyBot]:
    """Build a lightweight PTB-like context object for handler tests."""
    spy_bot = bot or SpyBot()
    context = SimpleNamespace(
        bot=spy_bot,
        bot_data={
            "config": config,
            "goswift_client": client or GoSwiftClient(config),
            "slot_filter": slot_filter or SlotFilter(),
            "last_run": last_run or LastRunInfo(),
        },
        args=list(args or []),
    )
    return context, spy_bot


def make_message_update(chat_id: int) -> tuple[SimpleNamespace, SpyMessage]:
    """Build an Update-like object containing a message and effective_chat."""
    message = SpyMessage(chat_id=chat_id)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=message,
        callback_query=None,
    )
    return update, message


def make_callback_update(chat_id: int, data: str) -> tuple[SimpleNamespace, SpyCallbackQuery]:
    """Build an Update-like object carrying a callback query."""
    message = SpyMessage(chat_id=chat_id)
    query = SpyCallbackQuery(data=data, message=message)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        message=None,
        callback_query=query,
    )
    return update, query


@dataclass
class Harness:
    """Reusable test harness with config, dependencies, and Telegram spies."""

    config: Config
    client: GoSwiftClient
    slot_filter: SlotFilter
    last_run: LastRunInfo
    context: SimpleNamespace
    bot: SpyBot
    runtime_config_path: Path


def build_harness(
    monkeypatch: pytest.MonkeyPatch,
    runtime_config_path: Path,
    env: dict[str, str] | None = None,
    runtime_config_data: str | None = None,
) -> Harness:
    """Create a fully wired test harness without starting Telegram polling."""
    from goswift_bot import config as config_module

    runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
    if runtime_config_data is None:
        runtime_config_path.write_text("{}")
    else:
        runtime_config_path.write_text(runtime_config_data)

    monkeypatch.setattr(
        config_module,
        "_get_runtime_config_path",
        lambda: runtime_config_path,
    )
    if env is None:
        apply_env(monkeypatch, default_test_env())
    else:
        apply_env(monkeypatch, env)

    config = Config.from_env()
    client = GoSwiftClient(config)
    slot_filter = SlotFilter()
    last_run = LastRunInfo(
        last_check_time=datetime(2026, 3, 31, 12, 0, 0),
        last_error=None,
        last_slots_found=0,
    )
    context, bot = make_context(
        config=config,
        client=client,
        slot_filter=slot_filter,
        last_run=last_run,
    )
    return Harness(
        config=config,
        client=client,
        slot_filter=slot_filter,
        last_run=last_run,
        context=context,
        bot=bot,
        runtime_config_path=runtime_config_path,
    )


def slot_lines(slots: list[Slot]) -> list[str]:
    """Return message lines expected from notifier formatting."""
    lines: list[str] = []
    for slot in slots:
        direction = f" ({slot.direction})" if slot.direction else ""
        checkpoint = f" at {slot.checkpoint}" if slot.checkpoint else ""
        lines.append(f"- {slot.date_time:%Y-%m-%d %H:%M}{checkpoint}{direction}")
    return lines
