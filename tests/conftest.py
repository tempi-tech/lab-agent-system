from __future__ import annotations

import sys
from pathlib import Path

# Ensure `import src...` works regardless of whether tests are run via
# `pytest` entrypoint or `python -m pytest`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

