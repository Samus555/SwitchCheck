import re

from switchcheck.models import ConfigurationData, Interface, InterfaceMode, Vlan


def _clean_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _expand_number_list(value: str) -> list[int]:
    values: set[int] = set()
    for part in re.split(r",\s*", value.strip()):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            if start.isdigit() and end.isdigit():
                values.update(range(int(start), int(end) + 1))
        elif part.isdigit():
            values.add(int(part))
    return sorted(values)


def _expand_interfaces(value: str) -> list[str]:
    """Expand simple Aruba port lists while preserving CX-style names."""
    result: list[str] = []
    for part in re.split(r",\s*", value.strip()):
        part = part.strip()
        match = re.fullmatch(r"(\d+)-(\d+)", part)
        if match:
            result.extend(str(port) for port in range(int(match.group(1)), int(match.group(2)) + 1))
            continue

        match = re.fullmatch(r"(.*/)(\d+)-(\d+)", part)
        if match:
            result.extend(
                f"{match.group(1)}{port}"
                for port in range(int(match.group(2)), int(match.group(3)) + 1)
            )
            continue
        if part:
            result.append(part)
    return result


def parse_aruba_configuration(config: str) -> ConfigurationData:
    """Parse interface and VLAN state from ArubaOS-Switch and Aruba CX configs."""
    interfaces: dict[str, Interface] = {}
    vlans: dict[int, Vlan] = {}
    current_interfaces: list[str] = []
    current_vlans: list[int] = []

    def get_interface(name: str) -> Interface:
        return interfaces.setdefault(name, Interface(name=name))

    def get_vlan(vid: int) -> Vlan:
        return vlans.setdefault(vid, Vlan(vid=vid))

    for raw_line in config.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("!", "#", ";")):
            continue

        trunk_match = re.fullmatch(
            r"trunk\s+(\S+)\s+(\S+)(?:\s+(?:trunk|lacp))?",
            line,
            re.IGNORECASE,
        )
        if trunk_match:
            member_names = _expand_interfaces(trunk_match.group(1))
            lag_name = trunk_match.group(2)
            get_interface(lag_name)
            for name in member_names:
                get_interface(name).lag = lag_name
            current_interfaces = []
            current_vlans = []
            continue

        interface_match = re.fullmatch(r"interface\s+(.+)", line, re.IGNORECASE)
        if interface_match:
            current_interfaces = _expand_interfaces(interface_match.group(1))
            current_vlans = []
            for name in current_interfaces:
                get_interface(name)
            continue

        vlan_match = re.fullmatch(
            r"vlan\s+([\d,\s-]+?)(?:\s+name\s+(.+))?",
            line,
            re.IGNORECASE,
        )
        if vlan_match:
            current_vlans = _expand_number_list(vlan_match.group(1))
            current_interfaces = []
            for vid in current_vlans:
                vlan = get_vlan(vid)
                if vlan_match.group(2):
                    vlan.name = _clean_value(vlan_match.group(2))
            continue

        if current_vlans:
            membership = re.fullmatch(r"(tagged|untagged)\s+(.+)", line, re.IGNORECASE)
            if membership:
                tagged = membership.group(1).lower() == "tagged"
                for name in _expand_interfaces(membership.group(2)):
                    interface = get_interface(name)
                    for vid in current_vlans:
                        if tagged:
                            interface.tagged_vlans = sorted({*interface.tagged_vlans, vid})
                        else:
                            interface.untagged_vlan = vid
                continue
            vlan_name = re.fullmatch(r"name\s+(.+)", line, re.IGNORECASE)
            vlan_description = re.fullmatch(r"description\s+(.+)", line, re.IGNORECASE)
            if vlan_name or vlan_description:
                for vid in current_vlans:
                    vlan = get_vlan(vid)
                    if vlan_name:
                        vlan.name = _clean_value(vlan_name.group(1))
                    else:
                        assert vlan_description is not None
                        vlan.description = _clean_value(vlan_description.group(1))
                continue

        if not current_interfaces:
            continue

        lowered = line.lower()
        description = re.fullmatch(r"(?:description|name)\s+(.+)", line, re.IGNORECASE)
        if description:
            for name in current_interfaces:
                get_interface(name).description = _clean_value(description.group(1))
        elif lowered in {"shutdown", "disable"}:
            for name in current_interfaces:
                get_interface(name).enabled = False
        elif lowered in {"no shutdown", "enable"}:
            for name in current_interfaces:
                get_interface(name).enabled = True
        else:
            lag = re.fullmatch(r"lag\s+(\S+)(?:\s+mode\s+\S+)?", line, re.IGNORECASE)
            access = re.fullmatch(r"vlan\s+access\s+(\d+)", line, re.IGNORECASE)
            native = re.fullmatch(r"vlan\s+trunk\s+native\s+(\d+)", line, re.IGNORECASE)
            allowed = re.fullmatch(r"vlan\s+trunk\s+allowed\s+(.+)", line, re.IGNORECASE)
            if lag:
                lag_name = lag.group(1)
                if not lag_name.lower().startswith(("lag", "trk")):
                    lag_name = f"lag {lag_name}"
                get_interface(lag_name)
                for name in current_interfaces:
                    get_interface(name).lag = lag_name
            elif access or native:
                vlan_id = int((access or native).group(1))
                get_vlan(vlan_id)
                for name in current_interfaces:
                    get_interface(name).untagged_vlan = vlan_id
            elif allowed:
                vlan_ids = _expand_number_list(allowed.group(1))
                for vlan_id in vlan_ids:
                    get_vlan(vlan_id)
                for name in current_interfaces:
                    get_interface(name).tagged_vlans = vlan_ids

    for interface in interfaces.values():
        if interface.tagged_vlans:
            interface.mode = InterfaceMode.TAGGED
        elif interface.untagged_vlan is not None:
            interface.mode = InterfaceMode.ACCESS

    return ConfigurationData(
        interfaces=sorted(interfaces.values(), key=lambda item: _natural_key(item.name)),
        vlans=sorted(vlans.values(), key=lambda item: item.vid),
    )


def parse_aruba_config(config: str) -> list[Interface]:
    """Return parsed interfaces for callers using the original parser API."""
    return parse_aruba_configuration(config).interfaces


def _natural_key(value: str) -> list[tuple[int, int | str]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value)
    ]
