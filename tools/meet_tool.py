# tools/meet_tool.py
"""
Google Meet tool implementations for the MCP server.

Design choice, worth calling out: there is no separate "create a Meet
link" REST call. Google Meet links are minted through the Calendar API
by attaching a `conferenceData.createRequest` to a calendar event --
that's how Google Calendar's own "Add Google Meet video conferencing"
button works, and it's the standard integration pattern (the dedicated
Meet REST API, meet.googleapis.com, is a separate beta surface for
managing persistent meeting "spaces" and is overkill for "give me a
Meet link for this meeting"). So this module is a thin, Meet-flavored
wrapper around the same Calendar API calendar_tool.py already talks
to -- it creates an event with conferencing enabled and returns the
resulting Meet join URL.

Because it's the same underlying Calendar API and the same OAuth
scope, this module reuses calendar_tool's auth: it reads the same
`X-Calendar-Access-Token` header (see calendar_tool.py's
`_auth_headers()` for the header-forwarding rationale) rather than
inventing a separate `X-Meet-Access-Token` for what would be an
identical token value. The RAG chatbot backend's meet_oauth wrapper
(tools/meet_tool.py there) forwards the same Calendar access token it
already fetches for calendar tools.

No OAuth happens in this module or on this server -- same model as
gmail_tool.py and calendar_tool.py: the backend owns the Google OAuth
flow and forwards a per-user token per request.
"""

import logging
import uuid
from typing import Optional

import httpx

from tools.calendar_tool import CALENDAR_API_BASE, _auth_headers

logger = logging.getLogger("meet-tool")


async def meet_create_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    attendees: Optional[str] = None,
    timezone: str = "UTC",
) -> str:
    """Create a calendar event with a Google Meet video call attached,
    and return the Meet join link.

    start_time / end_time: RFC3339 timestamps
        (e.g. "2025-06-01T15:00:00").
    attendees: comma-separated email addresses, or None for no invites.
    timezone: IANA timezone name (e.g. "Asia/Kolkata"); defaults to UTC.
    """
    logger.info(
        "meet_create_event(summary=%r, start_time=%r, end_time=%r)",
        summary, start_time, end_time,
    )

    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_time, "timeZone": timezone},
        "end": {"dateTime": end_time, "timeZone": timezone},
        "conferenceData": {
            "createRequest": {
                # Must be unique per request -- Google dedupes conference
                # creation on this key, so a fixed string would silently
                # reuse/collide across calls.
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    if attendees:
        body["attendees"] = [
            {"email": email.strip()} for email in attendees.split(",") if email.strip()
        ]

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers=_auth_headers(),
            params={"conferenceDataVersion": 1},  # required for conferenceData to take effect
            json=body,
        )
        resp.raise_for_status()
        ev = resp.json()

    meet_link = ev.get("hangoutLink") or ev.get("conferenceData", {}).get(
        "entryPoints", [{}]
    )[0].get("uri", "-")

    logger.info("meet_create_event: created id=%s meet_link=%s", ev.get("id"), meet_link)
    return (
        f"Meet event created. id={ev.get('id')} meet_link={meet_link} "
        f"calendar_link={ev.get('htmlLink', '-')}"
    )


async def meet_get_link(event_id: str) -> str:
    """Get the Google Meet join link for an existing calendar event by
    id (the id comes from meet_create_event or calendar_list_events
    output). Returns a message saying no Meet link exists if the event
    has no conferencing attached."""
    logger.info("meet_get_link(event_id=%r)", event_id)

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        ev = resp.json()

    meet_link = ev.get("hangoutLink")
    if not meet_link:
        return f"No Google Meet link is attached to event id={event_id}."
    return f"meet_link={meet_link}"
