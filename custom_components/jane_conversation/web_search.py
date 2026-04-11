"""Tavily web search wrapper for Jane."""

import logging

import requests

_LOGGER = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"
TIMEOUT = 10


def search_web(api_key: str, query: str, max_results: int = 3) -> str:
    """Search the web via Tavily API. Returns formatted text for GPT."""
    try:
        response = requests.post(
            TAVILY_API_URL,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "include_answer": True,
                "search_depth": "basic",
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        parts = [f'Web search results for "{query}":']

        # Tavily's pre-summarized answer
        if data.get("answer"):
            parts.append(f"\nAnswer: {data['answer']}")

        # Source snippets
        for i, result in enumerate(data.get("results", []), 1):
            title = result.get("title", "")
            content = result.get("content", "")
            if content:
                parts.append(f"\nSource {i}: {title}\n{content}")

        return "\n".join(parts)

    except requests.Timeout:
        _LOGGER.warning("Tavily search timed out for query: %s", query)
        return "Web search timed out. Please answer from your own knowledge."

    except Exception as e:
        _LOGGER.warning("Tavily search failed: %s", e)
        return f"Web search failed: {e}. Please answer from your own knowledge."
