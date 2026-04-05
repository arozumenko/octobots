"""Shared role aliases and display names for Octobots.

Used by supervisor, telegram-bridge, and scheduler to resolve
@shorthand → full role names consistently.
"""
from __future__ import annotations


# Shorthand → full role name
ROLE_ALIASES: dict[str, str] = {
    # Short aliases
    "pm": "project-manager",
    "max": "project-manager",
    "ba": "ba",
    "alex": "ba",
    "tl": "tech-lead",
    "rio": "tech-lead",
    "py": "python-dev",
    "js": "js-dev",
    "jay": "js-dev",
    "qa": "qa-engineer",
    "sage": "qa-engineer",
    "kit": "scout",
    "scout": "scout",
    "pa": "personal-assistant",
    # Full names (identity mapping)
    "project-manager": "project-manager",
    "tech-lead": "tech-lead",
    "python-dev": "python-dev",
    "js-dev": "js-dev",
    "qa-engineer": "qa-engineer",
    "personal-assistant": "personal-assistant",
    # Broadcast aliases
    "all": "all",
    "everyone": "all",
    "team": "all",
}

# Role → display label
ROLE_DISPLAY: dict[str, str] = {
    "project-manager": "📋 pm",
    "ba": "📝 ba",
    "tech-lead": "🏗️ tl",
    "python-dev": "🐍 py",
    "js-dev": "⚡ js",
    "qa-engineer": "🧪 qa",
    "scout": "🔍 scout",
    "personal-assistant": "🟣 pa",
}


def resolve_alias(name: str) -> str:
    """Resolve a role alias to the canonical role name.

    Returns the input unchanged if not a known alias.
    """
    return ROLE_ALIASES.get(name.lower(), name)
