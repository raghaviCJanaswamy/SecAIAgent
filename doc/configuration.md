# Configuration Reference

All configuration is read from environment variables. In local development, values are loaded from a `.env` file. In Kubernetes, they are injected via `ConfigMap` (non-sensitive) and `Secret` (sensitive).

Use [.env.example](../.env.example) as the starting template.

---

## Required

| Variable | Description |
|---|---|
| `GITHUB_TOKEN` | GitHub personal access token (or GitHub App token). Must have **Issues: Read & Write** and **Contents: Read-only** permissions on the target repositories. |

---

## Webhook Security

| Variable | Default | Description |
|---|---|---|
| `XRAY_WEBHOOK_SECRET` | _(empty)_ | Shared secret configured in JFrog Xray's webhook settings. Used to verify the `X-JFrog-Event-Auth: SHA256=<digest>` header on incoming requests. If empty, signature verification is skipped — **only acceptable in local development**. |

---

## JFrog Xray REST API

These are optional. The webhook payload from Xray usually contains enough information. The REST API client (`integrations/xray_client.py`) is available for additional CVE enrichment if needed.

| Variable | Default | Description |
|---|---|---|
| `JFROG_URL` | _(empty)_ | Base URL of your JFrog instance, e.g. `https://yourcompany.jfrog.io` |
| `JFROG_USERNAME` | _(empty)_ | JFrog username for basic auth |
| `JFROG_PASSWORD` | _(empty)_ | JFrog password for basic auth |
| `JFROG_API_KEY` | _(empty)_ | JFrog API key — alternative to username/password. Takes precedence if set. |

---

## GitHub Issue Settings

| Variable | Default | Description |
|---|---|---|
| `ISSUE_LABEL` | `security` | Label applied to every issue the agent creates. The label is created in the repository if it doesn't exist. |
| `ISSUE_LABEL_AUTOFIX` | `copilot-autofix` | Second label that signals GitHub Copilot to suggest a fix. The label is created in the repository if it doesn't exist. |
| `ISSUE_ASSIGNEES` | _(empty)_ | Comma-separated list of GitHub usernames to auto-assign to created issues, e.g. `alice,bob`. |

---

## Agent Behaviour

| Variable | Default | Description |
|---|---|---|
| `MIN_SEVERITY` | `Medium` | Minimum Xray severity level to act on. Accepted values: `Low`, `Medium`, `High`, `Critical`. Violations below this threshold are silently ignored. |
| `MANIFEST_PATHS` | `requirements.txt,pyproject.toml,Pipfile` | Ordered list of manifest file paths to scan in the target repository. The agent checks them in order and raises an issue for each one where the vulnerable package is found. Override with a comma-separated string to restrict or expand the list. |

---

## Kubernetes ConfigMap vs Secret split

The [k8s/configmap.yaml](../k8s/configmap.yaml) holds non-sensitive values that are safe to commit:

```yaml
ISSUE_LABEL: "security"
ISSUE_LABEL_AUTOFIX: "copilot-autofix"
MIN_SEVERITY: "Medium"
```

The [k8s/secret.yaml](../k8s/secret.yaml) holds sensitive values that must **never** be committed with real values:

```yaml
GITHUB_TOKEN
XRAY_WEBHOOK_SECRET
JFROG_URL
JFROG_USERNAME
JFROG_PASSWORD
```

In production, populate the Secret via AWS Secrets Manager + External Secrets Operator rather than the YAML file. See [deployment.md](deployment.md) for details.

---

## Example `.env` (local development)

```env
# Required
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# Webhook security (leave empty to skip verification locally)
XRAY_WEBHOOK_SECRET=

# JFrog (optional for local testing)
JFROG_URL=
JFROG_USERNAME=
JFROG_PASSWORD=

# Issue settings
ISSUE_LABEL=security
ISSUE_LABEL_AUTOFIX=copilot-autofix
ISSUE_ASSIGNEES=

# Agent behaviour
MIN_SEVERITY=Medium
```
