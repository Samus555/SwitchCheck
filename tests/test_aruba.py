from switchcheck.aruba import parse_aruba_config
from switchcheck.models import InterfaceMode


def test_parses_aruba_cx_interface_configuration() -> None:
    config = """
interface 1/1/1
    description Uplink to core
    no shutdown
    vlan trunk native 10
    vlan trunk allowed 20,30-31
interface 1/1/2
    description Workstation
    shutdown
    vlan access 40
"""

    interfaces = parse_aruba_config(config)

    assert len(interfaces) == 2
    assert interfaces[0].model_dump() == {
        "name": "1/1/1",
        "enabled": True,
        "description": "Uplink to core",
        "mode": InterfaceMode.TAGGED,
        "untagged_vlan": 10,
        "tagged_vlans": [20, 30, 31],
    }
    assert interfaces[1].enabled is False
    assert interfaces[1].mode is InterfaceMode.ACCESS
    assert interfaces[1].untagged_vlan == 40


def test_parses_arubaos_switch_vlan_configuration() -> None:
    config = """
interface 1
   name "Reception"
   enable
interface 2
   disable
vlan 10
   untagged 1-2
vlan 20
   tagged 1,2
"""

    interfaces = parse_aruba_config(config)

    assert [interface.name for interface in interfaces] == ["1", "2"]
    assert interfaces[0].description == "Reception"
    assert interfaces[0].untagged_vlan == 10
    assert interfaces[0].tagged_vlans == [20]
    assert interfaces[0].mode is InterfaceMode.TAGGED
    assert interfaces[1].enabled is False
