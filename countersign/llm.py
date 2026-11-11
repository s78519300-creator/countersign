"""Run a tool-calling LLM through an episode. Standard library only (urllib).

Two wire formats are supported: Anthropic Messages and OpenAI-style chat completions (which also
covers most local servers through --base-url). Bring your own key and model name.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional, Protocol

from .env import Env

SYSTEM_SUFFIX = ("\n\nWork through the tools. Amounts are integer cents and dates are day numbers. "
                 "When there is nothing more you can do today, call end_day.")
Call = tuple[str, str, dict]            # (call id, tool name, arguments)


class Backend(Protocol):
    def start(self, system: str, tools: list[dict]) -> None: ...
    def turn(self, results: Optional[list[tuple[str, str]]], nudge: str = "") -> list[Call]: ...


def _post(url: str, headers: dict, body: dict, timeout: int = 300, tries: int = 4) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code not in (408, 409, 429, 500, 502, 503, 529) or attempt == tries - 1:
                raise RuntimeError(f"HTTP {e.code} from {url}: {detail}") from None
        except urllib.error.URLError as e:
            if attempt == tries - 1:
                raise RuntimeError(f"cannot reach {url}: {e.reason}") from None
        time.sleep(2 ** attempt * 2)
    raise RuntimeError("unreachable")


class AnthropicBackend:
    def __init__(self, model: str, api_key: Optional[str] = None,
                 base_url: str = "https://api.anthropic.com", max_tokens: int = 4096):
        self.model, self.max_tokens = model, max_tokens
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.url = base_url.rstrip("/") + "/v1/messages"
        if not self.key:
            raise RuntimeError("set ANTHROPIC_API_KEY")

    def start(self, system: str, tools: list[dict]) -> None:
        self.system, self.tools = system, tools
        self.messages: list[dict] = [{"role": "user", "content": "Begin."}]

    def turn(self, results: Optional[list[tuple[str, str]]], nudge: str = "") -> list[Call]:
        if results:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "content": text} for cid, text in results]})
        elif nudge:
            self.messages.append({"role": "user", "content": nudge})
        out = _post(self.url, {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                    {"model": self.model, "max_tokens": self.max_tokens, "system": self.system,
                     "tools": self.tools, "messages": self.messages})
        content = out.get("content") or [{"type": "text", "text": "(no output)"}]
        self.messages.append({"role": "assistant", "content": content})
        return [(b["id"], b["name"], b.get("input") or {}) for b in content if b.get("type") == "tool_use"]


class OpenAIBackend:
    def __init__(self, model: str, api_key: Optional[str] = None,
                 base_url: str = "https://api.openai.com/v1"):
        self.model = model
        self.key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.url = base_url.rstrip("/") + "/chat/completions"

    def start(self, system: str, tools: list[dict]) -> None:
        self.tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                         "parameters": t["input_schema"]}} for t in tools]
        self.messages: list[dict] = [{"role": "system", "content": system},
                                     {"role": "user", "content": "Begin."}]

    def turn(self, results: Optional[list[tuple[str, str]]], nudge: str = "") -> list[Call]:
        if results:
            self.messages += [{"role": "tool", "tool_call_id": cid, "content": text} for cid, text in results]
        elif nudge:
            self.messages.append({"role": "user", "content": nudge})
        headers = {"authorization": f"Bearer {self.key}"} if self.key else {}
        out = _post(self.url, headers, {"model": self.model, "messages": self.messages, "tools": self.tools})
        msg = out["choices"][0]["message"]
        self.messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")})
        calls: list[Call] = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable_arguments": c["function"].get("arguments")}
            calls.append((c["id"], c["function"]["name"], args if isinstance(args, dict) else {}))
        return calls


def run_llm(env: Env, backend: Backend, max_turns: int = 400, max_nudges: int = 3) -> dict:
    """Drive `backend` until the period closes. Returns the scored result."""
    obs = env.reset()
    backend.start(obs["brief"] + SYSTEM_SUFFIX, obs["tools"])
    results: Optional[list[tuple[str, str]]] = None
    nudge, nudges = "", 0
    for _ in range(max_turns):
        calls = backend.turn(results, nudge)
        results, nudge = None, ""
        if not calls:
            nudges += 1
            if nudges > max_nudges:
                break
            nudge = "The period is still open. Keep working with the tools, or call end_day."
            continue
        results = []
        for cid, name, args in calls:
            out = env.step({"tool": name, "args": args})[0]
            results.append((cid, json.dumps(out)))
        if env.done:
            break
    stopped_early = not env.done
    while not env.done:                 # the model gave up: the rest of the period passes untouched
        env.call("end_day")
    return {**env.result(), "model_stopped_early": stopped_early}
