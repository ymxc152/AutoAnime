"""Network helpers for loopback trust decisions."""

import ipaddress


def is_loopback_host(host):
    """Return True when the client address is IPv4/IPv6 loopback (incl. IPv4-mapped)."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(str(host).split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)
