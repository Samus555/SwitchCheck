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
