from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from .config import Config, save_runtime_date_range, save_runtime_locations
from .goswift_client import GoSwiftClient, SessionExpiredError
from .locations import DEFAULT_LOCATION_KEYS
from .models import LastRunInfo
from .notifier import send_info_message, send_slots_message, format_status
from .scheduler import run_check_cycle
from .slot_filter import SlotFilter

LOCATION_CALLBACK_PREFIX = "locations:"
logger = logging.getLogger(__name__)


def _log_command_start(name: str, update: Update, args: list[str] | None = None) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    logger.info("Handling Telegram command: name=%s chat_id=%s", name, chat_id)
    logger.debug(
        "Telegram command payload: name=%s chat_id=%s args=%s update=%r",
        name,
        chat_id,
        args or [],
        update,
    )


def _log_command_end(name: str, outcome: str, **details: object) -> None:
    logger.info("Completed Telegram command: name=%s outcome=%s", name, outcome)
    if details:
        logger.debug(
            "Telegram command result: name=%s outcome=%s details=%s",
            name,
            outcome,
            details,
        )


def _locations_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Koidula",
                    callback_data=f"{LOCATION_CALLBACK_PREFIX}koidula",
                ),
                InlineKeyboardButton(
                    "Luhamaa",
                    callback_data=f"{LOCATION_CALLBACK_PREFIX}luhamaa",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Both",
                    callback_data=f"{LOCATION_CALLBACK_PREFIX}both",
                )
            ],
        ]
    )


