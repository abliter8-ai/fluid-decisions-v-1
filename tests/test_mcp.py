from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest


def _rpc(proc, message):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise AssertionError(f"MCP exited: {proc.stderr.read()}")
        payload = json.loads(line)
        if payload.get("id") == message.get("id"):
            return payload


@pytest.mark.skipif(not __import__("importlib").util.find_spec("mcp"), reason="mcp extra not installed")
def test_mcp_stdio_lists_tools_and_static_gate():
    with tempfile.TemporaryDirectory() as data_dir:
        env = {**os.environ, "PYTHONPATH": os.getcwd(), "FLUID_DATA_DIR": data_dir,
               "FD_MAX_URL": "http://127.0.0.1:1", "FD_FIRST_TIMEOUT": "0.1", "FD_TIMEOUT": "0.1"}
        proc = subprocess.Popen([sys.executable, "-m", "fluid.mcp_server"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        try:
            init = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}})
            assert "result" in init
            listed = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            names = {tool["name"] for tool in listed["result"]["tools"]}
            assert {"classify", "route", "gate_tool", "post_result", "verify_claims",
                    "health", "check_constraints", "check_validity"} <= names
            gated = _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "gate_tool", "arguments": {"tool_name": "shell", "tool_input": {"command": "rm -rf /"}}}})
            text = json.dumps(gated)
            assert "deny" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)
