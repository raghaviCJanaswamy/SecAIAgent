# JFrog Xray Webhook Setup

This guide explains how to configure JFrog Xray to send violation events to the agent.

---

## Overview

JFrog Xray fires a webhook when a **Policy Watch** is triggered or an **Artifact Scan** completes with violations. The agent receives this event at `POST /webhook/xray` and converts the payload into GitHub Issues.

---

## Step 1 — Deploy the agent and note its URL

The agent must be reachable from your JFrog instance. In production (EKS), the ALB endpoint is:

```
https://xray-agent.internal.yourcompany.com
```

In local development you can use a tunnel tool (e.g. ngrok) to expose `localhost:8080`:

```bash
ngrok http 8080
# Forwarding: https://abc123.ngrok.io -> localhost:8080
```

---

## Step 2 — Generate a shared webhook secret

Generate a random secret that will be used to sign and verify webhook payloads:

```bash
openssl rand -hex 32
# e.g.: a3f8c2d1b4e7f09a...
```

Set this value in:
- JFrog Xray webhook config (step 3)
- Agent environment variable `XRAY_WEBHOOK_SECRET` (same value)

---

## Step 3 — Create the webhook in JFrog Xray

1. Open your JFrog Platform UI
2. Navigate to **Administration → Xray → Watches & Policies**
3. Select the Watch you want to trigger the agent, or create a new one
4. Open the Watch settings → **Webhook** tab
5. Click **Add Webhook** and fill in:

| Field | Value |
|---|---|
| **Name** | `xray-agent` (or any label) |
| **URL** | `https://xray-agent.internal.yourcompany.com/webhook/xray` |
| **Secret** | The value from Step 2 |
| **Events** | `Policy Violation` (and/or `Artifact Scan Completed`) |

6. Add the following **custom headers** (required by the agent to know which repo to act on):

| Header name | Value |
|---|---|
| `X-Repo-Owner` | Your GitHub organisation or username, e.g. `acme-corp` |
| `X-Repo-Name` | The repository to raise issues in, e.g. `backend-api` |
| `X-Base-Branch` | Branch to scan manifest files on (default: `main`) |

7. Click **Test** to send a sample payload and confirm the agent responds `200 OK`
8. Click **Save**

---

## Step 4 — Verify signature verification is active

After setting `XRAY_WEBHOOK_SECRET` in the agent, restart it and check the logs:

```
# You should NOT see this warning in production:
WARNING — XRAY_WEBHOOK_SECRET not set — skipping signature verification (dev mode)
```

If the warning is absent, HMAC-SHA256 verification is active. Any webhook request with an invalid or missing signature will be rejected with `401`.

---

## Payload formats supported

The agent handles both Xray webhook payload shapes automatically:

### Policy violation shape

```json
{
  "violations": [
    {
      "severity": "High",
      "issues": [
        {
          "issue_id": "CVE-2023-32681",
          "summary": "requests proxy auth credential leak",
          "cves": [{ "cve": "CVE-2023-32681" }],
          "fixed_versions": ["2.31.0"],
          "components": [
            {
              "package_name": "requests",
              "package_version": "2.28.0",
              "fixed_versions": ["2.31.0"]
            }
          ]
        }
      ]
    }
  ]
}
```

### Artifact scan shape

```json
{
  "issues": [
    {
      "severity": "Medium",
      "issue_id": "CVE-2024-12345",
      "summary": "...",
      "fixed_versions": ["1.2.3"],
      "components": [...]
    }
  ]
}
```

The agent normalises both shapes into the same internal format. The `pip://` ecosystem prefix in component IDs (e.g. `pip://requests:2.28.0`) is stripped automatically.

---

## Multiple repositories

To route Xray events to different GitHub repositories, create one webhook per repository in the Watch settings, each with a different `X-Repo-Owner` / `X-Repo-Name` header combination. All webhooks point to the same agent URL.

---

## Testing without JFrog Xray

Use `curl` to simulate a webhook event:

```bash
# Without signature verification (XRAY_WEBHOOK_SECRET is empty)
curl -X POST http://localhost:8080/webhook/xray \
  -H "Content-Type: application/json" \
  -H "X-Repo-Owner: my-org" \
  -H "X-Repo-Name: my-repo" \
  -d @- <<'EOF'
{
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
}
EOF
```

To test with signature verification enabled:

```bash
SECRET="your-shared-secret"
BODY='{"violations":[...]}'

SIGNATURE=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

curl -X POST http://localhost:8080/webhook/xray \
  -H "Content-Type: application/json" \
  -H "X-JFrog-Event-Auth: SHA256=$SIGNATURE" \
  -H "X-Repo-Owner: my-org" \
  -H "X-Repo-Name: my-repo" \
  -d "$BODY"
```

---

## Async endpoint for short Xray timeouts

If your JFrog instance has a webhook timeout shorter than the agent's processing time (typically 2–5 seconds), use the async endpoint instead:

```
POST /webhook/xray/async
```

This returns `202 Accepted` immediately and processes the event in the background. All other headers and payload format are identical.
