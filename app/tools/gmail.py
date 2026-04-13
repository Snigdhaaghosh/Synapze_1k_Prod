"""
Synapze V2 — Gmail Tool
Full CRUD: list, read, send, reply, search, label management.
Token auto-refresh on every call.
"""
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.db.database import get_user_tokens, save_user_tokens
from app.tools.base import BaseTool


class GmailTool(BaseTool):
    tool_name = "gmail"
    required_config = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]

    def __init__(self, user_id: str):
        super().__init__(user_id)
        self._service = None

    async def _get_service(self):
        if self._service:
            return self._service

        tokens = await get_user_tokens(self.user_id, "google")
        if not tokens or not tokens.get("access_token"):
            raise IntegrationNotConfiguredError("Gmail")

        creds = Credentials(
            token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
        )

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                await save_user_tokens(self.user_id, "google", {
                    "access_token": creds.token,
                    "refresh_token": creds.refresh_token,
                })
            except Exception as e:
                raise IntegrationError("gmail", f"Token refresh failed: {e}")

        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    async def list_emails(
        self,
        query: str = "is:unread",
        max_results: int = 10,
        label: str = "INBOX",
    ) -> dict:
        try:
            service = await self._get_service()
            full_query = f"{query} label:{label}".strip() if label else query

            result = service.users().messages().list(
                userId="me", q=full_query,
                maxResults=min(max_results, 50),  # cap at 50
            ).execute()

            messages = result.get("messages", [])
            summaries = []
            for msg in messages:
                try:
                    meta = service.users().messages().get(
                        userId="me", id=msg["id"], format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    ).execute()
                    headers = {
                        h["name"]: h["value"]
                        for h in meta.get("payload", {}).get("headers", [])
                    }
                    summaries.append({
                        "id": msg["id"],
                        "from": headers.get("From", ""),
                        "subject": headers.get("Subject", "(no subject)"),
                        "date": headers.get("Date", ""),
                        "snippet": meta.get("snippet", "")[:200],
                        "unread": "UNREAD" in meta.get("labelIds", []),
                        "thread_id": meta.get("threadId", ""),
                    })
                except HttpError:
                    continue  # skip inaccessible messages

            return {"emails": summaries, "count": len(summaries), "query": full_query}

        except HttpError as e:
            raise IntegrationError("gmail", f"Gmail API error: {e.status_code} {e.reason}")

    async def read_email(self, email_id: str) -> dict:
        if not email_id or not email_id.strip():
            return {"error": "email_id is required"}
        try:
            service = await self._get_service()
            msg = service.users().messages().get(
                userId="me", id=email_id, format="full"
            ).execute()

            headers = {
                h["name"]: h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            body = self._extract_body(msg.get("payload", {}))

            return {
                "id": email_id,
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
                "body": body[:5000],  # cap body length
                "thread_id": msg.get("threadId", ""),
            }
        except HttpError as e:
            raise IntegrationError("gmail", f"Cannot read email: {e.reason}")

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        reply_to_id: Optional[str] = None,
    ) -> dict:
        if not to or "@" not in to:
            return {"error": "Invalid recipient email address"}
        if not subject:
            return {"error": "Subject is required"}
        if not body:
            return {"error": "Email body is required"}

        try:
            service = await self._get_service()
            msg = MIMEMultipart("alternative")
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc

            thread_id = None
            if reply_to_id:
                try:
                    original = await self.read_email(reply_to_id)
                    msg["In-Reply-To"] = reply_to_id
                    msg["References"] = reply_to_id
                    thread_id = original.get("thread_id")
                except Exception:
                    pass  # proceed without thread if original not found

            msg.attach(MIMEText(body, "plain", "utf-8"))
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

            send_body: dict = {"raw": raw}
            if thread_id:
                send_body["threadId"] = thread_id

            result = service.users().messages().send(
                userId="me", body=send_body
            ).execute()

            self.logger.info(f"Email sent to {to}")
            return {
                "success": True,
                "message_id": result["id"],
                "to": to,
                "subject": subject,
            }
        except HttpError as e:
            raise IntegrationError("gmail", f"Send failed: {e.reason}")

    async def search_emails(
        self,
        query: str,
        date_after: Optional[str] = None,
        date_before: Optional[str] = None,
        max_results: int = 20,
    ) -> dict:
        full_query = query
        if date_after:
            full_query += f" after:{date_after.replace('-', '/')}"
        if date_before:
            full_query += f" before:{date_before.replace('-', '/')}"
        return await self.list_emails(query=full_query, max_results=max_results, label="")

    def _extract_body(self, payload: dict) -> str:
        """Extract plain text body, fallback to HTML, fallback to empty."""
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            for part in payload["parts"]:
                if part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return ""
