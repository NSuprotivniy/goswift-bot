from __future__ import annotations

import gzip
import logging
from datetime import datetime
from pathlib import Path

import pytest

from goswift_bot.logging_utils import ManagedLogFileHandler, cleanup_logs_directory
from goswift_bot.models import Slot
from goswift_bot.scheduler import run_check_cycle

from .support.harness import build_harness


@pytest.mark.system
def test_run_check_cycle_logs_trigger_summary_and_slot_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = build_harness(
        monkeypatch,
        tmp_path / "runtime_config.json",
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_OWNER_CHAT_ID": "42",
            "GOSWIFT_LOCATIONS": "koidula",
            "GOSWIFT_DATE_FIRST": "2026-04-01",
            "GOSWIFT_DATE_LAST": "2026-04-01",
            "LOG_LEVEL": "DEBUG",
        },
    )
    slot = Slot(
        id="2026-04-01 09:00|loc=koidula",
        date_time=datetime(2026, 4, 1, 9, 0, 0),
        location_key="koidula",
        checkpoint="Koidula",
        direction=None,
        booking_url="https://www.eestipiir.ee/yphis/preReserveSelectVehicle.action",
    )

    monkeypatch.setattr(harness.client, "fetch_slots", lambda *_args, **_kwargs: [slot])
    monkeypatch.setattr("goswift_bot.scheduler.random.uniform", lambda *_args: 0.0)
    monkeypatch.setattr("goswift_bot.scheduler.time.sleep", lambda _seconds: None)

    with caplog.at_level(logging.DEBUG):
        new_slots, errors = run_check_cycle(
            harness.config,
            harness.client,
            harness.slot_filter,
            trigger="command:/check_now",
        )

    assert errors == []
    assert [item.id for item in new_slots] == [slot.id]
    assert "Starting GoSwift check cycle: trigger=command:/check_now" in caplog.text
    assert "Filtered new slots for location=Koidula date=2026-04-01 new_slots=1" in caplog.text
    assert "sorted_new_slot_ids=['2026-04-01 09:00|loc=koidula']" in caplog.text


@pytest.mark.system
def test_goswift_html_parser_emits_debug_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = build_harness(
        monkeypatch,
        tmp_path / "runtime_config.json",
        env={
            "TELEGRAM_BOT_TOKEN": "token",
            "TELEGRAM_OWNER_CHAT_ID": "42",
            "GOSWIFT_LOCATIONS": "koidula",
            "GOSWIFT_DIRECTION": "EE-RU",
            "LOG_LEVEL": "DEBUG",
        },
    )
    html = """
    <div class="timeslots_desktop">
      <div class="dayContainer">
        <div class="slotContainer slotLocked" data-time="01.04.2026 08:00">Locked</div>
        <div class="slotContainer" data-time="01.04.2026 09:30">Available</div>
        <div class="slotContainer">Missing time</div>
      </div>
    </div>
    """

    with caplog.at_level(logging.DEBUG):
        slots = list(harness.client._parse_slots_html(html, location_key="koidula", days=2))

    assert len(slots) == 1
    assert slots[0].id == "01.04.2026 09:30|loc=koidula|dir=EE-RU"
    assert "Parsing GoSwift HTML: location=Koidula requested_days=2 available_day_containers=1" in caplog.text
    assert "Skipping locked GoSwift slot: location=Koidula" in caplog.text
    assert "Skipping GoSwift slot without data-time: location=Koidula" in caplog.text
    assert "Yielding GoSwift slot: location=Koidula slot_id=01.04.2026 09:30|loc=koidula|dir=EE-RU" in caplog.text


@pytest.mark.system
def test_managed_log_file_handler_rotates_chunks_by_size(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    handler = ManagedLogFileHandler(
        logs_dir=logs_dir,
        session_started_at=datetime(2026, 4, 1, 12, 0, 0),
        max_chunk_bytes=80,
        max_total_bytes=10 * 1024,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    handler.emit(logging.makeLogRecord({"msg": "a" * 60, "levelno": logging.INFO, "levelname": "INFO"}))
    first_chunk = handler.current_log_path
    handler.emit(logging.makeLogRecord({"msg": "b" * 60, "levelno": logging.INFO, "levelname": "INFO"}))
    second_chunk = handler.current_log_path
    handler.close()

    assert first_chunk is not None
    assert second_chunk is not None
    assert first_chunk.name.endswith(".chunk0001.log")
    assert second_chunk.name.endswith(".chunk0002.log")
    assert first_chunk != second_chunk
    assert first_chunk.exists()
    assert second_chunk.exists()


@pytest.mark.system
def test_cleanup_logs_directory_archives_old_logs_then_deletes_old_archives(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    current_log = logs_dir / "goswift-bot-20260401-120000-000000.chunk0002.log"
    old_log = logs_dir / "goswift-bot-20260401-110000-000000.chunk0001.log"
    old_archive = logs_dir / "goswift-bot-20260401-100000-000000.chunk0001.log.gz"

    old_log.write_text("x" * 120, encoding="utf-8")
    current_log.write_text("y" * 120, encoding="utf-8")
    with gzip.open(old_archive, "wb") as handle:
        handle.write(b"z" * 120)

    cleanup_logs_directory(
        logs_dir,
        max_total_bytes=current_log.stat().st_size + 80,
        active_log_path=current_log,
    )

    archived_old_log = old_log.with_suffix(".log.gz")
    assert current_log.exists()
    assert not old_log.exists()
    assert archived_old_log.exists() or not old_archive.exists()

    cleanup_logs_directory(
        logs_dir,
        max_total_bytes=current_log.stat().st_size + 10,
        active_log_path=current_log,
    )

    assert current_log.exists()
    remaining_archives = sorted(logs_dir.glob("*.log.gz"))
    assert len(remaining_archives) <= 1


@pytest.mark.system
def test_managed_log_file_handler_disables_file_logging_on_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    logs_dir = tmp_path / "logs"
    handler = ManagedLogFileHandler(
        logs_dir=logs_dir,
        session_started_at=datetime(2026, 4, 1, 12, 0, 0),
        max_chunk_bytes=1024,
        max_total_bytes=10 * 1024,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    class BrokenStream:
        def write(self, _message: str) -> None:
            raise OSError("No space left on device")

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    handler.stream = BrokenStream()
    handler.emit(logging.makeLogRecord({"msg": "hello", "levelno": logging.INFO, "levelname": "INFO"}))
    handler.emit(logging.makeLogRecord({"msg": "world", "levelno": logging.INFO, "levelname": "INFO"}))

    captured = capsys.readouterr()
    handler.close()

    assert handler.file_logging_disabled is True
    assert "File logging disabled for this session" in captured.err
