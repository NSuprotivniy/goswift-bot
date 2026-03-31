# System Testing

This repository now uses `pytest` for system testing. The suite is designed to
exercise the bot at a high level while replacing Telegram with a local spy/mock
transport. Live GoSwift tests still perform real HTTP requests against the
configured GoSwift website.

## Test Philosophy

- `system` tests cover high-level behavior across modules.
- `live` tests perform real requests to GoSwift and are intended for manual runs.
- Telegram is never contacted during tests; all outbound messages are captured by
  test doubles and asserted exactly.
- The suite intentionally does not promise literal 100% branch coverage because
  live-only happy-path testing cannot deterministically reproduce every remote
  failure mode.

## Required Environment

Live tests can now run without a pre-exported GoSwift session cookie when the
portal accepts automatic session bootstrap.

Required for live scenarios:
- `GOSWIFT_BASE_URL`

Optional but supported:
- `GOSWIFT_COOKIE`
- `GOSWIFT_BASE_URL`
- `GOSWIFT_DIRECTION`
- `GOSWIFT_CATEGORY`
- `GOSWIFT_DATE_FIRST`
- `GOSWIFT_DATE_LAST`
- `GOSWIFT_LOCATIONS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_OWNER_CHAT_ID`
- `CHECK_INTERVAL_MINUTES`
- `GOSWIFT_LIVE_ENV_FILE`

Notes:
- If variables are not already exported, the live harness will also look for a
  local `.env` file by default.
- `TELEGRAM_*` values may be dummy values during tests because Telegram is mocked.
- Live tests are manual-only and should not run in default CI unless you
  explicitly provide secrets and opt in.

## Commands

Install dependencies:

```bash
source goswift_venv/bin/activate
pip install -r requirements.txt
```

Run the non-live system suite:

```bash
pytest -m "system and not live" -v
```

Run only live GoSwift system tests:

```bash
pytest -m "system and live" -v -s
```

Run the full system suite:

```bash
pytest -m "system" -v -s
```

## Scenario Matrix

### SYS-001: Config loading with runtime overrides
- Intent: prove that env loading, runtime JSON overrides, defaults, and date iteration behave as the bot expects.
- Dependencies: none beyond local filesystem.
- Command: `pytest -m "system and not live" -v tests/system/test_config_system.py`
- Expected result: config values are normalized; runtime dates and locations override env.
- Failure means: the bot may boot with the wrong dates, locations, interval, or base URL.

### SYS-002: Runtime config persistence
- Intent: prove that Telegram-driven state survives through `runtime_config.json`.
- Dependencies: none beyond local filesystem.
- Command: `pytest -m "system and not live" -v tests/system/test_config_system.py`
- Expected result: persisted JSON is updated and later `Config.from_env()` reads it back.
- Failure means: `/locations` or `/setdaterange` changes will not survive or will reload incorrectly.

### SYS-003: Live GoSwift fetch flow
- Intent: prove that the real GoSwift browser-like flow still returns parseable slot objects.
- Dependencies: real GoSwift website availability. `GOSWIFT_COOKIE` is optional fallback input.
- Command: `pytest -m "system and live" -v tests/system/test_live_goswift_system.py::test_fetch_slots_live_returns_normalized_slot_objects`
- Expected result: each configured location can be queried successfully; zero slots is acceptable.
- Failure means: the client flow, parsing contract, or session assumptions drifted from the live website.

### SYS-004: Live run_check_cycle orchestration
- Intent: prove that the scheduler/check-now core loop iterates locations and dates, sorts output, and avoids duplicate notifications on a repeat run.
- Dependencies: real GoSwift website availability. `GOSWIFT_COOKIE` is optional fallback input.
- Command: `pytest -m "system and live" -v tests/system/test_live_goswift_system.py::test_run_check_cycle_live_sorts_results_and_prevents_repeat_notifications`
- Expected result: first run completes, second run emits no new slots, soft errors stay in the returned list.
- Failure means: owner-facing checks may repeat slots, sort badly, or stop handling partial failures correctly.

