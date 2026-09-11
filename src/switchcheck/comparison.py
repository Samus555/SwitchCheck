import re
from difflib import SequenceMatcher

from switchcheck.models import (
    CompareStatus,
    ComparisonResult,
    ComparisonSummary,
    ConfigComparison,
    ConfigDiffLine,
    ConfigDiffSummary,
    FieldDifference,
    Interface,
    InterfaceComparison,
    Vlan,
    VlanComparison,
)

INTERFACE_FIELDS = ("enabled", "description", "mode", "untagged_vlan", "tagged_vlans")
VLAN_FIELDS = ("name", "description")


def normalize_interface_name(name: str) -> str:
    """Return the canonical interface key used across Aruba and NetBox."""
    return re.sub(r"\s+", "", name).lower()


def compare_interfaces(
    aruba_interfaces: list[Interface],
    netbox_interfaces: list[Interface],
    aruba_vlans: list[Vlan] | None = None,
    netbox_vlans: list[Vlan] | None = None,
    current_config: str = "",
    rendered_config: str | None = "",
    rendered_config_error: str | None = None,
) -> ComparisonResult:
    aruba_by_name = {normalize_interface_name(item.name): item for item in aruba_interfaces}
    netbox_by_name = {normalize_interface_name(item.name): item for item in netbox_interfaces}
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
            for field in INTERFACE_FIELDS
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

    vlan_comparisons = compare_vlans(aruba_vlans or [], netbox_vlans or [])

    return ComparisonResult(
        summary=_summarize(comparisons),
        interfaces=comparisons,
        vlan_summary=_summarize(vlan_comparisons),
        vlans=vlan_comparisons,
        config=compare_configs(current_config, rendered_config, rendered_config_error),
    )


def compare_vlans(aruba_vlans: list[Vlan], netbox_vlans: list[Vlan]) -> list[VlanComparison]:
    aruba_by_vid = {item.vid: item for item in aruba_vlans}
    netbox_by_vid = {item.vid: item for item in netbox_vlans}
    comparisons: list[VlanComparison] = []

    for vid in sorted(aruba_by_vid.keys() | netbox_by_vid.keys()):
        aruba = aruba_by_vid.get(vid)
        netbox = netbox_by_vid.get(vid)
        if aruba is None:
            comparisons.append(
                VlanComparison(vid=vid, status=CompareStatus.ONLY_NETBOX, netbox=netbox)
            )
            continue
        if netbox is None:
            comparisons.append(
                VlanComparison(vid=vid, status=CompareStatus.ONLY_ARUBA, aruba=aruba)
            )
            continue

        differences = [
            FieldDifference(
                field=field,
                aruba=getattr(aruba, field),
                netbox=getattr(netbox, field),
            )
            for field in VLAN_FIELDS
            if getattr(aruba, field) != getattr(netbox, field)
        ]
        comparisons.append(
            VlanComparison(
                vid=vid,
                status=CompareStatus.DIFFERENT if differences else CompareStatus.MATCH,
                aruba=aruba,
                netbox=netbox,
                differences=differences,
            )
        )
    return comparisons


def compare_configs(
    current: str, rendered: str | None, unavailable_message: str | None = None
) -> ConfigComparison:
    if rendered is None:
        return ConfigComparison(
            available=False,
            message=unavailable_message or "No rendered configuration is available.",
            summary=ConfigDiffSummary(
                unchanged=0,
                changed=0,
                current_only=0,
                rendered_only=0,
            ),
            lines=[],
        )

    current_lines = current.splitlines()
    rendered_lines = rendered.splitlines()
    matcher = SequenceMatcher(None, current_lines, rendered_lines, autojunk=False)
    lines: list[ConfigDiffLine] = []

    for (
        operation,
        current_start,
        current_end,
        rendered_start,
        rendered_end,
    ) in matcher.get_opcodes():
        current_block = current_lines[current_start:current_end]
        rendered_block = rendered_lines[rendered_start:rendered_end]
        row_count = max(len(current_block), len(rendered_block))
        for offset in range(row_count):
            has_current = offset < len(current_block)
            has_rendered = offset < len(rendered_block)
            if operation == "equal":
                status = "unchanged"
            elif operation == "replace" and has_current and has_rendered:
                status = "changed"
            elif has_current:
                status = "current_only"
            else:
                status = "rendered_only"
            lines.append(
                ConfigDiffLine(
                    status=status,
                    current_number=current_start + offset + 1 if has_current else None,
                    current_text=current_block[offset] if has_current else None,
                    rendered_number=rendered_start + offset + 1 if has_rendered else None,
                    rendered_text=rendered_block[offset] if has_rendered else None,
                )
            )

    counts = {
        status: sum(line.status == status for line in lines)
        for status in ("unchanged", "changed", "current_only", "rendered_only")
    }
    return ConfigComparison(summary=ConfigDiffSummary(**counts), lines=lines)


def _summarize(
    comparisons: list[InterfaceComparison] | list[VlanComparison],
) -> ComparisonSummary:
    counts = {status: 0 for status in CompareStatus}
    for item in comparisons:
        counts[item.status] += 1
    return ComparisonSummary(
        total=len(comparisons),
        matches=counts[CompareStatus.MATCH],
        differences=counts[CompareStatus.DIFFERENT],
        only_aruba=counts[CompareStatus.ONLY_ARUBA],
        only_netbox=counts[CompareStatus.ONLY_NETBOX],
    )


def _natural_key(value: str) -> list[tuple[int, int | str]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value)
    ]
