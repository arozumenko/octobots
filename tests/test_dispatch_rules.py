"""Tests for per-role dispatch rules rendering.

Covers:
  - Role without RULES.md → DEFAULT_DISPATCH_RULES used
  - Role with RULES.md → custom block used, placeholders substituted
  - {msg_id} and {octobots_dir} placeholders are substituted correctly
  - Unknown placeholders do not raise and become empty strings
  - Empty / whitespace-only RULES.md → falls back to default
  - TestFileReader: get_dispatch_rules resolution order and None fallback
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Make scripts/ importable without installing the package.
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from supervisor import DEFAULT_DISPATCH_RULES, render_dispatch_rules  # noqa: E402
import agent_registry  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

MSG_ID = "abc123"
OCTOBOTS_DIR = "/opt/octobots"


# ── tests ────────────────────────────────────────────────────────────────────

class TestDefaultFallback:
    """Role without RULES.md (or empty) should use DEFAULT_DISPATCH_RULES."""

    def test_absent_rules_uses_default(self):
        """None custom_rules → default block."""
        result = render_dispatch_rules(None, MSG_ID, OCTOBOTS_DIR)
        assert "RULES" in result
        assert "relay.py" in result

    def test_none_value_uses_default(self):
        """Explicitly passing None → default block."""
        result = render_dispatch_rules(None, MSG_ID, OCTOBOTS_DIR)
        assert "relay.py" in result

    def test_empty_string_uses_default(self):
        """Empty string → blank after strip → fallback to default."""
        result = render_dispatch_rules("", MSG_ID, OCTOBOTS_DIR)
        assert "relay.py" in result

    def test_whitespace_only_uses_default(self):
        """String containing only whitespace → fallback to default."""
        result = render_dispatch_rules("   \n\t  ", MSG_ID, OCTOBOTS_DIR)
        assert "relay.py" in result


class TestCustomRules:
    """Role with a non-empty custom_rules string → custom block is used."""

    def test_custom_block_replaces_default(self):
        custom = "Call submit_meal_analysis when done. No relay needed."
        result = render_dispatch_rules(custom, MSG_ID, OCTOBOTS_DIR)
        assert result == custom
        assert "relay.py" not in result

    def test_custom_block_with_msg_id_placeholder(self):
        custom = "Ack message {msg_id} via MCP tool."
        result = render_dispatch_rules(custom, MSG_ID, OCTOBOTS_DIR)
        assert MSG_ID in result
        assert "{msg_id}" not in result

    def test_custom_block_with_octobots_dir_placeholder(self):
        custom = "Run {octobots_dir}/scripts/helper.py"
        result = render_dispatch_rules(custom, MSG_ID, OCTOBOTS_DIR)
        assert OCTOBOTS_DIR in result
        assert "{octobots_dir}" not in result

    def test_both_placeholders_substituted(self):
        custom = "relay {octobots_dir}/relay.py ack {msg_id}"
        result = render_dispatch_rules(custom, MSG_ID, OCTOBOTS_DIR)
        assert MSG_ID in result
        assert OCTOBOTS_DIR in result
        assert "{msg_id}" not in result
        assert "{octobots_dir}" not in result

    def test_unknown_placeholder_becomes_empty_string(self):
        """An unknown placeholder must not raise; it becomes ''."""
        custom = "Do something {unknown_future_placeholder} and done."
        result = render_dispatch_rules(custom, MSG_ID, OCTOBOTS_DIR)
        assert "{unknown_future_placeholder}" not in result
        assert "Do something" in result
        assert "and done." in result

    def test_custom_rules_from_file(self, tmp_path):
        """RULES.md on disk → read by get_dispatch_rules → passed to render."""
        role = "test-role"
        agent_dir = tmp_path / role
        agent_dir.mkdir()
        rules_content = "Analyze and call submit_result. Ack via MCP. No relay."
        (agent_dir / "RULES.md").write_text(rules_content)

        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path):
            content = agent_registry.get_dispatch_rules(role)

        assert content == rules_content
        result = render_dispatch_rules(content, MSG_ID, OCTOBOTS_DIR)
        assert result == rules_content


class TestDefaultPlaceholders:
    """DEFAULT_DISPATCH_RULES placeholders are substituted by render_dispatch_rules."""

    def test_default_contains_msg_id(self):
        result = render_dispatch_rules(None, MSG_ID, OCTOBOTS_DIR)
        assert MSG_ID in result

    def test_default_contains_octobots_dir(self):
        result = render_dispatch_rules(None, MSG_ID, OCTOBOTS_DIR)
        assert OCTOBOTS_DIR in result

    def test_default_has_no_unresolved_placeholders(self):
        result = render_dispatch_rules(None, MSG_ID, OCTOBOTS_DIR)
        assert "{msg_id}" not in result
        assert "{octobots_dir}" not in result

    def test_bundled_default_rules_file_has_no_unresolved_placeholders(self):
        """The bundled shared/default_rules.md renders without leftover placeholders."""
        result = render_dispatch_rules(None, MSG_ID, OCTOBOTS_DIR)
        assert "{msg_id}" not in result
        assert "{octobots_dir}" not in result


class TestFileReader:
    """Tests for get_dispatch_rules — file resolution and None fallback."""

    def test_returns_none_when_no_rules_md(self, tmp_path):
        """No RULES.md anywhere → returns None."""
        role = "no-rules-role"
        agent_dir = tmp_path / role
        agent_dir.mkdir()
        # Only AGENT.md exists, no RULES.md
        (agent_dir / "AGENT.md").write_text("---\nname: no-rules-role\n---\n")

        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path):
            with patch.object(agent_registry, "PROJECT_DIR", tmp_path):
                result = agent_registry.get_dispatch_rules(role)

        assert result is None

    def test_returns_installed_agent_rules_md(self, tmp_path):
        """RULES.md in installed agents dir → content returned."""
        role = "python-dev"
        agent_dir = tmp_path / role
        agent_dir.mkdir()
        rules = "Do the work. Commit. PR. Ack via relay.py. Notify."
        (agent_dir / "RULES.md").write_text(rules)

        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path):
            with patch.object(agent_registry, "PROJECT_DIR", tmp_path):
                result = agent_registry.get_dispatch_rules(role)

        assert result == rules

    def test_project_override_beats_installed(self, tmp_path):
        """Project .octobots/roles/<role>/RULES.md takes priority over installed."""
        role = "python-dev"

        # Installed agent
        installed_dir = tmp_path / "installed" / role
        installed_dir.mkdir(parents=True)
        (installed_dir / "RULES.md").write_text("Installed rules")

        # Project override
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        override_dir = project_dir / ".octobots" / "roles" / role
        override_dir.mkdir(parents=True)
        (override_dir / "RULES.md").write_text("Project override rules")

        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path / "installed"):
            with patch.object(agent_registry, "PROJECT_DIR", project_dir):
                result = agent_registry.get_dispatch_rules(role)

        assert result == "Project override rules"

    def test_installed_used_when_no_project_override(self, tmp_path):
        """Only installed RULES.md present → that content is used."""
        role = "js-dev"

        installed_dir = tmp_path / "installed" / role
        installed_dir.mkdir(parents=True)
        (installed_dir / "RULES.md").write_text("JS dev rules from install")

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path / "installed"):
            with patch.object(agent_registry, "PROJECT_DIR", project_dir):
                result = agent_registry.get_dispatch_rules(role)

        assert result == "JS dev rules from install"

    def test_empty_rules_md_falls_through_to_none(self, tmp_path):
        """RULES.md exists but is empty → returns None (falls back to default)."""
        role = "empty-rules"
        agent_dir = tmp_path / role
        agent_dir.mkdir()
        (agent_dir / "RULES.md").write_text("")

        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path):
            with patch.object(agent_registry, "PROJECT_DIR", tmp_path):
                result = agent_registry.get_dispatch_rules(role)

        assert result is None

    def test_whitespace_only_rules_md_falls_through_to_none(self, tmp_path):
        """RULES.md with only whitespace → treated as empty → returns None."""
        role = "whitespace-role"
        agent_dir = tmp_path / role
        agent_dir.mkdir()
        (agent_dir / "RULES.md").write_text("   \n\t\n  ")

        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path):
            with patch.object(agent_registry, "PROJECT_DIR", tmp_path):
                result = agent_registry.get_dispatch_rules(role)

        assert result is None

    def test_role_not_found_returns_none(self, tmp_path):
        """Role with no agent dir → returns None."""
        with patch.object(agent_registry, "INSTALLED_AGENTS", tmp_path):
            with patch.object(agent_registry, "PROJECT_DIR", tmp_path):
                result = agent_registry.get_dispatch_rules("nonexistent-role")

        assert result is None
