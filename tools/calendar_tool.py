# tools/calendar_tool.py
"""
Google Calendar tool implementations for the MCP server.

Mirrors tools/gmail_tool.py's shape exactly:
  - plain REST calls over httpx (no google-api-python-client dependency)
  - no OAuth in this module/server; this server holds no Google client
    secret and never talks to Google's OAuth endpoints itself
  - auth is entirely the RAG chatbot backend's job
    (E:\\Ganesh\\Rag_Chatbot\\simple-rag\\calendar_oauth.py): it runs the
    Google OAuth2 flow, stores/refreshes the per-user token, and -- on
    every Calendar tool call it makes -- forwards that user's CURRENT
    valid access token as the `X-Calendar-Access-Token` HTTP header.
    This module just reads that header via FastMCP's
    `get_http_headers()`, same as gmail_tool.py's `_auth_headers()`.

  If the header is missing, it means the caller invoked a Calendar
  tool for a user who hasn't connected Calendar yet (or the backend
  has a bug) -- raising a clear RuntimeError here surfaces that as a
  normal tool error instead of a confusing 401 from Google's API.
"""

import logging
from typing import Optional

import httpx
from fastmcp.server.dependencies import get_http_headers

logger = logging.getLogger("calendar-tool")

CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

# Case-insensitive on the wire, but get_http_headers() normalizes keys
# to lowercase -- look up with the lowercase form.
_TOKEN_HEADER = "x-calendar-access-token"


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
            "No Calendar access token forwarded with this request. "
            "The user needs to connect Calendar in the chatbot first."
        )
    return {"Authorization": f"Bearer {token}"}


async def calendar_list_events(
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    query: str = "",
) -> str:
    """List upcoming events on the user's primary calendar.

    time_min / time_max: RFC3339 timestamps (e.g. "2025-06-01T00:00:00Z").
        If time_min is omitted, defaults to "now" on Google's side is
        NOT assumed here -- callers should pass an explicit time_min for
        predictable results; if omitted, Google returns events from the
        beginning of the calendar, so this defaults it to "now".
    max_results: how many events to return (capped at 25, same
        rationale as gmail_list_messages -- keep tool output small).
    query: free-text search over event fields (title, description,
        location, attendees), same as the Calendar UI's search box.
    """
    max_results = max(1, min(max_results, 25))
    logger.info(
        "calendar_list_events(time_min=%r, time_max=%r, max_results=%d, query=%r)",
        time_min, time_max, max_results, query,
    )

    params = {
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_min:
        params["timeMin"] = time_min
    else:
        from datetime import datetime, timezone
        params["timeMin"] = datetime.now(timezone.utc).isoformat()
    if time_max:
        params["timeMax"] = time_max
    if query:
        params["q"] = query

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers=_auth_headers(),
            params=params,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

    if not items:
        return "No events found."

    lines = []
    for ev in items:
        start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "?")
        end = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date", "?")
        lines.append(
            f"- id={ev.get('id')} | {ev.get('summary', '(no title)')} | "
            f"{start} -> {end} | location={ev.get('location', '-')}"
        )

    result = "\n".join(lines)
    logger.info("calendar_list_events: returned %d event(s)", len(items))
    return result


async def calendar_get_event(event_id: str) -> str:
    """Get full details of a single calendar event by id (the id comes
    from calendar_list_events output)."""
    logger.info("calendar_get_event(event_id=%r)", event_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        ev = resp.json()

    start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "?")
    end = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date", "?")
    attendees = ", ".join(a.get("email", "") for a in ev.get("attendees", [])) or "-"

    return (
        f"Title: {ev.get('summary', '(no title)')}\n"
        f"When: {start} -> {end}\n"
        f"Location: {ev.get('location', '-')}\n"
        f"Attendees: {attendees}\n"
        f"Description: {ev.get('description', '-')}\n"
        f"Link: {ev.get('htmlLink', '-')}"
    )


async def calendar_create_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: Optional[str] = None,
    timezone: str = "UTC",
) -> str:
    """Create an event on the user's primary calendar.

    start_time / end_time: RFC3339 timestamps
        (e.g. "2025-06-01T15:00:00").
    attendees: comma-separated email addresses, or None for no invites.
    timezone: IANA timezone name (e.g. "Asia/Kolkata"); defaults to UTC.
    """
    logger.info(
        "calendar_create_event(summary=%r, start_time=%r, end_time=%r)",
        summary, start_time, end_time,
    )

    body = {
        "summary": summary,
        "description": description,
        "location": location,
        "start": {"dateTime": start_time, "timeZone": timezone},
        "end": {"dateTime": end_time, "timeZone": timezone},
    }
    if attendees:
        body["attendees"] = [
            {"email": email.strip()} for email in attendees.split(",") if email.strip()
        ]

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers=_auth_headers(),
            json=body,
        )
        resp.raise_for_status()
        ev = resp.json()

    logger.info("calendar_create_event: created id=%s", ev.get("id"))
    return f"Event created. id={ev.get('id')} link={ev.get('htmlLink', '-')}"


async def calendar_delete_event(event_id: str) -> str:
    """Delete an event from the user's primary calendar by id.
    USE WITH CAUTION -- this is irreversible."""
    logger.info("calendar_delete_event(event_id=%r)", event_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.delete(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers=_auth_headers(),
        )
        # Google returns 204 with no body on success, and 410 if the
        # event was already deleted -- treat both as "gone", not an error.
        if resp.status_code not in (204, 410):
            resp.raise_for_status()

    logger.info("calendar_delete_event: deleted id=%s", event_id)
    return f"Deleted event id={event_id}"
