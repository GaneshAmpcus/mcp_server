# server.py
import logging

from fastmcp import FastMCP
import httpx

from tools.gmail_tool import gmail_get_message, gmail_list_messages, gmail_send_message
from tools.calendar_tool import (
    calendar_list_events,
    calendar_get_event,
    calendar_create_event,
    calendar_delete_event,
)
from tools.meet_tool import meet_create_event, meet_get_link

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("weather-server-by-ganesh")

logging.getLogger("mcp").setLevel(logging.DEBUG)
logging.getLogger("mcp.server").setLevel(logging.DEBUG)

# ---------------------------------------------------------------------
# Auth: deliberately NOT handled here.
#
# Per-user Google OAuth lives entirely in the RAG chatbot backend --
# Gmail in gmail_oauth.py, Calendar/Meet in calendar_oauth.py
# (E:\Ganesh\Rag_Chatbot\simple-rag\). This server never talks to
# Google's OAuth endpoints and holds no Google client secret. Each
# tool call below expects the CALLER (the backend) to have already
# obtained a valid Google access token for that user and to forward it
# on the request as a header -- `X-Gmail-Access-Token` for Gmail tools
# (see tools/gmail_tool.py's `_auth_headers()`) and
# `X-Calendar-Access-Token` for Calendar and Meet tools (see
# tools/calendar_tool.py's `_auth_headers()`; tools/meet_tool.py reuses
# it since Meet links are created through the same Calendar API).
#
# Whatever gate already protects this server overall (e.g. FastMCP
# Cloud's own login, referred to as "Horizon" in the chatbot's
# mcp_tools.py) is unrelated and untouched by this file -- that's
# platform-level access to the MCP server itself, not per-user Gmail
# authorization.
# ---------------------------------------------------------------------

mcp = FastMCP("weather-server")   # <-- stateless_http removed from here

@mcp.tool()
async def get_weather(city: str) -> str:
    """Get current weather for a city (free Open-Meteo API, no key)."""
    logger.info(f"TOOL CALLED: get_weather(city={city!r})")
    try:
        async with httpx.AsyncClient() as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1},
            )
            results = geo.json().get("results")
            if not results:
                logger.warning(f"No location found for city={city!r}")
                return f"Could not find location: {city}"
            loc = results[0]

            wx = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": loc["latitude"], "longitude": loc["longitude"],
                         "current_weather": True},
            )
            w = wx.json()["current_weather"]
            result = (f"Weather in {loc['name']}, {loc.get('country','')}: "
                      f"{w['temperature']}°C, wind {w['windspeed']} km/h")
            logger.info(f"TOOL RESULT: {result}")
            return result
    except Exception as e:
        logger.error(f"TOOL FAILED: get_weather(city={city!r}) -> {e}")
        raise

# ---------------------------------------------------------------------
# Gmail tools -- thin @mcp.tool wrappers around tools/gmail_tool.py.
# Kept as wrappers (rather than decorating inside gmail_tool.py) so
# that module has no import-time dependency on this server's `mcp`
# instance and stays easy to unit test in isolation.
# ---------------------------------------------------------------------

@mcp.tool()
async def list_gmail_messages(query: str = "", max_results: int = 10) -> str:
    """List/search the authenticated user's Gmail messages. `query` uses
    Gmail search syntax, e.g. 'from:someone@example.com is:unread'."""
    logger.info(f"TOOL CALLED: list_gmail_messages(query={query!r}, max_results={max_results})")
    try:
        return await gmail_list_messages(query, max_results)
    except Exception as e:
        logger.error(f"TOOL FAILED: list_gmail_messages -> {e}")
        raise


@mcp.tool()
async def get_gmail_message(message_id: str) -> str:
    """Get the full body of a single Gmail message by id (from
    list_gmail_messages output)."""
    logger.info(f"TOOL CALLED: get_gmail_message(message_id={message_id!r})")
    try:
        return await gmail_get_message(message_id)
    except Exception as e:
        logger.error(f"TOOL FAILED: get_gmail_message -> {e}")
        raise


@mcp.tool()
async def send_gmail_message(to: str, subject: str, body: str) -> str:
    """Send a plain-text email as the authenticated user."""
    logger.info(f"TOOL CALLED: send_gmail_message(to={to!r}, subject={subject!r})")
    try:
        return await gmail_send_message(to, subject, body)
    except Exception as e:
        logger.error(f"TOOL FAILED: send_gmail_message -> {e}")
        raise


