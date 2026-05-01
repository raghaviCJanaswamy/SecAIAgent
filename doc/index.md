# JFrog Xray AI Agent — Documentation

Automated vulnerability remediation pipeline: JFrog Xray detects a CVE → the agent raises a structured GitHub Issue → GitHub Copilot suggests the version-bump fix → a developer reviews and merges the PR.

No external LLM API is required. No AWS Bedrock. GitHub Copilot acts on issues natively inside GitHub.

---

## Documents

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | End-to-end flow diagram, component map, pipeline detail, security controls |
| [local-development.md](local-development.md) | Running with Python or Docker Compose, simulating webhooks, debugging tips |
| [deployment.md](deployment.md) | Building the Docker image, pushing to ECR, applying Kubernetes manifests on EKS, rolling updates |
| [configuration.md](configuration.md) | Every environment variable, defaults, ConfigMap vs Secret split |
| [xray-webhook-setup.md](xray-webhook-setup.md) | Configuring JFrog Xray to send webhooks, HMAC signing, multi-repo routing, testing without Xray |
| [api-reference.md](api-reference.md) | Endpoint specs, request headers, response schemas, error codes |

---

## Quick start (local)

```bash
# 1. Install dependencies
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env: set GITHUB_TOKEN at minimum

# 3. Start
uvicorn api.main:app --reload --port 8080

# 4. Simulate an Xray webhook
curl -X POST http://localhost:8080/webhook/xray \
  -H "Content-Type: application/json" \
  -H "X-Repo-Owner: your-org" \
  -H "X-Repo-Name: your-repo" \
  -d '{
    "violations": [{
      "severity": "High",
      "issues": [{
        "issue_id": "CVE-2023-32681",
        "summary": "requests proxy auth header leak",
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

---

## Quick start (Docker Desktop)

```bash
cp .env.example .env     # fill in GITHUB_TOKEN
docker compose up --build
# Agent available at http://localhost:8080
```

---

## Production deployment (EKS)

```bash
# Build & push to ECR
docker build --platform linux/amd64 -t $ECR_REPO:2.0.0 .
docker push $ECR_REPO:2.0.0

# Apply manifests
kubectl apply -f k8s/
```

See [deployment.md](deployment.md) for the full step-by-step guide.

---

## Repository layout

```
AIAgent-xray/
├── agent/
│   └── pipeline.py          Deterministic webhook-to-issue pipeline
├── api/
│   └── main.py              FastAPI app — webhook receiver
├── integrations/
│   ├── github_client.py     GitHub API (manifest read, issue create)
│   └── xray_client.py       JFrog Xray REST API (optional enrichment)
├── tools/
│   ├── dependency_parser.py requirements.txt / pyproject.toml / Pipfile parser
│   ├── issue_formatter.py   GitHub Issue body builder (Copilot-optimised)
│   └── version_resolver.py  PyPI version lookup
├── k8s/                     EKS Kubernetes manifests
├── doc/                     This documentation
├── config.py                Pydantic-settings (all config from env)
├── Dockerfile               Multi-stage production image
├── docker-compose.yml       Local development setup
├── requirements.txt         Python dependencies (no LLM SDK)
└── .env.example             Environment variable template
```
