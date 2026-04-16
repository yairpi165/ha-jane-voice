"""Tests for embedding generation and pgvector semantic search (S1.6)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jane_conversation.memory.embeddings import (
    _to_pg_vector,
    backfill_embeddings,
    generate_embedding,
)


class TestToPgVector:
    def test_basic(self):
        result = _to_pg_vector([0.1, 0.2, 0.3])
        assert result == "[0.1,0.2,0.3]"

    def test_empty(self):
        assert _to_pg_vector([]) == "[]"

    def test_high_precision(self):
        result = _to_pg_vector([0.123456789])
        assert "0.123456789" in result


class TestGenerateEmbedding:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_hass = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.embeddings = [MagicMock(values=[0.1, 0.2, 0.3])]

        mock_hass.async_add_executor_job = AsyncMock(return_value=mock_response)

        result = await generate_embedding(mock_hass, mock_client, "test text")
        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_failure_returns_none(self):
        mock_hass = MagicMock()
        mock_client = MagicMock()
        mock_hass.async_add_executor_job = AsyncMock(side_effect=Exception("API error"))

        result = await generate_embedding(mock_hass, mock_client, "test text")
        assert result is None


class TestBackfillEmbeddings:
    @pytest.mark.asyncio
    async def test_backfill_skips_when_all_embedded(self):
        mock_hass = MagicMock()
        mock_client = MagicMock()

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # No rows without embeddings

        mock_pool = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        count = await backfill_embeddings(mock_hass, mock_pool, mock_client)
        assert count == 0
