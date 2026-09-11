from typing import Any
from urllib.parse import urljoin

import httpx

from switchcheck.models import Interface, InterfaceMode


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
            if status == 404:
                raise NetBoxError("The NetBox API endpoint was not found.") from exc
            raise NetBoxError(f"NetBox returned HTTP {status}.") from exc
        except (httpx.RequestError, ValueError) as exc:
            raise NetBoxError("Could not connect to NetBox or read its response.") from exc

    async def get_interfaces(self, device_name: str) -> list[Interface]:
        devices = await self._get("dcim/devices/", {"name": device_name, "limit": 2})
        matches = devices.get("results", [])
        exact = [device for device in matches if device.get("name") == device_name]
        if not exact:
            raise NetBoxError(f'Device "{device_name}" was not found in NetBox.')

        device_id = exact[0]["id"]
        data = await self._get("dcim/interfaces/", {"device_id": device_id, "limit": 100})
        results = list(data.get("results", []))
        next_url = data.get("next")
        while next_url:
            data = await self._get(next_url)
            results.extend(data.get("results", []))
            next_url = data.get("next")

        return [self._to_interface(item) for item in results]

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
