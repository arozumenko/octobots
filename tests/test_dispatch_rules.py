"""Tests for per-role dispatch rules rendering.

Covers:
  - Role without dispatch_rules → DEFAULT_DISPATCH_RULES used
  - Role with dispatch_rules → custom block used, placeholders substituted
  - {msg_id} and {octobots_dir} placeholders are substituted correctly
  - Unknown placeholders do not raise and become empty strings
  - Empty / whitespace-only dispatch_rules → falls back to default
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make scripts/ importable without installing the package.
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from supervisor import DEFAULT_DISPATCH_RULES, render_dispatch_rules  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

MSG_ID = "abc123"
OCTOBOTS_DIR = "/opt/octobots"


# ── tests ────────────────────────────────────────────────────────────────────

class TestDefaultFallback:
    """Role without dispatch_rules (or empty) should use DEFAULT_DISPATCH_RULES."""

    def test_absent_key_uses_default(self):
        """Frontmatter with no dispatch_rules key → default block."""
        fm: dict = {}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert "RULES" in result
        assert "relay.py" in result

    def test_none_value_uses_default(self):
        """dispatch_rules: null in YAML parses to None → default block."""
        fm = {"dispatch_rules": None}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert "relay.py" in result

    def test_empty_string_uses_default(self):
        """dispatch_rules: '' → blank after strip → fallback to default."""
        fm = {"dispatch_rules": ""}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert "relay.py" in result

    def test_whitespace_only_uses_default(self):
        """dispatch_rules containing only whitespace → fallback to default."""
        fm = {"dispatch_rules": "   \n\t  "}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert "relay.py" in result


class TestCustomRules:
    """Role with a non-empty dispatch_rules → custom block is used."""

    def test_custom_block_replaces_default(self):
        custom = "Call submit_meal_analysis when done. No relay needed."
        fm = {"dispatch_rules": custom}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert result == custom
        assert "relay.py" not in result

    def test_custom_block_with_msg_id_placeholder(self):
        custom = "Ack message {msg_id} via MCP tool."
        fm = {"dispatch_rules": custom}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert MSG_ID in result
        assert "{msg_id}" not in result

    def test_custom_block_with_octobots_dir_placeholder(self):
        custom = "Run {octobots_dir}/scripts/helper.py"
        fm = {"dispatch_rules": custom}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert OCTOBOTS_DIR in result
        assert "{octobots_dir}" not in result

    def test_both_placeholders_substituted(self):
        custom = "relay {octobots_dir}/relay.py ack {msg_id}"
        fm = {"dispatch_rules": custom}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert MSG_ID in result
        assert OCTOBOTS_DIR in result
        assert "{msg_id}" not in result
        assert "{octobots_dir}" not in result

    def test_unknown_placeholder_becomes_empty_string(self):
        """An unknown placeholder must not raise; it becomes ''."""
        custom = "Do something {unknown_future_placeholder} and done."
        fm = {"dispatch_rules": custom}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert "{unknown_future_placeholder}" not in result
        assert "Do something" in result
        assert "and done." in result


class TestDefaultPlaceholders:
    """DEFAULT_DISPATCH_RULES placeholders are substituted by render_dispatch_rules."""

    def test_default_contains_msg_id(self):
        fm: dict = {}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert MSG_ID in result

    def test_default_contains_octobots_dir(self):
        fm: dict = {}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert OCTOBOTS_DIR in result

    def test_default_has_no_unresolved_placeholders(self):
        fm: dict = {}
        result = render_dispatch_rules(fm, MSG_ID, OCTOBOTS_DIR)
        assert "{msg_id}" not in result
        assert "{octobots_dir}" not in result
