"""Outbound webhook URL validation (SSRF guard).

Tenant-supplied webhook URLs are delivered to by the platform, so a
malicious or misconfigured endpoint must never let the platform make
requests to infrastructure-protected addresses (loopback, the metadata
endpoint 169.254.169.254, private subnets, link-local, unique-local).
"""

import ipaddress
import socket
from urllib.parse import urlparse

# Private / infrastructure address spaces that outbound webhooks must never
# be delivered to (spec 22 "Incoming webhook security": HTTPS webhook).
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),       # private
    ipaddress.ip_network("172.16.0.0/12"),    # private
    ipaddress.ip_network("192.168.0.0/16"),   # private
    ipaddress.ip_network("169.254.0.0/16"),   # link-local incl. 169.254.169.254
    ipaddress.ip_network("100.64.0.0/10"),    # carrier-grade NAT
    ipaddress.ip_network("0.0.0.0/8"),        # "this" network
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # unique-local
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
    ipaddress.ip_network("::/128"),           # IPv6 unspecified
]

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.google",
}

_ALLOWED_SCHEMES = {"https"}


class WebhookUrlError(ValueError):
    """Raised when a webhook URL fails SSRF / transport validation."""


def _address_is_private(address: ipaddress._BaseAddress) -> bool:
    return any(address in network for network in _PRIVATE_NETWORKS)


def _hostname_is_blocked(hostname: str) -> bool:
    lowered = hostname.lower()
    if lowered in _BLOCKED_HOSTNAMES:
        return True
    # DNS-search shortcuts like "internal" or single-label hosts resolve
    # unpredictably; require an explicit FQDN.
    if lowered.endswith(".local"):
        return True
    return len(lowered.split(".")) < 2


def validate_outbound_webhook_url(url: str) -> None:
    """Validate a webhook delivery URL against SSRF + transport rules.

    Raises WebhookUrlError on violation.
    """
    if not url or not isinstance(url, str):
        raise WebhookUrlError("Webhook URL is required")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise WebhookUrlError(
            f"Webhook URL scheme must be https (got {parsed.scheme!r})"
        )
    if not parsed.hostname:
        raise WebhookUrlError("Webhook URL must include a hostname")
    if parsed.username or parsed.password:
        raise WebhookUrlError("Webhook URLs must not embed credentials")

    hostname = parsed.hostname

    # Fast path: literal IP literal.
    try:
        address = ipaddress.ip_address(hostname)
        if _address_is_private(address):
            raise WebhookUrlError(
                f"Webhook URL targets a private address ({hostname})"
            )
        return
    except ValueError:
        pass

    if _hostname_is_blocked(hostname):
        raise WebhookUrlError(f"Webhook URL host {hostname!r} is not allowed")

    # Resolve hostname and reject when ANY resolved address is private.
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise WebhookUrlError(f"Webhook URL host {hostname!r} cannot be resolved")

    resolved = set()
    for _family, _type, _proto, _canonname, sockaddr in infos:
        try:
            resolved.add(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue

    if not resolved:
        raise WebhookUrlError(f"Webhook URL host {hostname!r} resolves to no addresses")

    for address in resolved:
        if _address_is_private(address):
            raise WebhookUrlError(
                f"Webhook URL host {hostname!r} resolves to a private address "
                f"({address})"
            )