from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import logging
from pathlib import Path

import pytest

from goswift_bot import main as main_module
from goswift_bot.config import Config
from goswift_bot.logging_utils import ManagedLogFileHandler


@dataclass
class FakeApplication:
    """Minimal application double capturing main() wiring behavior."""

    bot_data: dict = field(default_factory=dict)
    job_queue: object = field(default_factory=object)
    polling_started: bool = False

    def run_polling(self) -> None:
        self.polling_started = True


class FakeApplicationBuilder:
    """Chainable builder double matching the subset used by main()."""

    def __init__(self, app: FakeApplication) -> None:
        self.app = app
        self.seen_token: str | None = None
        self.concurrent_updates_value: bool | None = None

    def token(self, token: str) -> "FakeApplicationBuilder":
        self.seen_token = token
        return self

    def concurrent_updates(self, value: bool) -> "FakeApplicationBuilder":
        self.concurrent_updates_value = value
        return self

    def build(self) -> FakeApplication:
        return self.app


@pytest.mark.system
def test_main_wires_dependencies_without_real_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Objective:
        Provide the promised smoke-level coverage for main.py wiring while keeping
        the real Telegram polling loop out of the system suite.

    Required live env:
        None. The entire PTB boundary is replaced with a builder/application
        double so no network work occurs.

    Setup state:
        - Patch Config.from_env(), ApplicationBuilder, register_handlers(), and
          schedule_periodic_checks() with controlled test doubles.

    Steps:
        1. Invoke main.main().
        2. Observe what was placed into application.bot_data and whether the
           builder chain saw the expected token and concurrency flag.

    Assertions:
        - main() assembles config, GoSwiftClient, SlotFilter, and LastRunInfo.
        - Handlers and periodic scheduling hooks are called.
        - run_polling() is invoked only on the fake application, never on the
          real PTB application implementation.

    Known caveats:
        - This is a wiring smoke test, not a full process-level launch test.
    """
    fake_app = FakeApplication()
    fake_builder = FakeApplicationBuilder(fake_app)
    registered_apps: list[FakeApplication] = []
    scheduled: list[tuple[object, Config]] = []
    config = Config(
        telegram_bot_token="token",
        telegram_owner_chat_id=42,
        goswift_base_url="https://www.eestipiir.ee",
        goswift_cookie="cookie",
        goswift_locations=["koidula"],
        goswift_checkpoint_id=None,
        goswift_direction=None,
        goswift_category="B",
        goswift_date_first=None,
        goswift_date_last=None,
        check_interval=timedelta(minutes=10),
        log_level="DEBUG",
        logs_max_bytes=5 * 1024 * 1024 * 1024,
        log_chunk_bytes=1024 * 1024 * 1024,
    )

    monkeypatch.setattr(main_module.Config, "from_env", classmethod(lambda cls: config))
    monkeypatch.setattr(main_module, "ApplicationBuilder", lambda: fake_builder)
    monkeypatch.setattr(main_module, "register_handlers", lambda app: registered_apps.append(app))
    monkeypatch.setattr(
        main_module,
        "schedule_periodic_checks",
        lambda job_queue, cfg: scheduled.append((job_queue, cfg)),
    )
    monkeypatch.setattr(
        main_module,
        "configure_logging",
        lambda log_level_name="INFO", logs_max_bytes=1, log_chunk_bytes=1, now=None, log_path=None: Path("/tmp/goswift-session.chunk0001.log"),
    )

    main_module.main()

    assert fake_builder.seen_token == "token"
    assert fake_builder.concurrent_updates_value is False
    assert fake_app.bot_data["config"] is config
    assert "goswift_client" in fake_app.bot_data
    assert "slot_filter" in fake_app.bot_data
    assert "last_run" in fake_app.bot_data
    assert registered_apps == [fake_app]
    assert scheduled == [(fake_app.job_queue, config)]
    assert fake_app.polling_started is True


@pytest.mark.system
def test_configure_logging_creates_separate_session_files_and_writes_debug_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Objective:
        Verify that each server session gets its own log file in the local logs
        directory while keeping stdout logging enabled.

    Required live env:
        None. The test operates only on a temporary directory and the Python
        logging subsystem.

    Setup state:
        - Redirect the project root helper to a temporary directory.
        - Clear root logger handlers before and after the test to avoid leaking
          state between test cases.

    Steps:
        1. Configure logging twice with different timestamps.
        2. Emit a startup log after each configuration.
        3. Inspect the created files and root logger handlers.

    Assertions:
        - The logs directory is created automatically.
        - Each configure call creates a distinct file path.
        - The startup log is written to the configured file.
        - Root logging keeps both file and stream handlers active.
    """
    root_logger = main_module.logging.getLogger()
    original_handlers = list(root_logger.handlers)
    for handler in original_handlers:
        root_logger.removeHandler(handler)
        handler.close()

    monkeypatch.setattr(main_module, "_get_project_root", lambda: tmp_path)

    first_log_path = main_module.configure_logging(
        log_level_name="INFO",
        now=main_module.datetime(2026, 3, 31, 12, 0, 0, 123456)
    )
    main_module.logger.info("Starting GoSwift Telegram bot")

    second_log_path = main_module.configure_logging(
        log_level_name="DEBUG",
        now=main_module.datetime(2026, 3, 31, 12, 0, 1, 654321)
    )
    main_module.logger.info("Starting GoSwift Telegram bot")
    main_module.logger.debug("Debug details are enabled")

    try:
        assert (tmp_path / "logs").is_dir()
        assert first_log_path.parent == tmp_path / "logs"
        assert second_log_path.parent == tmp_path / "logs"
        assert first_log_path != second_log_path
        assert first_log_path.exists()
        assert second_log_path.exists()
        assert "Starting GoSwift Telegram bot" in first_log_path.read_text(encoding="utf-8")
        assert "Starting GoSwift Telegram bot" in second_log_path.read_text(encoding="utf-8")
        assert "Debug details are enabled" not in first_log_path.read_text(encoding="utf-8")
        assert "Debug details are enabled" in second_log_path.read_text(encoding="utf-8")

        assert root_logger.level == logging.DEBUG
        handler_types = {type(handler) for handler in root_logger.handlers}
        assert ManagedLogFileHandler in handler_types
        assert main_module.logging.StreamHandler in handler_types
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            root_logger.addHandler(handler)
