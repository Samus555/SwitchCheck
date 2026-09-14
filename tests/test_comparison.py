from switchcheck.comparison import compare_configs, compare_interfaces
from switchcheck.models import CompareStatus, Interface, InterfaceMode, Vlan


def test_compares_interfaces_and_summarizes_drift() -> None:
    aruba = [
        Interface(name="1", mode=InterfaceMode.ACCESS, untagged_vlan=10),
        Interface(name="2", enabled=False),
        Interface(name="3"),
    ]
    netbox = [
        Interface(name="1", mode=InterfaceMode.ACCESS, untagged_vlan=10),
        Interface(name="2", enabled=True),
        Interface(name="4"),
    ]

    result = compare_interfaces(aruba, netbox)

    assert result.summary.model_dump() == {
        "total": 4,
        "matches": 1,
        "differences": 1,
        "only_aruba": 1,
        "only_netbox": 1,
    }
    assert [item.status for item in result.interfaces] == [
        CompareStatus.MATCH,
        CompareStatus.DIFFERENT,
        CompareStatus.ONLY_ARUBA,
        CompareStatus.ONLY_NETBOX,
    ]
    assert result.interfaces[1].differences[0].field == "enabled"


def test_normalizes_interface_names_and_compares_vlan_membership() -> None:
    aruba = [
        Interface(
            name="1/1/1",
            mode=InterfaceMode.TAGGED,
            untagged_vlan=10,
            tagged_vlans=[20, 30],
            lag="lag1",
        )
    ]
    netbox = [
        Interface(
            name="1 / 1 / 1",
            mode=InterfaceMode.TAGGED,
            untagged_vlan=10,
            tagged_vlans=[20, 40],
            lag="LAG 1",
        )
    ]

    result = compare_interfaces(aruba, netbox)

    assert result.summary.total == 1
    assert result.interfaces[0].status is CompareStatus.DIFFERENT
    assert [difference.field for difference in result.interfaces[0].differences] == ["tagged vlans"]


def test_compares_vlan_metadata() -> None:
    result = compare_interfaces(
        [],
        [],
        [Vlan(vid=10, name="Users", description="Access"), Vlan(vid=20, name="Voice")],
        [Vlan(vid=10, name="Users", description="Clients"), Vlan(vid=30, name="Servers")],
    )

    assert result.vlan_summary.model_dump() == {
        "total": 3,
        "matches": 0,
        "differences": 1,
        "only_aruba": 1,
        "only_netbox": 1,
    }
    assert result.vlans[0].differences[0].field == "description"


def test_builds_side_by_side_configuration_diff() -> None:
    result = compare_configs(
        "hostname access-01\nvlan 10\n name Users",
        "hostname access-01\nvlan 10\n name Clients\nvlan 20",
    )

    assert result.available is True
    assert result.summary.unchanged == 2
    assert result.summary.changed == 1
    assert result.summary.rendered_only == 1


def test_config_diff_ignores_comments_case_and_cosmetic_whitespace() -> None:
    result = compare_configs(
        "! generated\nINTERFACE   1/1/1\n  description Office\n",
        "# template\ninterface 1/1/1\n description   office\n",
    )

    assert result.summary.unchanged == 2
    assert result.summary.changed == 0


def test_compares_extended_fields_and_generates_remediation_commands() -> None:
    result = compare_interfaces(
        [Interface(name="1/1/1", mtu=1500, speed=1000)],
        [
            Interface(
                name="1/1/1",
                description="Uplink",
                mtu=9198,
                speed=10000,
                mode=InterfaceMode.ACCESS,
                untagged_vlan=10,
            )
        ],
    )

    fields = {difference.field for difference in result.interfaces[0].differences}
    assert {"description", "mode", "untagged vlan", "mtu", "speed"} <= fields
    assert result.remediation_commands == [
        "interface 1/1/1",
        "    no shutdown",
        "    description Uplink",
        "    vlan access 10",
        "    mtu 9198",
        "    speed 10000",
        "exit",
    ]
