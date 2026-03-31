from __future__ import annotations

from datetime import date

import pytest

from goswift_bot.goswift_client import GoSwiftClient
from goswift_bot.scheduler import run_check_cycle
from goswift_bot.slot_filter import SlotFilter

from .support.harness import build_harness, require_live_env


@pytest.mark.system
@pytest.mark.live
def test_fetch_slots_live_returns_normalized_slot_objects(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Confirm that GoSwiftClient.fetch_slots performs the real browser-like
        sequence against the live GoSwift website and returns normalized Slot
        objects ready for downstream notification logic.

    Required live env:
        - Optional GOSWIFT_COOKIE for a browser-seeded GoSwift session.
        - Optional GOSWIFT_BASE_URL, GOSWIFT_DIRECTION, and GOSWIFT_CATEGORY.
        - TELEGRAM_* may be synthetic because Telegram is mocked in this suite.

    Setup state:
        - Load live env from the shell or .env.
        - Force the date range to a single deterministic day so the request
          surface remains small and easy to reason about.

    Steps:
        1. Build a harness with live env and a temporary runtime config file.
        2. Call fetch_slots() for each configured location using the configured day.
        3. Inspect every returned Slot object.

    Assertions:
        - The call succeeds without Telegram dependencies.
        - Every slot has a stable id, datetime, location key, checkpoint name, and
          booking URL pointing back to the GoSwift portal.
        - Returned slots, when present, match one of the configured locations.

    Known caveats:
        - Live site data changes constantly, so the test accepts zero slots as a
          valid outcome as long as the request flow completes successfully.
    """
    env = require_live_env()
    env = {
        **env,
        "GOSWIFT_DATE_FIRST": env.get("GOSWIFT_DATE_FIRST", "2026-03-09"),
        "GOSWIFT_DATE_LAST": env.get("GOSWIFT_DATE_LAST", "2026-03-09"),
    }
    harness = build_harness(monkeypatch, tmp_path / "runtime_config.json", env=env)

    seen_location_keys: set[str] = set()
    all_slots = []
    for location_key in harness.config.goswift_locations:
        slots = harness.client.fetch_slots(
            harness.config.goswift_date_first,
            location_key=location_key,
        )
        seen_location_keys.add(location_key)
        all_slots.extend(slots)
        for slot in slots:
            assert slot.id
            assert slot.location_key == location_key
            assert slot.checkpoint
            assert slot.booking_url.startswith(harness.config.goswift_base_url)
            assert "/yphis/preReserveSelectVehicle.action" in slot.booking_url

    assert seen_location_keys == set(harness.config.goswift_locations)
    assert all(
        slot.location_key in harness.config.goswift_locations for slot in all_slots
    )


@pytest.mark.system
@pytest.mark.live
def test_run_check_cycle_live_sorts_results_and_prevents_repeat_notifications(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Validate the high-level live checking cycle used by both the scheduler and
        the /check_now command, including multi-location iteration, sorting, and
        repeat-notification suppression through SlotFilter.

    Required live env:
        - Optional GOSWIFT_COOKIE for a browser-seeded GoSwift session.
        - Optional GOSWIFT_BASE_URL, GOSWIFT_DIRECTION, and GOSWIFT_CATEGORY.

    Setup state:
        - Use a one-day window to keep the live request set bounded.
        - Replace the inter-request delay with zero to keep the system test
          practical while preserving the same orchestration path.

    Steps:
        1. Build a live harness with a fresh SlotFilter.
        2. Run run_check_cycle() once and capture newly discovered slots.
        3. Run run_check_cycle() a second time with the same SlotFilter.

    Assertions:
        - The first run returns a list sorted by datetime/location and no hard
          failures escape the function.
        - The second run returns no new slots because prior slot ids are already
          recorded in SlotFilter.
        - Any soft errors are surfaced as human-readable strings.

    Known caveats:
        - When the live website exposes no slots, the deduplication assertion is
          still meaningful only for the empty set; this is an unavoidable limit of
          live-only happy-path coverage.
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

    first_slots, first_errors = run_check_cycle(
        harness.config,
        harness.client,
        harness.slot_filter,
    )
    second_slots, second_errors = run_check_cycle(
        harness.config,
        harness.client,
        harness.slot_filter,
    )

    assert first_slots == sorted(
        first_slots,
        key=lambda slot: (slot.date_time, slot.location_key),
    )
    assert second_slots == []
    assert all(isinstance(error, str) and error for error in first_errors)
    assert all(isinstance(error, str) and error for error in second_errors)


@pytest.mark.system
@pytest.mark.live
def test_live_client_can_be_wired_through_public_constructor(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """
    Objective:
        Provide a smoke-level public-constructor check showing that Config and
        GoSwiftClient can be assembled exactly as production code does before any
        live requests are made.

    Required live env:
        - Optional GOSWIFT_COOKIE when the live site needs a browser-seeded session.

    Setup state:
        - Build the harness from live env with a temporary runtime config path.

    Steps:
        1. Load config through the normal Config.from_env() path.
        2. Construct a fresh GoSwiftClient(config).

    Assertions:
        - The constructed client retains the same Config instance.
        - The configuration exposes at least one active location and a usable date.

    Known caveats:
        - This test is intentionally shallow; the deeper live behavior is covered
          by the dedicated fetch_slots and run_check_cycle scenarios.
    """
    env = require_live_env()
    harness = build_harness(monkeypatch, tmp_path / "runtime_config.json", env=env)
    client = GoSwiftClient(harness.config)

    assert client.config is harness.config
    assert harness.config.goswift_locations
    assert isinstance(harness.config.goswift_date_first, date)
    assert isinstance(harness.slot_filter, SlotFilter)
