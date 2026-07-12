"""Workaround for a broken-IPv6 host running Chromium against dual-stack sites.

Some machines have DNS-advertised IPv6 (AAAA records) but no working IPv6 route.
Chromium's resolver can try the IPv6 address, hang, and surface the whole navigation
as ``net::ERR_NAME_NOT_RESOLVED`` — even though a plain IPv4 connection to the same
host works fine. Resolving the target ourselves via an IPv4-only lookup and pinning
Chromium to that address with ``--host-resolver-rules`` sidesteps it entirely. If the
lookup fails for any reason, this returns no extra args and Chromium falls back to its
normal behavior.
"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger("engineeros.netfix")


def ipv4_pin_args(url: str) -> list[str]:
    host = urlparse(url).hostname
    if not host:
        return []
    # A bare host usually redirects to its www. variant (or vice-versa) and the crawl
    # then follows links there, so pin both so neither hits the broken IPv6 path.
    hosts = {host}
    hosts.add(host[4:] if host.startswith("www.") else f"www.{host}")

    rules: list[str] = []
    for h in hosts:
        try:
            ipv4 = socket.getaddrinfo(h, None, socket.AF_INET)[0][4][0]
        except OSError as exc:
            logger.info("IPv4 pin skipped for %s: %r", h, exc)
            continue
        rules.append(f"MAP {h} {ipv4}")
    return [f"--host-resolver-rules={','.join(rules)}"] if rules else []
