from __future__ import annotations

from datetime import date

import pytest

from goswift_bot.config import (
    Config,
    save_runtime_date_range,
    save_runtime_locations,
)

from .support.harness import build_harness


@pytest.mark.system
def test_config_from_env_uses_runtime_overrides_and_normalizes_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Verify that Config.from_env reads required env values, normalizes location
        configuration, and allows runtime_config.json to override date range and
        active locations.

    Required live env:
        None. This scenario uses deterministic synthetic env values because it
        validates configuration loading rather than HTTP communication.

    Setup state:
        - A temporary runtime_config.json contains dates and a single location.
        - Environment variables provide valid Telegram and GoSwift values plus a
          broader default location list that should be overridden by runtime data.

    Steps:
        1. Patch the runtime config path to a temporary file.
        2. Load Config.from_env().
        3. Observe effective date range, normalized locations, and interval.

    Assertions:
        - Runtime dates win over env defaults.
        - Runtime locations win over env-provided GOSWIFT_LOCATIONS.
        - Base URL trimming, category defaults, and date iteration work as expected.

    Known caveats:
        - This test intentionally does not hit the real GoSwift site; it covers
          only the configuration boundary of the system suite.
    """
    harness = build_harness(
        monkeypatch,
        tmp_path / "runtime_config.json",
        env={
            "TELEGRAM_BOT_TOKEN": "token-from-env",
            "TELEGRAM_OWNER_CHAT_ID": "42",
            "GOSWIFT_COOKIE": "cookie-from-env",
            "GOSWIFT_BASE_URL": "https://www.eestipiir.ee/",
            "GOSWIFT_LOCATIONS": "luhamaa,koidula",
            "GOSWIFT_CATEGORY": "B",
            "CHECK_INTERVAL_MINUTES": "15",
        },
        runtime_config_data=(
            '{\n'
            '  "date_first": "2026-03-09",\n'
            '  "date_last": "2026-03-11",\n'
            '  "locations": ["luhamaa"]\n'
            '}'
        ),
    )

    cfg = harness.config

    assert cfg.telegram_bot_token == "token-from-env"
    assert cfg.telegram_owner_chat_id == 42
    assert cfg.goswift_cookie == "cookie-from-env"
    assert cfg.goswift_base_url == "https://www.eestipiir.ee"
    assert cfg.goswift_locations == ["luhamaa"]
    assert cfg.goswift_date_first == date(2026, 3, 9)
    assert cfg.goswift_date_last == date(2026, 3, 11)
    assert cfg.iter_dates() == [
        date(2026, 3, 9),
        date(2026, 3, 10),
        date(2026, 3, 11),
    ]
    assert int(cfg.check_interval.total_seconds()) == 900


@pytest.mark.system
def test_config_from_env_allows_missing_optional_cookie(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Verify that Config.from_env no longer requires GOSWIFT_COOKIE and keeps
        the field unset when the operator relies on automatic session bootstrap.

    Required live env:
        None. This scenario validates configuration behavior only.

    Setup state:
        - A temporary runtime_config.json starts empty.
        - Environment variables provide the required Telegram values and a valid
          GoSwift base URL, but omit GOSWIFT_COOKIE.

    Steps:
        1. Build a harness without GOSWIFT_COOKIE in env.
        2. Load Config.from_env() through the harness.

    Assertions:
        - Config loads successfully.
        - goswift_cookie is None.
        - Other GoSwift defaults remain intact.

    Known caveats:
        - This covers the config contract only; the live HTTP behavior is covered
          by the live system scenarios.
    """
    harness = build_harness(
        monkeypatch,
        tmp_path / "runtime_config.json",
        env={
            "TELEGRAM_BOT_TOKEN": "token-from-env",
            "TELEGRAM_OWNER_CHAT_ID": "42",
            "GOSWIFT_BASE_URL": "https://www.eestipiir.ee/",
            "GOSWIFT_LOCATIONS": "luhamaa,koidula",
            "GOSWIFT_CATEGORY": "B",
            "CHECK_INTERVAL_MINUTES": "15",
        },
    )

    cfg = harness.config

    assert cfg.goswift_cookie is None
    assert cfg.goswift_base_url == "https://www.eestipiir.ee"
    assert cfg.goswift_locations == ["koidula", "luhamaa"]
    assert int(cfg.check_interval.total_seconds()) == 900


@pytest.mark.system
def test_runtime_config_persistence_updates_future_config_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Ensure the persisted runtime configuration APIs update the shared JSON
        state and that later Config.from_env() calls observe those changes.

    Required live env:
        None. Persistence behavior is validated locally because it is part of the
        bot's runtime state handling rather than the remote GoSwift contract.

    Setup state:
        - A temporary runtime_config.json starts empty.
        - A harness loads Config from synthetic but valid env values.

    Steps:
        1. Persist a new date range using save_runtime_date_range().
        2. Persist a new active location using save_runtime_locations().
        3. Reload Config.from_env() from the same patched runtime path.

    Assertions:
        - The JSON file stores the new values.
        - Reloaded config reflects the persisted date range and locations.
        - Date range helpers still report a valid inclusive interval.

    Known caveats:
        - This test focuses on the runtime state shared with Telegram commands,
          not on live HTTP traffic.
    """
    harness = build_harness(
        monkeypatch,
        tmp_path / "runtime_config.json",
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_OWNER_CHAT_ID": "42",
            "GOSWIFT_COOKIE": "cookie",
            "GOSWIFT_LOCATIONS": "koidula,luhamaa",
        },
    )

    save_runtime_date_range(date(2026, 4, 1), date(2026, 4, 3))
    normalized = save_runtime_locations(["koidula"])

    reloaded = Config.from_env()

    assert normalized == ["koidula"]
    assert '"date_first": "2026-04-01"' in harness.runtime_config_path.read_text()
    assert '"date_last": "2026-04-03"' in harness.runtime_config_path.read_text()
    assert '"locations": [\n    "koidula"\n  ]' in harness.runtime_config_path.read_text()
    assert reloaded.goswift_locations == ["koidula"]
    assert reloaded.date_range_ok is True
    assert reloaded.iter_dates() == [
        date(2026, 4, 1),
        date(2026, 4, 2),
        date(2026, 4, 3),
    ]
