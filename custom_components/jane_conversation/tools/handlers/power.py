"""Power handlers — eval_template, bulk_control, search_web."""

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


async def handle_eval_template(hass: HomeAssistant, args: dict) -> str:
    """Evaluate a Jinja2 template."""
    template_str = args.get("template", "")
    if not template_str:
        return "Error: template is required."

    try:
        from homeassistant.helpers.template import Template

        tpl = Template(template_str, hass)
        result = tpl.async_render()
        return str(result)
    except Exception as e:
        return f"Template error: {e}"


async def handle_bulk_control(hass: HomeAssistant, args: dict) -> str:
    """Control multiple entities at once."""
    entity_ids = args.get("entity_ids", [])
    domain = args.get("domain", "")
    service = args.get("service", "")
    data = args.get("data", {}) or {}

    if not entity_ids:
        return "Error: entity_ids list is required."

    results = []
    for eid in entity_ids:
        try:
            service_data = {"entity_id": eid}
            service_data.update(data)
            await hass.services.async_call(domain, service, service_data, blocking=True)
            results.append(f"{eid}: OK")
        except Exception as e:
            results.append(f"{eid}: failed ({e})")

    return f"Bulk {domain}.{service} on {len(entity_ids)} entities:\n" + "\n".join(results)


async def handle_search_web(hass: HomeAssistant, args: dict, tavily_api_key: str | None = None) -> str:
    """Search the web using Gemini + Google Search grounding."""
    query = args.get("query", "")
    if not query:
        return "No search query provided."

    try:
        from google.genai import types

        from ...const import DOMAIN

        client = getattr(hass.data.get(DOMAIN), "gemini_client", None)
        if client is None:
            return "Web search unavailable: Gemini client not initialized."

        response = await hass.async_add_executor_job(
            lambda: client.models.generate_content(
                model="gemini-2.5-flash",
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    max_output_tokens=500,
                ),
            )
        )

        if response.candidates and response.candidates[0].content.parts:
            return response.candidates[0].content.parts[0].text
        return "No search results found."
    except Exception as e:
        _LOGGER.error("Google Search failed: %s", e)
        return f"Search failed: {e}"
