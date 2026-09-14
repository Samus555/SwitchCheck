import httpx
import pytest
import respx

from switchcheck.models import Interface, Vlan
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
    assert dict(vlan_route.calls[0].request.url.params) == {"limit": "100"}


@pytest.mark.asyncio
@respx.mock
async def test_fetches_interfaces_from_every_virtual_chassis_member() -> None:
    def device_response(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("name"):
            results = [
                {
                    "id": 7,
                    "name": "stack-01",
                    "vc_position": 1,
                    "virtual_chassis": {"id": 3, "name": "stack"},
                }
            ]
        else:
            results = [
                {"id": 7, "name": "stack-01", "vc_position": 1},
                {"id": 8, "name": "stack-02", "vc_position": 2},
            ]
        return httpx.Response(200, json={"next": None, "results": results})

    def interface_response(request: httpx.Request) -> httpx.Response:
        device_id = int(request.url.params["device_id"])
        name = "1/1/1" if device_id == 7 else "2/1/1"
        return httpx.Response(200, json={"next": None, "results": [{"name": name}]})

    device_route = respx.get("https://netbox.example/api/dcim/devices/").mock(
        side_effect=device_response
    )
    interface_route = respx.get("https://netbox.example/api/dcim/interfaces/").mock(
        side_effect=interface_response
    )
    respx.post("https://netbox.example/api/dcim/devices/7/render-config/").mock(
        return_value=httpx.Response(200, json={"content": "interface 1/1/1\ninterface 2/1/1"})
    )
    respx.get("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(200, json={"next": None, "results": []})
    )

    async with NetBoxClient("https://netbox.example", "secret") as client:
        result = await client.get_configuration("stack-01")

    assert [interface.name for interface in result.interfaces] == ["1/1/1", "2/1/1"]
    assert dict(device_route.calls[1].request.url.params) == {
        "virtual_chassis_id": "3",
        "limit": "100",
    }
    assert [call.request.url.params["device_id"] for call in interface_route.calls] == ["7", "8"]


def test_selects_virtual_chassis_member_when_creating_interface() -> None:
    primary = {"id": 7, "vc_position": 1}
    secondary = {"id": 8, "vc_position": 2}

    selected = NetBoxClient._select_interface_device("2/1/12", [primary, secondary], primary)

    assert selected == secondary


@pytest.mark.asyncio
@respx.mock
async def test_exposes_safe_netbox_validation_details() -> None:
    respx.get("https://netbox.example/api/test/").mock(
        return_value=httpx.Response(400, json={"vid": ["Enter a valid number."]})
    )

    async with NetBoxClient("https://netbox.example", "secret") as client:
        with pytest.raises(NetBoxError, match="vid: Enter a valid number"):
            await client._get("test/")


@pytest.mark.asyncio
@respx.mock
async def test_imports_selected_interface_field() -> None:
    respx.get("https://netbox.example/api/dcim/devices/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 7, "name": "access-01"}]})
    )
    respx.get("https://netbox.example/api/dcim/interfaces/").mock(
        return_value=httpx.Response(
            200,
            json={"next": None, "results": [{"id": 8, "name": "1 / 1 / 1"}]},
        )
    )
    respx.get("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(200, json={"next": None, "results": []})
    )
    patch_route = respx.patch("https://netbox.example/api/dcim/interfaces/8/").mock(
        return_value=httpx.Response(200, json={"id": 8})
    )

    async with NetBoxClient("https://netbox.example", "secret") as client:
        message = await client.import_interface(
            "access-01",
            Interface(name="1/1/1", description="Imported description"),
            ["description"],
        )

    assert message == 'Interface "1/1/1" was updated in NetBox.'
    assert patch_route.calls[0].request.content == b'{"description":"Imported description"}'


@pytest.mark.asyncio
@respx.mock
async def test_reuses_preloaded_data_across_batch_imports() -> None:
    device_route = respx.get("https://netbox.example/api/dcim/devices/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 7, "name": "access-01"}]})
    )
    interface_route = respx.get("https://netbox.example/api/dcim/interfaces/").mock(
        return_value=httpx.Response(
            200,
            json={
                "next": None,
                "results": [
                    {"id": 8, "name": "1/1/1"},
                    {"id": 9, "name": "1/1/2"},
                ],
            },
        )
    )
    vlan_route = respx.get("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(200, json={"next": None, "results": []})
    )
    respx.patch("https://netbox.example/api/dcim/interfaces/8/").mock(
        return_value=httpx.Response(200, json={"id": 8})
    )
    respx.patch("https://netbox.example/api/dcim/interfaces/9/").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )

    async with NetBoxClient("https://netbox.example", "secret") as client:
        await client.prepare_import("access-01")
        await client.import_interface(
            "access-01", Interface(name="1/1/1", description="First"), ["description"]
        )
        await client.import_interface(
            "access-01", Interface(name="1/1/2", description="Second"), ["description"]
        )

    assert device_route.call_count == 1
    assert interface_route.call_count == 1
    assert vlan_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_adds_missing_vlan() -> None:
    respx.get("https://netbox.example/api/dcim/devices/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 7, "name": "access-01"}]})
    )
    respx.get("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(200, json={"next": None, "results": []})
    )
    post_route = respx.post("https://netbox.example/api/ipam/vlans/").mock(
        return_value=httpx.Response(201, json={"id": 10})
    )

    async with NetBoxClient("https://netbox.example", "secret") as client:
        message = await client.import_vlan(
            "access-01",
            Vlan(vid=10, name="Users", description="Employee access"),
            [],
            create=True,
        )

    assert message == "VLAN 10 was added to NetBox."
    assert post_route.calls[0].request.content == (
        b'{"name":"Users","description":"Employee access","vid":10}'
    )
