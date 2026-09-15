import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from switchcheck.models import (
    ConfigurationData,
    DeviceSummary,
    ImportResource,
    Interface,
    InterfaceMode,
    NetBoxImportAction,
    NetBoxImportResult,
    Vlan,
)


class NetBoxError(RuntimeError):
    """A safe, user-facing NetBox communication error."""


class _BulkWriteRejected(NetBoxError):
    """A bulk payload was rejected without being applied."""


@dataclass
class _PreparedWrite:
    method: str
    collection_path: str
    item_path: str
    payload: dict[str, Any]
    message: str
    cache: list[dict[str, Any]] | None = None
    cache_record: dict[str, Any] | None = None

    def record_success(self, response: dict[str, Any]) -> None:
        if self.cache is not None and self.cache_record is not None:
            self.cache.append({"id": response.get("id"), **self.cache_record})


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
        self._devices_by_name: dict[str, dict[str, Any]] = {}
        self._device_groups: dict[int, list[dict[str, Any]]] = {}
        self._interfaces_by_device: dict[int, list[dict[str, Any]]] = {}
        self._vlans: list[dict[str, Any]] | None = None

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

    async def _write(self, method: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.request(
                method,
                urljoin(self.base_url, path),
                json=payload,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise NetBoxError(
                    "The API token does not have permission to modify NetBox."
                ) from exc
            detail = self._error_detail(exc.response)
            if detail:
                raise NetBoxError(f"NetBox rejected the update: {detail}") from exc
            raise NetBoxError(f"NetBox rejected the update (HTTP {status}).") from exc
        except (httpx.RequestError, ValueError) as exc:
            raise NetBoxError("Could not send the update to NetBox.") from exc

    async def _write_bulk(
        self, method: str, path: str, payload: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        try:
            response = await self.client.request(
                method,
                urljoin(self.base_url, path),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("NetBox returned a non-list bulk response.")
            return data
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                raise NetBoxError(
                    "The API token does not have permission to modify NetBox."
                ) from exc
            detail = self._error_detail(exc.response)
            message = (
                f"NetBox rejected the bulk update: {detail}"
                if detail
                else f"NetBox rejected the bulk update (HTTP {status})."
            )
            if status in {400, 405}:
                raise _BulkWriteRejected(message) from exc
            raise NetBoxError(message) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise NetBoxError("Could not send the bulk update to NetBox.") from exc

    async def _find_device(self, device_name: str) -> dict[str, Any]:
        cached = self._devices_by_name.get(device_name)
        if cached is not None:
            return cached

        devices = await self._get("dcim/devices/", {"name": device_name, "limit": 2})
        exact = [
            device for device in devices.get("results", []) if device.get("name") == device_name
        ]
        if not exact:
            raise NetBoxError(f'Device "{device_name}" was not found in NetBox.')
        self._devices_by_name[device_name] = exact[0]
        return exact[0]

    async def list_devices(self, query: str = "") -> list[DeviceSummary]:
        params: dict[str, Any] = {"limit": 50}
        if query:
            params["q"] = query
        records = await self._get_paginated("dcim/devices/", params)
        return [
            DeviceSummary(
                name=str(item["name"]),
                display=str(item.get("display") or item["name"]),
                site=(item.get("site") or {}).get("name"),
                status=(item.get("status") or {}).get("label"),
            )
            for item in records
            if item.get("name")
        ]

    async def _get_device_group(self, device: dict[str, Any]) -> list[dict[str, Any]]:
        cached = self._device_groups.get(device["id"])
        if cached is not None:
            return cached

        virtual_chassis = device.get("virtual_chassis") or {}
        chassis_id = virtual_chassis.get("id")
        if chassis_id is None:
            members = [device]
            self._device_groups[device["id"]] = members
            return members

        members = await self._get_paginated(
            "dcim/devices/", {"virtual_chassis_id": chassis_id, "limit": 100}
        )
        members_by_id = {member["id"]: member for member in members}
        members_by_id.setdefault(device["id"], device)
        members = list(members_by_id.values())
        for member in members:
            self._device_groups[member["id"]] = members
        return members

    async def _get_interface_records(self, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        missing_devices = [
            device for device in devices if device["id"] not in self._interfaces_by_device
        ]
        if missing_devices:
            fetched = await asyncio.gather(
                *(
                    self._get_paginated(
                        "dcim/interfaces/", {"device_id": device["id"], "limit": 100}
                    )
                    for device in missing_devices
                )
            )
            for device, interfaces in zip(missing_devices, fetched, strict=True):
                self._interfaces_by_device[device["id"]] = interfaces
        return [
            interface
            for device in devices
            for interface in self._interfaces_by_device[device["id"]]
        ]

    async def _get_vlan_records(self) -> list[dict[str, Any]]:
        if self._vlans is None:
            self._vlans = await self._get_paginated("ipam/vlans/", {"limit": 100})
        return self._vlans

    async def prepare_import(self, device_name: str) -> None:
        """Preload shared NetBox data once before executing a batch."""
        device = await self._find_device(device_name)
        devices = await self._get_device_group(device)
        await asyncio.gather(self._get_interface_records(devices), self._get_vlan_records())

    async def get_configuration(
        self, device_name: str, aruba_vlan_ids: set[int] | None = None
    ) -> ConfigurationData:
        device = await self._find_device(device_name)
        rendered_config, rendered_config_error = await self._render_config(device["id"])
        devices = await self._get_device_group(device)
        interface_results = await self._get_interface_records(devices)
        interfaces = [self._to_interface(item) for item in interface_results]

        relevant_vids = set(aruba_vlan_ids or ())
        for interface in interfaces:
            if interface.untagged_vlan is not None:
                relevant_vids.add(interface.untagged_vlan)
            relevant_vids.update(interface.tagged_vlans)

        site_id = (device.get("site") or {}).get("id")
        location_id = (device.get("location") or {}).get("id")
        vlan_results = await self._get_vlan_records()
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

    async def import_interface(
        self,
        device_name: str,
        source: Interface,
        fields: list[str],
        *,
        create: bool = False,
    ) -> str:
        prepared = await self._prepare_interface_import(device_name, source, fields, create=create)
        response = await self._write(prepared.method, prepared.item_path, prepared.payload)
        prepared.record_success(response)
        return prepared.message

    async def _prepare_interface_import(
        self,
        device_name: str,
        source: Interface,
        fields: list[str],
        *,
        create: bool = False,
    ) -> _PreparedWrite:
        allowed = {
            "enabled",
            "description",
            "mode",
            "untagged_vlan",
            "tagged_vlans",
            "lag",
            "mtu",
            "speed",
            "duplex",
            "type",
            "mac_address",
            "mgmt_only",
            "custom_fields",
        }
        requested = allowed if create else set(fields) & allowed
        if not requested:
            raise NetBoxError("Select at least one supported interface field to import.")

        device = await self._find_device(device_name)
        devices = await self._get_device_group(device)
        interface_results = await self._get_interface_records(devices)
        normalized_name = self._normalize_interface_name(source.name)
        target = next(
            (
                item
                for item in interface_results
                if self._normalize_interface_name(str(item["name"])) == normalized_name
            ),
            None,
        )
        if create and target is not None:
            raise NetBoxError(f'Interface "{source.name}" already exists in NetBox.')
        if not create and target is None:
            raise NetBoxError(f'Interface "{source.name}" was not found in NetBox.')

        vlan_results = await self._get_vlan_records()
        source_vids = set(source.tagged_vlans)
        if source.untagged_vlan is not None:
            source_vids.add(source.untagged_vlan)
        site_id = (device.get("site") or {}).get("id")
        location_id = (device.get("location") or {}).get("id")
        applicable_vlans = [
            item
            for item in vlan_results
            if self._vlan_applies_to_device(item, site_id, location_id, source_vids)
        ]
        vlan_by_vid: dict[int, dict[str, Any]] = {}
        for item in applicable_vlans:
            vid = int(item["vid"])
            current = vlan_by_vid.get(vid)
            if current is None or self._scope_priority(
                item, site_id, location_id
            ) > self._scope_priority(current, site_id, location_id):
                vlan_by_vid[vid] = item
        vlan_ids = {vid: item["id"] for vid, item in vlan_by_vid.items()}
        interfaces_by_name = {
            self._normalize_interface_name(str(item["name"])): item for item in interface_results
        }

        payload: dict[str, Any] = {}
        if "enabled" in requested:
            payload["enabled"] = source.enabled
        if "description" in requested:
            payload["description"] = source.description
        if "mode" in requested:
            payload["mode"] = None if source.mode is InterfaceMode.OTHER else source.mode.value
        if "untagged_vlan" in requested:
            if source.untagged_vlan is None:
                payload["untagged_vlan"] = None
            elif source.untagged_vlan not in vlan_ids:
                raise NetBoxError(f"Add VLAN {source.untagged_vlan} to NetBox first.")
            else:
                payload["untagged_vlan"] = vlan_ids[source.untagged_vlan]
        if "tagged_vlans" in requested:
            missing_vlans = [vid for vid in source.tagged_vlans if vid not in vlan_ids]
            if missing_vlans:
                missing = ", ".join(str(vid) for vid in missing_vlans)
                raise NetBoxError(f"Add tagged VLANs {missing} to NetBox first.")
            payload["tagged_vlans"] = [
                vlan_ids[vid] for vid in source.tagged_vlans if vid in vlan_ids
            ]
        if "lag" in requested:
            lag = interfaces_by_name.get(self._normalize_interface_name(source.lag or ""))
            if source.lag and lag is None:
                raise NetBoxError(f'Add aggregate interface "{source.lag}" to NetBox first.')
            payload["lag"] = lag["id"] if lag else None
        for field in ("mtu", "speed", "duplex", "mac_address", "mgmt_only", "custom_fields"):
            if field in requested:
                value = getattr(source, field)
                payload[field] = value * 1000 if field == "speed" and value is not None else value
        if "type" in requested and source.type:
            payload["type"] = source.type

        if create:
            target_device = self._select_interface_device(source.name, devices, device)
            payload.update(
                {
                    "device": target_device["id"],
                    "name": source.name,
                    "type": "lag" if normalized_name.startswith(("lag", "trk")) else "other",
                }
            )
            return _PreparedWrite(
                method="POST",
                collection_path="dcim/interfaces/",
                item_path="dcim/interfaces/",
                payload=payload,
                message=f'Interface "{source.name}" was added to NetBox.',
                cache=self._interfaces_by_device[target_device["id"]],
                cache_record={"name": source.name},
            )

        return _PreparedWrite(
            method="PATCH",
            collection_path="dcim/interfaces/",
            item_path=f"dcim/interfaces/{target['id']}/",
            payload=payload,
            message=f'Interface "{source.name}" was updated in NetBox.',
            cache_record={"id": target["id"]},
        )

    async def import_vlan(
        self,
        device_name: str,
        source: Vlan,
        fields: list[str],
        *,
        create: bool = False,
    ) -> str:
        prepared = await self._prepare_vlan_import(device_name, source, fields, create=create)
        response = await self._write(prepared.method, prepared.item_path, prepared.payload)
        prepared.record_success(response)
        return prepared.message

    async def _prepare_vlan_import(
        self,
        device_name: str,
        source: Vlan,
        fields: list[str],
        *,
        create: bool = False,
    ) -> _PreparedWrite:
        allowed = {"name", "description"}
        requested = allowed if create else set(fields) & allowed
        if not requested:
            raise NetBoxError("Select at least one supported VLAN field to import.")

        device = await self._find_device(device_name)
        site_id = (device.get("site") or {}).get("id")
        location_id = (device.get("location") or {}).get("id")
        vlan_results = await self._get_vlan_records()
        candidates = [
            item
            for item in vlan_results
            if int(item["vid"]) == source.vid
            and self._vlan_applies_to_device(item, site_id, location_id, {source.vid})
        ]
        target = max(
            candidates,
            key=lambda item: self._scope_priority(item, site_id, location_id),
            default=None,
        )
        if create and target is not None:
            raise NetBoxError(f"VLAN {source.vid} already exists in NetBox.")
        if not create and target is None:
            raise NetBoxError(f"VLAN {source.vid} was not found in NetBox.")

        payload: dict[str, Any] = {}
        if "name" in requested:
            payload["name"] = source.name or f"VLAN {source.vid}"
        if "description" in requested:
            payload["description"] = source.description

        if create:
            payload["vid"] = source.vid
            return _PreparedWrite(
                method="POST",
                collection_path="ipam/vlans/",
                item_path="ipam/vlans/",
                payload=payload,
                message=f"VLAN {source.vid} was added to NetBox.",
                cache=vlan_results,
                cache_record=payload,
            )

        return _PreparedWrite(
            method="PATCH",
            collection_path="ipam/vlans/",
            item_path=f"ipam/vlans/{target['id']}/",
            payload=payload,
            message=f"VLAN {source.vid} was updated in NetBox.",
            cache_record={"id": target["id"]},
        )

    async def import_many(
        self, device_name: str, actions: list[NetBoxImportAction]
    ) -> list[NetBoxImportResult]:
        """Apply compatible actions with NetBox's bulk write API."""
        results: list[NetBoxImportResult | None] = [None] * len(actions)
        prepared_groups: dict[tuple[str, str], list[tuple[int, _PreparedWrite]]] = {}

        for index, action in enumerate(actions):
            try:
                if action.resource is ImportResource.INTERFACE:
                    if action.interface is None:
                        raise NetBoxError("Interface data is required.")
                    prepared = await self._prepare_interface_import(
                        device_name,
                        action.interface,
                        action.fields,
                        create=action.create,
                    )
                else:
                    if action.vlan is None:
                        raise NetBoxError("VLAN data is required.")
                    prepared = await self._prepare_vlan_import(
                        device_name,
                        action.vlan,
                        action.fields,
                        create=action.create,
                    )
            except NetBoxError as exc:
                results[index] = NetBoxImportResult(success=False, message=str(exc))
                continue
            prepared_groups.setdefault((prepared.method, prepared.collection_path), []).append(
                (index, prepared)
            )

        for group in prepared_groups.values():
            if len(group) == 1:
                index, prepared = group[0]
                results[index] = await self._apply_prepared(prepared)
                continue

            method = group[0][1].method
            payloads = []
            for _index, prepared in group:
                payload = dict(prepared.payload)
                if method == "PATCH" and prepared.cache_record is not None:
                    payload["id"] = prepared.cache_record["id"]
                payloads.append(payload)

            try:
                responses = await self._write_bulk(method, group[0][1].collection_path, payloads)
                if len(responses) != len(group):
                    raise NetBoxError("NetBox returned an incomplete bulk update response.")
                for (index, prepared), response in zip(group, responses, strict=True):
                    prepared.record_success(response)
                    results[index] = NetBoxImportResult(success=True, message=prepared.message)
            except _BulkWriteRejected:
                semaphore = asyncio.Semaphore(8)

                async def apply_one(
                    index: int,
                    prepared: _PreparedWrite,
                    concurrency_limit: asyncio.Semaphore = semaphore,
                ) -> tuple[int, NetBoxImportResult]:
                    async with concurrency_limit:
                        return index, await self._apply_prepared(prepared)

                individual_results = await asyncio.gather(
                    *(apply_one(index, prepared) for index, prepared in group)
                )
                for index, result in individual_results:
                    results[index] = result
            except NetBoxError as exc:
                for index, _prepared in group:
                    results[index] = NetBoxImportResult(success=False, message=str(exc))

        return [
            result
            if result is not None
            else NetBoxImportResult(success=False, message="The NetBox update was not applied.")
            for result in results
        ]

    async def _apply_prepared(self, prepared: _PreparedWrite) -> NetBoxImportResult:
        try:
            response = await self._write(prepared.method, prepared.item_path, prepared.payload)
            prepared.record_success(response)
            return NetBoxImportResult(success=True, message=prepared.message)
        except NetBoxError as exc:
            return NetBoxImportResult(success=False, message=str(exc))

    @staticmethod
    def _normalize_interface_name(name: str) -> str:
        return re.sub(r"\s+", "", name).lower()

    @staticmethod
    def _select_interface_device(
        interface_name: str,
        devices: list[dict[str, Any]],
        default: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = re.sub(r"\s+", "", interface_name).lower()
        if normalized.startswith(("lag", "trk", "bridge-aggregation")):
            return default

        member_match = re.search(r"\d+", normalized)
        if member_match:
            member_position = int(member_match.group())
            for device in devices:
                if device.get("vc_position") == member_position:
                    return device
        return default

    @staticmethod
    def _to_interface(item: dict[str, Any]) -> Interface:
        untagged = item.get("untagged_vlan")
        tagged = item.get("tagged_vlans") or []
        lag = item.get("lag")
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
            lag=lag.get("name") if lag else None,
            mtu=item.get("mtu"),
            speed=item["speed"] // 1000 if item.get("speed") is not None else None,
            duplex=(item.get("duplex") or {}).get("value")
            if isinstance(item.get("duplex"), dict)
            else item.get("duplex"),
            type=(item.get("type") or {}).get("value")
            if isinstance(item.get("type"), dict)
            else item.get("type"),
            mac_address=(
                (item.get("primary_mac_address") or {}).get("mac_address")
                if isinstance(item.get("primary_mac_address"), dict)
                else item.get("mac_address") or item.get("primary_mac_address")
            ),
            mgmt_only=bool(item.get("mgmt_only", False)),
            custom_fields=item.get("custom_fields") or {},
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
