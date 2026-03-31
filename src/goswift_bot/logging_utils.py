from __future__ import annotations

import gzip
import logging
import re
import sys
from datetime import datetime
from pathlib import Path


def compute_directory_size(directory: Path) -> int:
    total = 0
    for path in directory.iterdir():
        if path.is_file():
            total += path.stat().st_size
    return total


def _log_sort_key(path: Path) -> tuple[float, str]:
    stat = path.stat()
    return (stat.st_mtime, path.name)


def _iter_unarchived_logs(logs_dir: Path, active_log_path: Path | None) -> list[Path]:
    return sorted(
        [
            path
            for path in logs_dir.iterdir()
            if path.is_file() and path.suffix == ".log" and path != active_log_path
        ],
        key=_log_sort_key,
    )


def _iter_archived_logs(logs_dir: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in logs_dir.iterdir()
            if path.is_file() and path.suffixes[-2:] == [".log", ".gz"]
        ],
        key=_log_sort_key,
    )


def gzip_log_file(path: Path) -> Path:
    gz_path = path.with_suffix(path.suffix + ".gz")
    temp_path = gz_path.with_suffix(gz_path.suffix + ".tmp")
    try:
        with path.open("rb") as source, gzip.open(temp_path, "wb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
        temp_path.replace(gz_path)
        path.unlink()
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return gz_path


def cleanup_logs_directory(
    logs_dir: Path,
    *,
    max_total_bytes: int,
    active_log_path: Path | None = None,
) -> None:
    if not logs_dir.exists():
        return

    while compute_directory_size(logs_dir) > max_total_bytes:
        unarchived = _iter_unarchived_logs(logs_dir, active_log_path)
        if unarchived:
            candidate = unarchived[0]
            try:
                gzip_log_file(candidate)
                continue
            except Exception:
                pass

        archived = _iter_archived_logs(logs_dir)
        if archived:
            archived[0].unlink(missing_ok=True)
            continue

        break


class ManagedLogFileHandler(logging.Handler):
    """Size-aware log file handler with gzip retention and graceful degradation."""

    def __init__(
        self,
        *,
        logs_dir: Path,
        session_started_at: datetime,
        max_chunk_bytes: int,
        max_total_bytes: int,
        encoding: str = "utf-8",
        current_log_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.session_started_at = session_started_at
        self.max_chunk_bytes = max_chunk_bytes
        self.max_total_bytes = max_total_bytes
        self.encoding = encoding
        if current_log_path is None:
            self.session_name = session_started_at.strftime("goswift-bot-%Y%m%d-%H%M%S-%f")
            self.current_chunk_index = 1
        else:
            self.session_name, self.current_chunk_index = self._parse_chunk_path(current_log_path)
        self.current_log_path: Path | None = current_log_path
        self.stream = None
        self.current_size_bytes = 0
        self.file_logging_disabled = False

        cleanup_logs_directory(
            self.logs_dir,
            max_total_bytes=self.max_total_bytes,
            active_log_path=current_log_path,
        )
        self._open_new_chunk()

    @staticmethod
    def _parse_chunk_path(path: Path) -> tuple[str, int]:
        match = re.fullmatch(r"(.+)\.chunk(\d{4})\.log", path.name)
        if match is None:
            raise RuntimeError(f"Unexpected log chunk name: {path.name}")
        return match.group(1), int(match.group(2))

    def _chunk_path(self, chunk_index: int) -> Path:
        return self.logs_dir / f"{self.session_name}.chunk{chunk_index:04d}.log"

    def _warn_to_stderr(self, message: str) -> None:
        sys.stderr.write(f"{message}\n")
        sys.stderr.flush()

    def _disable_file_logging(self, reason: str) -> None:
        if self.file_logging_disabled:
            return

        self.file_logging_disabled = True
        self._close_stream()
        self._warn_to_stderr(
            f"[goswift] File logging disabled for this session: {reason}. Continuing with stdout/stderr logging only."
        )

    def _close_stream(self) -> None:
        if self.stream is None:
            return
        try:
            self.stream.flush()
        finally:
            self.stream.close()
            self.stream = None

    def _open_new_chunk(self) -> None:
        if self.file_logging_disabled:
            return

        new_path = self.current_log_path or self._chunk_path(self.current_chunk_index)
        try:
            self.stream = new_path.open("a", encoding=self.encoding)
        except OSError as exc:
            self._disable_file_logging(f"could not open log chunk {new_path.name}: {exc}")
            return

        self.current_log_path = new_path
        self.current_size_bytes = new_path.stat().st_size

    def _rotate_if_needed(self, message_bytes: bytes) -> None:
        if self.file_logging_disabled or self.stream is None or self.current_log_path is None:
            return

        if self.current_size_bytes == 0:
            return
        if self.current_size_bytes + len(message_bytes) <= self.max_chunk_bytes:
            return

        closed_chunk_path = self.current_log_path
        self._close_stream()
        self.current_chunk_index += 1
        self.current_log_path = None
        self.current_size_bytes = 0

        try:
            cleanup_logs_directory(
                self.logs_dir,
                max_total_bytes=self.max_total_bytes,
                active_log_path=None,
            )
        except Exception as exc:
            self._disable_file_logging(f"could not clean up logs after rotating {closed_chunk_path.name}: {exc}")
            return

        self._open_new_chunk()

    def emit(self, record: logging.LogRecord) -> None:
        if self.file_logging_disabled or self.stream is None:
            return

        try:
            message = self.format(record) + "\n"
            message_bytes = message.encode(self.encoding)
            self._rotate_if_needed(message_bytes)
            if self.file_logging_disabled or self.stream is None:
                return

            self.stream.write(message)
            self.stream.flush()
            self.current_size_bytes += len(message_bytes)
        except OSError as exc:
            self._disable_file_logging(f"could not write to log file: {exc}")
        except Exception as exc:
            self._disable_file_logging(f"unexpected log file handler error: {exc}")

    def close(self) -> None:
        self._close_stream()
        super().close()
