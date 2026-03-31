from __future__ import annotations

from html import escape
from typing import Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .models import Slot, LastRunInfo


async def send_slots_message(
    chat_id: int,
    slots: Iterable[Slot],
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    slots = list(slots)
    if not slots:
        return

    lines: list[str] = []
    for slot in slots:
        direction_text = f" ({slot.direction})" if slot.direction else ""
        checkpoint_text = f" at {slot.checkpoint}" if slot.checkpoint else ""
        lines.append(
            f"- {slot.date_time:%Y-%m-%d %H:%M}{checkpoint_text}{direction_text}"
        )

    # Use a generic booking link from the first slot.
    booking_url = slots[0].booking_url

    text = (
        "✅ <b>New GoSwift slot(s) available</b>\n\n"
        + "\n".join(lines)
        + "\n\nYou can book via the GoSwift portal:"
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Open GoSwift booking", url=booking_url)]]
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def send_info_message(
    chat_id: int,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


def format_status(last_run: LastRunInfo) -> str:
    parts: list[str] = []
    if last_run.last_check_time:
        parts.append(f"Last check: <b>{last_run.last_check_time:%Y-%m-%d %H:%M:%S}</b>")
    else:
        parts.append("Last check: <b>never</b>")

    parts.append(f"Last slots found: <b>{last_run.last_slots_found}</b>")

    if last_run.last_error:
        parts.append(f"Last error: <b>{escape(last_run.last_error)}</b>")
    else:
        parts.append("Last error: <b>none</b>")

    return "\n".join(parts)
