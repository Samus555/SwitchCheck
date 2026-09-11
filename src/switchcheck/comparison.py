import re

from switchcheck.models import (
    CompareStatus,
    ComparisonResult,
    ComparisonSummary,
    FieldDifference,
    Interface,
    InterfaceComparison,
)

COMPARISON_FIELDS = ("enabled", "description", "mode", "untagged_vlan", "tagged_vlans")


def compare_interfaces(
    aruba_interfaces: list[Interface], netbox_interfaces: list[Interface]
) -> ComparisonResult:
    aruba_by_name = {item.name.casefold(): item for item in aruba_interfaces}
    netbox_by_name = {item.name.casefold(): item for item in netbox_interfaces}
    names = sorted(aruba_by_name.keys() | netbox_by_name.keys(), key=_natural_key)
    comparisons: list[InterfaceComparison] = []

    for normalized_name in names:
        aruba = aruba_by_name.get(normalized_name)
        netbox = netbox_by_name.get(normalized_name)
        name = aruba.name if aruba else netbox.name  # type: ignore[union-attr]

        if aruba is None:
            comparisons.append(
                InterfaceComparison(
                    name=name,
                    status=CompareStatus.ONLY_NETBOX,
                    netbox=netbox,
                )
            )
            continue
        if netbox is None:
            comparisons.append(
                InterfaceComparison(
                    name=name,
                    status=CompareStatus.ONLY_ARUBA,
                    aruba=aruba,
                )
            )
            continue

        differences = [
            FieldDifference(
                field=field.replace("_", " "),
                aruba=getattr(aruba, field),
                netbox=getattr(netbox, field),
            )
            for field in COMPARISON_FIELDS
            if getattr(aruba, field) != getattr(netbox, field)
        ]
        comparisons.append(
            InterfaceComparison(
                name=name,
                status=CompareStatus.DIFFERENT if differences else CompareStatus.MATCH,
                aruba=aruba,
                netbox=netbox,
                differences=differences,
            )
        )

    counts = {status: 0 for status in CompareStatus}
    for item in comparisons:
        counts[item.status] += 1

    return ComparisonResult(
        summary=ComparisonSummary(
            total=len(comparisons),
            matches=counts[CompareStatus.MATCH],
            differences=counts[CompareStatus.DIFFERENT],
            only_aruba=counts[CompareStatus.ONLY_ARUBA],
            only_netbox=counts[CompareStatus.ONLY_NETBOX],
        ),
        interfaces=comparisons,
    )


def _natural_key(value: str) -> list[tuple[int, int | str]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value)
    ]
