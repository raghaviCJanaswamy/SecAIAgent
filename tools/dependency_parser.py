"""Parse Python dependency manifests to extract {package: version} mappings.

Supported formats:
- requirements.txt  (pinned with ==)
- pyproject.toml    ([project].dependencies or [tool.poetry.dependencies])
- Pipfile           ([packages] section)
"""
from __future__ import annotations

import re
from typing import Any

import tomlkit


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------

_REQ_LINE = re.compile(
    r"^\s*(?P<pkg>[A-Za-z0-9_.-]+)\s*==\s*(?P<ver>[^\s;#]+)",
    re.IGNORECASE,
)


def parse_requirements_txt(content: str) -> dict[str, str]:
    """Return {package_name_lower: version} from requirements.txt content."""
    result: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        m = _REQ_LINE.match(line)
        if m:
            result[m.group("pkg").lower()] = m.group("ver")
    return result


def update_requirements_txt(content: str, fixes: dict[str, str]) -> str:
    """Return updated requirements.txt with packages bumped to new versions.

    fixes: {package_lower: new_version}
    """
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        m = _REQ_LINE.match(line)
        if m:
            pkg_lower = m.group("pkg").lower()
            if pkg_lower in fixes:
                old_ver = m.group("ver")
                new_ver = fixes[pkg_lower]
                line = line.replace(f"=={old_ver}", f"=={new_ver}", 1)
        out.append(line)
    return "".join(out)


# ---------------------------------------------------------------------------
# pyproject.toml
# ---------------------------------------------------------------------------

_PEP508_VER = re.compile(r"[><=!~^]+\s*(?P<ver>[^\s,;]+)")


def _extract_version_from_specifier(spec: str) -> str | None:
    """Extract the first version number from a PEP 508 specifier string."""
    m = _PEP508_VER.search(spec)
    return m.group("ver") if m else None


def parse_pyproject_toml(content: str) -> dict[str, str]:
    """Return {package_lower: version} from pyproject.toml content.

    Handles both PEP 621 ([project].dependencies) and Poetry
    ([tool.poetry.dependencies]).
    """
    doc = tomlkit.loads(content)
    result: dict[str, str] = {}

    # PEP 621
    pep_deps: list[str] = (
        doc.get("project", {}).get("dependencies", [])  # type: ignore[union-attr]
    )
    for dep in pep_deps:
        dep = str(dep)
        name_match = re.match(r"^([A-Za-z0-9_.-]+)", dep)
        if not name_match:
            continue
        pkg = name_match.group(1).lower()
        ver = _extract_version_from_specifier(dep[name_match.end():])
        if ver:
            result[pkg] = ver

    # Poetry
    poetry_deps: dict[str, Any] = (
        doc.get("tool", {}).get("poetry", {}).get("dependencies", {})  # type: ignore[union-attr]
    )
    for pkg, spec in poetry_deps.items():
        pkg_lower = pkg.lower()
        if isinstance(spec, str):
            ver = _extract_version_from_specifier(spec)
            if ver:
                result[pkg_lower] = ver
        elif isinstance(spec, dict):
            ver_val = spec.get("version", "")
            ver = _extract_version_from_specifier(str(ver_val))
            if ver:
                result[pkg_lower] = ver

    return result


def update_pyproject_toml(content: str, fixes: dict[str, str]) -> str:
    """Return updated pyproject.toml with packages bumped."""
    doc = tomlkit.loads(content)

    # PEP 621
    pep_deps = doc.get("project", {}).get("dependencies", [])  # type: ignore[union-attr]
    if pep_deps:
        new_deps = []
        for dep in pep_deps:
            dep_str = str(dep)
            name_m = re.match(r"^([A-Za-z0-9_.-]+)", dep_str)
            if name_m and name_m.group(1).lower() in fixes:
                pkg = name_m.group(1).lower()
                new_ver = fixes[pkg]
                dep_str = re.sub(r"[><=!~^]+\s*[^\s,;]+", f">={new_ver}", dep_str, count=1)
                new_deps.append(dep_str)
            else:
                new_deps.append(dep_str)
        doc["project"]["dependencies"] = new_deps  # type: ignore[index]

    # Poetry
    poetry_deps = doc.get("tool", {}).get("poetry", {}).get("dependencies", {})  # type: ignore[union-attr]
    for pkg in list(poetry_deps.keys()):
        if pkg.lower() in fixes:
            new_ver = fixes[pkg.lower()]
            spec = poetry_deps[pkg]
            if isinstance(spec, str):
                poetry_deps[pkg] = re.sub(r"[><=!~^]+\s*[^\s,;]+", f"^{new_ver}", spec, count=1)
            elif isinstance(spec, dict) and "version" in spec:
                spec["version"] = f"^{new_ver}"

    return tomlkit.dumps(doc)


# ---------------------------------------------------------------------------
# Pipfile
# ---------------------------------------------------------------------------

def parse_pipfile(content: str) -> dict[str, str]:
    """Return {package_lower: version} from Pipfile content."""
    doc = tomlkit.loads(content)
    result: dict[str, str] = {}
    packages: dict[str, Any] = doc.get("packages", {})  # type: ignore[assignment]
    for pkg, spec in packages.items():
        spec_str = str(spec) if not isinstance(spec, dict) else str(spec.get("version", ""))
        ver = _extract_version_from_specifier(spec_str)
        if ver:
            result[pkg.lower()] = ver
    return result


def update_pipfile(content: str, fixes: dict[str, str]) -> str:
    """Return updated Pipfile with packages bumped."""
    doc = tomlkit.loads(content)
    packages = doc.get("packages", {})  # type: ignore[union-attr]
    for pkg in list(packages.keys()):
        if pkg.lower() in fixes:
            new_ver = fixes[pkg.lower()]
            spec = packages[pkg]
            if isinstance(spec, str):
                packages[pkg] = re.sub(r"[><=!~^*]+\s*[^\s,;\"']*", f"=={new_ver}", spec, count=1)
                if packages[pkg] == spec:  # no operator found, was "*" or similar
                    packages[pkg] = f"=={new_ver}"
            elif isinstance(spec, dict) and "version" in spec:
                spec["version"] = f"=={new_ver}"
    return tomlkit.dumps(doc)


# ---------------------------------------------------------------------------
# Unified API
# ---------------------------------------------------------------------------

MANIFEST_PARSERS = {
    "requirements.txt": parse_requirements_txt,
    "pyproject.toml": parse_pyproject_toml,
    "Pipfile": parse_pipfile,
}

MANIFEST_UPDATERS = {
    "requirements.txt": update_requirements_txt,
    "pyproject.toml": update_pyproject_toml,
    "Pipfile": update_pipfile,
}


def parse_manifest(filename: str, content: str) -> dict[str, str]:
    for key, parser in MANIFEST_PARSERS.items():
        if filename.endswith(key):
            return parser(content)
    raise ValueError(f"Unsupported manifest file: {filename}")


def update_manifest(filename: str, content: str, fixes: dict[str, str]) -> str:
    for key, updater in MANIFEST_UPDATERS.items():
        if filename.endswith(key):
            return updater(content, fixes)
    raise ValueError(f"Unsupported manifest file: {filename}")
