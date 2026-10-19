"""Test-only: the Python MCP server with ONE tool description altered.

Used by tests/test_mcp_rust_parity.py to prove the parity harness detects a
difference. Refuses to run unless ZURVAN_PARITY_PLANT=1, so it cannot be
mistaken for the real server.
"""
import os
import sys
from pathlib import Path

if os.environ.get("ZURVAN_PARITY_PLANT") != "1":
    raise SystemExit("refusing: _parity_plant_server.py is a test fixture (set ZURVAN_PARITY_PLANT=1)")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.mcp_server as srv  # noqa: E402

srv.mcp._tool_manager._tools["zurvan_status"].description = "PLANTED DIFFERENCE"
srv.mcp.run(transport="stdio")
