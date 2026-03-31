from __future__ import annotations

import asyncio
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

    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    if chat_id != cfg.telegram_owner_chat_id:
        await update.message.reply_text(
            "This bot is private and only usable from the configured owner chat."
        )
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


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    last_run: LastRunInfo = context.bot_data["last_run"]

    if not update.effective_chat:
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
        return

    text = (
        format_status(last_run)
        + "\n"
        + f"Active locations: <b>{cfg.active_locations_text}</b>"
    )
    await send_info_message(update.effective_chat.id, text, context)


async def check_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    client: GoSwiftClient = context.bot_data["goswift_client"]
    slot_filter: SlotFilter = context.bot_data["slot_filter"]
    last_run: LastRunInfo = context.bot_data["last_run"]

    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    if chat_id != cfg.telegram_owner_chat_id:
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
            lambda: run_check_cycle(cfg, client, slot_filter),
        )

        last_run.last_check_time = datetime.now(UTC)
        last_run.last_error = "; ".join(errors) if errors else None
        last_run.last_slots_found = len(new_slots)

        if new_slots:
            await send_slots_message(chat_id, new_slots, context)
        else:
            text = "No new available slots found at this moment."
            if errors:
                text += "\n\nSome location checks failed:\n" + "\n".join(
                    f"- {escape(err)}" for err in errors
                )
            await send_info_message(chat_id, text, context)
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


async def locations_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]

    if not update.effective_chat:
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=_locations_text(cfg),
        parse_mode="HTML",
        reply_markup=_locations_keyboard(),
        disable_web_page_preview=True,
    )


async def locations_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]

    query = update.callback_query
    if query is None or query.message is None or query.data is None:
        return
    if query.message.chat_id != cfg.telegram_owner_chat_id:
        await query.answer()
        return
    if not query.data.startswith(LOCATION_CALLBACK_PREFIX):
        return

    selected = query.data.removeprefix(LOCATION_CALLBACK_PREFIX)
    if selected == "both":
        location_keys = list(DEFAULT_LOCATION_KEYS)
    else:
        location_keys = [selected]

    normalized = save_runtime_locations(location_keys)
    cfg.set_locations(normalized)

    await query.answer("Locations updated")
    await query.edit_message_text(
        text=(
            "<b>Locations updated</b>\n\n"
            f"Current selection: <b>{cfg.active_locations_text}</b>"
        ),
        parse_mode="HTML",
        reply_markup=_locations_keyboard(),
    )


async def daterange_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]

    if not update.effective_chat:
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
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


async def setdaterange_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    cfg: Config = context.bot_data["config"]

    if not update.effective_chat:
        return
    if update.effective_chat.id != cfg.telegram_owner_chat_id:
        return

    args = context.args or []
    if len(args) != 2:
        await send_info_message(
            update.effective_chat.id,
            "Usage: /setdaterange &lt;YYYY-MM-DD&gt; &lt;YYYY-MM-DD&gt;\n"
            "Example: /setdaterange 2026-03-01 2026-03-10",
            context,
        )
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
        return

    if date_first > date_last:
        await send_info_message(
            update.effective_chat.id,
            "First date must be before or equal to last date.",
            context,
        )
        return

    save_runtime_date_range(date_first, date_last)
    cfg.goswift_date_first = date_first
    cfg.goswift_date_last = date_last

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


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("check_now", check_now_command))
    app.add_handler(CommandHandler("locations", locations_command))
    app.add_handler(CommandHandler("daterange", daterange_command))
    app.add_handler(CommandHandler("setdaterange", setdaterange_command))
    app.add_handler(CallbackQueryHandler(locations_callback, pattern=f"^{LOCATION_CALLBACK_PREFIX}"))
