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
- Previews value-level NetBox change plans before applying a batch
- Reads live interface data from the NetBox REST API
- Discovers NetBox devices with search and audits up to 20 configuration files at once
- Retrieves running configurations through SSH commands or SFTP
- Highlights matches, configuration drift, and interfaces missing on either side
- Compares MTU, speed, duplex, type, MAC address, management state, and custom fields when present
- Ignores comments, blank lines, case, and cosmetic whitespace in rendered configuration diffs
- Generates filtered Aruba CX or legacy ArubaOS-Switch remediation commands from NetBox intent
- Exports comparison reports as JSON, CSV, or standalone HTML
- Remembers non-secret browser preferences such as URL, device, and TLS settings
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

For a bulk audit, select multiple configuration files. Each filename without its `.txt`, `.cfg`,
or `.conf` extension is used as the NetBox device name. SSH passwords, private keys, and NetBox
tokens are never written to browser storage. SSH uses standard host-key verification unless a
trusted known-hosts entry is supplied with the request. Switch connections accept either a DNS
hostname or an IPv4/IPv6 address.

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

The Remediation tab can limit generated commands to VLANs, interfaces, names, descriptions,
untagged VLANs, tagged VLANs, or LAGs before copying them.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The OpenAPI documentation is available at `/docs`, and the health endpoint is `/health`.
