# API Reference

Base URL (local): `http://localhost:8080`
Base URL (production): `https://xray-agent.internal.yourcompany.com`

Interactive Swagger UI is available at `/docs` (disabled in production by default).

---

## GET /health

Liveness and readiness probe. Returns `200 OK` if the service is running.

**Response**

```json
{ "status": "ok" }
```

Used by:
- Kubernetes liveness and readiness probes
- AWS ALB health check (`/health`)

---

## POST /webhook/xray

Receive a JFrog Xray violation webhook event, process it, and open GitHub Issues.

Returns `200 OK` after the pipeline completes (synchronous).

**Request headers**

| Header | Required | Description |
|---|---|---|
| `Content-Type` | Yes | Must be `application/json` |
| `X-Repo-Owner` | Yes | GitHub organisation or username that owns the target repository |
| `X-Repo-Name` | Yes | GitHub repository name to raise issues in |
| `X-Base-Branch` | No | Branch to scan manifest files on. Defaults to `main` |
| `X-JFrog-Event-Auth` | No* | HMAC-SHA256 signature in format `SHA256=<hex_digest>`. Required when `XRAY_WEBHOOK_SECRET` is configured. |

**Request body**

Standard JFrog Xray violation webhook payload (JSON). See [xray-webhook-setup.md](xray-webhook-setup.md) for the supported payload formats.

**Response body**

```json
{
  "status": "processed",
  "repo": "my-org/my-repo",
  "issues_created": 2,
  "issue_urls": [
    "https://github.com/my-org/my-repo/issues/42",
    "https://github.com/my-org/my-repo/issues/43"
  ],
  "skipped": 1,
  "errors": []
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"processed"` on success |
| `repo` | string | `owner/name` of the target repository |
| `issues_created` | integer | Number of GitHub Issues opened in this run |
| `issue_urls` | string[] | URLs of all created issues |
| `skipped` | integer | Packages skipped (already fixed, or not found in manifests) |
| `errors` | string[] | Non-fatal errors (e.g. PyPI lookup failed for a package) |

**Error responses**

| Status | Cause |
|---|---|
| `400 Bad Request` | Missing `X-Repo-Owner` or `X-Repo-Name` header |
| `401 Unauthorized` | HMAC signature invalid or missing (when `XRAY_WEBHOOK_SECRET` is set) |
| `422 Unprocessable Entity` | Malformed JSON body |
| `500 Internal Server Error` | Unhandled exception — check server logs |

---

## POST /webhook/xray/async

Identical to `POST /webhook/xray` but returns `202 Accepted` immediately and processes the event in a background thread.

Use this endpoint if your JFrog instance enforces a webhook response timeout shorter than the agent's typical processing time.

**Request headers and body**: identical to `POST /webhook/xray`

**Response** (`202 Accepted`)

```json
{
  "status": "accepted",
  "repo": "my-org/my-repo",
  "message": "Webhook accepted. Processing in background — check server logs."
}
```

Processing results are written to server logs only. If you need structured results, use the synchronous endpoint.

---

## Pipeline processing logic (for both endpoints)

1. **Signature verification** — validates HMAC-SHA256 if `XRAY_WEBHOOK_SECRET` is set
2. **Payload parsing** — normalises Xray violation JSON to internal `VulnEntry` list
3. **Severity filter** — drops entries below `MIN_SEVERITY`
4. **Package grouping** — groups all CVEs for the same package into one issue
5. **Version resolution** — queries PyPI to find the lowest stable version ≥ the Xray fixed version
6. **Version guard** — skips packages where the current version is already ≥ the fixed version
7. **Manifest scan** — reads the target repo's manifest files via GitHub API; skips packages not declared in any manifest
8. **Issue creation** — opens a GitHub Issue (idempotent — skips if an open issue with the same title already exists)

---

## Idempotency

Both endpoints are safe to call multiple times for the same Xray event. The `create_issue` step checks for an existing open issue with the same title before creating a new one. Re-triggering for the same CVE and package will return `issues_created: 0, skipped: 1`.
