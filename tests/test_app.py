import httpx
import respx
from fastapi.testclient import TestClient

from switchcheck.app import app

client = TestClient(app)


def test_home_and_health() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Switch intent, verified." in response.text
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
