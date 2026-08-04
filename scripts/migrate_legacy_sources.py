#!/usr/bin/env python3
"""Plan historical source migration evidence without mutating Research Hub state."""

# Direct execution bootstraps the project root before local-package imports.
# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_hub.importers.legacy_evidence import main


if __name__ == "__main__":
    raise SystemExit(main())
