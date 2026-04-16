"""Tests for _repair_json — truncated JSON recovery from Gemini extraction."""

import json

import pytest

from jane_conversation.memory.extraction import _repair_json


class TestRepairJson:
    """4 truncation patterns that Gemini can produce."""

    def test_truncated_mid_value(self):
        """Pattern 1: truncated in the middle of a string value."""
        raw = '{"user": "likes pi'
        result = _repair_json(raw)
        assert result["user"] == "likes pi"

    def test_truncated_mid_key(self):
        """Pattern 2: truncated in the middle of a key after a complete entry."""
        raw = '{"user": "done", "fam'
        result = _repair_json(raw)
        assert result["user"] == "done"

    def test_truncated_mid_array(self):
        """Pattern 3: truncated inside a nested array of objects."""
        raw = '{"preferences": [{"key": "food", "value": "pizza"}, {"key": "tea"'
        result = _repair_json(raw)
        assert result["preferences"][0]["key"] == "food"

    def test_truncated_with_escaped_quotes(self):
        """Pattern 4: truncated with escaped quotes in value."""
        raw = '{"user": "he said \\"hello\\" and then'
        result = _repair_json(raw)
        assert "hello" in result["user"]

    def test_escaped_quotes_even_real_quotes(self):
        """Escaped quotes must not break the unclosed-string check.

        Here we have 4 real quotes (all paired) + 2 escaped quotes.
        Without correct counting the parity check would add a spurious quote.
        """
        raw = '{"user": "likes \\"pizza\\""}'
        result = _repair_json(raw)
        assert "pizza" in result["user"]

    def test_trailing_comma(self):
        """Trailing comma after last complete value."""
        raw = '{"user": "done", "family": "ok",'
        result = _repair_json(raw)
        assert result["user"] == "done"
        assert result["family"] == "ok"

    def test_valid_json_passthrough(self):
        """Already-valid JSON should not raise."""
        raw = '{"user": "complete"}'
        result = _repair_json(raw)
        assert result["user"] == "complete"

    def test_completely_broken_raises(self):
        """Unrepairable garbage should raise JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            _repair_json("not json at all {{{")
