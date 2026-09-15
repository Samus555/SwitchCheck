import httpx
import respx
from fastapi.testclient import TestClient

from switchcheck.app import app
from switchcheck.models import (
    ConfigurationData,
    DeviceSummary,
    ImportResource,
    Interface,
    NetBoxImportResult,
)

client = TestClient(app)


def test_home_and_health() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Aruba configuration" in response.text
    assert "ArubaOS-Switch (legacy, exit)" in response.text
    assert "ArubaOS-Switch (legacy, quit)" in response.text
    assert 'value="tagged_vlans"' in response.text
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

        async def prepare_import(self, _device, _vlan_ids) -> None:
            pass

        async def import_many(self, _device, actions):
            results = []
            for action in actions:
                source = (
                    action.interface if action.resource is ImportResource.INTERFACE else action.vlan
                )
                identifier = source.name if action.interface else source.vid
                calls.append(f"{action.resource.value}:{identifier}:{action.create}")
                results.append(NetBoxImportResult(success=True, message="Applied"))
            return results

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

        async def prepare_import(self, _device, _vlan_ids) -> None:
            pass

        async def import_many(self, _device, actions):
            calls.extend(action.fields[0] for action in actions)
            return [
                NetBoxImportResult(success=True, message=f"Updated {action.fields[0]}")
                for action in actions
            ]

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


def test_batch_import_accepts_200_actions(monkeypatch) -> None:
    batch_sizes = []
    preloaded_vlan_ids = []

    class FakeNetBoxClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            pass

        async def prepare_import(self, _device, vlan_ids) -> None:
            preloaded_vlan_ids.append(vlan_ids)

        async def import_many(self, _device, actions):
            batch_sizes.append(len(actions))
            return [
                NetBoxImportResult(success=True, message="Interface updated") for _action in actions
            ]

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
                for port in range(1, 201)
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["applied"] == 200
    assert batch_sizes == [200]
    assert preloaded_vlan_ids == [set()]


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
