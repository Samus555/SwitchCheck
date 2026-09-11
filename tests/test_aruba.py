from switchcheck.aruba import parse_aruba_config, parse_aruba_configuration
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
        "lag": None,
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


def test_parses_vlan_names_descriptions_and_interface_membership() -> None:
    config = """
vlan 10
   name "Users"
   description "Employee access"
   untagged 1
vlan 20 name "Voice"
   tagged 1
"""

    parsed = parse_aruba_configuration(config)

    assert [vlan.model_dump() for vlan in parsed.vlans] == [
        {"vid": 10, "name": "Users", "description": "Employee access"},
        {"vid": 20, "name": "Voice", "description": ""},
    ]
    assert parsed.interfaces[0].untagged_vlan == 10
    assert parsed.interfaces[0].tagged_vlans == [20]


def test_detects_cx_and_arubaos_link_aggregation_membership() -> None:
    cx = parse_aruba_configuration(
        """
interface lag 1
   description Uplink bundle
interface 1/1/1
   lag 1
interface 1/1/2
   lag 1 mode active
"""
    )
    aos = parse_aruba_configuration("trunk 1-2 Trk1 lacp")

    assert {item.name: item.lag for item in cx.interfaces} == {
        "1/1/1": "lag 1",
        "1/1/2": "lag 1",
        "lag 1": None,
    }
    assert {item.name: item.lag for item in aos.interfaces} == {
        "1": "Trk1",
        "2": "Trk1",
        "Trk1": None,
    }


def test_parses_comware_bridge_aggregation_and_vlan_commands() -> None:
    parsed = parse_aruba_configuration(
        """
interface Bridge-Aggregation1
 description Core uplink
 port link-type trunk
 port trunk pvid vlan 100
 port trunk permit vlan 100 200 to 202
interface Ten-GigabitEthernet1/0/1
 undo shutdown
 port link-aggregation group 1
interface GigabitEthernet1/0/2
 port link-type access
 port access vlan 300
"""
    )
    interfaces = {item.name: item for item in parsed.interfaces}

    aggregate = interfaces["Bridge-Aggregation1"]
    assert aggregate.mode is InterfaceMode.TAGGED
    assert aggregate.untagged_vlan == 100
    assert aggregate.tagged_vlans == [200, 201, 202]
    assert interfaces["Ten-GigabitEthernet1/0/1"].lag == "Bridge-Aggregation1"
    assert interfaces["GigabitEthernet1/0/2"].mode is InterfaceMode.ACCESS
    assert interfaces["GigabitEthernet1/0/2"].untagged_vlan == 300
    assert [vlan.vid for vlan in parsed.vlans] == [100, 200, 201, 202, 300]
