"""FastAPI application — JFrog Xray webhook receiver.

Receives Xray violation events, processes them through the deterministic
pipeline, and raises GitHub Issues for GitHub Copilot to fix.

No LLM API calls are made. No AWS Bedrock. No Copilot API.
GitHub Copilot acts on the issues natively inside GitHub.

Endpoints:
  POST /webhook/xray   — receives Xray webhook events (HMAC-SHA256 verified)
  GET  /health         — liveness probe
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sys
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="JFrog Xray → GitHub Issue Agent",
    description=(
        "Receives JFrog Xray webhook events and raises structured GitHub Issues "
        "so GitHub Copilot can suggest and open fix PRs. No external LLM API required."
    ),
    version="2.0.0",
    # Disable Swagger in production by uncommenting:
    # docs_url=None, redoc_url=None,
)


# ---------------------------------------------------------------------------
# HMAC signature verification
# ---------------------------------------------------------------------------

def _verify_xray_signature(body: bytes, signature_header: str) -> bool:
    """Verify the HMAC-SHA256 signature sent by JFrog Xray.

    Xray sends the signature in the header:
      X-JFrog-Event-Auth: SHA256=<hex_digest>

    If no webhook secret is configured, verification is skipped (dev mode).
    """
    if not settings.xray_webhook_secret:
        logger.warning("XRAY_WEBHOOK_SECRET not set — skipping signature verification (dev mode)")
        return True

    if not signature_header:
        return False

    prefix = "SHA256="
    if not signature_header.startswith(prefix):
        return False

    received_digest = signature_header[len(prefix):]
    expected_digest = hmac.new(
        settings.xray_webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_digest, received_digest)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class WebhookResponse(BaseModel):
    status: str
    repo: str
    issues_created: int
    issue_urls: list[str]
    skipped: int
    errors: list[str]


class HealthResponse(BaseModel):
    status: str = "ok"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["Ops"])
def health() -> HealthResponse:
    return HealthResponse()


@app.post("/webhook/xray", response_model=WebhookResponse, tags=["Webhook"])
async def xray_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_jfrog_event_auth: str = Header(default="", alias="X-JFrog-Event-Auth"),
    x_repo_owner: str = Header(default="", alias="X-Repo-Owner"),
    x_repo_name: str = Header(default="", alias="X-Repo-Name"),
    x_base_branch: str = Header(default="", alias="X-Base-Branch"),
) -> WebhookResponse:
    """Receive a JFrog Xray violation webhook and open GitHub Issues.

    Required custom headers (set in the Xray webhook config):
      X-Repo-Owner   — GitHub org or user that owns the target repo
      X-Repo-Name    — GitHub repository name
      X-Base-Branch  — branch to scan manifests on (default: main)

    Optional:
      X-JFrog-Event-Auth — HMAC-SHA256 signature for payload verification
    """
    body = await request.body()

    # Verify signature
    if not _verify_xray_signature(body, x_jfrog_event_auth):
        logger.warning("Rejected webhook: invalid signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    repo_owner = x_repo_owner or settings.default_repo_owner
    repo_name = x_repo_name or settings.default_repo_name

    if not repo_owner or not repo_name:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing required headers: X-Repo-Owner and X-Repo-Name "
                "(or set DEFAULT_REPO_OWNER / DEFAULT_REPO_NAME in .env)"
            ),
        )

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    from agent.pipeline import run_pipeline, PipelineResult

    try:
        result: PipelineResult = run_pipeline(
            payload=payload,
            repo_owner=repo_owner,
            repo_name=repo_name,
            base_branch=x_base_branch,
        )
    except Exception as exc:
        logger.exception("Pipeline failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}") from exc

    return WebhookResponse(
        status="processed",
        repo=f"{repo_owner}/{repo_name}",
        issues_created=len(result.issues_created),
        issue_urls=[ir.issue_url for ir in result.issues_created],
        skipped=len(result.skipped),
        errors=result.errors,
    )


@app.post("/webhook/xray/async", tags=["Webhook"])
async def xray_webhook_async(
    request: Request,
    background_tasks: BackgroundTasks,
    x_jfrog_event_auth: str = Header(default="", alias="X-JFrog-Event-Auth"),
    x_repo_owner: str = Header(default="", alias="X-Repo-Owner"),
    x_repo_name: str = Header(default="", alias="X-Repo-Name"),
    x_base_branch: str = Header(default="", alias="X-Base-Branch"),
) -> JSONResponse:
    """Fire-and-forget variant — returns 202 immediately, processes in background.

    Use this if Xray has a short webhook timeout (< 5 s).
    """
    body = await request.body()

    if not _verify_xray_signature(body, x_jfrog_event_auth):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    repo_owner = x_repo_owner or settings.default_repo_owner
    repo_name = x_repo_name or settings.default_repo_name

    if not repo_owner or not repo_name:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing required headers: X-Repo-Owner and X-Repo-Name "
                "(or set DEFAULT_REPO_OWNER / DEFAULT_REPO_NAME in .env)"
            ),
        )

    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    def _run() -> None:
        from agent.pipeline import run_pipeline
        try:
            run_pipeline(
                payload=payload,
                repo_owner=repo_owner,
                repo_name=repo_name,
                base_branch=x_base_branch,
            )
        except Exception:
            logger.exception("Background pipeline run failed")

    background_tasks.add_task(_run)

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "repo": f"{repo_owner}/{repo_name}",
            "message": "Webhook accepted. Processing in background — check server logs.",
        },
    )
