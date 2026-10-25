"""Dispatch oncall plugin backed by Grafana OnCall schedules."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pydantic import Field, SecretStr

from dispatch.config import BaseConfigurationModel
from dispatch.decorators import apply, counter, timer
from dispatch.exceptions import DispatchPluginException
from dispatch.plugins.bases import OncallPlugin

from . import __version__
from .client import SHIFT_TIME_FORMAT, OnCallClient, OnCallError, next_oncall_email, shift_change

log = logging.getLogger(__name__)

NEXT_ONCALL_LOOKAHEAD = timedelta(days=14)
SHIFT_CHECK_LOOKBACK = timedelta(days=7)


class GrafanaOncallConfiguration(BaseConfigurationModel):
    """Connection settings for a Grafana OnCall instance."""

    api_url: str = Field(
        title="OnCall API URL",
        description="Base URL of the OnCall engine, e.g. https://oncall.example.com. The plugin appends /api/v1.",
    )
    grafana_url: str = Field(
        title="Grafana URL",
        description="Public URL of the Grafana that hosts the OnCall plugin. Sent as X-Grafana-URL and used for schedule links.",
    )
    api_key: SecretStr = Field(
        title="API token",
        description="OnCall API token (Grafana OnCall > Settings > API tokens). A Grafana service-account token works for lookups only; paging requires an OnCall API token.",
    )
    timeout: int = Field(10, title="HTTP timeout", description="Seconds to wait for each OnCall API call.")


@apply(counter, exclude=["__init__"])
@apply(timer, exclude=["__init__"])
class GrafanaOncallPlugin(OncallPlugin):
    title = "Grafana OnCall Plugin - Oncall Management"
    slug = "grafana-oncall"
    author = "helldweller"
    author_url = "https://github.com/helldweller/dispatch-grafana-oncall"
    description = "Uses Grafana OnCall schedules to resolve and page oncall engineers. Service external id = OnCall schedule id."
    version = __version__

    def __init__(self):
        self.configuration_schema = GrafanaOncallConfiguration

    def _client(self) -> OnCallClient:
        config = self.configuration
        return OnCallClient(
            api_url=config.api_url,
            api_key=config.api_key.get_secret_value(),
            grafana_url=config.grafana_url,
            timeout=config.timeout,
        )

    def get(self, service_id: str, **kwargs) -> str:
        """Returns the email of the person currently on call for the schedule."""
        try:
            users = self._client().get_oncall_now(service_id)
        except OnCallError as e:
            raise DispatchPluginException(str(e)) from e
        if not users:
            raise DispatchPluginException(f"Nobody is on call for Grafana OnCall schedule {service_id}.")
        return users[0]["email"]

    def page(
        self,
        service_id: str,
        incident_name: str,
        incident_title: str,
        incident_description: str,
        **kwargs,
    ) -> dict:
        """Pages everyone currently on call for the schedule through OnCall direct paging."""
        client = self._client()
        try:
            users = client.get_oncall_now(service_id)
            if not users:
                raise DispatchPluginException(f"Nobody is on call for Grafana OnCall schedule {service_id}.")
            return client.escalate(
                user_ids=[user["id"] for user in users],
                title=f"{incident_name} - {incident_title}",
                message=incident_description,
            )
        except OnCallError as e:
            raise DispatchPluginException(str(e)) from e

    def get_schedule_id_from_service_id(self, service_id: str) -> str | None:
        """Dispatch services map 1:1 to OnCall schedules, so the ids are the same."""
        return service_id or None

    def get_service_url(self, service_id: str) -> str | None:
        """Link to the schedule in the Grafana OnCall UI."""
        return self._client().schedule_url(service_id) if service_id else None

    def get_next_oncall(self, service_id: str) -> str | None:
        """Email of the next person on the rotation, or None when nothing is known."""
        now = datetime.now(timezone.utc)
        try:
            client = self._client()
            current = client.get_oncall_now(service_id)
            shifts = client.get_final_shifts(service_id, now, now + NEXT_ONCALL_LOOKAHEAD)
        except OnCallError as e:
            log.warning("Could not resolve next oncall for schedule %s: %s", service_id, e)
            return None
        return next_oncall_email(shifts, now, current_email=current[0]["email"] if current else None)

    def did_oncall_just_go_off_shift(self, schedule_id: str, hour: int) -> dict | None:
        """Returns who just went off shift (email, shift_start, shift_end, schedule_name) or None."""
        now = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
        try:
            client = self._client()
            schedule = client.get_schedule(schedule_id)
            shifts = client.get_final_shifts(schedule_id, now - SHIFT_CHECK_LOOKBACK, now + timedelta(hours=3))
        except OnCallError as e:
            log.warning("Could not check shift change for schedule %s: %s", schedule_id, e)
            return None
        change = shift_change(shifts, now)
        if not change:
            return None
        return {
            "email": change["email"],
            "shift_start": change["shift_start"].strftime(SHIFT_TIME_FORMAT),
            "shift_end": change["shift_end"].strftime(SHIFT_TIME_FORMAT),
            "schedule_name": schedule.get("name"),
        }
