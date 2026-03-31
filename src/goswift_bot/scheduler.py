from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime

from telegram.ext import ContextTypes, JobQueue

from .config import Config
from .goswift_client import GoSwiftClient, SessionExpiredError
from .locations import LOCATIONS
from .models import LastRunInfo
from .notifier import send_info_message, send_slots_message
from .slot_filter import SlotFilter

logger = logging.getLogger(__name__)


def run_check_cycle(
    cfg: Config,
    client: GoSwiftClient,
    slot_filter: SlotFilter,
) -> tuple[list, list[str]]:
    """Fetch slots for all active locations and dates, return new ones and soft errors."""
    all_new: list = []
    errors: list[str] = []

    dates = cfg.iter_dates()
    total_requests = len(cfg.goswift_locations) * len(dates)
    request_index = 0

    for location_key in cfg.goswift_locations:
        location_title = LOCATIONS[location_key].title
        for target_date in dates:
            request_index += 1
            logger.info(
                "Checking GoSwift location=%s date=%s (%d/%d)",
                location_title,
                target_date.isoformat(),
                request_index,
                total_requests,
            )
            try:
                slots = client.fetch_slots(target_date, location_key=location_key)
                logger.info(
                    "Found %d raw slots for location=%s date=%s",
                    len(slots),
                    location_title,
                    target_date.isoformat(),
                )
                new_slots = slot_filter.filter_new(slots)
                all_new.extend(new_slots)
            except SessionExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Failed to check GoSwift location=%s date=%s",
                    location_title,
                    target_date.isoformat(),
                )
                errors.append(f"{location_title} on {target_date}: {exc}")

            if request_index < total_requests:
                delay = random.uniform(1, 10)
                time.sleep(delay)

    all_new.sort(key=lambda slot: (slot.date_time, slot.location_key))
    return all_new, errors


async def periodic_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    client: GoSwiftClient = context.bot_data["goswift_client"]
    slot_filter: SlotFilter = context.bot_data["slot_filter"]
    last_run: LastRunInfo = context.bot_data["last_run"]

    chat_id = cfg.telegram_owner_chat_id

    try:
        loop = asyncio.get_event_loop()
        new_slots, errors = await loop.run_in_executor(
            None,
            lambda: run_check_cycle(cfg, client, slot_filter),
        )

        last_run.last_check_time = datetime.now(UTC)
        last_run.last_error = "; ".join(errors) if errors else None
        last_run.last_slots_found = len(new_slots)

        if new_slots:
            await send_slots_message(chat_id, new_slots, context)
    except SessionExpiredError as exc:
        last_run.last_check_time = datetime.now(UTC)
        last_run.last_error = str(exc)
        last_run.last_slots_found = 0
        await send_info_message(
            chat_id,
            "GoSwift session flow was interrupted. "
            "The bot will try to build a fresh session again on the next check. "
            "If this keeps happening, refresh GOSWIFT_COOKIE and restart the bot.",
            context,
        )
    except Exception as exc:  # noqa: BLE001
        last_run.last_check_time = datetime.now(UTC)
        last_run.last_error = str(exc)
        last_run.last_slots_found = 0
        await send_info_message(
            chat_id,
            "Error while checking GoSwift slots. See logs for details.",
            context,
        )


def schedule_periodic_checks(job_queue: JobQueue, config: Config) -> None:
    interval_seconds = int(config.check_interval.total_seconds())
    job_queue.run_repeating(
        periodic_check,
        interval=interval_seconds,
        first=interval_seconds,
        name="goswift_periodic_check",
    )
