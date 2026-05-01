"""Deterministic Xray → GitHub Issue pipeline. No LLM API calls.

Flow per Xray webhook event:
  1. Parse + normalise the Xray violation payload
  2. Filter by minimum severity
  3. For each affected package, find which manifest files in the target repo
     contain it and determine the fixed version (via PyPI lookup)
  4. Group multiple CVEs for the same package into a single issue
  5. Open a GitHub Issue with full context so GitHub Copilot can suggest the fix
  6. Return a result summary
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from packaging.version import Version, InvalidVersion

from config import settings
from integrations.github_client import GitHubClient
from tools.dependency_parser import parse_manifest, MANIFEST_PARSERS
from tools.issue_formatter import (
    format_issue_title,
    format_issue_body_multi,
)
from tools.version_resolver import pick_best_fixed_version

logger = logging.getLogger(__name__)

_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class VulnEntry:
    cve_id: str
    package: str
    version: str
    severity: str
    fixed_versions: list[str]
    description: str
    summary: str


@dataclass
class IssueResult:
    package: str
    cve_ids: list[str]
    issue_number: int
    issue_url: str
    manifest_file: str
    current_version: str
    fixed_version: str
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class PipelineResult:
    issues_created: list[IssueResult] = field(default_factory=list)
    skipped: list[IssueResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Xray payload normalisation
# ---------------------------------------------------------------------------

def _severity_rank(s: str) -> int:
    return _SEVERITY_RANK.get(s.lower(), 0)


def _min_severity_rank() -> int:
    return _SEVERITY_RANK.get(settings.min_severity.lower(), 2)


def parse_xray_payload(payload: dict) -> list[VulnEntry]:
    """Convert raw Xray webhook JSON into a flat list of VulnEntry objects."""
    entries: list[VulnEntry] = []

    # Xray webhook wraps violations in different shapes depending on watch type.
    # Handle both the policy-violation shape and the artifact-scan shape.
    violations: list[dict] = payload.get("violations", payload.get("issues", [payload]))

    for v in violations:
        severity = (v.get("severity") or "Unknown").lower()
        for issue in v.get("issues", [v]):
            cve_list = [c.get("cve") for c in issue.get("cves", []) if c.get("cve")]
            if not cve_list:
                cve_list = [issue.get("issue_id") or issue.get("id") or "UNKNOWN"]

            components = issue.get("components") or v.get("components") or [{}]
            for comp in components:
                pkg = (
                    comp.get("package_name")
                    or comp.get("component_id", "")
                    or issue.get("package_name", "")
                )
                # Strip ecosystem prefix e.g. "pip://requests:2.27.0"
                pkg = re.sub(r"^[a-z]+://", "", pkg).split(":")[0]
                version = (
                    comp.get("package_version")
                    or comp.get("component_version", "")
                    or issue.get("version", "")
                )
                fixed = comp.get("fixed_versions") or issue.get("fixed_versions") or []

                for cve_id in cve_list:
                    entries.append(
                        VulnEntry(
                            cve_id=cve_id,
                            package=pkg.lower().strip(),
                            version=version.strip(),
                            severity=severity,
                            fixed_versions=fixed,
                            description=issue.get("description", ""),
                            summary=issue.get("summary", ""),
                        )
                    )

    # Deduplicate by (cve_id, package)
    seen: set[tuple] = set()
    unique: list[VulnEntry] = []
    for e in entries:
        key = (e.cve_id, e.package)
        if key not in seen and e.package:
            seen.add(key)
            unique.append(e)

    logger.info("Parsed %d unique vulnerability entries from Xray payload", len(unique))
    return unique


# ---------------------------------------------------------------------------
# Manifest scanning
# ---------------------------------------------------------------------------

def _find_in_manifests(
    gh: GitHubClient,
    repo,
    base_branch: str,
    package: str,
    fixed_version: str,
) -> list[dict]:
    """Scan the repo's manifest files for `package` and return finding dicts."""
    findings: list[dict] = []
    for mpath in settings.manifest_paths:
        try:
            content, _ = gh.get_file_content(repo, mpath, ref=base_branch)
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning("Could not read %s: %s", mpath, exc)
            continue

        pkg_map = parse_manifest(mpath, content)
        if package not in pkg_map:
            continue

        current = pkg_map[package]

        # Build the relevant snippet (the line containing the package)
        snippet_lines = [
            ln for ln in content.splitlines()
            if re.search(rf"(?i)\b{re.escape(package)}\b", ln)
        ]
        snippet = "\n".join(snippet_lines) or f"{package}=={current}"

        # Build what the fixed snippet should look like
        fixed_snippet = snippet.replace(current, fixed_version, 1)

        findings.append(
            {
                "manifest_file": mpath,
                "current_version": current,
                "fixed_version": fixed_version,
                "snippet": snippet,
                "fixed_snippet": fixed_snippet,
            }
        )

    return findings


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    payload: dict,
    repo_owner: str,
    repo_name: str,
    base_branch: str = "main",
) -> PipelineResult:
    """Main entry point: process one Xray webhook event for a target repo."""
    result = PipelineResult()
    min_rank = _min_severity_rank()

    # 1. Parse vulnerabilities
    vulns = parse_xray_payload(payload)

    # 2. Filter by severity
    vulns = [v for v in vulns if _severity_rank(v.severity) >= min_rank]
    if not vulns:
        logger.info(
            "No vulnerabilities at or above '%s' severity — nothing to do.",
            settings.min_severity,
        )
        return result

    # 3. Group by package so one issue covers all CVEs for the same package
    by_package: dict[str, list[VulnEntry]] = defaultdict(list)
    for v in vulns:
        by_package[v.package].append(v)

    gh = GitHubClient()
    repo = gh.get_repo(repo_owner, repo_name)
    if not base_branch:
        base_branch = repo.default_branch
    issue_labels = [settings.issue_label, settings.issue_label_autofix]

    # 4. For each package group, resolve fixed version + open issue
    for package, pkg_vulns in by_package.items():
        # Collect all fixed_versions across all CVEs for this package
        all_fixed: list[str] = []
        for v in pkg_vulns:
            all_fixed.extend(v.fixed_versions)

        fixed_version = pick_best_fixed_version(package, all_fixed)
        if not fixed_version:
            msg = f"{package}: could not resolve a fixed version from PyPI — skipping"
            logger.warning(msg)
            result.errors.append(msg)
            continue

        # Check if current version is already safe
        current_version = pkg_vulns[0].version
        try:
            if Version(current_version) >= Version(fixed_version):
                skip = IssueResult(
                    package=package,
                    cve_ids=[v.cve_id for v in pkg_vulns],
                    issue_number=0,
                    issue_url="",
                    manifest_file="",
                    current_version=current_version,
                    fixed_version=fixed_version,
                    skipped=True,
                    skip_reason=f"Current version {current_version} >= fixed {fixed_version}",
                )
                result.skipped.append(skip)
                logger.info(
                    "Skipping %s — already at %s >= %s",
                    package, current_version, fixed_version,
                )
                continue
        except InvalidVersion:
            pass

        # Find which manifest files in the repo contain this package
        findings = _find_in_manifests(gh, repo, base_branch, package, fixed_version)

        if not findings:
            msg = (
                f"{package}: not found in any manifest file "
                f"({', '.join(settings.manifest_paths)}) — skipping issue creation"
            )
            logger.info(msg)
            result.skipped.append(
                IssueResult(
                    package=package,
                    cve_ids=[v.cve_id for v in pkg_vulns],
                    issue_number=0,
                    issue_url="",
                    manifest_file="",
                    current_version=current_version,
                    fixed_version=fixed_version,
                    skipped=True,
                    skip_reason="Package not found in repo manifests",
                )
            )
            continue

        # Highest severity across all CVEs in this group
        top_severity = max(pkg_vulns, key=lambda v: _severity_rank(v.severity)).severity

        # One issue per manifest file found (usually just one, but could be more)
        for finding in findings:
            cve_ids = [v.cve_id for v in pkg_vulns]
            title = format_issue_title(
                cve_ids[0] if len(cve_ids) == 1 else f"{len(cve_ids)} CVEs",
                package,
            )
            body = format_issue_body_multi(
                vulnerabilities=[
                    {
                        "cve_id": v.cve_id,
                        "package": v.package,
                        "severity": top_severity,
                        "description": v.description,
                        "summary": v.summary,
                    }
                    for v in pkg_vulns
                ],
                manifest_findings=[finding],
            )

            try:
                issue_num, issue_url = gh.create_issue(
                    repo,
                    title=title,
                    body=body,
                    labels=issue_labels,
                )
                ir = IssueResult(
                    package=package,
                    cve_ids=cve_ids,
                    issue_number=issue_num,
                    issue_url=issue_url,
                    manifest_file=finding["manifest_file"],
                    current_version=finding["current_version"],
                    fixed_version=finding["fixed_version"],
                )
                result.issues_created.append(ir)
                logger.info(
                    "Raised issue #%d for %s (%s) in %s",
                    issue_num, package, ", ".join(cve_ids), finding["manifest_file"],
                )
            except Exception as exc:
                msg = f"Failed to create issue for {package}: {exc}"
                logger.error(msg)
                result.errors.append(msg)

    return result
