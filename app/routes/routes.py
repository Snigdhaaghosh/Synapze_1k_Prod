"""
Synapze Enterprise — API Routes
Additions over v2:
- /auth/refresh  — token refresh endpoint
- /auth/logout   — explicit token revocation
- /agent/sessions — list all user sessions
- /users/me/usage — usage summary
- /admin/*        — admin endpoints (suspended/unsuspend)
"""
import hashlib
import hmac
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, status, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, field_validator

from app.auth.jwt import (
    get_current_user, require_user, create_access_token,
    create_refresh_token, decode_token, revoke_token,
)
from app.config import settings
from app.core.exceptions import SynapzeError, AuthError, TokenExpiredError
from app.core.logging import get_logger
from app.core.security import sanitize_input, mask_email
from app.db import database as db

logger = get_logger("routes")

# ═══════════════════════════════════════════════════════════════
# AGENT ROUTES
# ═══════════════════════════════════════════════════════════════

agent_router = APIRouter(prefix="/agent", tags=["agent"])


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        v = sanitize_input(v, max_length=10_000)
        if not v.strip():
            raise ValueError("Message cannot be empty")
        return v

    @field_validator("session_id")
    @classmethod
    def valid_session_id(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > 64:
            raise ValueError("Invalid session_id")
        return v


@agent_router.post("/chat")
async def chat(req: ChatRequest, current: dict = Depends(get_current_user)):
    from app.agent.core import SynapzeAgent
    user_id = current["user_id"]
    session_id = req.session_id or str(uuid.uuid4())
    await db.create_session(user_id, session_id)
    agent = SynapzeAgent()
    try:
        return await agent.run(message=req.message, user_id=user_id,
                               session_id=session_id, stream=False)
    except SynapzeError as e:
        raise HTTPException(status_code=400, detail=e.to_dict())


@agent_router.post("/stream")
async def stream_chat(req: ChatRequest, current: dict = Depends(get_current_user)):
    from app.agent.core import SynapzeAgent
    user_id = current["user_id"]
    session_id = req.session_id or str(uuid.uuid4())
    await db.create_session(user_id, session_id)
    agent = SynapzeAgent()
    gen = await agent.run(message=req.message, user_id=user_id,
                          session_id=session_id, stream=True)
    return StreamingResponse(
        gen,
        media_type="application/x-ndjson",
        headers={"X-Session-ID": session_id},
    )


@agent_router.get("/sessions")
async def list_sessions(
    current: dict = Depends(get_current_user),
    limit: int = Query(default=50, le=100),
):
    sessions = await db.get_user_sessions(current["user_id"], limit=limit)
    return {"sessions": sessions}


@agent_router.get("/sessions/{session_id}/history")
async def get_history(session_id: str, current: dict = Depends(get_current_user),
                      limit: int = Query(default=50, le=200)):
    if not await db.verify_session_ownership(session_id, current["user_id"]):
        raise HTTPException(status_code=404, detail="Session not found")
    history = await db.get_history(session_id, limit=limit)
    return {"session_id": session_id, "history": history}


@agent_router.delete("/sessions/{session_id}")
async def clear_session(session_id: str, current: dict = Depends(get_current_user)):
    await db.clear_session_history(session_id, current["user_id"])
    return {"cleared": True, "session_id": session_id}


# ═══════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════

auth_router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
]


def _get_google_flow():
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(
        {"web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }},
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


@auth_router.get("/google")
async def google_login():
    flow = _get_google_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true"
    )
    return RedirectResponse(auth_url)


@auth_router.get("/google/callback")
async def google_callback(request: Request, code: str):
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    try:
        flow = _get_google_flow()
        flow.fetch_token(code=code)
        creds = flow.credentials

        import google.oauth2.id_token as id_token_mod
        import google.auth.transport.requests as grequests
        id_info = id_token_mod.verify_oauth2_token(
            creds.id_token, grequests.Request(), settings.GOOGLE_CLIENT_ID
        )

        user_id = id_info["sub"]
        email = id_info["email"]
        name = id_info.get("name", email)

        await db.upsert_user(user_id=user_id, email=email, name=name)
        await db.save_user_tokens(user_id=user_id, provider="google", tokens={
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
        })
        await db.audit(user_id=user_id, action="auth.google_login",
                       details={"email": mask_email(email)},
                       ip=request.client.host if request.client else None)

        access_token = create_access_token(user_id=user_id, email=email)
        refresh_token = create_refresh_token(user_id=user_id, email=email)
        logger.info(f"Auth success", extra={"user": mask_email(email)})

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
            "user": {"email": email, "name": name},
        }
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"OAuth failed: {str(e)[:200]}")


class RefreshRequest(BaseModel):
    refresh_token: str


