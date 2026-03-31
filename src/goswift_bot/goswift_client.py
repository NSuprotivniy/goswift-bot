from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from http.cookies import SimpleCookie
from typing import Iterable, List

import requests
from bs4 import BeautifulSoup

from .config import Config
from .locations import LOCATIONS
from .models import Slot

logger = logging.getLogger(__name__)

_RESPONSE_PREVIEW_LIMIT = 800
_RESPONSE_BODY_LOG_LIMIT = 4000


def _log_response_body(resp: requests.Response) -> None:
    """Log response body: preview at INFO, full/truncated at DEBUG."""
    text = resp.text
    preview = text[:_RESPONSE_PREVIEW_LIMIT]
    if len(text) > _RESPONSE_PREVIEW_LIMIT:
        logger.info(
            "GoSwift response body preview (first %d of %d chars):\n%s\n... [truncated]",
            _RESPONSE_PREVIEW_LIMIT,
            len(text),
            preview,
        )
    else:
        logger.info("GoSwift response body:\n%s", text)

    if len(text) > _RESPONSE_BODY_LOG_LIMIT:
        logger.debug(
            "GoSwift full response (first %d of %d chars):\n%s\n... [truncated]",
            _RESPONSE_BODY_LOG_LIMIT,
            len(text),
            text[:_RESPONSE_BODY_LOG_LIMIT],
        )
    else:
        logger.debug("GoSwift full response body:\n%s", text)


def _cookie_dict_from_header(cookie_header: str) -> dict[str, str]:
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    return {name: morsel.value for name, morsel in cookie.items()}


class SessionExpiredError(Exception):
    """Raised when the GoSwift flow stops behaving like an active session."""


@dataclass
class GoSwiftClient:
    config: Config

    def _new_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/145.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Accept-Language": "en-US,en-GB;q=0.9,en;q=0.8,ru;q=0.7,fi;q=0.6",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.config.goswift_base_url}/yphis/preReserveSelectQueueType.action",
                "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
        )
        if self.config.goswift_cookie:
            # Seed the session from a browser-exported Cookie header, but let
            # requests manage cookie updates after that.
            session.cookies.update(_cookie_dict_from_header(self.config.goswift_cookie))
        return session

    def fetch_slots(
        self,
        preferred_date: date,
        location_key: str,
        days: int = 4,
    ) -> List[Slot]:
        """
        Fetch available slots for a specific location starting from preferred_date.

        The flow mirrors the browser: select vehicle page, waiting area, queue type,
        then query timeslots for the preferred date.
        """
        location = LOCATIONS[location_key]
        session = self._new_session()
        self._prepare_location(session, location_key)

        url = f"{self.config.goswift_base_url}/yphis/findOpenTimeslot.action"
        params = {
            "preferredDate": preferred_date.strftime("%d.%m.%Y"),
            "_": int(time.time() * 1000),
        }

        logger.info(
            "Fetching GoSwift slots for location=%s preferred_date=%s",
            location.title,
            preferred_date.isoformat(),
        )
        logger.debug("GoSwift request: GET %s params=%s", url, params)
        resp = session.get(url, params=params, timeout=20)
        self._validate_response(resp, location_key)

        return list(
            self._parse_slots_html(
                resp.text,
                location_key=location_key,
                days=days,
            )
        )

    def _prepare_location(self, session: requests.Session, location_key: str) -> None:
        location = LOCATIONS[location_key]
        base = f"{self.config.goswift_base_url}/yphis"

        self._request(
            session,
            "GET",
            f"{base}/preReserveSelectVehicle.action",
            location_key=location_key,
        )
        self._request(
            session,
            "POST",
            f"{base}/preReserveSelectWaitingArea.action",
            data={
                "placeInQueue.id": "",
                "placeInQueue.version": "",
                "placeInQueue.vehicleInQueue.vehicleCategory.name": self.config.goswift_category
                or "B",
            },
            location_key=location_key,
        )
        self._request(
            session,
            "POST",
            f"{base}/preReserveSelectQueueType.action",
            data={
                "placeInQueue.id": "",
                "placeInQueue.version": "",
                "placeInQueue.borderCrossingPoint.id": location.border_crossing_point_id,
            },
            location_key=location_key,
        )
        self._request(
            session,
            "POST",
            f"{base}/preReserveSelectQueueType.action",
            data={
                "placeInQueue.id": "",
                "placeInQueue.version": "",
                "queueType": "1",
                "action:preReserveSelectTimeslot": "Вперёд",
            },
            location_key=location_key,
        )

    def _request(
        self,
        session: requests.Session,
        method: str,
        url: str,
        location_key: str,
        data: dict[str, str] | None = None,
    ) -> requests.Response:
        logger.debug(
            "GoSwift flow request: location=%s method=%s url=%s data=%s",
            LOCATIONS[location_key].title,
            method,
            url,
            data,
        )
        resp = session.request(method, url, data=data, timeout=20)
        self._validate_response(resp, location_key)
        return resp

    def _validate_response(self, resp: requests.Response, location_key: str) -> None:
        logger.info(
            "GoSwift response: location=%s status=%s url=%s content-type=%s",
            LOCATIONS[location_key].title,
            resp.status_code,
            resp.url,
            resp.headers.get("Content-Type", ""),
        )
        _log_response_body(resp)

        if resp.history:
            history_chain = " -> ".join(
                f"{item.status_code} {item.url}" for item in resp.history
            )
            logger.info(
                "GoSwift redirect history for %s: %s -> %s %s",
                LOCATIONS[location_key].title,
                history_chain,
                resp.status_code,
                resp.url,
            )

        if resp.status_code == 302 or "login" in resp.url.lower():
            raise SessionExpiredError(
                f"GoSwift redirected away from the booking flow for {LOCATIONS[location_key].title}"
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Unexpected GoSwift status code for {LOCATIONS[location_key].title}: "
                f"{resp.status_code}"
            )

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            raise RuntimeError(
                f"Unexpected GoSwift content type for {LOCATIONS[location_key].title}: "
                f"{content_type}"
            )

    def _parse_slots_html(
        self,
        html: str,
        location_key: str,
        days: int,
    ) -> Iterable[Slot]:
        soup = BeautifulSoup(html, "html.parser")
        location = LOCATIONS[location_key]

        day_containers = soup.select("div.timeslots_desktop div.dayContainer")
        for day_container in day_containers[:days]:
            slot_divs = day_container.select("div.slotContainer")
            for div in slot_divs:
                classes = div.get("class", [])
                if "slotLocked" in classes:
                    continue

                text = " ".join(div.get_text(strip=True).split())
                if "Недоступно" in text:
                    continue

                when_raw = div.get("data-time")
                if not when_raw:
                    continue

                try:
                    dt = datetime.strptime(when_raw, "%d.%m.%Y %H:%M")
                except ValueError:
                    continue

                direction = self.config.goswift_direction
                slot_id_parts = [when_raw, f"loc={location_key}"]
                if direction:
                    slot_id_parts.append(f"dir={direction}")
                slot_id = "|".join(slot_id_parts)

                booking_url = (
                    f"{self.config.goswift_base_url}/yphis/preReserveSelectVehicle.action"
                )

                yield Slot(
                    id=slot_id,
                    date_time=dt,
                    location_key=location_key,
                    checkpoint=location.title,
                    direction=direction,
                    booking_url=booking_url,
                )
