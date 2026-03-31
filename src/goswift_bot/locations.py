from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Location:
    key: str
    title: str
    border_crossing_point_id: str


LOCATIONS: dict[str, Location] = {
    "koidula": Location(
        key="koidula",
        title="Koidula",
        border_crossing_point_id="2",
    ),
    "luhamaa": Location(
        key="luhamaa",
        title="Luhamaa",
        border_crossing_point_id="3",
    ),
}

DEFAULT_LOCATION_KEYS: list[str] = ["koidula", "luhamaa"]


def normalize_location_keys(location_keys: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw_key in location_keys:
        key = raw_key.strip().lower()
        if not key:
            continue
        if key not in LOCATIONS:
            raise RuntimeError(f"Unknown GoSwift location: {raw_key!r}")
        if key in seen:
            continue
        seen.add(key)
        normalized.append(key)

    if not normalized:
        raise RuntimeError("At least one GoSwift location must be configured")

    return sorted(normalized, key=lambda key: DEFAULT_LOCATION_KEYS.index(key))


def format_location_titles(location_keys: list[str] | tuple[str, ...]) -> str:
    return ", ".join(LOCATIONS[key].title for key in normalize_location_keys(location_keys))


def location_keys_from_legacy_checkpoint(checkpoint_id: str) -> list[str]:
    raw_value = checkpoint_id.strip().lower()
    for key, location in LOCATIONS.items():
        if raw_value == key or raw_value == location.border_crossing_point_id:
            return [key]
    raise RuntimeError(f"Unknown legacy GoSwift checkpoint identifier: {checkpoint_id!r}")
