"""Resolve the safest available version of a PyPI package.

Given a package name and a minimum required version (from a CVE fix),
returns the lowest published version that is >= the required version.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import httpx
from packaging.version import Version, InvalidVersion

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/{package}/json"


@lru_cache(maxsize=256)
def _fetch_pypi_versions(package: str) -> list[str]:
    """Fetch all published versions for a package from PyPI."""
    url = _PYPI_URL.format(package=package)
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        return list(data.get("releases", {}).keys())
    except httpx.HTTPError as exc:
        logger.warning("PyPI lookup failed for %s: %s", package, exc)
        return []


def resolve_safe_version(package: str, min_version: str) -> str | None:
    """Return the lowest published PyPI version >= min_version, or None."""
    raw_versions = _fetch_pypi_versions(package)
    valid: list[Version] = []
    for v in raw_versions:
        try:
            parsed = Version(v)
            if not parsed.is_prerelease and not parsed.is_devrelease:
                valid.append(parsed)
        except InvalidVersion:
            continue

    try:
        floor = Version(min_version)
    except InvalidVersion:
        logger.warning("Invalid min_version '%s' for %s", min_version, package)
        return None

    candidates = sorted(v for v in valid if v >= floor)
    if not candidates:
        logger.warning("No published version >= %s found for %s", min_version, package)
        return None

    result = str(candidates[0])
    logger.info("Resolved %s >= %s -> %s", package, min_version, result)
    return result


def pick_best_fixed_version(package: str, fixed_versions: list[str]) -> str | None:
    """Given Xray's list of fixed versions, pick the best one available on PyPI."""
    if not fixed_versions:
        return None

    best: Version | None = None
    for fv in fixed_versions:
        resolved = resolve_safe_version(package, fv)
        if resolved:
            v = Version(resolved)
            if best is None or v < best:
                best = v

    return str(best) if best else None