# ---------------------------------------------------------------------
# Calendar tools -- thin @mcp.tool wrappers around tools/calendar_tool.py.
# Same auth model as Gmail: this server holds no Google client secret;
# the RAG chatbot backend forwards a per-user Google access token as
# the `X-Calendar-Access-Token` header on every call (see
# calendar_tool.py's `_auth_headers()`).
# ---------------------------------------------------------------------

@mcp.tool()
async def list_calendar_events(
    time_min: str = "", time_max: str = "", max_results: int = 10, query: str = ""
) -> str:
    """List the authenticated user's upcoming Google Calendar events.
    time_min/time_max are optional RFC3339 timestamps; query does a
    free-text search over title/description/location/attendees."""
    logger.info(
        f"TOOL CALLED: list_calendar_events(time_min={time_min!r}, time_max={time_max!r}, "
        f"max_results={max_results}, query={query!r})"
    )
    try:
        return await calendar_list_events(
            time_min or None, time_max or None, max_results, query
        )
    except Exception as e:
        logger.error(f"TOOL FAILED: list_calendar_events -> {e}")
        raise


@mcp.tool()
async def get_calendar_event(event_id: str) -> str:
    """Get full details of a single Google Calendar event by id (from
    list_calendar_events output)."""
    logger.info(f"TOOL CALLED: get_calendar_event(event_id={event_id!r})")
    try:
        return await calendar_get_event(event_id)
    except Exception as e:
        logger.error(f"TOOL FAILED: get_calendar_event -> {e}")
        raise


@mcp.tool()
async def create_calendar_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: str = "",
    timezone: str = "UTC",
) -> str:
    """Create an event on the authenticated user's primary Google
    Calendar. start_time/end_time are RFC3339 timestamps; attendees is
    a comma-separated list of email addresses."""
    logger.info(
        f"TOOL CALLED: create_calendar_event(summary={summary!r}, "
        f"start_time={start_time!r}, end_time={end_time!r})"
    )
    try:
        return await calendar_create_event(
            summary, start_time, end_time, description, location,
            attendees or None, timezone,
        )
    except Exception as e:
        logger.error(f"TOOL FAILED: create_calendar_event -> {e}")
        raise


@mcp.tool()
async def delete_calendar_event(event_id: str) -> str:
    """Delete a Google Calendar event by id. USE WITH CAUTION --
    this is irreversible."""
    logger.info(f"TOOL CALLED: delete_calendar_event(event_id={event_id!r})")
    try:
        return await calendar_delete_event(event_id)
    except Exception as e:
        logger.error(f"TOOL FAILED: delete_calendar_event -> {e}")
        raise


# ---------------------------------------------------------------------
# Google Meet tools -- thin @mcp.tool wrappers around tools/meet_tool.py.
# Built on top of the Calendar API's conferenceData (see meet_tool.py's
# module docstring for why); reuses the same `X-Calendar-Access-Token`
# header the calendar tools above use, since it's the same Google
# token/scope under the hood.
# ---------------------------------------------------------------------

@mcp.tool()
async def create_meet_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    attendees: str = "",
    timezone: str = "UTC",
) -> str:
    """Create a calendar event with a Google Meet video call attached
    and return the Meet join link. start_time/end_time are RFC3339
    timestamps; attendees is a comma-separated list of email
    addresses."""
    logger.info(
        f"TOOL CALLED: create_meet_event(summary={summary!r}, "
        f"start_time={start_time!r}, end_time={end_time!r})"
    )
    try:
        return await meet_create_event(
            summary, start_time, end_time, description, attendees or None, timezone,
        )
    except Exception as e:
        logger.error(f"TOOL FAILED: create_meet_event -> {e}")
        raise


@mcp.tool()
async def get_meet_link(event_id: str) -> str:
    """Get the Google Meet join link for an existing calendar event by
    id (from create_meet_event or list_calendar_events output)."""
    logger.info(f"TOOL CALLED: get_meet_link(event_id={event_id!r})")
    try:
        return await meet_get_link(event_id)
    except Exception as e:
        logger.error(f"TOOL FAILED: get_meet_link -> {e}")
        raise


if __name__ == "__main__":
    logger.info("Starting MCP server on http://127.0.0.1:8000/mcp (transport=streamable-http)")
    mcp.run(transport="streamable-http", stateless_http=True)  # <-- moved here