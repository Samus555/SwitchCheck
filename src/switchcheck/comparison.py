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
    InterfaceMode,
    Vlan,
    VlanComparison,
)

INTERFACE_FIELDS = (
    "enabled",
    "description",
    "mode",
    "untagged_vlan",
    "tagged_vlans",
    "lag",
    "mtu",
    "speed",
    "duplex",
    "type",
    "mac_address",
    "mgmt_only",
    "custom_fields",
)
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
            if _should_compare_interface_field(field, getattr(aruba, field))
            and not _interface_values_equal(field, getattr(aruba, field), getattr(netbox, field))
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
        remediation_commands=generate_aruba_commands(comparisons, vlan_comparisons),
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


def _interface_values_equal(field: str, aruba_value: object, netbox_value: object) -> bool:
    if field == "lag" and isinstance(aruba_value, str) and isinstance(netbox_value, str):
        return normalize_interface_name(aruba_value) == normalize_interface_name(netbox_value)
    if field == "mac_address" and isinstance(aruba_value, str) and isinstance(netbox_value, str):
        return re.sub(r"[^0-9a-f]", "", aruba_value.lower()) == re.sub(
            r"[^0-9a-f]", "", netbox_value.lower()
        )
    return aruba_value == netbox_value


def _should_compare_interface_field(field: str, aruba_value: object) -> bool:
    if field in {"mtu", "speed", "duplex", "type", "mac_address"}:
        return aruba_value is not None
    if field == "custom_fields":
        return bool(aruba_value)
    return True


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

    current_lines = _significant_config_lines(current)
    rendered_lines = _significant_config_lines(rendered)
    matcher = SequenceMatcher(
        None,
        [normalized for _, _, normalized in current_lines],
        [normalized for _, _, normalized in rendered_lines],
        autojunk=False,
    )
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
                    current_number=current_block[offset][0] if has_current else None,
                    current_text=current_block[offset][1] if has_current else None,
                    rendered_number=rendered_block[offset][0] if has_rendered else None,
                    rendered_text=rendered_block[offset][1] if has_rendered else None,
                )
            )

    counts = {
        status: sum(line.status == status for line in lines)
        for status in ("unchanged", "changed", "current_only", "rendered_only")
    }
    return ConfigComparison(summary=ConfigDiffSummary(**counts), lines=lines)


def _significant_config_lines(config: str) -> list[tuple[int, str, str]]:
    """Keep display text and line numbers while normalizing cosmetic differences."""
    result: list[tuple[int, str, str]] = []
    for number, text in enumerate(config.splitlines(), 1):
        stripped = text.strip()
        if not stripped or stripped.startswith(("!", "#", ";")):
            continue
        normalized = re.sub(r"\s+", " ", stripped).lower()
        result.append((number, text, normalized))
    return result


def generate_aruba_commands(
    comparisons: list[InterfaceComparison], vlan_comparisons: list[VlanComparison]
) -> list[str]:
    """Generate Aruba CX commands which make the switch follow NetBox intent."""
    commands: list[str] = []
    for item in comparisons:
        target = item.netbox
        if target is None or item.status is CompareStatus.MATCH:
            continue
        commands.append(f"interface {target.name}")
        commands.append("    no shutdown" if target.enabled else "    shutdown")
        commands.append(
            f"    description {_cli_value(target.description)}"
            if target.description
            else "    no description"
        )
        if target.mode is InterfaceMode.ACCESS and target.untagged_vlan is not None:
            commands.append(f"    vlan access {target.untagged_vlan}")
        elif target.mode is InterfaceMode.TAGGED:
            if target.untagged_vlan is not None:
                commands.append(f"    vlan trunk native {target.untagged_vlan}")
            if target.tagged_vlans:
                commands.append(
                    "    vlan trunk allowed " + ",".join(str(vid) for vid in target.tagged_vlans)
                )
        if target.mtu is not None:
            commands.append(f"    mtu {target.mtu}")
        if target.speed is not None:
            commands.append(f"    speed {target.speed}")
        if target.lag:
            commands.append(f"    lag {target.lag}")
        commands.append("exit")
    for item in vlan_comparisons:
        target = item.netbox
        if target is None or item.status is CompareStatus.MATCH:
            continue
        commands.append(f"vlan {target.vid}")
        if target.name:
            commands.append(f"    name {_cli_value(target.name)}")
        if target.description:
            commands.append(f"    description {_cli_value(target.description)}")
        commands.append("exit")
    return commands


def _cli_value(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()


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
