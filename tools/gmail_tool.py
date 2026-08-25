# tools/gmail_tool.py
"""
Gmail tool implementations for the MCP server.

Design choice: these call the Gmail REST API directly over httpx
(same library server.py's weather tool already uses) instead of
adding google-api-python-client as a dependency -- one fewer package,
and it's a handful of plain REST calls anyway.

Auth model:
  This module does NOT do any OAuth and this server holds no Google
  client secret. Auth is entirely the RAG chatbot backend's job
  (E:\Ganesh\Rag_Chatbot\simple-rag\gmail_oauth.py): it runs the
  Google OAuth2 flow, stores/refreshes the per-user token, and -- on
  every Gmail tool call it makes -- forwards that user's CURRENT valid
  access token as the `X-Gmail-Access-Token` HTTP header. This module
  just reads that header via FastMCP's `get_http_headers()`.

  If the header is missing, it means the caller invoked a Gmail tool
  for a user who hasn't connected Gmail yet (or the backend has a
  bug) -- raising a clear RuntimeError here surfaces that as a normal
  tool error instead of a confusing 401 from Gmail's API.
"""

import base64
import logging
from email.mime.text import MIMEText
from typing import Optional

import httpx
from fastmcp.server.dependencies import get_http_headers

logger = logging.getLogger("gmail-tool")

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Case-insensitive on the wire, but get_http_headers() normalizes keys
# to lowercase -- look up with the lowercase form.
_TOKEN_HEADER = "x-gmail-access-token"


def _auth_headers() -> dict:
    """Read the Google access token the backend forwarded for this
    request. `include_all=True` because FastMCP strips non-standard
    headers like this one by default (it only keeps a known-safe set
    meant for proxying, e.g. authorization) -- this is a custom header
    this server defines itself, so it has to be explicitly included."""
    headers = get_http_headers(include_all=True)
    token = headers.get(_TOKEN_HEADER)
    if not token:
        raise RuntimeError(
            "No Gmail access token forwarded with this request. "
            "The user needs to connect Gmail in the chatbot first."
        )
    return {"Authorization": f"Bearer {token}"}


def _extract_plain_text(payload: dict) -> Optional[str]:
    """Gmail messages are a MIME tree, not a flat body -- walk it for
    the first text/plain part rather than assuming a flat structure
    (multipart/alternative and HTML-only messages are both common)."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(
            payload["body"]["data"]
        ).decode("utf-8", errors="replace")

    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    return None


async def gmail_list_messages(query: str = "", max_results: int = 10) -> str:
    """List/search the authenticated user's Gmail messages.

    query: Gmail search syntax, e.g. "from:boss@company.com is:unread",
           "subject:invoice after:2025/01/01", or "" for most recent.
    max_results: how many messages to return (capped at 25 to keep
        tool output small -- callers can narrow with `query` instead
        of raising this).
    """
    max_results = max(1, min(max_results, 25))
    logger.info("gmail_list_messages(query=%r, max_results=%d)", query, max_results)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{GMAIL_API_BASE}/messages",
            headers=_auth_headers(),
            params={"q": query, "maxResults": max_results},
        )
        resp.raise_for_status()
        ids = [m["id"] for m in resp.json().get("messages", [])]

        if not ids:
            return "No messages found."

        lines = []
        for msg_id in ids:
            detail = await client.get(
                f"{GMAIL_API_BASE}/messages/{msg_id}",
                headers=_auth_headers(),
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "Subject", "Date"],
                },
            )
            detail.raise_for_status()
            payload = detail.json()
            headers = {
                h["name"]: h["value"]
                for h in payload.get("payload", {}).get("headers", [])
            }
            snippet = payload.get("snippet", "")
            lines.append(
                f"- id={msg_id} | From: {headers.get('From', '?')} | "
                f"Subject: {headers.get('Subject', '(no subject)')} | "
                f"Date: {headers.get('Date', '?')}\n  {snippet}"
            )

    result = "\n".join(lines)
    logger.info("gmail_list_messages: returned %d message(s)", len(ids))
    return result


async def gmail_get_message(message_id: str) -> str:
    """Get the full plain-text body and headers of a single Gmail
    message by id (the id comes from gmail_list_messages output)."""
    logger.info("gmail_get_message(message_id=%r)", message_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{GMAIL_API_BASE}/messages/{message_id}",
            headers=_auth_headers(),
            params={"format": "full"},
        )
        resp.raise_for_status()
        payload = resp.json()

    headers = {
        h["name"]: h["value"]
        for h in payload.get("payload", {}).get("headers", [])
    }
    body = _extract_plain_text(payload.get("payload", {})) or payload.get("snippet", "")

    return (
        f"From: {headers.get('From', '?')}\n"
        f"To: {headers.get('To', '?')}\n"
        f"Subject: {headers.get('Subject', '(no subject)')}\n"
        f"Date: {headers.get('Date', '?')}\n\n"
        f"{body}"
    )


async def gmail_send_message(to: str, subject: str, body: str) -> str:
    """Send a plain-text email as the authenticated user.

    Requires the gmail.send (or gmail.compose) scope in addition to
    gmail.readonly -- see server.py's GoogleProvider config.
    """
    logger.info("gmail_send_message(to=%r, subject=%r)", to, subject)

    mime = MIMEText(body)
    mime["to"] = to
    mime["subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{GMAIL_API_BASE}/messages/send",
            headers=_auth_headers(),
            json={"raw": raw},
        )
        resp.raise_for_status()
        result = resp.json()

    logger.info("gmail_send_message: sent, id=%s", result.get("id"))
    return f"Sent. Message id={result.get('id')}"
