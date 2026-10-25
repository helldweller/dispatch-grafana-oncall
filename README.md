# dispatch-grafana-oncall

[Grafana OnCall](https://github.com/grafana/oncall) plugin for [Dispatch](https://github.com/Jamyn/dispatch)
(the maintained continuation of Netflix/dispatch). It implements Dispatch's `oncall` plugin
interface on top of the OnCall public API, so Dispatch can:

- resolve who is on call for a schedule and make them incident commander (incident role policies);
- page the on-call engineers when an incident is created or via `/dispatch-engage-oncall`;
- add the next person on the rotation as an observer (`engage_next_oncall`);
- link to the schedule from Slack case messages;
- run the experimental shift-end feedback collection.

The plugin is a separate pip package registered through the `dispatch.plugins` entry point group.
Dispatch itself is not modified.

## How it maps to OnCall

| Dispatch                       | Grafana OnCall                                                      |
|--------------------------------|---------------------------------------------------------------------|
| Service `type`                 | `grafana-oncall` (the plugin slug)                                  |
| Service `external_id`          | OnCall **schedule id** (`S…`, from `/a/grafana-oncall-app/schedules/<id>`) |
| `get(service_id)`              | `GET /api/v1/schedules/<id>` → first user in `on_call_now` → email  |
| `page(...)`                    | `POST /api/v1/escalation` with `users` = everyone in `on_call_now`  |
| `get_next_oncall(service_id)`  | `GET /api/v1/schedules/<id>/final_shifts` (next 14 days)            |
| `did_oncall_just_go_off_shift` | `final_shifts` over the last 7 days, PagerDuty-plugin semantics     |
| `get_service_url(service_id)`  | `<grafana_url>/a/grafana-oncall-app/schedules/<id>`                 |

Emails come from the OnCall user records (synced from Grafana), so they must match the emails of
Dispatch individuals for the commander/observer assignment to land on the right person.

## Installation

Install the package into the same Python environment as Dispatch, then register the plugin:

```bash
pip install git+https://github.com/helldweller/dispatch-grafana-oncall@v0.1.0
dispatch plugins install
```

Restart the `web` and `scheduler` processes afterwards.

With [dispatch-docker](https://github.com/Jamyn/dispatch-docker) add the line to `requirements.txt`
(the "plugins here" file) and rebuild the image:

```
dispatch-grafana-oncall @ git+https://github.com/helldweller/dispatch-grafana-oncall@v0.1.0
```

## Configuration

Dispatch UI → **Settings → Plugins → Grafana OnCall Plugin - Oncall Management**:

| Field          | Value                                                                                  |
|----------------|----------------------------------------------------------------------------------------|
| `api_url`      | Base URL of the OnCall engine, e.g. `https://oncall.example.com`. `/api/v1` is appended. |
| `grafana_url`  | Public URL of the Grafana that hosts the OnCall plugin, e.g. `https://grafana.example.com`. |
| `api_key`      | OnCall API token (see below).                                                          |
| `timeout`      | HTTP timeout in seconds, default 10.                                                   |

Only one `oncall` plugin can be enabled per project, and the `Service.type` must equal the plugin
slug, otherwise Dispatch logs "Unable to engage the oncall".

### Token

Create the token in Grafana OnCall: **Settings → API tokens → Create**. The token is sent as the raw
`Authorization` header value, as OnCall expects.

A Grafana service-account token (`glsa_…`, role Admin) also works for every lookup
(`schedules`, `users`, `final_shifts`), but the `escalation` endpoint accepts only OnCall API
tokens: with a service-account token paging fails with `403 Invalid token.` and the plugin raises
`DispatchPluginException`. `grafana_url` must equal the organisation's Grafana URL as OnCall knows
it (the public one), otherwise OnCall answers `429 Organization being synced`.

### Dispatch objects

1. **Service** (Settings → Services): type `grafana-oncall`, external id = OnCall schedule id.
2. **Incident role policy** (Settings → Incident Roles → Incident Commander): pick the service.
   Enable *Engage next oncall* to add the next rotation member as observer.
3. For paging on creation, set *Page commander* on the incident priority.

## Development

```bash
uv run --group dev pytest   # unit tests, no network (requests-mock)
uv build                    # wheel in dist/
```

The HTTP client (`client.py`) has no Dispatch imports and is what the tests cover; `plugin.py` is
the thin adapter to Dispatch's `OncallPlugin` interface and can only be imported inside a Dispatch
environment.

## License

Apache-2.0.
