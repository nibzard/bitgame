"""Test bootstrap: put reference/ on sys.path so imports stay flat."""

import sys
from pathlib import Path

REFERENCE = Path(__file__).resolve().parents[1] / "reference"
if str(REFERENCE) not in sys.path:
    sys.path.insert(0, str(REFERENCE))
