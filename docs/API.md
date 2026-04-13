# Synapze Enterprise — API Reference

Base URL: `https://your-domain.com`

All endpoints require `Authorization: Bearer <access_token>` except where noted.

---

## Authentication

### `GET /auth/google`
Redirects to Google OAuth. No auth required.

### `GET /auth/google/callback?code=...`
OAuth callback. Returns `access_token`, `refresh_token`, and user info.

```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": { "email": "user@example.com", "name": "User Name" }
}
```

### `POST /auth/refresh`
Exchange refresh token for a new access token.
```json
{ "refresh_token": "eyJ..." }
```

### `POST /auth/logout`
Revoke the current access token.

### `GET /auth/me`
Get current user info and 30-day usage summary.

---

## Agent

### `POST /agent/chat`
Synchronous agent run. Returns when complete.
```json
{ "message": "Check my emails", "session_id": "optional-uuid" }
```
Response:
```json
{
  "response": "You have 3 unread emails...",
  "session_id": "uuid",
  "tool_calls": [{ "tool": "gmail_list_emails", "success": true }],
  "tokens_used": 1250,
  "duration_ms": 3200
}
```

### `POST /agent/stream`
Streaming agent run (NDJSON). Each line is a JSON event:
- `{"type": "text", "chunk": "partial response text"}`
- `{"type": "tool_start", "tool": "gmail_list_emails"}`
- `{"type": "tool_result", "tool": "gmail_list_emails", "result": {...}, "success": true}`
- `{"type": "done", "tool_calls": 2, "tokens": 1500, "duration_ms": 4200}`
- `{"type": "error", "message": "..."}`

### `GET /agent/sessions`
List all sessions for the current user.

### `GET /agent/sessions/{session_id}/history`
Get conversation history for a session.

### `DELETE /agent/sessions/{session_id}`
Clear conversation history for a session.

---

## Tasks

### `POST /tasks/schedule`
Schedule an agent task for future execution.
```json
{
  "message": "Send Rahul the project update",
  "run_at": "2025-06-10T09:00:00+05:30",
  "session_id": "optional-uuid"
}
```

### `GET /tasks/{task_id}`
Check task status: `pending | running | done | failed | cancelled`

### `DELETE /tasks/{task_id}`
Cancel a scheduled task.

---

## Health

| Endpoint | Auth | Description |
|---|---|---|
| `GET /health` | None | Liveness probe — always fast |
| `GET /health/ready` | None | Readiness — checks DB + Redis |
| `GET /health/detailed` | `X-Internal-Token` | Full diagnostic |
| `GET /metrics` | Bearer token | Prometheus metrics |

---

## Error Format

All errors return:
```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable description",
  "details": {}
}
```

Common codes: `AUTH_ERROR`, `TOKEN_EXPIRED`, `RATE_LIMIT`, `NOT_FOUND`, `VALIDATION_ERROR`, `TOOL_ERROR`

---

## Rate Limits

- Default: **60 requests/minute** per user (sliding window)
- Auth endpoints: **10 requests/minute** per IP
- Streaming: **20 requests/minute** per user

Headers on 429:
```
Retry-After: 60
X-RateLimit-Reset: <unix timestamp>
```
