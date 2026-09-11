from switchcheck.comparison import compare_interfaces
from switchcheck.models import CompareStatus, Interface, InterfaceMode


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
