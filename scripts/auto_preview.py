"""Portable repository entry point for auto_preview."""

from __future__ import annotations

import sys
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _PROJECT_ROOT / "src"
while str(_SRC_ROOT) in sys.path:
    sys.path.remove(str(_SRC_ROOT))
sys.path.insert(0, str(_SRC_ROOT))

from auto_preview.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
