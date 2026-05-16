"""Inject the repo root into sys.path so `from scripts.lib import ...`
works when pytest is invoked from outside the repo root (the
backend-fast.yml workflow runs `cd backend && uv run pytest ../scripts/tests/`
which leaves pytest's rootdir at the repo root but doesn't expose the
repo root on PYTHONPATH for nested package imports).

Phase 4 D4.2.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
