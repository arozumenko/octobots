"""Root conftest — adds the repo root to sys.path so `bridges`, `scripts`,
and `tests` packages are importable without installation."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the octobots root is on the path.
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
