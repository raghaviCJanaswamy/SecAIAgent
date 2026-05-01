"""JFrog Xray REST API client.

Fetches active violations/vulnerabilities and normalises them into a flat list
of dicts that the agent can work with.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

_VIOLATIONS_PATH = "/xray/api/v1/violations"
_SUMMARY_PATH = "/xray/api/v2/summary/artifact"


def _auth_headers() -> dict[str, str]:
    if settings.jfrog_api_key:
        return {"X-JFrog-Art-Api": settings.jfrog_api_key}
    return {}


def _auth_tuple() -> tuple[str, str] | None:
    if settings.jfrog_username and settings.jfrog_password:
        return (settings.jfrog_username, settings.jfrog_password)
    return None


def _normalize_violation(v: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a single Xray violation record into one or more normalised CVE dicts."""
    results: list[dict[str, Any]] = []
    severity = v.get("severity", "Unknown")
    for issue in v.get("issues", [v]):
        cve_ids = [c.get("cve") for c in issue.get("cves", []) if c.get("cve")]
        if not cve_ids:
            cve_ids = [issue.get("issue_id", "UNKNOWN")]
        for component in issue.get("components", [{}]):
            pkg = component.get("package_name") or component.get("component_id", "")
            version = component.get("package_version") or component.get("component_version", "")
            fixed_versions = component.get("fixed_versions") or issue.get("fixed_versions") or []
            for cve_id in cve_ids:
                results.append(
                    {
                        "cve_id": cve_id,
                        "package": pkg,
                        "version": version,
                        "severity": severity,
                        "fixed_versions": fixed_versions,
                        "description": issue.get("description", ""),
                        "summary": issue.get("summary", ""),
                    }
                )
    return results


class XrayClient:
    def __init__(self) -> None:
        self._base = settings.jfrog_url.rstrip("/")
        self._headers = _auth_headers()
        self._auth = _auth_tuple()

    def _get(self, path: str, **kwargs: Any) -> Any:
        url = self._base + path
        response = httpx.get(
            url,
            headers=self._headers,
            auth=self._auth,
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, json: Any, **kwargs: Any) -> Any:
        url = self._base + path
        response = httpx.post(
            url,
            headers=self._headers,
            auth=self._auth,
            json=json,
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response.json()

    def fetch_violations(
        self,
        filters: dict[str, Any] | None = None,
        page_num: int = 1,
        num_of_rows: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch active violations from Xray, return normalised vulnerability list."""
        payload: dict[str, Any] = {
            "filters": filters or {"min_severity": "Medium"},
            "pagination": {"order_by": "severity", "direction": "desc", "page_num": page_num, "num_of_rows": num_of_rows},
        }
        try:
            data = self._post(_VIOLATIONS_PATH, json=payload)
        except httpx.HTTPStatusError as exc:
            logger.error("Xray API error %s: %s", exc.response.status_code, exc.response.text)
            raise

        raw_violations: list[dict] = data.get("violations", [])
        logger.info("Fetched %d violations from Xray", len(raw_violations))

        normalised: list[dict[str, Any]] = []
        for v in raw_violations:
            normalised.extend(_normalize_violation(v))

        # Deduplicate by (cve_id, package, version)
        seen: set[tuple] = set()
        unique: list[dict[str, Any]] = []
        for item in normalised:
            key = (item["cve_id"], item["package"], item["version"])
            if key not in seen:
                seen.add(key)
                unique.append(item)

        logger.info("Normalised to %d unique vulnerability entries", len(unique))
        return unique

    def fetch_artifact_summary(self, path: str) -> dict[str, Any]:
        """Fetch vulnerability summary for a specific artifact path."""
        data = self._post(_SUMMARY_PATH, json={"paths": [path]})
        return data
