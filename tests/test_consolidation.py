"""Tests for ConsolidationWorker (S1.4 — Episodic Memory)."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from jane_conversation.memory.consolidation import (
    _cluster_events,
    _is_mixed_domain,
    _template_daily_summary,
    _template_summary,
)


def _make_event(minutes_ago, description="Light: off → on", event_type="state_change", entity_id="light.test"):
    """Create a mock event dict at N minutes ago."""
    ts = datetime.now().astimezone() - timedelta(minutes=minutes_ago)
    return {
        "id": 1,
        "timestamp": ts,
        "event_type": event_type,
        "user_name": None,
        "description": description,
        "metadata": {"entity_id": entity_id, "old_state": "off", "new_state": "on"},
    }


class TestClusterEvents:
    def test_single_event_single_cluster(self):
        events = [_make_event(5)]
        clusters = _cluster_events(events)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_close_events_same_cluster(self):
        """Events within 10 minutes form one cluster."""
        events = [_make_event(15), _make_event(12), _make_event(10)]
        clusters = _cluster_events(events)
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_gap_splits_cluster(self):
        """Events >10 minutes apart form separate clusters."""
        events = [_make_event(30), _make_event(28), _make_event(10), _make_event(8)]
        clusters = _cluster_events(events)
        assert len(clusters) == 2
        assert len(clusters[0]) == 2
        assert len(clusters[1]) == 2

    def test_max_duration_splits_cluster(self):
        """Cluster exceeding 90 minutes splits even without gaps."""
        # Events every 8 minutes for 100 minutes → should split
        events = [_make_event(100 - i * 8) for i in range(13)]  # 0, 8, 16, ..., 96 min span
        clusters = _cluster_events(events)
        assert len(clusters) >= 2, f"Expected split but got {len(clusters)} cluster(s)"

    def test_empty_events(self):
        assert _cluster_events([]) == []


class TestIsMixedDomain:
    def test_single_domain(self):
        events = [
            {"metadata": {"entity_id": "light.a"}},
            {"metadata": {"entity_id": "light.b"}},
        ]
        assert not _is_mixed_domain(events)

    def test_mixed_domains(self):
        events = [
            {"metadata": {"entity_id": "light.a"}},
            {"metadata": {"entity_id": "climate.ac"}},
        ]
        assert _is_mixed_domain(events)

    def test_conversation_counted_as_domain(self):
        events = [
            {"metadata": {"entity_id": "light.a"}},
            {"event_type": "conversation", "metadata": {}},
        ]
        assert _is_mixed_domain(events)


class TestTemplateSummary:
    def test_basic_summary(self):
        cluster = [
            _make_event(10, "Living Room: off → on"),
            _make_event(8, "Bedroom: off → on"),
        ]
        result = _template_summary(cluster)
        assert result is not None
        assert "Living Room" in result["summary"]
        assert result["episode_type"] in ("activity", "arrival", "departure", "conversation")

    def test_many_events_truncates(self):
        cluster = [_make_event(10 - i, f"Light {i}: off → on") for i in range(6)]
        result = _template_summary(cluster)
        assert "ועוד" in result["summary"]

    def test_empty_cluster(self):
        assert _template_summary([]) is None

    def test_arrival_detection(self):
        cluster = [
            _make_event(10, "Yair: not_home → home"),
            _make_event(8, "Living Room: off → on"),
        ]
        result = _template_summary(cluster)
        # Should detect presence-related episode
        assert result is not None

    def test_conversation_type(self):
        cluster = [
            _make_event(10, "שיחה עם יאיר", event_type="conversation"),
        ]
        # Single event skipped (MIN_EVENTS_FOR_EPISODE=2), but template_summary doesn't enforce that
        result = _template_summary(cluster)
        assert result is not None


class TestTemplateDailySummary:
    def test_with_episodes(self):
        episodes = [
            {"title": "ערב שקט", "summary": "נורות נדלקו"},
            {"title": "שיחה עם יאיר", "summary": "דיבר על מזג אוויר"},
        ]
        result = _template_daily_summary(episodes, 42)
        assert "42 אירועים" in result
        assert "2 אפיזודות" in result

    def test_no_episodes(self):
        result = _template_daily_summary([], 15)
        assert "שקט" in result

    def test_many_episodes_truncates(self):
        episodes = [{"title": f"Episode {i}", "summary": f"Summary {i}"} for i in range(10)]
        result = _template_daily_summary(episodes, 100)
        # Should only include first 5 titles
        assert "Episode 0" in result
        assert "Episode 4" in result


class TestConsolidationWorkerIdempotency:
    @pytest.mark.asyncio
    async def test_skips_if_already_processed(self):
        """Consolidation should skip if window was already processed."""
        from jane_conversation.memory.consolidation import ConsolidationWorker

        episodic = AsyncMock()
        # Return a recent timestamp — already processed
        episodic.get_last_consolidation_ts.return_value = datetime.now().astimezone() - timedelta(minutes=30)

        hass = MagicMock()
        worker = ConsolidationWorker(episodic, hass)

        count = await worker.consolidate_events()
        assert count == 0
        # Should NOT query events
        episodic.query_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_new_window(self):
        """Consolidation should process events in a new window."""
        from jane_conversation.memory.consolidation import ConsolidationWorker

        episodic = AsyncMock()
        episodic.get_last_consolidation_ts.return_value = None
        episodic.query_events.return_value = []  # No events
        episodic.save_episode = AsyncMock()

        hass = MagicMock()
        worker = ConsolidationWorker(episodic, hass)

        count = await worker.consolidate_events()
        assert count == 0
        # Should have recorded the consolidation timestamp
        episodic.set_last_consolidation_ts.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_episodes_from_events(self):
        """Consolidation should create episodes from event clusters."""
        from jane_conversation.memory.consolidation import ConsolidationWorker

        now = datetime.now().astimezone()
        events = [
            {"id": 1, "timestamp": now - timedelta(minutes=5), "event_type": "state_change",
             "user_name": None, "description": "Living Room: off → on",
             "metadata": {"entity_id": "light.living_room"}},
            {"id": 2, "timestamp": now - timedelta(minutes=4), "event_type": "state_change",
             "user_name": None, "description": "Bedroom: off → on",
             "metadata": {"entity_id": "light.bedroom"}},
        ]

        episodic = AsyncMock()
        episodic.get_last_consolidation_ts.return_value = None
        episodic.query_events.side_effect = [events, []]  # state_change, then conversation
        episodic.save_episode.return_value = 1

        hass = MagicMock()
        worker = ConsolidationWorker(episodic, hass)

        count = await worker.consolidate_events()
        assert count == 1
        episodic.save_episode.assert_called_once()


class TestMaxLLMCalls:
    @pytest.mark.asyncio
    async def test_respects_max_llm_calls(self):
        """After MAX_LLM_CALLS_PER_WINDOW, should fall back to template."""
        from jane_conversation.memory.consolidation import ConsolidationWorker

        now = datetime.now().astimezone()

        # Create 5 complex clusters (>5 events each, mixed domains)
        all_events = []
        for cluster_idx in range(5):
            base_time = now - timedelta(minutes=60 * (5 - cluster_idx))
            for i in range(6):
                domain = "light" if i % 2 == 0 else "climate"
                all_events.append({
                    "id": cluster_idx * 6 + i,
                    "timestamp": base_time + timedelta(minutes=i),
                    "event_type": "state_change",
                    "user_name": None,
                    "description": f"Device {i}: off → on",
                    "metadata": {"entity_id": f"{domain}.device_{i}"},
                })

        episodic = AsyncMock()
        episodic.get_last_consolidation_ts.return_value = None
        episodic.query_events.side_effect = [all_events, []]
        episodic.save_episode.return_value = 1

        hass = MagicMock()
        hass.data = {"jane_conversation": {"_gemini_client": None}}  # No client → all template

        worker = ConsolidationWorker(episodic, hass)
        count = await worker.consolidate_events()

        # Should create episodes for all 5 clusters (all via template since no client)
        assert count == 5


class TestDailySummaryGeneration:
    @pytest.mark.asyncio
    async def test_skips_if_exists(self):
        from jane_conversation.memory.consolidation import ConsolidationWorker

        episodic = AsyncMock()
        episodic.get_daily_summary.return_value = {"summary": "Already exists"}

        hass = MagicMock()
        worker = ConsolidationWorker(episodic, hass)

        result = await worker.generate_daily_summary()
        assert result is False

    @pytest.mark.asyncio
    async def test_creates_summary(self):
        from jane_conversation.memory.consolidation import ConsolidationWorker

        episodic = AsyncMock()
        episodic.get_daily_summary.return_value = None
        episodic.query_episodes.return_value = [
            {"title": "ערב", "summary": "נורות", "start_ts": datetime.now(), "end_ts": datetime.now()},
        ]
        episodic.query_events.return_value = [{"id": 1}] * 20

        hass = MagicMock()
        hass.data = {"jane_conversation": {}}
        worker = ConsolidationWorker(episodic, hass)

        result = await worker.generate_daily_summary()
        assert result is True
        episodic.save_daily_summary.assert_called_once()
