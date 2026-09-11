import re

from switchcheck.models import Interface, InterfaceMode


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


def parse_aruba_config(config: str) -> list[Interface]:
    """Parse relevant interface state from ArubaOS-Switch and Aruba CX configs."""
    interfaces: dict[str, Interface] = {}
    current_interfaces: list[str] = []
    current_vlan: int | None = None

    def get_interface(name: str) -> Interface:
        return interfaces.setdefault(name, Interface(name=name))

    for raw_line in config.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("!", "#", ";")):
            continue

        interface_match = re.fullmatch(r"interface\s+(.+)", line, re.IGNORECASE)
        if interface_match:
            current_interfaces = _expand_interfaces(interface_match.group(1))
            current_vlan = None
            for name in current_interfaces:
                get_interface(name)
            continue

        vlan_match = re.fullmatch(r"vlan\s+(\d+)", line, re.IGNORECASE)
        if vlan_match:
            current_vlan = int(vlan_match.group(1))
            current_interfaces = []
            continue

        if current_vlan is not None:
            membership = re.fullmatch(r"(tagged|untagged)\s+(.+)", line, re.IGNORECASE)
            if membership:
                tagged = membership.group(1).lower() == "tagged"
                for name in _expand_interfaces(membership.group(2)):
                    interface = get_interface(name)
                    if tagged:
                        interface.tagged_vlans = sorted(
                            {*interface.tagged_vlans, current_vlan}
                        )
                    else:
                        interface.untagged_vlan = current_vlan
                continue

        if not current_interfaces:
            continue

        lowered = line.lower()
        description = re.fullmatch(r"(?:description|name)\s+[\"']?(.+?)[\"']?", line, re.IGNORECASE)
        if description:
            for name in current_interfaces:
                get_interface(name).description = description.group(1)
        elif lowered in {"shutdown", "disable"}:
            for name in current_interfaces:
                get_interface(name).enabled = False
        elif lowered in {"no shutdown", "enable"}:
            for name in current_interfaces:
                get_interface(name).enabled = True
        else:
            access = re.fullmatch(r"vlan\s+access\s+(\d+)", line, re.IGNORECASE)
            native = re.fullmatch(r"vlan\s+trunk\s+native\s+(\d+)", line, re.IGNORECASE)
            allowed = re.fullmatch(r"vlan\s+trunk\s+allowed\s+(.+)", line, re.IGNORECASE)
            if access or native:
                vlan_id = int((access or native).group(1))
                for name in current_interfaces:
                    get_interface(name).untagged_vlan = vlan_id
            elif allowed:
                vlan_ids = _expand_number_list(allowed.group(1))
                for name in current_interfaces:
                    get_interface(name).tagged_vlans = vlan_ids

    for interface in interfaces.values():
        if interface.tagged_vlans:
            interface.mode = InterfaceMode.TAGGED
        elif interface.untagged_vlan is not None:
            interface.mode = InterfaceMode.ACCESS

    return sorted(interfaces.values(), key=lambda item: _natural_key(item.name))


def _natural_key(value: str) -> list[tuple[int, int | str]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value)
    ]
