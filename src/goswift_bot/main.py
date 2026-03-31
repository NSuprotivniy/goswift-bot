from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from telegram.ext import ApplicationBuilder

from .bot_commands import register_handlers
from .config import (
    Config,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOGS_MAX_GB,
    DEFAULT_LOG_CHUNK_MB,
    read_log_chunk_bytes_from_env,
    read_log_level_from_env,
    read_logs_max_bytes_from_env,
)
from .goswift_client import GoSwiftClient
from .logging_utils import ManagedLogFileHandler
from .models import LastRunInfo
from .scheduler import schedule_periodic_checks
from .slot_filter import SlotFilter


logger = logging.getLogger(__name__)
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _get_logs_dir() -> Path:
    return _get_project_root() / "logs"


def _get_log_level_value(log_level_name: str) -> int:
    return getattr(logging, log_level_name, logging.INFO)


def configure_logging(
    log_level_name: str = DEFAULT_LOG_LEVEL,
    logs_max_bytes: int = int(DEFAULT_LOGS_MAX_GB * 1024 * 1024 * 1024),
    log_chunk_bytes: int = DEFAULT_LOG_CHUNK_MB * 1024 * 1024,
    now: datetime | None = None,
    log_path: Path | None = None,
) -> Path:
    logs_dir = _get_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    started_at = now or datetime.now()

    level_value = _get_log_level_value(log_level_name)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    root_logger.setLevel(level_value)

    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = ManagedLogFileHandler(
        logs_dir=logs_dir,
        session_started_at=started_at,
        max_chunk_bytes=log_chunk_bytes,
        max_total_bytes=logs_max_bytes,
        current_log_path=log_path,
    )
    file_handler.setLevel(level_value)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level_value)
    stream_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    log_path = file_handler.current_log_path or (
        logs_dir / f"{started_at.strftime('goswift-bot-%Y%m%d-%H%M%S-%f')}.chunk0001.log"
    )
    return log_path


def _log_startup_summary(config: Config, log_path: Path) -> None:
    logger.info("Starting GoSwift Telegram bot")
    logger.info("Session log file: %s", log_path)
    logger.info(
        "Startup config: log_level=%s locations=%s date_first=%s date_last=%s interval_minutes=%s base_url=%s cookie_present=%s",
        config.log_level,
        config.goswift_locations,
        config.goswift_date_first,
        config.goswift_date_last,
        int(config.check_interval.total_seconds() // 60),
        config.goswift_base_url,
        bool(config.goswift_cookie),
    )
    logger.info(
        "Log retention config: logs_max_bytes=%d log_chunk_bytes=%d",
        config.logs_max_bytes,
        config.log_chunk_bytes,
    )
    logger.debug(
        "Startup config details: owner_chat_id=%s checkpoint_id=%s direction=%s category=%s telegram_bot_token=%s goswift_cookie=%s",
        config.telegram_owner_chat_id,
        config.goswift_checkpoint_id,
        config.goswift_direction,
        config.goswift_category,
        config.telegram_bot_token,
        config.goswift_cookie,
    )


def main() -> None:
    bootstrap_log_level = read_log_level_from_env()
    bootstrap_logs_max_bytes = read_logs_max_bytes_from_env()
    bootstrap_log_chunk_bytes = read_log_chunk_bytes_from_env()
    log_path = configure_logging(
        log_level_name=bootstrap_log_level,
        logs_max_bytes=bootstrap_logs_max_bytes,
        log_chunk_bytes=bootstrap_log_chunk_bytes,
    )
    config = Config.from_env()
    configure_logging(
        log_level_name=config.log_level,
        logs_max_bytes=config.logs_max_bytes,
        log_chunk_bytes=config.log_chunk_bytes,
        log_path=log_path,
    )

    application = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .concurrent_updates(False)
        .build()
    )

    goswift_client = GoSwiftClient(config)
    slot_filter = SlotFilter()
    last_run = LastRunInfo()

    application.bot_data["config"] = config
    application.bot_data["goswift_client"] = goswift_client
    application.bot_data["slot_filter"] = slot_filter
    application.bot_data["last_run"] = last_run

    register_handlers(application)
    schedule_periodic_checks(application.job_queue, config)

    _log_startup_summary(config, log_path)

    # run_polling handles initialization, starting, polling loop, and shutdown.
    application.run_polling()


if __name__ == "__main__":
    main()
