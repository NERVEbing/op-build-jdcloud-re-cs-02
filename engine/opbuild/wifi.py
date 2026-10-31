"""JDCloud RE-CS-02 wireless facts, wifi parameter checks and isolation rules."""

from __future__ import annotations

import ipaddress
import re

from .common import OpError

# Read from the device (r0-eb3c2fea, 2026-10-31) with "iw phy <phy> channels/info", CN domain.
RADIOS = {
    "radio0": {"channels": (149, 153, 157, 161, 165), "max_width": 80},
    "radio1": {"channels": tuple(range(1, 14)), "max_width": 40},
    "radio2": {"channels": (36, 40, 44, 48, 52, 56, 60, 64), "max_width": 160},
}
# Channel 165 has no 40 MHz partner in the CN regulatory domain.
TWENTY_MHZ_ONLY = {("radio0", 165)}
RESERVED_NETWORKS = {"lan", "wan", "wan6", "loopback"}
# 50-op-wifi serves DHCP from host .100 with a limit of 100 addresses.
DHCP_POOL = range(100, 200)
# Pre-shared keys are 8-63 printable ASCII characters; pure SAE only needs 8.
PSK_ENCRYPTIONS = {"psk2", "psk-mixed", "sae-mixed"}


def check(params: dict, lan_ip: str) -> None:
    """Cross-field checks that the parameter schema cannot express."""
    errors: list[str] = []
    for radio, settings in params.get("radios", {}).items():
        facts = RADIOS[radio]
        channel = settings.get("channel")
        if channel is not None and channel not in facts["channels"]:
            choices = ", ".join(map(str, facts["channels"]))
            errors.append(f"wifi.radios.{radio}.channel: {channel} is not available on {radio} ({choices})")
        htmode = settings.get("htmode")
        if htmode is not None:
            width = int(htmode[2:])
            if width > facts["max_width"]:
                errors.append(f"wifi.radios.{radio}.htmode: {radio} supports up to HE{facts['max_width']}")
            elif (radio, channel) in TWENTY_MHZ_ONLY and width != 20:
                errors.append(f"wifi.radios.{radio}.htmode: channel {channel} supports HE20 only")

    networks = params.get("networks", {})
    taken = {"lan": ipaddress.IPv4Interface(f"{lan_ip}/24").network}
    for name, network in networks.items():
        where = f"wifi.networks.{name}"
        if name in RESERVED_NETWORKS:
            errors.append(f"{where}: name is reserved")
        interface = ipaddress.IPv4Interface(network["subnet"])
        if interface.network.prefixlen != 24:
            errors.append(f"{where}.subnet: must be a /24 to match the DHCP pool .100-.199")
            continue
        host = int(interface.ip) - int(interface.network.network_address)
        if host in (0, 255):
            errors.append(f"{where}.subnet: gateway must not be the network or broadcast address")
        elif host in DHCP_POOL:
            errors.append(f"{where}.subnet: gateway must be outside the DHCP pool .100-.199")
        for other, used in taken.items():
            if interface.network.overlaps(used):
                errors.append(f"{where}.subnet: overlaps {other} ({used})")
        taken[name] = interface.network

    names: set[str] = set()
    for index, ap in enumerate(params.get("ap", [])):
        where = f"wifi.ap[{index}]"
        name = ap["name"]
        if name in names:
            errors.append(f"{where}.name: duplicate access point name {name}")
        if re.fullmatch(r"radio[0-9]+", name):
            errors.append(f"{where}.name: {name} is reserved for wireless devices")
        names.add(name)
        if ap["network"] != "lan" and ap["network"] not in networks:
            errors.append(f"{where}.network: {ap['network']} is neither lan nor a defined network")
        encryption, key = ap["encryption"], ap.get("key")
        if encryption == "none":
            if key is not None:
                errors.append(f"{where}.key: must be omitted when encryption is none")
        elif key is None:
            errors.append(f"{where}.key: required for {encryption}")
        elif encryption in PSK_ENCRYPTIONS:
            if not 8 <= len(key) <= 63 or not all(" " <= char <= "~" for char in key):
                errors.append(f"{where}.key: must be 8-63 printable ASCII characters for {encryption}")
        elif len(key) < 8:
            errors.append(f"{where}.key: must be at least 8 characters for {encryption}")
    if errors:
        raise OpError("invalid wifi parameters:\n  " + "\n  ".join(errors))


def isolation_rules(name: str, gateway: str) -> str:
    """nftables chains that restrict an internet-only network to DHCP, DNS and WAN."""
    bridge = f"br-{name}"
    return f"""# Run before fw4 and custom accept rules at priority 0.
chain {name}_pre_input {{
    type filter hook input priority -1; policy accept;

    iifname "{bridge}" meta nfproto ipv6 counter drop
    iifname "{bridge}" udp dport 67 counter accept
    iifname "{bridge}" ip daddr {gateway} meta l4proto {{ tcp, udp }} th dport 53 counter accept
    iifname "{bridge}" counter drop
}}

chain {name}_pre_forward {{
    type filter hook forward priority -1; policy accept;

    iifname "{bridge}" meta nfproto ipv6 counter drop
    oifname "{bridge}" meta nfproto ipv6 counter drop
    # Block private and non-public destinations even when routed through WAN.
    iifname "{bridge}" ip daddr {{
        0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8,
        169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16,
        198.18.0.0/15, 224.0.0.0/3
    }} counter drop
    iifname "{bridge}" oifname != {{ "wan", "pppoe-wan" }} counter drop
    oifname "{bridge}" iifname != {{ "wan", "pppoe-wan" }} counter drop
}}
"""
