"""Secret-safe network diagnostics using the same HTTP stack as draft writes."""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

import httpx

_PUBLIC_IP_ENDPOINTS = {
    "cloudflare": "https://www.cloudflare.com/cdn-cgi/trace",
    "aws": "https://checkip.amazonaws.com/",
    "ipify": "https://api.ipify.org/",
}


def _normalise_ip(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def _parse_cloudflare_trace(value: str) -> str | None:
    for line in value.splitlines():
        if line.startswith("ip="):
            return _normalise_ip(line.removeprefix("ip="))
    return None


async def public_ip_cross_check() -> dict[str, Any]:
    """Query three independent reflectors only after explicit user opt-in."""

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:

        async def query(name: str, url: str) -> tuple[str, str | None, str | None]:
            try:
                response = await client.get(url)
                response.raise_for_status()
                value = (
                    _parse_cloudflare_trace(response.text)
                    if name == "cloudflare"
                    else _normalise_ip(response.text)
                )
                if value is None:
                    return name, None, "invalid-response"
                return name, value, None
            except (httpx.HTTPError, ValueError):
                return name, None, "unavailable"

        results = await asyncio.gather(
            *(query(name, url) for name, url in _PUBLIC_IP_ENDPOINTS.items())
        )

    observations = {name: {"ip": ip, "error": error} for name, ip, error in results}
    successful = [ip for _, ip, _ in results if ip is not None]
    unique = sorted(set(successful))
    return {
        "observations": observations,
        "consensus_ip": unique[0]
        if len(unique) == 1 and len(successful) >= 2
        else None,
        "consistent": len(unique) == 1 and len(successful) >= 2,
        "successful_checks": len(successful),
    }
