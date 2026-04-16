"""Embedding generation and storage for pgvector semantic search (S1.6).

Uses Gemini text-embedding-004 (768 dims) to embed episode summaries
and daily summaries. Stored as vector(768) in PostgreSQL via pgvector.
"""

import logging

_LOGGER = logging.getLogger(__name__)

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMS = 768


def _to_pg_vector(embedding: list[float]) -> str:
    """Convert Python float list to pgvector-compatible string."""
    return "[" + ",".join(map(str, embedding)) + "]"


async def generate_embedding(hass, client, text: str) -> list[float] | None:
    """Generate embedding via Gemini text-embedding-004. Returns None on failure."""
    try:
        from google.genai import types

        response = await hass.async_add_executor_job(
            lambda: client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMS),
            ),
        )
        return response.embeddings[0].values
    except Exception as e:
        _LOGGER.warning("Embedding generation failed: %s", e)
        return None


async def store_episode_embedding(pool, episode_id: int, embedding: list[float]):
    """Store embedding vector for an episode."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE episodes SET embedding = $1::vector WHERE id = $2",
            _to_pg_vector(embedding), episode_id,
        )


async def store_summary_embedding(pool, summary_date, embedding: list[float]):
    """Store embedding vector for a daily summary."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE daily_summaries SET embedding = $1::vector WHERE summary_date = $2",
            _to_pg_vector(embedding), summary_date,
        )


async def backfill_embeddings(hass, pool, client) -> int:
    """Backfill embeddings for episodes/summaries that don't have one yet.

    Runs as a background task after startup. Non-blocking, idempotent.
    Returns total number of embeddings generated.
    """
    count = 0

    try:
        async with pool.acquire() as conn:
            episodes = await conn.fetch(
                "SELECT id, title, summary FROM episodes WHERE embedding IS NULL"
            )
            summaries = await conn.fetch(
                "SELECT summary_date, summary FROM daily_summaries WHERE embedding IS NULL"
            )
    except Exception as e:
        _LOGGER.warning("Backfill query failed: %s", e)
        return 0

    for ep in episodes:
        text = f"{ep['title']} {ep['summary']}"
        embedding = await generate_embedding(hass, client, text)
        if embedding:
            await store_episode_embedding(pool, ep["id"], embedding)
            count += 1

    for ds in summaries:
        embedding = await generate_embedding(hass, client, ds["summary"])
        if embedding:
            await store_summary_embedding(pool, ds["summary_date"], embedding)
            count += 1

    if count:
        _LOGGER.info("Backfill: generated %d embeddings (%d episodes, %d summaries)",
                      count, len(episodes), len(summaries))
    return count
