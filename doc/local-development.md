# Local Development

## Prerequisites

| Tool | Minimum version | Purpose |
|---|---|---|
| Python | 3.11 | Runtime |
| uv | any | Fast package manager (or use pip) |
| Docker Desktop | 4.x | Container testing |
| Git | any | Source control |
| A GitHub account | — | Issue creation target |
| JFrog Xray instance | — | Webhook source (or use curl to simulate) |

---

## Option A — Run directly with Python (fastest for development)

### 1. Clone and create a virtual environment

```bash
cd AIAgent-xray
uv venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
GITHUB_TOKEN=ghp_your_token_here
XRAY_WEBHOOK_SECRET=          # leave empty to skip signature check locally
JFROG_URL=                    # optional for local testing
MIN_SEVERITY=Medium
ISSUE_LABEL=security
ISSUE_LABEL_AUTOFIX=copilot-autofix
```

The `GITHUB_TOKEN` must have:
- **Repository permissions** → Issues: Read & Write
- **Repository permissions** → Contents: Read-only (to read manifest files)

### 3. Start the server

```bash
uvicorn api.main:app --reload --port 8080
```

The server starts at `http://localhost:8080`.

- Swagger UI: `http://localhost:8080/docs`
- Health check: `http://localhost:8080/health`

### 4. Test with a simulated webhook

```bash
curl -X POST http://localhost:8080/webhook/xray \
  -H "Content-Type: application/json" \
  -H "X-Repo-Owner: raghaviCJanaswamy" \
  -H "X-Repo-Name: pygoat" \
  -H "X-Base-Branch: master" \
  -d '{
    "violations": [{
      "severity": "High",
      "issues": [{
        "issue_id": "CVE-2023-32681",
        "summary": "Requests library forwards proxy auth credentials to destination server",
        "description": "Unintended proxy-authorization header leak",
        "cves": [{"cve": "CVE-2023-32681"}],
        "fixed_versions": ["2.31.0"],
        "components": [{
          "package_name": "requests",
          "package_version": "2.28.0",
          "fixed_versions": ["2.31.0"]
        }]
      }]
    }]
  }'
```

Expected response:

```json
{
  "status": "processed",
  "repo": "your-github-org/your-repo",
  "issues_created": 1,
  "issue_urls": ["https://github.com/your-github-org/your-repo/issues/42"],
  "skipped": 0,
  "errors": []
}
```

---

## Option B — Run with Docker Compose (mirrors production)

### 1. Configure environment

```bash
cp .env.example .env
# Fill in GITHUB_TOKEN at minimum
```

### 2. Build and start

```bash
docker compose up --build
```

The container mounts your local source files and enables `--reload`, so code changes are picked up without restarting.

### 3. Verify

```bash
curl http://localhost:8080/health
# {"status":"ok"}
```

### 4. View logs

```bash
docker compose logs -f xray-agent
```

### 5. Stop

```bash
docker compose down
```

---

## Project structure

```
AIAgent-xray/
├── agent/
│   └── pipeline.py          Core webhook-to-issue logic
├── api/
│   └── main.py              FastAPI app (webhook endpoint)
├── integrations/
│   ├── github_client.py     PyGitHub wrapper
│   └── xray_client.py       Xray REST API client
├── tools/
│   ├── dependency_parser.py Manifest file parser
│   ├── issue_formatter.py   GitHub Issue body builder
│   └── version_resolver.py  PyPI version lookup
├── k8s/                     EKS Kubernetes manifests
├── config.py                Pydantic-settings
├── Dockerfile               Production container image
├── docker-compose.yml       Local development setup
├── requirements.txt         Python dependencies
└── .env.example             Environment variable template
```

---

## Common development tasks

### Check which packages the agent would act on for a given manifest

```python
from tools.dependency_parser import parse_requirements_txt
with open("path/to/requirements.txt") as f:
    pkgs = parse_requirements_txt(f.read())
print(pkgs)  # {"requests": "2.28.0", "flask": "2.2.5", ...}
```

### Resolve the safe fixed version for a package

```python
from tools.version_resolver import resolve_safe_version
print(resolve_safe_version("requests", "2.31.0"))
# "2.31.0"
```

### Preview the GitHub Issue body that would be created

```python
from tools.issue_formatter import format_issue_body_multi
body = format_issue_body_multi(
    vulnerabilities=[{
        "cve_id": "CVE-2023-32681",
        "package": "requests",
        "severity": "High",
        "description": "Proxy auth header leak",
        "summary": "requests leaks proxy credentials",
    }],
    manifest_findings=[{
        "manifest_file": "requirements.txt",
        "current_version": "2.28.0",
        "fixed_version": "2.31.0",
        "snippet": "requests==2.28.0",
        "fixed_snippet": "requests==2.31.0",
    }],
)
print(body)
```

---

## Debugging tips

| Symptom | Check |
|---|---|
| `401 Invalid webhook signature` | Set `XRAY_WEBHOOK_SECRET=` (empty) to skip verification locally |
| `400 Missing required headers` | Add `X-Repo-Owner` and `X-Repo-Name` headers to your curl request |
| Issue not created — `skipped: 1` | Package is not found in any manifest file in the repo, or current version is already >= fixed version |
| `errors: ["requests: could not resolve a fixed version"]` | The package name doesn't match PyPI exactly, or fixed_versions list is empty in the Xray payload |
| `ValidationError: github_token field required` | `.env` file missing or `GITHUB_TOKEN` not set |
