# SwitchCheck

SwitchCheck is a stateless web application that compares an Aruba switch configuration with
interface intent stored in NetBox. It has no database, user accounts, or server-side sessions.

## Features

- Supports ArubaOS-Switch VLAN membership and Aruba CX interface syntax
- Normalizes interface names across platforms by ignoring spaces and letter case
- Compares interface state, description, mode, untagged VLAN, and tagged VLANs
- Detects Aruba CX LAG, ArubaOS-Switch trunk, and Comware Bridge-Aggregation membership
- Compares VLAN IDs, names, and descriptions
- Highlights line-by-line differences against the configuration rendered by NetBox
- Adds missing interfaces/VLANs and imports selected Aruba values into NetBox
- Applies multiple selected NetBox changes in one dependency-aware batch
- Reads live interface data from the NetBox REST API
- Highlights matches, configuration drift, and interfaces missing on either side
- Keeps the configuration and API token in memory only for the duration of a request
- Includes a responsive, shadcn-inspired web interface

## Run locally

Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run uvicorn switchcheck.app:app --reload
```

Open <http://127.0.0.1:8000>.

The NetBox token needs read access to devices, interfaces, and VLANs. Write permissions for
interfaces and VLANs are required to use the import actions. To enable rendered configuration
comparison, it also needs the `render_config` action for devices. SwitchCheck accepts NetBox URLs
with or without the `/api` suffix. TLS certificate verification is enabled by default.

## Run with Docker

```bash
docker build -t switchcheck .
docker run --rm -p 8000:8000 switchcheck
```

The container runs as an unprivileged user and includes a health check for `/health`.

## Supported configuration

Aruba CX:

```text
interface 1/1/1
    description Uplink to core
    no shutdown
    vlan trunk native 10
    vlan trunk allowed 20,30-32
```

ArubaOS-Switch:

```text
interface 1
    name "Reception"
    enable
vlan 10
    untagged 1-4
vlan 20
    tagged 1-4
```

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The OpenAPI documentation is available at `/docs`, and the health endpoint is `/health`.
