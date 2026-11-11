"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0), standard library only.

The episode lives in this process. The agent on the other side of the pipe can only call tools;
it cannot read the answer key or the verifier. The result is written to a file when the period closes.
"""
from __future__ import annotations

import json
import sys
from typing import IO, Optional

from . import __version__
from .env import Env
from .tools import TOOLS

PROTOCOL = "2025-06-18"


def serve(level: str, seed: int, result_path: Optional[str] = None,
          stdin: IO[str] = sys.stdin, stdout: IO[str] = sys.stdout) -> None:
    env = Env(level, seed)
    obs = env.reset()
    written = False

    def send(msg: dict) -> None:
        stdout.write(json.dumps(msg) + "\n")
        stdout.flush()

    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        if not isinstance(req, dict):
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})
            continue
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if rid is None:                       # notification
            continue
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "countersign", "version": __version__},
                      "instructions": obs["brief"]}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": t["name"], "description": t["description"],
                                 "inputSchema": t["input_schema"]} for t in TOOLS]}
        elif method == "tools/call":
            out, _, done, _ = env.step({"tool": params.get("name", ""), "args": params.get("arguments")})
            result = {"content": [{"type": "text", "text": json.dumps(out)}], "isError": "error" in out}
            if done and result_path and not written:
                written = True
                with open(result_path, "w", encoding="utf-8") as f:
                    json.dump({"result": env.result(), "trajectory": env.trajectory()}, f, indent=1)
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})
            continue
        send({"jsonrpc": "2.0", "id": rid, "result": result})
