# server.py
import logging

from fastmcp import FastMCP
import httpx

from tools.gmail_tool import gmail_get_message, gmail_list_messages, gmail_send_message

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
# Per-user Google/Gmail OAuth lives entirely in the RAG chatbot backend
# (E:\Ganesh\Rag_Chatbot\simple-rag\gmail_oauth.py). This server never
# talks to Google's OAuth endpoints and holds no Google client secret.
# Each Gmail tool call below expects the CALLER (the backend) to have
# already obtained a valid Google access token for that user and to
# forward it on the request as the `X-Gmail-Access-Token` header --
# see tools/gmail_tool.py's `_auth_headers()` for where that's read.
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


if __name__ == "__main__":
    logger.info("Starting MCP server on http://127.0.0.1:8000/mcp (transport=streamable-http)")
    mcp.run(transport="streamable-http", stateless_http=True)  # <-- moved here