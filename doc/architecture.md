# Architecture

## Overview

The JFrog Xray AI Agent is a lightweight webhook receiver that bridges JFrog Xray vulnerability scanning and GitHub Copilot-driven auto-remediation. It requires no external LLM API — no AWS Bedrock, no Anthropic, no OpenAI. GitHub Copilot acts on GitHub Issues natively inside GitHub.

## End-to-End Flow

```
┌─────────────────────┐
│   JFrog Xray        │
│  (Policy Watch /    │
│   Artifact Scan)    │
└──────────┬──────────┘
           │  HTTPS POST (JSON payload)
           │  Header: X-JFrog-Event-Auth: SHA256=<hmac>
           │  Header: X-Repo-Owner: <org>
           │  Header: X-Repo-Name: <repo>
           ▼
┌─────────────────────────────────────────────────────────────┐
│                  xray-agent (FastAPI / Python)               │
│                                                             │
│  POST /webhook/xray                                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  1. Verify HMAC-SHA256 signature                    │   │
│  │  2. Parse + normalise Xray violation payload        │   │
│  │  3. Filter by MIN_SEVERITY (default: Medium)        │   │
│  │  4. Group CVEs by affected package                  │   │
│  │  5. Resolve safe fixed version via PyPI API         │   │
│  │  6. Scan repo manifest files via GitHub API         │   │
│  │     (requirements.txt / pyproject.toml / Pipfile)  │   │
│  │  7. Build structured issue body with exact diff     │   │
│  │     and @github-copilot fix instructions            │   │
│  │  8. Open GitHub Issue (idempotent)                  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
           │
           │  GitHub API  (Issues write)
           ▼
┌─────────────────────┐
│   GitHub Issue      │
│   Labels:           │
│   • security        │
│   • copilot-autofix │
└──────────┬──────────┘
           │  Copilot reads issue body
           ▼
┌─────────────────────┐
│  GitHub Copilot     │
│  suggests PR fix    │
│  (version bump in   │
│   manifest file)    │
└──────────┬──────────┘
           │  PR reviewed & merged by dev team
           ▼
┌─────────────────────┐
│  Vulnerability      │
│  resolved           │
└─────────────────────┘
```

## Design Constraints

| Constraint | Decision |
|---|---|
| No AWS Bedrock | No LLM calls made anywhere in the agent |
| No Copilot API access | Copilot acts on GitHub Issues natively — no API integration needed |
| Approved runtime | Amazon EKS on AWS |
| LLM for fix suggestions | GitHub Copilot reads the issue body and proposes the PR |

## Component Map

```
AIAgent-xray/
├── api/
│   └── main.py              FastAPI app — webhook receiver, HMAC verification
├── agent/
│   └── pipeline.py          Core deterministic pipeline (no LLM)
├── integrations/
│   ├── xray_client.py       JFrog Xray REST API client (optional enrichment)
│   └── github_client.py     GitHub API — read manifests, create issues
├── tools/
│   ├── dependency_parser.py Parse requirements.txt / pyproject.toml / Pipfile
│   ├── version_resolver.py  PyPI version lookup (find safe fixed version)
│   └── issue_formatter.py   Build structured GitHub Issue body for Copilot
├── config.py                Pydantic-settings — all config from env vars
├── Dockerfile               Multi-stage, non-root, production image
├── docker-compose.yml       Local Docker Desktop development setup
└── k8s/                     EKS production Kubernetes manifests
```

## Pipeline Detail (`agent/pipeline.py`)

```
parse_xray_payload()
    │  Normalises both Xray webhook shapes:
    │  • policy-violation  {"violations": [...]}
    │  • artifact-scan     {"issues": [...]}
    │  Strips ecosystem prefix (pip://requests:2.27.0 → requests)
    │  Deduplicates by (cve_id, package)
    ▼
severity filter
    │  Drops entries below MIN_SEVERITY (default: Medium)
    │  Ranks: Critical=4 High=3 Medium=2 Low=1
    ▼
group by package
    │  All CVEs for the same package → one GitHub Issue
    ▼
pick_best_fixed_version()   [tools/version_resolver.py]
    │  Queries PyPI JSON API for all published versions
    │  Returns lowest stable version ≥ any fixed_version from Xray
    │  Skips pre-releases and dev releases
    ▼
version already safe?
    │  If current_version ≥ fixed_version → skip (logged)
    ▼
_find_in_manifests()
    │  Reads each manifest file from the GitHub repo
    │  Parses with dependency_parser
    │  Builds before/after snippet for the issue body
    │  Skips manifests where the package is not declared
    ▼
format_issue_body_multi()   [tools/issue_formatter.py]
    │  Structured Markdown body with:
    │  • Severity badge, CVE links
    │  • Exact before/after diff block
    │  • @github-copilot explicit fix instructions
    │  • PR title template for Copilot to use
    ▼
create_issue()              [integrations/github_client.py]
    │  Idempotent: skips if open issue with same title exists
    │  Ensures labels exist (creates if missing)
    │  Assigns to configured GitHub usernames
    ▼
PipelineResult
    • issues_created: list of IssueResult
    • skipped:        packages already fixed / not in manifests
    • errors:         PyPI lookup failures, GitHub API errors
```

## Idempotency

The pipeline is safe to call repeatedly for the same Xray event. Before creating an issue the agent searches open issues with the same title. If one already exists the package is skipped. This prevents duplicate issues when Xray re-fires a watch or the webhook is retried.

## Security Controls

| Control | Implementation |
|---|---|
| Webhook authenticity | HMAC-SHA256 verification of `X-JFrog-Event-Auth` header |
| Secret storage | Kubernetes Secret (production) / `.env` file (local only) |
| Container user | Non-root uid 1001, all Linux capabilities dropped |
| Read-only filesystem | Container root FS is read-only; `/tmp` uses emptyDir |
| Network | Internal ALB only (VPC CIDR restricted, HTTPS/443) |
| GitHub token scope | Minimum: `repo` Issues write + Contents read |
| No outbound LLM calls | Zero external AI API calls from the agent |
