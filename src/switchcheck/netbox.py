from typing import Any
from urllib.parse import urljoin

import httpx

from switchcheck.models import ConfigurationData, Interface, InterfaceMode, Vlan


class NetBoxError(RuntimeError):
    """A safe, user-facing NetBox communication error."""


class NetBoxClient:
    def __init__(self, url: str, token: str, *, verify_tls: bool = True) -> None:
        base = url.rstrip("/")
        if not base.endswith("/api"):
            base = f"{base}/api"
        self.base_url = f"{base}/"
        self.client = httpx.AsyncClient(
            headers={
                "Authorization": f"Token {token}",
                "Accept": "application/json",
                "User-Agent": "SwitchCheck/0.1",
            },
            timeout=15,
            verify=verify_tls,
        )

    async def __aenter__(self) -> "NetBoxClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self.client.get(urljoin(self.base_url, path), params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise NetBoxError("NetBox rejected the API token.") from exc
            if status == 400:
                detail = self._error_detail(exc.response)
                message = f"NetBox rejected the API request: {detail}" if detail else ""
                raise NetBoxError(message or "NetBox rejected the API request (HTTP 400).") from exc
            if status == 404:
                raise NetBoxError("The NetBox API endpoint was not found.") from exc
            raise NetBoxError(f"NetBox returned HTTP {status}.") from exc
        except (httpx.RequestError, ValueError) as exc:
            raise NetBoxError("Could not connect to NetBox or read its response.") from exc

    async def _render_config(self, device_id: int) -> tuple[str | None, str | None]:
        try:
            response = await self.client.post(
                urljoin(self.base_url, f"dcim/devices/{device_id}/render-config/"),
                json={},
            )
            if response.status_code == 400:
                return None, "NetBox has no configuration template assigned to this device."
            if response.status_code in {401, 403}:
                return None, "The API token does not have permission to render configurations."
            if response.status_code == 404:
                return None, "This NetBox version does not provide device configuration rendering."
            response.raise_for_status()
            data = response.json()
            content = data.get("content") if isinstance(data, dict) else None
            if not isinstance(content, str):
                return None, "NetBox returned an invalid rendered configuration."
            return content, None
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            return None, "NetBox could not render the device configuration."

    async def _get_paginated(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        data = await self._get(path, params)
        results = list(data.get("results", []))
        next_url = data.get("next")
        while next_url:
            data = await self._get(next_url)
            results.extend(data.get("results", []))
            next_url = data.get("next")
        return results

    async def get_configuration(
        self, device_name: str, aruba_vlan_ids: set[int] | None = None
    ) -> ConfigurationData:
        devices = await self._get("dcim/devices/", {"name": device_name, "limit": 2})
        matches = devices.get("results", [])
        exact = [device for device in matches if device.get("name") == device_name]
        if not exact:
            raise NetBoxError(f'Device "{device_name}" was not found in NetBox.')

        device = exact[0]
        rendered_config, rendered_config_error = await self._render_config(device["id"])
        interface_results = await self._get_paginated(
            "dcim/interfaces/", {"device_id": device["id"], "limit": 100}
        )
        interfaces = [self._to_interface(item) for item in interface_results]

        relevant_vids = set(aruba_vlan_ids or ())
        for interface in interfaces:
            if interface.untagged_vlan is not None:
                relevant_vids.add(interface.untagged_vlan)
            relevant_vids.update(interface.tagged_vlans)

        site_id = (device.get("site") or {}).get("id")
        location_id = (device.get("location") or {}).get("id")
        vlan_results = await self._get_paginated("ipam/vlans/", {"limit": 100})
        applicable = [
            item
            for item in vlan_results
            if self._vlan_applies_to_device(item, site_id, location_id, relevant_vids)
        ]
        by_vid: dict[int, dict[str, Any]] = {}
        for item in applicable:
            vid = int(item["vid"])
            current = by_vid.get(vid)
            if current is None or self._scope_priority(
                item, site_id, location_id
            ) > self._scope_priority(current, site_id, location_id):
                by_vid[vid] = item

        return ConfigurationData(
            interfaces=interfaces,
            vlans=[self._to_vlan(item) for _, item in sorted(by_vid.items())],
            rendered_config=rendered_config,
            rendered_config_error=rendered_config_error,
        )

    async def get_interfaces(self, device_name: str) -> list[Interface]:
        """Return interfaces for callers using the original client API."""
        return (await self.get_configuration(device_name)).interfaces

    @staticmethod
    def _to_interface(item: dict[str, Any]) -> Interface:
        untagged = item.get("untagged_vlan")
        tagged = item.get("tagged_vlans") or []
        mode_value = (item.get("mode") or {}).get("value")
        mode = InterfaceMode.OTHER
        if mode_value == "access":
            mode = InterfaceMode.ACCESS
        elif mode_value in {"tagged", "tagged-all"}:
            mode = InterfaceMode.TAGGED

        return Interface(
            name=str(item["name"]),
            enabled=bool(item.get("enabled", True)),
            description=item.get("description") or item.get("label") or "",
            mode=mode,
            untagged_vlan=untagged.get("vid") if untagged else None,
            tagged_vlans=sorted(vlan["vid"] for vlan in tagged if "vid" in vlan),
        )

    @staticmethod
    def _to_vlan(item: dict[str, Any]) -> Vlan:
        return Vlan(
            vid=int(item["vid"]),
            name=item.get("name") or "",
            description=item.get("description") or "",
        )

    @staticmethod
    def _vlan_applies_to_device(
        item: dict[str, Any],
        site_id: int | None,
        location_id: int | None,
        relevant_vids: set[int],
    ) -> bool:
        site = item.get("site")
        scope = item.get("scope")
        if site:
            return site_id is not None and site.get("id") == site_id
        if scope:
            scope_type = item.get("scope_type")
            if scope_type in {"dcim.site", "dcim | site"}:
                return site_id is not None and scope.get("id") == site_id
            if scope_type in {"dcim.location", "dcim | location"}:
                return location_id is not None and scope.get("id") == location_id
            return False
        return int(item["vid"]) in relevant_vids

    @staticmethod
    def _scope_priority(item: dict[str, Any], site_id: int | None, location_id: int | None) -> int:
        site = item.get("site") or {}
        scope = item.get("scope") or {}
        return int(
            (site_id is not None and site.get("id") == site_id)
            or (site_id is not None and scope.get("id") == site_id)
            or (location_id is not None and scope.get("id") == location_id)
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return ""
        if isinstance(data, dict):
            for key in ("detail", "error"):
                if isinstance(data.get(key), str):
                    return data[key][:300]
            messages = [
                f"{field}: {', '.join(str(value) for value in values)}"
                for field, values in data.items()
                if isinstance(values, list)
            ]
            return "; ".join(messages)[:300]
        return ""
