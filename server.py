# server.py
import logging
from fastmcp import FastMCP
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("weather-server-by-ganesh")

logging.getLogger("mcp").setLevel(logging.DEBUG)
logging.getLogger("mcp.server").setLevel(logging.DEBUG)

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

if __name__ == "__main__":
    logger.info("Starting MCP server on http://127.0.0.1:8000/mcp (transport=streamable-http)")
    mcp.run(transport="streamable-http", stateless_http=True)  # <-- moved here