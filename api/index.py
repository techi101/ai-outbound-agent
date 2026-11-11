"""Vercel entrypoint. All routes are rewritten here; the app lives in server.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import app  # noqa: E402,F401
