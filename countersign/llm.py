"""Run a tool-calling LLM through an episode. Standard library only (urllib).

Two wire formats are supported: Anthropic Messages and OpenAI-style chat completions. The second
also covers Google Gemini, OpenRouter, Groq and most local servers through a base URL.
Bring your own key and model name.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable, Optional, Protocol

from .env import Env

SYSTEM_SUFFIX = ("\n\nWork through the tools. Amounts are integer cents and dates are day numbers. "
                 "When there is nothing more you can do today, call end_day.")
Call = tuple[str, str, dict]            # (call id, tool name, arguments)

# provider -> (wire format, default base URL, environment variable holding the key)
PROVIDERS = {
    "anthropic": ("anthropic", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY"),
}


class QuotaExhausted(RuntimeError):
    """The provider says no more requests today (or keeps rate-limiting after every retry)."""


class Backend(Protocol):
    usage: dict

    def start(self, system: str, tools: list[dict]) -> None: ...
    def turn(self, results: Optional[list[tuple[str, str]]], nudge: str = "") -> list[Call]: ...


def _retry_hint(headers, detail: str) -> Optional[float]:
    value = headers.get("retry-after") if headers else None
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    m = re.search(r'retry in ([\d.]+)\s*s|"retryDelay":\s*"([\d.]+)s"', detail)
    return float(m.group(1) or m.group(2)) if m else None


def _is_daily(detail: str) -> bool:
    flat = re.sub(r"[\s_-]", "", detail.lower())
    return any(k in flat for k in ("perday", "daily", "quotaexceeded", "insufficientquota", "creditbalance"))


class Http:
    """POST JSON with pacing and patient handling of rate limits."""

    def __init__(self, rpm: float = 0, timeout: int = 300, tries: int = 7,
                 sleep: Callable[[float], None] = time.sleep):
        self.gap = 60.0 / rpm if rpm else 0.0
        self.timeout, self.tries, self.sleep = timeout, tries, sleep
        self._last = 0.0

    def post(self, url: str, headers: dict, body: dict) -> dict:
        data = json.dumps(body).encode()
        for attempt in range(self.tries):
            wait = self._last + self.gap - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")
                short = detail[:600]
                if e.code == 429:
                    if _is_daily(detail):
                        raise QuotaExhausted(f"daily quota reached: {short}") from None
                    if attempt == self.tries - 1:
                        raise QuotaExhausted(f"still rate-limited after {self.tries} tries: {short}") from None
                    self.sleep(min(_retry_hint(e.headers, detail) or 15 * (attempt + 1), 120) + 1)
                elif e.code in (408, 409, 500, 502, 503, 504, 529) and attempt < self.tries - 1:
                    self.sleep(min(2 ** attempt * 2, 60))
                else:
                    raise RuntimeError(f"HTTP {e.code} from {url}: {short}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == self.tries - 1:
                    raise RuntimeError(f"cannot reach {url}: {e}") from None
                self.sleep(min(2 ** attempt * 2, 60))
        raise RuntimeError("unreachable")


class AnthropicBackend:
    def __init__(self, model: str, api_key: Optional[str] = None, base_url: str = "https://api.anthropic.com",
                 max_tokens: int = 4096, http: Optional[Http] = None):
        self.model, self.max_tokens = model, max_tokens
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.http = http or Http()
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        if not self.key:
            raise RuntimeError("set ANTHROPIC_API_KEY")

    def start(self, system: str, tools: list[dict]) -> None:
        self.system = [{"type": "text", "text": system}]
        self.tools = [dict(t) for t in tools]
        self.tools[-1]["cache_control"] = {"type": "ephemeral"}      # cache the fixed prefix
        self.messages: list[dict] = [{"role": "user", "content": [{"type": "text", "text": "Begin."}]}]

    def turn(self, results: Optional[list[tuple[str, str]]], nudge: str = "") -> list[Call]:
        if results:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": cid, "content": text} for cid, text in results]})
        elif nudge:
            self.messages.append({"role": "user", "content": [{"type": "text", "text": nudge}]})
        for m in self.messages:                                      # one moving cache breakpoint
            if m["role"] == "user":
                for block in m["content"]:
                    block.pop("cache_control", None)
        self.messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}
        out = self.http.post(self.url, {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                             {"model": self.model, "max_tokens": self.max_tokens, "system": self.system,
                              "tools": self.tools, "messages": self.messages})
        u = out.get("usage") or {}
        self.usage["requests"] += 1
        self.usage["input_tokens"] += (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                       + u.get("cache_creation_input_tokens", 0))
        self.usage["output_tokens"] += u.get("output_tokens", 0)
        content = out.get("content") or [{"type": "text", "text": "(no output)"}]
        self.messages.append({"role": "assistant", "content": content})
        return [(b["id"], b["name"], b.get("input") or {}) for b in content if b.get("type") == "tool_use"]


class OpenAIBackend:
    _DROP = ("refusal", "annotations", "audio", "function_call")

    def __init__(self, model: str, api_key: Optional[str] = None, base_url: str = "https://api.openai.com/v1",
                 reasoning_effort: str = "", http: Optional[Http] = None):
        self.model, self.effort = model, reasoning_effort
        self.key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.http = http or Http()
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

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
        body = {"model": self.model, "messages": self.messages, "tools": self.tools}
        if self.effort:
            body["reasoning_effort"] = self.effort
        out = self.http.post(self.url, headers, body)
        u = out.get("usage") or {}
        self.usage["requests"] += 1
        self.usage["input_tokens"] += u.get("prompt_tokens", 0) or 0
        self.usage["output_tokens"] += u.get("completion_tokens", 0) or 0
        choices = out.get("choices") or [{}]
        msg = choices[0].get("message") or {"role": "assistant", "content": "(no output)"}
        # Keep everything the server sent (some providers attach fields that must be echoed back,
        # such as reasoning signatures on tool calls), minus output-only keys and nulls.
        keep = {k: v for k, v in msg.items() if k not in self._DROP and v is not None}
        keep.setdefault("role", "assistant")
        if "content" not in keep and not keep.get("tool_calls"):
            keep["content"] = "(no output)"
        self.messages.append(keep)
        calls: list[Call] = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable_arguments": fn.get("arguments")}
            calls.append((c.get("id") or f"call_{i}", fn.get("name", ""), args if isinstance(args, dict) else {}))
        return calls


def make_backend(provider: str, model: str, base_url: str = "", api_key_env: str = "", rpm: float = 0,
                 reasoning_effort: str = "") -> Backend:
    wire, default_url, default_env = PROVIDERS[provider]
    key = os.environ.get(api_key_env or default_env, "")
    if not key and not base_url:
        raise RuntimeError(f"set the {api_key_env or default_env} environment variable")
    http = Http(rpm=rpm)
    if wire == "anthropic":
        return AnthropicBackend(model, api_key=key, base_url=base_url or default_url, http=http)
    return OpenAIBackend(model, api_key=key, base_url=base_url or default_url,
                         reasoning_effort=reasoning_effort, http=http)


def list_models(provider: str, base_url: str = "", api_key_env: str = "") -> list[str]:
    """Model IDs the key can see, from the provider's /models endpoint."""
    wire, default_url, default_env = PROVIDERS[provider]
    key = os.environ.get(api_key_env or default_env, "")
    url = (base_url or default_url).rstrip("/") + ("/v1/models" if wire == "anthropic" else "/models")
    headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if wire == "anthropic"
               else {"authorization": f"Bearer {key}"})
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode(errors='replace')[:600]}") from None
    return sorted(str(m.get("id", "")) for m in data.get("data", []))


def run_llm(env: Env, backend: Backend, max_turns: int = 400, max_nudges: int = 3,
            log: Optional[Callable[[str], None]] = None) -> dict:
    """Drive `backend` until the period closes. Returns the scored result."""
    obs = env.reset()
    backend.start(obs["brief"] + SYSTEM_SUFFIX, obs["tools"])
    results: Optional[list[tuple[str, str]]] = None
    nudge, nudges = "", 0
    for turn in range(1, max_turns + 1):
        try:
            calls = backend.turn(results, nudge)
        except Exception as e:
            if log:
                log(f"request {turn} failed on day {env.world.day}: {type(e).__name__}")   # type: ignore[union-attr]
            raise
        if log:
            log(f"request {turn}, day {env.world.day}: "                                # type: ignore[union-attr]
                + (", ".join(name for _, name, _ in calls) or "(no tool call)"))
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
    return {**env.result(), "model_stopped_early": stopped_early, "usage": dict(getattr(backend, "usage", {}))}
