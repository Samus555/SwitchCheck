import asyncio

import httpx
import respx
from fastapi.testclient import TestClient

from switchcheck.app import app
from switchcheck.models import ConfigurationData, DeviceSummary, Interface

client = TestClient(app)


def test_home_and_health() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Aruba configuration" in response.text
    assert client.get("/health").json() == {"status": "ok"}


@respx.mock
def test_compare_endpoint_fetches_netbox_and_returns_result() -> None:
    respx.get("https://netbox.example/api/dcim/devices/").mock(
        return_value=httpx.Response(
            200,
            json={"count": 1, "next": None, "results": [{"id": 7, "name": "access-01"}]},
        )
    )
    respx.get("https://netbox.example/api/dcim/interfaces/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [
                    {
                        "name": "1/1/1",
                        "enabled": True,
                        "description": "Office",
                        "mode": {"value": "access"},
                        "untagged_vlan": {"vid": 10},
                        "tagged_vlans": [],
                    }
                ],
            },
        )
    )
    respx.post("https://netbox.example/api/dcim/devices/7/render-config/").mock(
        return_value=httpx.Response(
            200,
            json={
                "configtemplate": {"id": 3, "name": "aruba-cx"},
                "content": "interface 1/1/1\n description Office\n vlan access 10",
            },
        )
    )
    respx.get("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"id": 10, "vid": 10, "name": "", "description": ""}],
            },
        )
    )

    response = client.post(
        "/api/compare",
        json={
            "config": "interface 1/1/1\n description Office\n vlan access 10",
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "device": "access-01",
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["matches"] == 1
    assert response.json()["vlan_summary"]["matches"] == 1
    assert response.json()["config"]["available"] is True


def test_compare_rejects_config_without_interfaces() -> None:
    response = client.post(
        "/api/compare",
        json={
            "config": "hostname switch-01",
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "device": "access-01",
        },
    )

    assert response.status_code == 422


@respx.mock
def test_import_endpoint_adds_vlan_to_netbox() -> None:
    respx.get("https://netbox.example/api/dcim/devices/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 7, "name": "access-01"}]})
    )
    respx.get("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(200, json={"next": None, "results": []})
    )
    create_route = respx.post("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(201, json={"id": 10})
    )

    response = client.post(
        "/api/netbox/import",
        json={
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "device": "access-01",
            "resource": "vlan",
            "create": True,
            "vlan": {"vid": 10, "name": "Users", "description": "Access"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"message": "VLAN 10 was added to NetBox."}
    assert create_route.called


def test_batch_import_orders_dependencies(monkeypatch) -> None:
    calls: list[str] = []

    class FakeNetBoxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def prepare_import(self, _device) -> None:
            pass

        async def import_vlan(self, _device, source, _fields, *, create=False):
            calls.append(f"vlan:{source.vid}:{create}")
            return f"VLAN {source.vid} added"

        async def import_interface(self, _device, source, _fields, *, create=False):
            calls.append(f"interface:{source.name}:{create}")
            return f"Interface {source.name} added"

    monkeypatch.setattr("switchcheck.app.NetBoxClient", FakeNetBoxClient)
    response = client.post(
        "/api/netbox/import-batch",
        json={
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "device": "access-01",
            "actions": [
                {
                    "resource": "interface",
                    "create": True,
                    "interface": {"name": "1/1/1", "untagged_vlan": 10},
                },
                {
                    "resource": "vlan",
                    "create": True,
                    "vlan": {"vid": 10, "name": "Users"},
                },
                {
                    "resource": "interface",
                    "create": True,
                    "interface": {"name": "lag 1"},
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["applied"] == 3
    assert calls == ["vlan:10:True", "interface:lag 1:True", "interface:1/1/1:True"]


def test_batch_import_applies_interface_mode_before_vlan_assignment(monkeypatch) -> None:
    calls: list[str] = []

    class FakeNetBoxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def prepare_import(self, _device) -> None:
            pass

        async def import_interface(self, _device, _source, fields, *, create=False):
            calls.append(fields[0])
            return f"Updated {fields[0]}"

    monkeypatch.setattr("switchcheck.app.NetBoxClient", FakeNetBoxClient)
    response = client.post(
        "/api/netbox/import-batch",
        json={
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "device": "access-01",
            "actions": [
                {
                    "resource": "interface",
                    "fields": ["untagged_vlan"],
                    "interface": {"name": "1/1/1", "untagged_vlan": 10},
                },
                {
                    "resource": "interface",
                    "fields": ["mode"],
                    "interface": {"name": "1/1/1", "mode": "access"},
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["applied"] == 2
    assert calls == ["mode", "untagged_vlan"]


def test_device_discovery_endpoint(monkeypatch) -> None:
    class FakeNetBoxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def list_devices(self, query):
            assert query == "edge"
            return [DeviceSummary(name="edge-01", display="edge-01", site="HQ")]

    monkeypatch.setattr("switchcheck.app.NetBoxClient", FakeNetBoxClient)
    response = client.post(
        "/api/netbox/devices",
        json={
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "query": "edge",
        },
    )

    assert response.status_code == 200
    assert response.json()[0]["name"] == "edge-01"


def test_batch_import_runs_independent_actions_concurrently(monkeypatch) -> None:
    active = 0
    max_active = 0

    class FakeNetBoxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def prepare_import(self, _device) -> None:
            pass

        async def import_interface(self, _device, source, _fields, *, create=False):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return f"Interface {source.name} updated"

    monkeypatch.setattr("switchcheck.app.NetBoxClient", FakeNetBoxClient)
    response = client.post(
        "/api/netbox/import-batch",
        json={
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "device": "access-01",
            "actions": [
                {
                    "resource": "interface",
                    "fields": ["description"],
                    "interface": {"name": f"1/1/{port}", "description": "Updated"},
                }
                for port in range(1, 4)
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["applied"] == 3
    assert max_active == 3


def test_change_plan_reports_before_and_after_without_writing(monkeypatch) -> None:
    class FakeNetBoxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def get_configuration(self, _device):
            return ConfigurationData(interfaces=[Interface(name="1/1/1", description="Old")])

    monkeypatch.setattr("switchcheck.app.NetBoxClient", FakeNetBoxClient)
    response = client.post(
        "/api/netbox/change-plan",
        json={
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "device": "edge-01",
            "actions": [
                {
                    "resource": "interface",
                    "fields": ["description"],
                    "interface": {"name": "1/1/1", "description": "New"},
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["actions"][0]["changes"]["description"] == {
        "before": "Old",
        "after": "New",
    }


def test_bulk_compare_summarizes_device_drift(monkeypatch) -> None:
    class FakeNetBoxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def get_configuration(self, *_args):
            return ConfigurationData(interfaces=[Interface(name="1", enabled=False)])

    monkeypatch.setattr("switchcheck.app.NetBoxClient", FakeNetBoxClient)
    response = client.post(
        "/api/compare-bulk",
        json={
            "netbox_url": "https://netbox.example",
            "token": "secret",
            "audits": [{"device": "edge-01", "config": "interface 1\n no shutdown"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["devices_with_drift"] == 1
