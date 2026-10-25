"""Thin client for the Grafana OnCall public API (``/api/v1``).

Kept free of Dispatch imports so it can be unit-tested without a Dispatch installation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import requests

# Timestamp format Dispatch's feedback scheduler parses (``%Y-%m-%dT%H:%M:%SZ``).
SHIFT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
QUERY_TIME_FORMAT = "%Y-%m-%dT%H:%M"


class OnCallError(Exception):
    """Raised when the OnCall API returns an error."""


class OnCallClient:
    """Minimal wrapper around the OnCall endpoints the Dispatch plugin needs."""

    def __init__(self, api_url: str, api_key: str, grafana_url: str, timeout: float = 10):
        self.base_url = api_url.rstrip("/") + "/api/v1"
        self.grafana_url = grafana_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        # OnCall expects the raw token without "Bearer". X-Grafana-URL is required for
        # Grafana service-account tokens and ignored for OnCall API tokens.
        self.session.headers.update({"Authorization": api_key, "X-Grafana-URL": self.grafana_url})

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self.session.request(method, self.base_url + path, timeout=self.timeout, **kwargs)
        if not response.ok:
            raise OnCallError(f"{method} {path} failed: {response.status_code} {response.text[:300]}")
        return response.json()

    def get_schedule(self, schedule_id: str) -> dict:
        return self._request("GET", f"/schedules/{schedule_id}/")

    def get_user(self, user_id: str) -> dict:
        return self._request("GET", f"/users/{user_id}/")

    def get_oncall_now(self, schedule_id: str) -> list[dict]:
        """Users currently on call for the schedule, in the order OnCall reports them."""
        schedule = self.get_schedule(schedule_id)
        return [self.get_user(user_id) for user_id in schedule.get("on_call_now") or []]

    def get_final_shifts(self, schedule_id: str, start: datetime, end: datetime) -> list[dict]:
        """Resolved shifts (rotations plus overrides) between two UTC instants."""
        params = {
            "start_date": start.strftime(QUERY_TIME_FORMAT),
            "end_date": end.strftime(QUERY_TIME_FORMAT),
        }
        return self._request("GET", f"/schedules/{schedule_id}/final_shifts/", params=params)["results"]

    def escalate(self, user_ids: list[str], title: str, message: str | None = None) -> dict:
        """Direct-page users. OnCall accepts this only with an OnCall API token, not a Grafana service-account token."""
        payload = {
            "users": [{"id": user_id, "important": False} for user_id in user_ids],
            "title": title,
            "message": message,
        }
        return self._request("POST", "/escalation", json=payload)

    def schedule_url(self, schedule_id: str) -> str:
        return f"{self.grafana_url}/a/grafana-oncall-app/schedules/{schedule_id}"


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def shift_at(shifts: list[dict], at: datetime) -> dict | None:
    """First shift covering the instant ``at``."""
    for shift in shifts:
        if _parse(shift["shift_start"]) <= at < _parse(shift["shift_end"]):
            return shift
    return None


def next_oncall_email(shifts: list[dict], now: datetime, current_email: str | None) -> str | None:
    """Email of the first person after ``now`` who is not the current on-call, or None."""
    for shift in sorted(shifts, key=lambda s: s["shift_start"]):
        if _parse(shift["shift_end"]) > now and shift["user_email"] != current_email:
            return shift["user_email"]
    return None


def shift_change(shifts: list[dict], now: datetime) -> dict | None:
    """Person who just went off shift, mirroring Dispatch's PagerDuty plugin contract.

    Compares who was on call 18 hours ago with who is on call in 2 hours. When they differ,
    returns the earlier person with the start of their contiguous run of shifts.
    """
    previous = shift_at(shifts, now - timedelta(hours=18))
    upcoming = shift_at(shifts, now + timedelta(hours=2))
    if not previous or not upcoming or previous["user_email"] == upcoming["user_email"]:
        return None
    start = _parse(previous["shift_start"])
    # ponytail: walks back only through the fetched window; a run longer than the window is truncated to it.
    while (earlier := shift_at(shifts, start - timedelta(seconds=1))) and earlier["user_email"] == previous["user_email"]:
        start = _parse(earlier["shift_start"])
    return {"email": previous["user_email"], "shift_start": start, "shift_end": _parse(previous["shift_end"])}
