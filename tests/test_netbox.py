import httpx
import pytest
import respx

from switchcheck.netbox import NetBoxClient, NetBoxError


@pytest.mark.asyncio
@respx.mock
async def test_fetches_unfiltered_vlans_and_scopes_them_locally() -> None:
    respx.get("https://netbox.example/api/dcim/devices/").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": 7,
                        "name": "access-01",
                        "site": {"id": 5},
                        "location": {"id": 9},
                    }
                ]
            },
        )
    )
    respx.post("https://netbox.example/api/dcim/devices/7/render-config/").mock(
        return_value=httpx.Response(400, json={"error": "No config template found."})
    )
    respx.get("https://netbox.example/api/dcim/interfaces/").mock(
        return_value=httpx.Response(
            200,
            json={
                "next": None,
                "results": [
                    {
                        "name": "1 / 1 / 1",
                        "mode": {"value": "access"},
                        "untagged_vlan": {"vid": 10},
                    }
                ],
            },
        )
    )
    vlan_route = respx.get("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(
            200,
            json={
                "next": None,
                "results": [
                    {"id": 1, "vid": 10, "name": "Global in use"},
                    {"id": 2, "vid": 20, "name": "Local", "site": {"id": 5}},
                    {"id": 3, "vid": 30, "name": "Other site", "site": {"id": 6}},
                    {"id": 4, "vid": 40, "name": "Unused global"},
                    {
                        "id": 5,
                        "vid": 50,
                        "name": "Location",
                        "scope_type": "dcim.location",
                        "scope": {"id": 9},
                    },
                ],
            },
        )
    )

    async with NetBoxClient("https://netbox.example", "secret") as client:
        result = await client.get_configuration("access-01")

    assert [vlan.vid for vlan in result.vlans] == [10, 20, 50]
    assert result.rendered_config is None
    assert vlan_route.calls[0].request.url.params == {"limit": "100"}


@pytest.mark.asyncio
@respx.mock
async def test_exposes_safe_netbox_validation_details() -> None:
    respx.get("https://netbox.example/api/test/").mock(
        return_value=httpx.Response(400, json={"vid": ["Enter a valid number."]})
    )

    async with NetBoxClient("https://netbox.example", "secret") as client:
        with pytest.raises(NetBoxError, match="vid: Enter a valid number"):
            await client._get("test/")