def _locations_text(cfg: Config) -> str:
    return (
        "<b>Active locations</b>\n\n"
        f"Current selection: <b>{cfg.active_locations_text}</b>\n\n"
        "Choose what to monitor:"
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    _log_command_start("start", update)

    if not update.effective_chat:
        _log_command_end("start", "ignored_no_chat")
        return

    chat_id = update.effective_chat.id
    if chat_id != cfg.telegram_owner_chat_id:
        logger.warning(
            "Rejected Telegram command from non-owner chat: name=start chat_id=%s owner_chat_id=%s",
            chat_id,
            cfg.telegram_owner_chat_id,
        )
        await update.message.reply_text(
            "This bot is private and only usable from the configured owner chat."
        )
        _log_command_end("start", "denied_non_owner", chat_id=chat_id)
        return

    text = (
        "Hi! I will monitor GoSwift border queue slots for you.\n\n"
        "Commands:\n"
        "- /status – show last check info\n"
        "- /check_now – run an immediate check\n"
        "- /locations – choose active locations\n"
        "- /daterange – show current date range\n"
        "- /setdaterange – change date range (e.g. /setdaterange 2026-03-01 2026-03-10)\n"
        "- /help – show this help\n\n"
        f"Current locations: <b>{cfg.active_locations_text}</b>\n"
        "I will also notify you automatically when new slots become available."
    )
    await update.message.reply_text(text, parse_mode="HTML")
    _log_command_end("start", "success", chat_id=chat_id)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    last_run: LastRunInfo = context.bot_data["last_run"]
    _log_command_start("status", update)

    if not update.effective_chat:
        _log_command_end("status", "ignored_no_chat")
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
        _log_command_end("status", "denied_non_owner", chat_id=update.effective_chat.id)
        return

    text = (
        format_status(last_run)
        + "\n"
        + f"Active locations: <b>{cfg.active_locations_text}</b>"
    )
    await send_info_message(update.effective_chat.id, text, context)
    _log_command_end(
        "status",
        "success",
        chat_id=update.effective_chat.id,
        last_slots_found=last_run.last_slots_found,
        last_error=last_run.last_error,
    )


async def check_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    client: GoSwiftClient = context.bot_data["goswift_client"]
    slot_filter: SlotFilter = context.bot_data["slot_filter"]
    last_run: LastRunInfo = context.bot_data["last_run"]
    _log_command_start("check_now", update)

    if not update.effective_chat:
        _log_command_end("check_now", "ignored_no_chat")
        return
    chat_id = update.effective_chat.id
    if chat_id != cfg.telegram_owner_chat_id:
        _log_command_end("check_now", "denied_non_owner", chat_id=chat_id)
        return

    await send_info_message(
        chat_id,
        f"Checking GoSwift slots for <b>{cfg.active_locations_text}</b>...",
        context,
    )

    try:
        loop = asyncio.get_event_loop()
        new_slots, errors = await loop.run_in_executor(
            None,
            lambda: run_check_cycle(cfg, client, slot_filter, trigger="command:/check_now"),
        )

        last_run.last_check_time = datetime.now(UTC)
        last_run.last_error = "; ".join(errors) if errors else None
        last_run.last_slots_found = len(new_slots)
        logger.info(
            "Updated LastRunInfo after /check_now: last_check_time=%s last_slots_found=%d last_error=%s",
            last_run.last_check_time,
            last_run.last_slots_found,
            last_run.last_error,
        )

        if new_slots:
            await send_slots_message(chat_id, new_slots, context)
            _log_command_end(
                "check_now",
                "success_slots_found",
                chat_id=chat_id,
                slots=[slot.id for slot in new_slots],
            )
        else:
            text = "No new available slots found at this moment."
            if errors:
                text += "\n\nSome location checks failed:\n" + "\n".join(
                    f"- {escape(err)}" for err in errors
                )
            await send_info_message(chat_id, text, context)
            _log_command_end(
                "check_now",
                "success_no_slots",
                chat_id=chat_id,
                errors=errors,
            )
    except SessionExpiredError as exc:
        last_run.last_check_time = datetime.now(UTC)
        last_run.last_error = str(exc)
        last_run.last_slots_found = 0
        logger.warning(
            "/check_now interrupted by session expiration: chat_id=%s last_error=%s",
            chat_id,
            last_run.last_error,
        )
        await send_info_message(
            chat_id,
            "GoSwift session flow was interrupted. "
            "The bot will try to build a fresh session again on the next check. "
            "If this keeps happening, refresh GOSWIFT_COOKIE and restart the bot.",
            context,
        )
        _log_command_end("check_now", "session_expired", chat_id=chat_id, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        last_run.last_check_time = datetime.now(UTC)
        last_run.last_error = str(exc)
        last_run.last_slots_found = 0
        logger.exception("/check_now failed: chat_id=%s", chat_id)
        await send_info_message(
            chat_id,
            "Error while checking GoSwift slots. See logs for details.",
            context,
        )
        _log_command_end("check_now", "error", chat_id=chat_id, error=str(exc))


async def locations_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    _log_command_start("locations", update)

    if not update.effective_chat:
        _log_command_end("locations", "ignored_no_chat")
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
        _log_command_end("locations", "denied_non_owner", chat_id=update.effective_chat.id)
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=_locations_text(cfg),
        parse_mode="HTML",
        reply_markup=_locations_keyboard(),
        disable_web_page_preview=True,
    )
    _log_command_end(
        "locations",
        "success",
        chat_id=update.effective_chat.id,
        active_locations=cfg.goswift_locations,
    )


async def locations_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    query = update.callback_query
    logger.info(
        "Handling Telegram callback: name=locations_callback chat_id=%s data=%s",
        query.message.chat_id if query and query.message else None,
        query.data if query else None,
    )
    logger.debug("Telegram callback payload: update=%r", update)

    if query is None or query.message is None or query.data is None:
        _log_command_end("locations_callback", "ignored_incomplete_callback")
        return
    if query.message.chat_id != cfg.telegram_owner_chat_id:
        await query.answer()
        _log_command_end(
            "locations_callback",
            "denied_non_owner",
            chat_id=query.message.chat_id,
        )
        return
    if not query.data.startswith(LOCATION_CALLBACK_PREFIX):
        _log_command_end(
            "locations_callback",
            "ignored_unexpected_callback",
            data=query.data,
        )
        return

    selected = query.data.removeprefix(LOCATION_CALLBACK_PREFIX)
    if selected == "both":
        location_keys = list(DEFAULT_LOCATION_KEYS)
    else:
        location_keys = [selected]

    normalized = save_runtime_locations(location_keys)
    cfg.set_locations(normalized)
    logger.info("Updated runtime locations via callback: locations=%s", normalized)

    await query.answer("Locations updated")
    await query.edit_message_text(
        text=(
            "<b>Locations updated</b>\n\n"
            f"Current selection: <b>{cfg.active_locations_text}</b>"
        ),
        parse_mode="HTML",
        reply_markup=_locations_keyboard(),
    )
    _log_command_end(
        "locations_callback",
        "success",
        chat_id=query.message.chat_id,
        active_locations=normalized,
    )


async def daterange_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    _log_command_start("daterange", update)

    if not update.effective_chat:
        _log_command_end("daterange", "ignored_no_chat")
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
        _log_command_end("daterange", "denied_non_owner", chat_id=update.effective_chat.id)
        return

    if cfg.date_range_ok:
        dates = cfg.iter_dates()
        text = (
            f"<b>Current date range</b>\n\n"
            f"From: <b>{cfg.goswift_date_first}</b>\n"
            f"To: <b>{cfg.goswift_date_last}</b>\n"
            f"Total days: <b>{len(dates)}</b>\n"
            f"Active locations: <b>{cfg.active_locations_text}</b>"
        )
    else:
        text = "No date range configured. Use /setdaterange to set one."
    await send_info_message(update.effective_chat.id, text, context)
    _log_command_end(
        "daterange",
        "success",
        chat_id=update.effective_chat.id,
        date_first=cfg.goswift_date_first,
        date_last=cfg.goswift_date_last,
    )


async def setdaterange_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    cfg: Config = context.bot_data["config"]
    args = context.args or []
    _log_command_start("setdaterange", update, args=args)

    if not update.effective_chat:
        _log_command_end("setdaterange", "ignored_no_chat")
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
        _log_command_end("setdaterange", "denied_non_owner", chat_id=update.effective_chat.id)
        return

    if len(args) != 2:
        await send_info_message(
            update.effective_chat.id,
            "Usage: /setdaterange &lt;YYYY-MM-DD&gt; &lt;YYYY-MM-DD&gt;\n"
            "Example: /setdaterange 2026-03-01 2026-03-10",
            context,
        )
        _log_command_end("setdaterange", "validation_error_wrong_arg_count", args=args)
        return

    try:
        date_first = date.fromisoformat(args[0])
        date_last = date.fromisoformat(args[1])
    except ValueError:
        await send_info_message(
            update.effective_chat.id,
            "Invalid date format. Use YYYY-MM-DD for both dates.",
            context,
        )
        _log_command_end("setdaterange", "validation_error_bad_date_format", args=args)
        return

    if date_first > date_last:
        await send_info_message(
            update.effective_chat.id,
            "First date must be before or equal to last date.",
            context,
        )
        _log_command_end(
            "setdaterange",
            "validation_error_reversed_range",
            date_first=date_first,
            date_last=date_last,
        )
        return

    save_runtime_date_range(date_first, date_last)
    cfg.goswift_date_first = date_first
    cfg.goswift_date_last = date_last
    logger.info(
        "Updated runtime date range via command: date_first=%s date_last=%s",
        date_first,
        date_last,
    )

    dates = cfg.iter_dates()
    await send_info_message(
        update.effective_chat.id,
        f"Date range updated.\n\n"
        f"From: <b>{date_first}</b>\n"
        f"To: <b>{date_last}</b>\n"
        f"Total days: <b>{len(dates)}</b>\n"
        f"Active locations: <b>{cfg.active_locations_text}</b>",
        context,
    )
    _log_command_end(
        "setdaterange",
        "success",
        chat_id=update.effective_chat.id,
        date_first=date_first,
        date_last=date_last,
        total_days=len(dates),
    )


def register_handlers(app: Application) -> None:
    logger.info("Registering Telegram handlers")
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("check_now", check_now_command))
    app.add_handler(CommandHandler("locations", locations_command))
    app.add_handler(CommandHandler("daterange", daterange_command))
    app.add_handler(CommandHandler("setdaterange", setdaterange_command))
    app.add_handler(CallbackQueryHandler(locations_callback, pattern=f"^{LOCATION_CALLBACK_PREFIX}"))
