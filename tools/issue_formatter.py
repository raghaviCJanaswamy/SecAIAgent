"""Format a GitHub Issue body that gives GitHub Copilot maximum context to fix the vulnerability.

The issue body follows a structured template so Copilot Autofix can:
1. Understand exactly which package/version is vulnerable
2. Find the manifest file(s) that need updating
3. Know the target version to bump to
4. See the exact diff it needs to produce

Copilot reads the issue body when generating a fix suggestion. The richer
and more precise the context, the more accurate the Copilot suggestion.
"""
from __future__ import annotations

from packaging.version import Version


_SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}

_BUMP_TYPE_LABELS = {
    "patch": "Patch bump — low risk",
    "minor": "Minor bump — low–medium risk",
    "major": "Major bump — review breaking changes before merging",
    "unknown": "Version change — review carefully",
}


def _bump_type(current: str, target: str) -> str:
    """Classify the version jump as patch / minor / major."""
    try:
        c = Version(current)
        t = Version(target)
        if t.major > c.major:
            return "major"
        if t.minor > c.minor:
            return "minor"
        if t.micro > c.micro:
            return "patch"
        return "patch"
    except Exception:
        return "unknown"


def format_issue_title(cve_id: str, package: str) -> str:
    return f"[Security] {cve_id}: {package} vulnerable — Copilot fix requested"


def format_issue_body(
    cve_id: str,
    package: str,
    current_version: str,
    fixed_version: str,
    severity: str,
    manifest_file: str,
    manifest_snippet: str,
    fixed_manifest_snippet: str,
    description: str,
    summary: str,
    xray_artifact: str = "",
    nvd_url: str = "",
) -> str:
    sev_lower = severity.lower()
    sev_emoji = _SEVERITY_EMOJI.get(sev_lower, "⚪")
    bump = _bump_type(current_version, fixed_version)
    bump_label = _BUMP_TYPE_LABELS.get(bump, _BUMP_TYPE_LABELS["unknown"])

    nvd_section = ""
    if nvd_url:
        nvd_section = f"\n- **NVD Reference**: {nvd_url}"

    artifact_section = ""
    if xray_artifact:
        artifact_section = f"\n- **Affected Artifact**: `{xray_artifact}`"

    return f"""## {sev_emoji} {cve_id} — `{package}` is vulnerable

> **Detected by**: JFrog Xray
> **Severity**: {severity.upper()}
> **Package**: `{package}`
> **Current version**: `{current_version}`
> **Fixed version**: `{fixed_version}` ({bump_label})

---

### Summary

{summary or description or "No description provided by Xray."}

### References
- **CVE**: [{cve_id}](https://www.cve.org/CVERecord?id={cve_id}){nvd_section}{artifact_section}

---

### What needs to change

**File**: `{manifest_file}`

Bump `{package}` from `{current_version}` to `{fixed_version}`.

**Current**:
```
{manifest_snippet.strip()}
```

**Should become**:
```
{fixed_manifest_snippet.strip()}
```

---

### Instructions for GitHub Copilot

@github-copilot Please fix this vulnerability by updating `{manifest_file}`:

1. Find the line declaring `{package}` (currently pinned to `{current_version}`)
2. Change the version to `{fixed_version}`
3. Do not change any other dependencies
4. Open a pull request with the title: `fix: bump {package} from {current_version} to {fixed_version} ({cve_id})`
5. In the PR description, note this resolves issue #{'{'}issue_number{'}'} and closes this security finding

> **Note for reviewers**: This is a {bump} version bump. {_BUMP_TYPE_LABELS[bump]}.
> Please verify the application still passes tests before merging.

---

*This issue was automatically raised by the JFrog Xray AI Agent.*
*Labels: `{settings_placeholder_label}` `{settings_placeholder_autofix}`*
"""


def format_issue_body_multi(
    vulnerabilities: list[dict],
    manifest_findings: list[dict],
) -> str:
    """Format a single issue covering multiple CVEs affecting the same package.

    vulnerabilities: list of normalised Xray vuln dicts
    manifest_findings: list of {manifest_file, current_version, fixed_version, snippet, fixed_snippet}
    """
    if not vulnerabilities:
        return ""

    first = vulnerabilities[0]
    package = first["package"]
    severity = max(
        (v["severity"] for v in vulnerabilities),
        key=lambda s: {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(s.lower(), 0),
    )
    sev_emoji = _SEVERITY_EMOJI.get(severity.lower(), "⚪")
    cve_ids = [v["cve_id"] for v in vulnerabilities]
    cve_list = "\n".join(f"- `{c}`" for c in cve_ids)

    manifest_blocks = ""
    for mf in manifest_findings:
        bump = _bump_type(mf["current_version"], mf["fixed_version"])
        bump_label = _BUMP_TYPE_LABELS.get(bump, _BUMP_TYPE_LABELS["unknown"])
        manifest_blocks += f"""
#### `{mf['manifest_file']}`

Bump `{package}` from `{mf['current_version']}` to `{mf['fixed_version']}` ({bump_label})

**Current**:
```
{mf['snippet'].strip()}
```

**Should become**:
```
{mf['fixed_snippet'].strip()}
```
"""

    cve_links = "\n".join(
        f"- [{c}](https://www.cve.org/CVERecord?id={c})" for c in cve_ids
    )

    copilot_instructions = "\n".join(
        f"   - In `{mf['manifest_file']}`: change `{package}=={mf['current_version']}` "
        f"to `{package}=={mf['fixed_version']}`"
        for mf in manifest_findings
    )

    return f"""## {sev_emoji} {len(cve_ids)} CVE(s) in `{package}` — Copilot fix requested

> **Detected by**: JFrog Xray
> **Severity**: {severity.upper()}
> **Package**: `{package}`
> **CVEs addressed**: {", ".join(f"`{c}`" for c in cve_ids)}

---

### Vulnerabilities

{cve_list}

### References

{cve_links}

---

### What needs to change

{manifest_blocks}

---

### Instructions for GitHub Copilot

@github-copilot Please fix these vulnerabilities:

1. Update the following files:
{copilot_instructions}
2. Do not change any other dependencies
3. Open a pull request with the title: `fix: patch {package} — resolves {", ".join(cve_ids)}`
4. Reference this issue number in the PR description

> Please verify tests pass before merging.

---

*This issue was automatically raised by the JFrog Xray AI Agent.*
"""


# Placeholder replaced at call-site — avoids circular import with config
settings_placeholder_label = "security"
settings_placeholder_autofix = "copilot-autofix"
