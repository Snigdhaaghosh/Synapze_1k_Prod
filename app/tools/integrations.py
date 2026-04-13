"""
Synapze V2 — Calendar, WhatsApp, Slack tools
All extend BaseTool for consistent error handling + timeout.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings
from app.core.exceptions import IntegrationError, IntegrationNotConfiguredError
from app.db.database import get_user_tokens, save_user_tokens, get_whatsapp_messages
from app.tools.base import BaseTool


# ── Google Calendar ────────────────────────────────────────────────────────

class CalendarTool(BaseTool):
    tool_name = "calendar"
    required_config = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]

    def __init__(self, user_id: str):
        super().__init__(user_id)
        self._service = None

    async def _get_service(self):
        if self._service:
            return self._service
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        tokens = await get_user_tokens(self.user_id, "google")
        if not tokens or not tokens.get("access_token"):
            raise IntegrationNotConfiguredError("Calendar")

        creds = Credentials(
            token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            await save_user_tokens(self.user_id, "google", {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
            })

        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    async def list_events(self, days_ahead: int = 7, calendar_id: str = "primary") -> dict:
        service = await self._get_service()
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=max(1, min(days_ahead, 90)))

        result = service.events().list(
            calendarId=calendar_id,
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        events = []
        for e in result.get("items", []):
            start = e.get("start", {})
            end_e = e.get("end", {})
            events.append({
                "id": e["id"],
                "title": e.get("summary", "(no title)"),
                "start": start.get("dateTime", start.get("date", "")),
                "end": end_e.get("dateTime", end_e.get("date", "")),
                "location": e.get("location", ""),
                "description": (e.get("description", "") or "")[:500],
                "attendees": [a["email"] for a in e.get("attendees", [])],
                "meet_link": e.get("hangoutLink", ""),
            })
        return {"events": events, "count": len(events)}

    async def check_availability(self, start_datetime: str, end_datetime: str) -> dict:
        service = await self._get_service()
        result = service.freebusy().query(body={
            "timeMin": start_datetime,
            "timeMax": end_datetime,
            "items": [{"id": "primary"}],
        }).execute()
        busy = result.get("calendars", {}).get("primary", {}).get("busy", [])
        return {"has_conflict": len(busy) > 0, "conflicts": busy}

    async def create_event(
        self,
        title: str,
        start_datetime: str,
        end_datetime: str,
        description: str = "",
        attendees: Optional[list] = None,
        location: str = "",
        add_meet_link: bool = False,
    ) -> dict:
        if not title or not start_datetime or not end_datetime:
            return {"error": "title, start_datetime, and end_datetime are required"}

        service = await self._get_service()

        # Check conflicts first
        conflicts = await self.check_availability(start_datetime, end_datetime)
        if conflicts["has_conflict"]:
            return {
                "success": False,
                "conflict": True,
                "conflicts": conflicts["conflicts"],
                "message": f"Time slot has {len(conflicts['conflicts'])} conflict(s). Reply 'force create' to override.",
            }

        body: dict = {
            "summary": title,
            "start": {"dateTime": start_datetime, "timeZone": "UTC"},
            "end": {"dateTime": end_datetime, "timeZone": "UTC"},
            "description": description,
            "location": location,
        }
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees if "@" in a]
        if add_meet_link:
            body["conferenceData"] = {
                "createRequest": {"requestId": f"synapze-{int(datetime.now().timestamp())}"}
            }

        kwargs: dict = {
            "calendarId": "primary",
            "body": body,
            "sendUpdates": "all",
        }
        if add_meet_link:
            kwargs["conferenceDataVersion"] = 1

        result = service.events().insert(**kwargs).execute()
        return {
            "success": True,
            "event_id": result["id"],
            "title": title,
            "start": start_datetime,
            "end": end_datetime,
            "meet_link": result.get("hangoutLink", ""),
            "html_link": result.get("htmlLink", ""),
        }

    async def delete_event(self, event_id: str) -> dict:
        if not event_id:
            return {"error": "event_id is required"}
        service = await self._get_service()
        service.events().delete(calendarId="primary", eventId=event_id).execute()
        return {"success": True, "deleted_event_id": event_id}


# ── WhatsApp (Twilio) ──────────────────────────────────────────────────────

class WhatsAppTool(BaseTool):
    tool_name = "whatsapp"
    required_config = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_WHATSAPP_NUMBER"]

    def __init__(self, user_id: str):
        super().__init__(user_id)
        from twilio.rest import Client as SyncClient
from twilio.rest.aio import Client as AsyncClient
        self._client = AsyncClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        self._from = f"whatsapp:{settings.TWILIO_WHATSAPP_NUMBER}"

    async def send_message(self, to: str, message: str,
                            media_url: Optional[str] = None) -> dict:
        if not to:
            return {"error": "Recipient number is required"}
        if not message and not media_url:
            return {"error": "Message or media_url is required"}

        to_fmt = f"whatsapp:{to}" if not to.startswith("whatsapp:") else to
        kwargs: dict = {"from_": self._from, "to": to_fmt, "body": message or ""}
        if media_url:
            kwargs["media_url"] = [media_url]

        msg = self._client.messages.create(**kwargs)
        self.logger.info(f"WhatsApp sent to {to}")
        return {"success": True, "sid": msg.sid, "to": to, "status": msg.status}

    async def list_recent(self, limit: int = 20,
                           from_number: Optional[str] = None) -> dict:
        messages = await get_whatsapp_messages(
            self.user_id, limit=min(limit, 100), from_number=from_number
        )
        return {"messages": messages, "count": len(messages)}


# ── Slack ──────────────────────────────────────────────────────────────────

class SlackTool(BaseTool):
    tool_name = "slack"
    required_config = ["SLACK_BOT_TOKEN"]

    def __init__(self, user_id: str):
        super().__init__(user_id)
        from slack_sdk.web.async_client import AsyncWebClient
        self._client = AsyncWebClient(token=settings.SLACK_BOT_TOKEN)
        self._channel_cache: dict = {}

    async def send_message(self, channel: str, message: str,
                            thread_ts: Optional[str] = None) -> dict:
        if not channel or not message:
            return {"error": "channel and message are required"}
        from slack_sdk.errors import SlackApiError
        try:
            channel_id = await self._resolve_channel(channel)
            kwargs: dict = {"channel": channel_id, "text": message}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            result = await self._client.chat_postMessage(**kwargs)
            return {"success": True, "channel": channel, "ts": result["ts"]}
        except SlackApiError as e:
            raise IntegrationError("slack", f"Slack error: {e.response['error']}")

    async def list_messages(self, channel: str, limit: int = 20,
                             oldest: Optional[str] = None) -> dict:
        from slack_sdk.errors import SlackApiError
        try:
            channel_id = await self._resolve_channel(channel)
            kwargs: dict = {"channel": channel_id, "limit": min(limit, 100)}
            if oldest:
                kwargs["oldest"] = oldest
            result = await self._client.conversations_history(**kwargs)
            messages = [
                {
                    "ts": m["ts"],
                    "user": m.get("user", ""),
                    "text": m.get("text", ""),
                    "reply_count": m.get("reply_count", 0),
                }
                for m in result.get("messages", [])
                if m.get("type") == "message" and "bot_id" not in m
            ]
            return {"messages": messages, "channel": channel, "count": len(messages)}
        except SlackApiError as e:
            raise IntegrationError("slack", f"Slack error: {e.response['error']}")

    async def list_channels(self) -> dict:
        from slack_sdk.errors import SlackApiError
        try:
            result = await self._client.conversations_list(
                types="public_channel,private_channel"
            )
            channels = [
                {"id": c["id"], "name": c["name"],
                 "is_private": c.get("is_private", False)}
                for c in result.get("channels", [])
            ]
            return {"channels": channels, "count": len(channels)}
        except SlackApiError as e:
            raise IntegrationError("slack", f"Slack error: {e.response['error']}")

    async def _resolve_channel(self, channel: str) -> str:
        if channel in self._channel_cache:
            return self._channel_cache[channel]
        if channel.startswith(("C", "D", "G")):
            return channel
        result = await self._client.conversations_list(
            types="public_channel,private_channel"
        )
        name = channel.lstrip("#")
        for c in result.get("channels", []):
            if c["name"] == name:
                self._channel_cache[channel] = c["id"]
                return c["id"]
        return channel  # fallback — let Slack return the error
