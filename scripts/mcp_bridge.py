"""Launcher for the local read-only MCP bridge (stdio).

Usage (spawned by an MCP client such as DeepSeek Harness):

    python scripts/mcp_bridge.py --base-url http://127.0.0.1:8765
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.mcp_bridge import main  # noqa: E402


if __name__ == '__main__':
    raise SystemExit(main())
