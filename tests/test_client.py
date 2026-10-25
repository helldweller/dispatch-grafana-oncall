from datetime import datetime, timezone

import pytest

from dispatch_grafana_oncall.client import (
    OnCallClient,
    OnCallError,
    next_oncall_email,
    shift_change,
)

API = "https://oncall.example.com"
GRAFANA = "https://grafana.example.com"


@pytest.fixture
def client():
    return OnCallClient(api_url=API + "/", api_key="glsa_secret", grafana_url=GRAFANA + "/")


def test_get_oncall_now_resolves_users_and_sends_headers(client, requests_mock):
    schedule = requests_mock.get(f"{API}/api/v1/schedules/S1/", json={"id": "S1", "on_call_now": ["U1", "U2"]})
    requests_mock.get(f"{API}/api/v1/users/U1/", json={"id": "U1", "email": "a@example.com"})
    requests_mock.get(f"{API}/api/v1/users/U2/", json={"id": "U2", "email": "b@example.com"})

    users = client.get_oncall_now("S1")

    assert [u["email"] for u in users] == ["a@example.com", "b@example.com"]
    assert schedule.last_request.headers["Authorization"] == "glsa_secret"
    assert schedule.last_request.headers["X-Grafana-URL"] == GRAFANA


def test_get_oncall_now_empty_schedule(client, requests_mock):
    requests_mock.get(f"{API}/api/v1/schedules/S1/", json={"id": "S1", "on_call_now": []})
    assert client.get_oncall_now("S1") == []


def test_escalate_payload(client, requests_mock):
    escalation = requests_mock.post(f"{API}/api/v1/escalation", json={"id": "I1"})

    assert client.escalate(["U1"], title="dispatch-1 - DB down", message="details") == {"id": "I1"}
    assert escalation.last_request.json() == {
        "users": [{"id": "U1", "important": False}],
        "title": "dispatch-1 - DB down",
        "message": "details",
    }


def test_http_error_raises(client, requests_mock):
    requests_mock.post(f"{API}/api/v1/escalation", status_code=403, json={"detail": "Invalid token."})
    with pytest.raises(OnCallError, match="403"):
        client.escalate(["U1"], title="t")


def test_final_shifts_query(client, requests_mock):
    shifts = requests_mock.get(f"{API}/api/v1/schedules/S1/final_shifts/", json={"results": [{"user_email": "a"}]})
    start = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)

    assert client.get_final_shifts("S1", start, start.replace(day=26)) == [{"user_email": "a"}]
    assert shifts.last_request.qs == {"start_date": ["2026-10-25t09:30"], "end_date": ["2026-10-26t09:30"]}


def test_schedule_url(client):
    assert client.schedule_url("S1") == f"{GRAFANA}/a/grafana-oncall-app/schedules/S1"


def _shift(email, start, end):
    return {"user_email": email, "shift_start": start, "shift_end": end}


SHIFTS = [
    _shift("a@x", "2026-10-23T09:00:00Z", "2026-10-24T09:00:00Z"),
    _shift("a@x", "2026-10-24T09:00:00Z", "2026-10-25T09:00:00Z"),
    _shift("b@x", "2026-10-25T09:00:00Z", "2026-10-26T09:00:00Z"),
]


def test_next_oncall_email_skips_current():
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    assert next_oncall_email(SHIFTS, now, current_email="a@x") == "b@x"
    assert next_oncall_email(SHIFTS[:2], now, current_email="a@x") is None


def test_shift_change_detects_handover_and_run_start():
    at_handover = datetime(2026, 10, 25, 10, tzinfo=timezone.utc)
    change = shift_change(SHIFTS, at_handover)
    assert change["email"] == "a@x"
    assert change["shift_start"] == datetime(2026, 10, 23, 9, tzinfo=timezone.utc)
    assert change["shift_end"] == datetime(2026, 10, 25, 9, tzinfo=timezone.utc)


def test_shift_change_none_when_same_person():
    assert shift_change(SHIFTS, datetime(2026, 10, 24, 12, tzinfo=timezone.utc)) is None
    assert shift_change([], datetime(2026, 10, 24, 12, tzinfo=timezone.utc)) is None
