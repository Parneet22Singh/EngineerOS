"""Shared Chromium launch configuration for every module that drives a browser.

Centralizes two cross-cutting concerns so no module reinvents them:

  * IPv4 pinning (see netfix) for hosts on machines with broken IPv6 routing.
  * Headed / browser-channel selection so WAF-protected sites (Akamai, Cloudflare)
    that reject headless bots can be scanned with a real, headed browser instead.

Per-scan overrides may be passed via the module's ``options`` dict
(``browser_headed`` / ``browser_channel``); otherwise the configured defaults apply.
"""
from __future__ import annotations

from app.config import Settings
from app.core.netfix import ipv4_pin_args

BASE_ARGS = ["--no-sandbox", "--disable-gpu"]


def build_launch_kwargs(settings: Settings, url: str, options: dict | None = None) -> dict:
    """Return kwargs for ``playwright.chromium.launch(**kwargs)``.

    Merges configured browser defaults with per-scan overrides and pins the target
    host to IPv4. ``options`` keys ``browser_headed`` (bool) and ``browser_channel``
    (str) take precedence over settings when present.
    """
    options = options or {}

    headed = options.get("browser_headed")
    if headed is None:
        headed = settings.browser_headed

    channel = options.get("browser_channel")
    if channel is None:
        channel = settings.browser_channel

    kwargs: dict = {
        "headless": not headed,
        "args": [*BASE_ARGS, *ipv4_pin_args(url)],
    }
    if channel:
        kwargs["channel"] = channel
    return kwargs