### SYS-005: `/start` owner and non-owner behavior
- Intent: prove that the owner sees help text while outsiders are rejected.
- Dependencies: mocked Telegram only.
- Command: `pytest -m "system and not live" -v tests/system/test_commands_system.py::test_start_command_serves_owner_help_and_rejects_other_chats`
- Expected result: owner help text uses HTML; non-owner receives the privacy warning.
- Failure means: bot access control or onboarding text regressed.

### SYS-006: `/status` output
- Intent: prove that current in-memory state is rendered correctly for the owner.
- Dependencies: mocked Telegram only.
- Command: `pytest -m "system and not live" -v tests/system/test_commands_system.py::test_status_command_reports_last_run_and_active_locations`
- Expected result: message includes last check, slot count, error, and active locations.
- Failure means: operator visibility into bot health is degraded.

### SYS-007: Live `/check_now`
- Intent: prove that the manual immediate-check command performs a real GoSwift fetch and sends a user-visible outcome.
- Dependencies: real GoSwift website availability; mocked Telegram for assertions. `GOSWIFT_COOKIE` is optional fallback input.
- Command: `pytest -m "system and live" -v -s tests/system/test_commands_system.py::test_check_now_command_live_sends_progress_and_final_result`
- Expected result: progress message is sent first; final message is either a slot notification or a no-slots info message.
- Failure means: the most important operator command no longer works end-to-end.

### SYS-008: `/locations` rendering and callback persistence
- Intent: prove that location selection UI and persistence behave as expected.
- Dependencies: mocked Telegram and local filesystem.
- Command: `pytest -m "system and not live" -v tests/system/test_commands_system.py::test_locations_command_and_callback_persist_runtime_selection`
- Expected result: keyboard contains all choices; callback updates config and runtime JSON.
- Failure means: operators may think they changed monitored locations when they did not.

### SYS-009: `/daterange` and `/setdaterange`
- Intent: prove that date range display and update flows work together with persisted runtime state.
- Dependencies: mocked Telegram and local filesystem.
- Command: `pytest -m "system and not live" -v tests/system/test_commands_system.py::test_daterange_commands_show_and_update_persisted_state`
- Expected result: current range is displayed correctly and updates persist immediately.
- Failure means: operators cannot trust configured monitoring dates.

### SYS-010: Notification formatting
- Intent: prove the exact outbound Telegram payload for newly found slots.
- Dependencies: mocked Telegram only.
- Command: `pytest -m "system and not live" -v tests/system/test_commands_system.py::test_notifier_formats_exact_slot_message_and_button_payload`
- Expected result: exact text, HTML mode, button label, URL, and preview settings all match expectations.
- Failure means: user-facing notifications become malformed or less actionable.

### SYS-011: Live periodic scheduler entry point
- Intent: prove that the periodic scheduler hook updates status and optionally emits notifications without running the real polling loop.
- Dependencies: real GoSwift website availability; mocked Telegram. `GOSWIFT_COOKIE` is optional fallback input.
- Command: `pytest -m "system and live" -v -s tests/system/test_commands_system.py::test_periodic_check_live_updates_last_run_and_sends_optional_message`
- Expected result: `LastRunInfo` updates; slot notifications are sent when slots exist; silence is acceptable when no slots exist.
- Failure means: autonomous periodic monitoring may silently stop updating health state.

### SYS-012: `main.py` wiring smoke test
- Intent: prove that production dependency wiring still assembles the application object, handlers, scheduler, and bot data correctly.
- Dependencies: local doubles only.
- Command: `pytest -m "system and not live" -v tests/system/test_main_smoke.py`
- Expected result: fake builder sees the token, bot data is populated, scheduler/handlers are invoked, fake polling starts.
- Failure means: the process may fail at startup even if lower-level modules still pass.

## Residual Gaps

The following remain intentionally outside this live-only happy-path suite:

- expired session redirect handling (`302` / login redirect)
- malformed or unexpected HTML
- non-200 status responses
- unexpected content types
- malformed runtime JSON and invalid user input branches
- GoSwift-side partial failures that cannot be reproduced deterministically

If you later want literal branch-level coverage, the next step is to add a
non-live negative test layer with a local HTTP double of GoSwift.
