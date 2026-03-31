from __future__ import annotations

import logging

from telegram.ext import ApplicationBuilder

from .bot_commands import register_handlers
from .config import Config
from .goswift_client import GoSwiftClient
from .models import LastRunInfo
from .scheduler import schedule_periodic_checks
from .slot_filter import SlotFilter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    config = Config.from_env()

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

    logger.info("Starting GoSwift Telegram bot")

    # run_polling handles initialization, starting, polling loop, and shutdown.
    application.run_polling()


if __name__ == "__main__":
    main()