@auth_router.post("/refresh")
async def refresh_token(req: RefreshRequest):
    """Exchange a refresh token for a new access token."""
    try:
        payload = decode_token(req.refresh_token, expected_type="refresh")
    except TokenExpiredError:
        raise HTTPException(status_code=401, detail={"error": "REFRESH_TOKEN_EXPIRED",
                                                      "message": "Refresh token expired — please log in again"})
    except AuthError as e:
        raise HTTPException(status_code=401, detail=e.to_dict())

    # Verify user still exists
    try:
        await db.get_user(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail={"error": "USER_NOT_FOUND"})

    new_access = create_access_token(user_id=payload["sub"], email=payload["email"])
    return {
        "access_token": new_access,
        "token_type": "Bearer",
        "expires_in": settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
    }


@auth_router.post("/logout")
async def logout(current: dict = Depends(get_current_user)):
    """Explicitly revoke the current access token."""
    jti = current.get("token_jti", "")
    if jti:
        await revoke_token(jti)
    await db.audit(user_id=current["user_id"], action="auth.logout")
    return {"logged_out": True}


@auth_router.get("/me")
async def me(current: dict = Depends(get_current_user)):
    usage = await db.get_usage_summary(current["user_id"], days=30)
    return {
        "user_id": current["user_id"],
        "email": current["email"],
        "name": current["user"].get("name", ""),
        "usage_last_30d": usage,
    }


# ═══════════════════════════════════════════════════════════════
# TASK ROUTES
# ═══════════════════════════════════════════════════════════════

tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


class ScheduleRequest(BaseModel):
    message: str
    run_at: Optional[str] = None
    session_id: Optional[str] = None

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        v = sanitize_input(v)
        if not v.strip():
            raise ValueError("message cannot be empty")
        return v


@tasks_router.post("/schedule")
async def schedule_task(req: ScheduleRequest, user_id: str = Depends(require_user)):
    from app.tasks.jobs import run_scheduled_task
    from datetime import datetime

    session_id = req.session_id or str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    eta = None
    if req.run_at:
        try:
            eta = datetime.fromisoformat(req.run_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid run_at datetime format (use ISO 8601)")

    celery_task = run_scheduled_task.apply_async(
        args=[user_id, session_id, req.message],
        task_id=task_id,
        eta=eta,
    )
    await db.audit(user_id=user_id, action="task.scheduled",
                   details={"session_id": session_id, "scheduled_for": req.run_at})
    return {"task_id": celery_task.id, "session_id": session_id,
            "scheduled_for": req.run_at or "immediate"}


@tasks_router.get("/{task_id}")
async def task_status(task_id: str, user_id: str = Depends(require_user)):
    from app.tasks.worker import celery_app
    result = celery_app.AsyncResult(task_id)
    return {"task_id": task_id, "status": result.status,
            "result": result.result if result.ready() else None}


@tasks_router.delete("/{task_id}")
async def cancel_task(task_id: str, user_id: str = Depends(require_user)):
    from app.tasks.worker import celery_app
    celery_app.control.revoke(task_id, terminate=True)
    await db.audit(user_id=user_id, action="task.cancelled", details={"task_id": task_id})
    return {"cancelled": True, "task_id": task_id}


# ═══════════════════════════════════════════════════════════════
# WEBHOOK ROUTES
# ═══════════════════════════════════════════════════════════════

webhooks_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@webhooks_router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(...),
    NumMedia: int = Form(default=0),
    X_Twilio_Signature: str = Header(default="", alias="X-Twilio-Signature"),
):
    if settings.TWILIO_AUTH_TOKEN:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
        form_data = dict(await request.form())
        if not validator.validate(str(request.url), form_data, X_Twilio_Signature):
            logger.warning(f"Invalid Twilio signature from {From}")
            raise HTTPException(status_code=403, detail="Invalid signature")

    from_number = From.replace("whatsapp:", "")
    from datetime import datetime
    await db.save_whatsapp_message({
        "message_sid": MessageSid,
        "from_number": from_number,
        "body": sanitize_input(Body, max_length=4096),
        "num_media": NumMedia,
        "received_at": datetime.utcnow().isoformat(),
    })
    return {"status": "ok"}


@webhooks_router.post("/slack/events")
async def slack_events(request: Request):
    body_bytes = await request.body()
    body_text = body_bytes.decode("utf-8")

    if settings.SLACK_SIGNING_SECRET:
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        sig_base = f"v0:{timestamp}:{body_text}"
        expected = "v0=" + hmac.new(
            settings.SLACK_SIGNING_SECRET.encode(), sig_base.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=403, detail="Invalid Slack signature")

    payload = json.loads(body_text)
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}
    return {"ok": True}
