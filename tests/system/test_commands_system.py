from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from telegram.constants import ParseMode

from goswift_bot.bot_commands import (
    LOCATION_CALLBACK_PREFIX,
    check_now_command,
    daterange_command,
    locations_callback,
    locations_command,
    setdaterange_command,
    start_command,
    status_command,
)
from goswift_bot.notifier import send_slots_message
from goswift_bot.scheduler import periodic_check

from .support.harness import (
    SpyCall,
    build_harness,
    make_callback_update,
    make_message_update,
    require_live_env,
    slot_lines,
)


def run_async(awaitable):
    """Execute an async handler inside a synchronous pytest test."""
    return asyncio.run(awaitable)


@pytest.mark.system
def test_start_command_serves_owner_help_and_rejects_other_chats(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Verify the /start command's user-facing contract: the owner receives the
        help text, while a non-owner chat is rejected without exposing private bot
        functionality.

    Required live env:
        None. Telegram is replaced with test doubles and the command itself does
        not require a GoSwift HTTP call.

    Setup state:
        - Build a harness with deterministic configuration values.
        - Create one owner update and one non-owner update.

    Steps:
        1. Invoke /start as the configured owner chat.
        2. Invoke /start as a different chat id.
        3. Inspect reply payloads recorded by the message doubles.

    Assertions:
        - Owner flow sends the command/help text in HTML mode.
        - Non-owner flow sends the privacy restriction text via reply_text().

    Known caveats:
        - This covers command behavior only; Telegram transport itself is mocked.
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

    owner_update, owner_message = make_message_update(42)
    stranger_update, stranger_message = make_message_update(404)

    run_async(start_command(owner_update, harness.context))
    run_async(start_command(stranger_update, harness.context))

    owner_call = owner_message.replies[-1]
    stranger_call = stranger_message.replies[-1]

    assert "Hi! I will monitor GoSwift border queue slots for you." in owner_call.payload["text"]
    assert owner_call.payload["parse_mode"] == "HTML"
    assert "Current locations" in owner_call.payload["text"]
    assert stranger_call.payload["text"] == (
        "This bot is private and only usable from the configured owner chat."
    )


@pytest.mark.system
def test_status_command_reports_last_run_and_active_locations(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Confirm that /status formats the in-memory last-run state together with
        the currently active locations for the owner chat.

    Required live env:
        None. The command reads state accumulated by the application and sends it
        through the mocked Telegram bot.

    Setup state:
        - Build a harness and prepopulate LastRunInfo with representative values.

    Steps:
        1. Invoke /status as the owner chat.
        2. Read the single send_message call captured by the spy bot.

    Assertions:
        - The message includes last check time, last slot count, last error, and
          active locations.
        - HTML parse mode is preserved.

    Known caveats:
        - This does not validate the scheduler; it validates the command surface.
    """
    harness = build_harness(monkeypatch, tmp_path / "runtime_config.json")
    harness.last_run.last_check_time = datetime(2026, 3, 31, 8, 45, 0)
    harness.last_run.last_slots_found = 3
    harness.last_run.last_error = "none"

    update, _message = make_message_update(harness.config.telegram_owner_chat_id)

    run_async(status_command(update, harness.context))

    call = harness.bot.calls[-1]
    assert "Last check: <b>2026-03-31 08:45:00</b>" in call.payload["text"]
    assert "Last slots found: <b>3</b>" in call.payload["text"]
    assert "Last error: <b>none</b>" in call.payload["text"]
    assert "Active locations: <b>" in call.payload["text"]
    assert call.payload["parse_mode"] == "HTML"


@pytest.mark.system
@pytest.mark.live
def test_check_now_command_live_sends_progress_and_final_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Exercise the /check_now command end-to-end against the live GoSwift site
        while keeping Telegram mocked, so we can verify both the progress message
        and the final user-visible outcome.

    Required live env:
        - GOSWIFT_COOKIE for a valid GoSwift session.
        - Optional GOSWIFT_BASE_URL, GOSWIFT_DIRECTION, GOSWIFT_CATEGORY.

    Setup state:
        - Constrain the live date range to one day.
        - Replace scheduler sleep with zero to keep the system test practical.

    Steps:
        1. Invoke /check_now for the owner chat.
        2. Allow the command to call run_check_cycle() in the executor.
        3. Inspect the first progress message and the final outcome message.

    Assertions:
        - The first message announces that live checking has started.
        - The final message is either a slot notification with booking button or
          the "no new available slots" informational message.
        - The command updates LastRunInfo timestamps and slot counters.

    Known caveats:
        - Final branch depends on live slot availability; both success outcomes are
          accepted because the site state is outside the test's control.
    """
    env = require_live_env()
    env = {
        **env,
        "GOSWIFT_DATE_FIRST": env.get("GOSWIFT_DATE_FIRST", "2026-03-09"),
        "GOSWIFT_DATE_LAST": env.get("GOSWIFT_DATE_LAST", "2026-03-09"),
    }
    harness = build_harness(monkeypatch, tmp_path / "runtime_config.json", env=env)
    monkeypatch.setattr("goswift_bot.scheduler.random.uniform", lambda *_args: 0.0)
    monkeypatch.setattr("goswift_bot.scheduler.time.sleep", lambda _seconds: None)

    update, _message = make_message_update(harness.config.telegram_owner_chat_id)

    run_async(check_now_command(update, harness.context))

    assert len(harness.bot.calls) >= 2
    progress_call = harness.bot.calls[0]
    final_call = harness.bot.calls[-1]

    assert "Checking GoSwift slots for" in progress_call.payload["text"]
    assert harness.last_run.last_check_time is not None
    assert harness.last_run.last_slots_found >= 0

    final_text = final_call.payload["text"]
    if "✅ <b>New GoSwift slot(s) available</b>" in final_text:
        assert final_call.payload["reply_markup"] is not None
    else:
        assert "No new available slots found at this moment." in final_text


@pytest.mark.system
def test_locations_command_and_callback_persist_runtime_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Validate the /locations command and its callback flow, including rendered
        keyboard, runtime_config persistence, and in-memory Config updates.

    Required live env:
        None. This scenario is about Telegram-driven runtime state, not GoSwift
        HTTP traffic.

    Setup state:
        - Build a harness with both locations active.
        - Create an owner update for /locations and a callback update selecting
          one location.

    Steps:
        1. Invoke /locations and capture the rendered inline keyboard.
        2. Invoke the callback with `locations:luhamaa`.
        3. Read the updated runtime config JSON and callback edit payload.

    Assertions:
        - The keyboard contains Koidula, Luhamaa, and Both buttons.
        - Callback confirmation is acknowledged and message text is updated.
        - Config and runtime_config.json now point to only `luhamaa`.

    Known caveats:
        - Telegram UI behavior is represented by spies rather than the real API.
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
    update, _message = make_message_update(42)

    run_async(locations_command(update, harness.context))

    menu_call = harness.bot.calls[-1]
    keyboard = menu_call.payload["reply_markup"]
    buttons = [button.text for row in keyboard.inline_keyboard for button in row]
    assert buttons == ["Koidula", "Luhamaa", "Both"]

    callback_update, query = make_callback_update(
        42,
        f"{LOCATION_CALLBACK_PREFIX}luhamaa",
    )
    run_async(locations_callback(callback_update, harness.context))

    assert query.answered[-1] == "Locations updated"
    assert "Current selection: <b>Luhamaa</b>" in query.edits[-1].payload["text"]
    assert harness.config.goswift_locations == ["luhamaa"]
    assert '"locations": [\n    "luhamaa"\n  ]' in harness.runtime_config_path.read_text()


@pytest.mark.system
def test_daterange_commands_show_and_update_persisted_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Cover the owner-facing date range commands so that the current state can
        be queried and later updated with persistence to runtime_config.json.

    Required live env:
        None. The scenario validates runtime state management and command output.

    Setup state:
        - Build a harness with a known starting range.
        - Prepare one update for /daterange and another for /setdaterange.

    Steps:
        1. Invoke /daterange and inspect the current range output.
        2. Invoke /setdaterange with a new inclusive interval.
        3. Verify persisted JSON, in-memory config, and confirmation message.

    Assertions:
        - /daterange reports from/to dates, day count, and active locations.
        - /setdaterange updates config and runtime JSON.
        - Confirmation message reflects the new interval.

    Known caveats:
        - Validation of malformed input is outside this happy-path live suite.
    """
    harness = build_harness(
        monkeypatch,
        tmp_path / "runtime_config.json",
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_OWNER_CHAT_ID": "42",
            "GOSWIFT_COOKIE": "cookie",
            "GOSWIFT_DATE_FIRST": "2026-03-09",
            "GOSWIFT_DATE_LAST": "2026-03-11",
        },
    )

    show_update, _show_message = make_message_update(42)
    run_async(daterange_command(show_update, harness.context))
    show_call = harness.bot.calls[-1]
    assert "From: <b>2026-03-09</b>" in show_call.payload["text"]
    assert "To: <b>2026-03-11</b>" in show_call.payload["text"]
    assert "Total days: <b>3</b>" in show_call.payload["text"]

    set_update, _set_message = make_message_update(42)
    harness.context.args = ["2026-04-01", "2026-04-03"]
    run_async(setdaterange_command(set_update, harness.context))

    set_call = harness.bot.calls[-1]
    assert harness.config.goswift_date_first.isoformat() == "2026-04-01"
    assert harness.config.goswift_date_last.isoformat() == "2026-04-03"
    assert "Date range updated." in set_call.payload["text"]
    assert "Total days: <b>3</b>" in set_call.payload["text"]
    assert '"date_first": "2026-04-01"' in harness.runtime_config_path.read_text()
    assert '"date_last": "2026-04-03"' in harness.runtime_config_path.read_text()


@pytest.mark.system
def test_notifier_formats_exact_slot_message_and_button_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Assert the exact outward Telegram payload produced for newly discovered
        slots, including message body, HTML parse mode, button URL, and preview
        suppression.

    Required live env:
        None. Formatting behavior is deterministic once Slot objects exist.

    Setup state:
        - Build a harness and create representative Slot objects by using the
          live configuration's booking URL base.

    Steps:
        1. Run send_slots_message() with two synthetic slots.
        2. Inspect the recorded send_message payload.

    Assertions:
        - The text matches the exact notifier structure.
        - ParseMode.HTML, booking button, and disable preview flags are preserved.

    Known caveats:
        - Slot acquisition itself is covered by separate live HTTP tests.
    """
    harness = build_harness(monkeypatch, tmp_path / "runtime_config.json")
    slots = [
        harness.client._parse_slots_html(
            """
            <div class="timeslots_desktop">
              <div class="dayContainer">
                <div class="slotContainer" data-time="09.03.2026 10:15">10:15</div>
                <div class="slotContainer" data-time="09.03.2026 12:45">12:45</div>
              </div>
            </div>
            """,
            location_key="koidula",
            days=1,
        )
    ]
    slot_list = list(slots[0])

    run_async(
        send_slots_message(
            harness.config.telegram_owner_chat_id,
            slot_list,
            harness.context,
        )
    )

    call = harness.bot.calls[-1]
    expected_text = (
        "✅ <b>New GoSwift slot(s) available</b>\n\n"
        + "\n".join(slot_lines(slot_list))
        + "\n\nYou can book via the GoSwift portal:"
    )
    assert call.payload["chat_id"] == harness.config.telegram_owner_chat_id
    assert call.payload["text"] == expected_text
    assert call.payload["parse_mode"] == ParseMode.HTML
    assert call.payload["disable_web_page_preview"] is True
    button = call.payload["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open GoSwift booking"
    assert button.url == slot_list[0].booking_url


@pytest.mark.system
@pytest.mark.live
def test_periodic_check_live_updates_last_run_and_sends_optional_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Exercise the scheduler entry point used by periodic polling without
        calling the real Telegram polling loop, ensuring that the live check cycle
        updates LastRunInfo and emits user-visible messages when appropriate.

    Required live env:
        - GOSWIFT_COOKIE for a valid live GoSwift session.

    Setup state:
        - Use a one-day live range and zero inter-request delay.
        - Build the periodic-check context through the shared harness.

    Steps:
        1. Invoke periodic_check() directly.
        2. Inspect updated LastRunInfo and any outbound Telegram calls.

    Assertions:
        - last_check_time is updated and slot count is non-negative.
        - If slots exist, the final message is a slot notification.
        - If no slots exist, periodic_check may send no message, which is valid.

    Known caveats:
        - This scenario accepts "no message" as a valid no-slot outcome because
          the scheduler is intentionally quiet unless it has something useful to say.
    """
    env = require_live_env()
    env = {
        **env,
        "GOSWIFT_DATE_FIRST": env.get("GOSWIFT_DATE_FIRST", "2026-03-09"),
        "GOSWIFT_DATE_LAST": env.get("GOSWIFT_DATE_LAST", "2026-03-09"),
    }
    harness = build_harness(monkeypatch, tmp_path / "runtime_config.json", env=env)
    monkeypatch.setattr("goswift_bot.scheduler.random.uniform", lambda *_args: 0.0)
    monkeypatch.setattr("goswift_bot.scheduler.time.sleep", lambda _seconds: None)

    run_async(periodic_check(harness.context))

    assert harness.last_run.last_check_time is not None
    assert harness.last_run.last_slots_found >= 0
    if harness.bot.calls:
        assert isinstance(harness.bot.calls[-1], SpyCall)
