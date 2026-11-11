import functools
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from countersign import LEVELS, POLICIES, Env, generate, run_episode
from countersign.audit import audit
from countersign.cli import main
from countersign.llm import AnthropicBackend, Http, OpenAIBackend, QuotaExhausted, run_llm
from countersign.policies import reference
from countersign.report import render

SEEDS = range(40)


@pytest.mark.parametrize("level", LEVELS)
def test_reference_clerk_solves_every_scenario(level):
    for seed in SEEDS:
        res, _ = run_episode(reference, level, seed)
        assert res["passed"], (level, seed, [d for d in res["per_document"] if not d["correct"]])
        assert res["reward"] == 1.0 and res["fraud_loss_cents"] == 0 and not res["out_of_steps"]


@pytest.mark.parametrize("name", [n for n in POLICIES if n != "reference"])
@pytest.mark.parametrize("level", LEVELS)
def test_shortcut_policies_never_pass(name, level):
    for seed in SEEDS:
        res, _ = run_episode(POLICIES[name], level, seed)
        assert not res["passed"], (name, level, seed)


def test_eager_helper_loses_money_to_fraud():
    for seed in SEEDS:
        res, _ = run_episode(POLICIES["pay_all"], "hard", seed)
        assert res["fraud_loss_cents"] > 0 and res["policy_violations"] > 0


def test_full_audit_is_sound():
    out = audit(seeds=12)
    assert out["ok"], out["failures"]
    assert out["ablation_relevant_runs"] > 100 and out["ablation_relevant_passes"] == 0


def test_same_seed_same_world_and_different_seed_differs():
    a = run_episode(reference, "hard", 5)[1].trajectory()
    b = run_episode(reference, "hard", 5)[1].trajectory()
    c = run_episode(reference, "hard", 6)[1].trajectory()
    assert a == b and a != c


def test_policy_parameters_vary_between_companies():
    thresholds = {generate(s, "medium").params.approval_threshold for s in range(40)}
    tolerances = {generate(s, "medium").params.tolerance_bp for s in range(40)}
    assert len(thresholds) > 1 and len(tolerances) > 1


def test_bad_tool_calls_return_errors_not_exceptions():
    env = Env("easy", 0)
    env.reset()
    bad = [{"tool": "nope", "args": {}}, {"tool": "get_invoice", "args": {}},
           {"tool": "get_invoice", "args": {"invoice_id": 7}}, {"tool": "get_invoice", "args": "x"},
           {"tool": "reject_invoice", "args": {"invoice_id": "D-0001", "reason": "because"}},
           {"tool": "pay_invoice", "args": {"invoice_id": "D-0001", "amount_cents": 1.5}},
           {"tool": "pay_invoice", "args": {"invoice_id": "D-0001", "amount_cents": True}},
           {"tool": "get_status", "args": {"extra": 1}}, {"tool": "get_invoice", "args": {"invoice_id": "D-9999"}},
           {}, "not a dict", {"tool": "pay_invoice", "args": {"invoice_id": "D-0001", "amount_cents": -5}}]
    for action in bad:
        obs, reward, done, _ = env.step(action)
        assert "error" in obs and reward == 0.0 and not done


def test_future_documents_are_invisible():
    env = Env("hard", 3)
    env.reset()
    world = env.world
    future = [d.id for d in world.s.invoices.values() if d.received_day > 1]
    assert future
    assert "error" in env.call("get_invoice", invoice_id=future[0])
    listed = {r["invoice_id"] for r in env.call("list_invoices")["invoices"]}
    assert not listed & set(future)


def test_period_can_only_close_on_the_last_day_and_then_stays_closed():
    env = Env("easy", 1)
    env.reset()
    assert "error" in env.call("close_period")
    while not env.done:
        env.call("end_day")
    assert env.call("get_status") == {"error": "the period is closed"}


def test_step_limit_closes_the_period():
    env = Env("easy", 1)
    env.reset()
    env.world.p.max_steps = 5
    for _ in range(5):
        env.call("get_status")
    assert env.done and env.result()["out_of_steps"] and not env.result()["passed"]


def _scenario_with(case, level="hard"):
    for seed in range(200):
        s = generate(seed, level)
        if case in s.cases:
            doc = next(d for d, t in s.truth.items() if t.case == case)
            return seed, doc
    raise AssertionError(case)


def _advance_to(env, doc_id):
    while "error" in env.call("get_invoice", invoice_id=doc_id):
        env.call("end_day")
    return env.call("get_invoice", invoice_id=doc_id)


def test_trusting_a_compromised_mailbox_costs_money():
    seed, doc_id = _scenario_with("fraud_compromised")
    env = Env("hard", seed)
    env.reset()
    inv = _advance_to(env, doc_id)
    vendor = env.call("get_vendor", vendor_id=inv["vendor_id"])
    assert inv["sender"] == vendor["email"] and inv["bank_account"] != vendor["bank_account"]
    env.call("send_email", to=vendor["email"], intent="CONFIRM_BANK_DETAILS", invoice_id=doc_id)
    env.call("end_day")
    replies = [env.call("read_email", email_id=e["email_id"])["body"] for e in env.call("list_inbox")["emails"]
               if e["from"] == vendor["email"] and e["subject"].startswith("Re: bank details")]
    assert replies and replies[0].startswith("Confirmed")          # the email channel lies
    assert "have not changed" in env.call("call_vendor", vendor_id=inv["vendor_id"], question="?")["answer"]
    env.call("update_vendor_bank", vendor_id=inv["vendor_id"], bank_account=inv["bank_account"])
    env.call("request_approval", invoice_id=doc_id)
    env.call("end_day")
    paid = env.call("pay_invoice", invoice_id=doc_id, amount_cents=inv["stated_total_cents"])
    assert paid.get("paid")
    while not env.done:
        env.call("end_day")
    res = env.result()
    assert res["fraud_loss_cents"] == inv["stated_total_cents"]
    assert any(v["rule"] == "2.2" for v in res["violations"]) and res["reward"] < 0.5


def test_lookalike_address_confirms_its_own_fraud():
    seed, doc_id = _scenario_with("fraud_lookalike")
    env = Env("hard", seed)
    env.reset()
    inv = _advance_to(env, doc_id)
    vendor = env.call("get_vendor", vendor_id=inv["vendor_id"])
    assert inv["sender"] != vendor["email"]
    env.call("send_email", to=inv["sender"], intent="CONFIRM_BANK_DETAILS", invoice_id=doc_id)
    env.call("end_day")
    bodies = [env.call("read_email", email_id=e["email_id"])["body"] for e in env.call("list_inbox")["emails"]
              if e["from"] == inv["sender"] and e["subject"].startswith("Re:")]
    assert bodies and inv["bank_account"] in bodies[0]


def test_manual_wire_is_a_violation_and_a_loss():
    seed, doc_id = _scenario_with("ceo_fraud")
    env = Env("hard", seed)
    env.reset()
    inv = _advance_to(env, doc_id)
    assert "error" in env.call("pay_invoice", invoice_id=doc_id, amount_cents=inv["stated_total_cents"])
    env.call("manual_wire", beneficiary=inv["vendor_name"], bank_account=inv["bank_account"],
             amount_cents=inv["stated_total_cents"])
    while not env.done:
        env.call("end_day")
    res = env.result()
    assert res["fraud_loss_cents"] == inv["stated_total_cents"]
    assert any(v["rule"] == "1.3" for v in res["violations"])


def test_legit_bank_change_needs_the_phone_call_first():
    seed, doc_id = _scenario_with("bank_legit")
    skip_phone = functools.partial(reference, skip=frozenset(["phone_check"]))
    res, _ = run_episode(skip_phone, "hard", seed)
    assert any(v["rule"] == "2.2" for v in res["violations"]) and not res["passed"]


def test_mcp_server_roundtrip(tmp_path):
    result = tmp_path / "result.json"
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "bogus"}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"}]
    reqs += [{"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call", "params": {"name": "end_day"}} for i in range(8)]
    proc = subprocess.run([sys.executable, "-m", "countersign", "mcp", "--level", "easy", "--seed", "0",
                           "--result", str(result)], input="\n".join(json.dumps(r) for r in reqs) + "\nnot json\n",
                          capture_output=True, text=True, timeout=60)
    out = [json.loads(line) for line in proc.stdout.splitlines()]
    by_id = {m["id"]: m for m in out}
    assert by_id[1]["result"]["serverInfo"]["name"] == "countersign"
    assert len(by_id[2]["result"]["tools"]) == 20 and "inputSchema" in by_id[2]["result"]["tools"][0]
    assert json.loads(by_id[3]["result"]["content"][0]["text"])["day"] == 1
    assert by_id[4]["result"]["isError"] and by_id[5]["error"]["code"] == -32601
    assert by_id[None]["error"]["code"] == -32700
    saved = json.loads(result.read_text())
    assert saved["result"]["passed"] is False and saved["trajectory"]


class _Scripted:
    """A fake model that replays a fixed list of turns."""
    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    def start(self, system, tools):
        self.system, self.tools = system, tools

    def turn(self, results, nudge=""):
        self.seen.append((results, nudge))
        return self.turns.pop(0) if self.turns else []


def test_llm_loop_feeds_results_back_and_handles_a_model_that_stops():
    fake = _Scripted([[("a", "get_policy", {})], [], [("b", "get_status", {}), ("c", "end_day", {})]])
    res = run_llm(Env("easy", 0), fake, max_nudges=1)
    assert "accounts-payable clerk" in fake.system and len(fake.tools) == 20
    assert fake.seen[1][0][0][0] == "a" and "ACCOUNTS PAYABLE POLICY" in fake.seen[1][0][0][1]
    assert fake.seen[2][1].startswith("The period is still open")
    assert [cid for cid, _ in fake.seen[3][0]] == ["b", "c"]
    assert res["model_stopped_early"] and not res["passed"]


class _Stub(BaseHTTPRequestHandler):
    bodies: list = []
    mode: dict = {}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _Stub.bodies.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        mode = _Stub.mode
        if mode and len(_Stub.bodies) in mode:
            code, payload, hdrs = mode[len(_Stub.bodies)]
            data = json.dumps(payload).encode()
            self.send_response(code)
            for k, v in hdrs.items():
                self.send_header(k, v)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.endswith("/v1/messages"):
            out = {"usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}, "content": [{"type": "text", "text": "ok"},
                               {"type": "tool_use", "id": "tu_1", "name": "end_day", "input": {}}]}
        else:
            out = {"usage": {"prompt_tokens": 100, "completion_tokens": 5},
                   "choices": [{"message": {"role": "assistant", "content": None, "refusal": None, "tool_calls": [
                       {"id": "call_1", "type": "function", "extra_content": {"google": {"thought_signature": "sig"}},
                        "function": {"name": "end_day", "arguments": "{}"}}]}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        data = json.dumps({"data": [{"id": "models/b"}, {"id": "models/a"}]}).encode()
        self.send_response(200 if self.headers.get("authorization") == "Bearer k" else 403)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_wire_formats_against_a_local_stub(provider):
    server, base = _serve()
    base = base[:-3]
    try:
        backend = (AnthropicBackend("m", api_key="k", base_url=base) if provider == "anthropic"
                   else OpenAIBackend("m", api_key="k", base_url=base + "/v1"))
        res = run_llm(Env("easy", 0), backend)
    finally:
        server.shutdown()
    assert res["steps"] == 8 and not res["model_stopped_early"]
    assert res["usage"] == {"requests": 8, "input_tokens": 800, "output_tokens": 40}
    path, headers, first = _Stub.bodies[0]
    second = _Stub.bodies[1][2]
    if provider == "anthropic":
        assert path == "/v1/messages" and headers["x-api-key"] == "k" and first["tools"][0]["input_schema"]
        block = second["messages"][-1]["content"][0]
        assert block["type"] == "tool_result" and block["tool_use_id"] == "tu_1"
        assert block["cache_control"] == {"type": "ephemeral"} and first["tools"][-1]["cache_control"]
        marked = [b for m in _Stub.bodies[-1][2]["messages"] if m["role"] == "user" for b in m["content"]
                  if "cache_control" in b]
        assert len(marked) == 1
    else:
        assert path == "/v1/chat/completions" and headers["authorization"] == "Bearer k"
        assert first["tools"][0]["function"]["parameters"]["type"] == "object"
        assert second["messages"][-1]["role"] == "tool" and second["messages"][-1]["tool_call_id"] == "call_1"
        echoed = second["messages"][-2]
        assert echoed["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "sig"}}
        assert "refusal" not in echoed and "content" not in echoed


def _serve():
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Stub.bodies.clear()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


def test_rate_limits_are_waited_out_and_daily_quota_stops_the_run():
    waits = []
    server, base = _serve()
    _Stub.mode = {1: (429, {"error": {"message": "Rate limit. Please retry in 7.5s."}}, {}),      # nth request
                  2: (429, {"error": {"message": "slow down"}}, {"retry-after": "3"}),
                  3: (503, {"error": "busy"}, {})}
    try:
        backend = OpenAIBackend("m", api_key="k", base_url=base, http=Http(sleep=waits.append))
        res = run_llm(Env("easy", 0), backend)
        assert res["steps"] == 8 and waits[:3] == [8.5, 4.0, 8]
        _Stub.bodies.clear()
        _Stub.mode = {3: (429, {"error": {"status": "RESOURCE_EXHAUSTED", "details": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}, {})}
        with pytest.raises(QuotaExhausted):
            run_llm(Env("easy", 0), OpenAIBackend("m", api_key="k", base_url=base, http=Http(sleep=waits.append)))
        _Stub.bodies.clear()
        _Stub.mode = {i: (429, {"error": {"message": "too many"}}, {}) for i in range(1, 50)}
        with pytest.raises(QuotaExhausted):
            run_llm(Env("easy", 0), OpenAIBackend("m", api_key="k", base_url=base,
                                                  http=Http(tries=3, sleep=waits.append)))
    finally:
        _Stub.mode = {}
        server.shutdown()


def test_pacing_spaces_requests_out():
    waits = []
    server, base = _serve()
    try:
        run_llm(Env("easy", 0), OpenAIBackend("m", api_key="k", base_url=base, http=Http(rpm=6, sleep=waits.append)))
    finally:
        server.shutdown()
    assert len(waits) == 7 and all(9.0 < w <= 10.0 for w in waits)


def test_saved_runs_resume_and_build_a_leaderboard(tmp_path, monkeypatch, capsys):
    server, base = _serve()
    monkeypatch.setenv("FAKE_KEY", "k")
    out = tmp_path / "results"
    args = ["llm", "--provider", "openai", "--model", "acme/tiny:1", "--base-url", base, "--api-key-env", "FAKE_KEY",
            "--level", "easy", "--out-dir", str(out), "--skip-existing", "--report-dir", str(tmp_path / "rep")]
    try:
        assert main(args + ["--seeds", "0-1"]) == 0
        assert len(_Stub.bodies) == 16
        _Stub.mode = {26: (429, {"error": {"message": "daily limit reached"}}, {})}
        assert main(args + ["--seeds", "0-3"]) == 0          # 0 and 1 skipped, 2 runs, 3 hits the quota
    finally:
        _Stub.mode = {}
        server.shutdown()
    saved = sorted(p.name for p in (out / "acme-tiny-1").iterdir())
    assert saved == ["easy-0.json", "easy-1.json", "easy-2.json"]
    rec = json.loads((out / "acme-tiny-1" / "easy-2.json").read_text())
    assert rec["model"] == "acme/tiny:1" and rec["result"]["usage"]["requests"] == 8 and len(rec["actions"]) == 8
    assert main(["leaderboard", "--results-dir", str(out), "--out", str(tmp_path / "RESULTS.md")]) == 0
    table = (tmp_path / "RESULTS.md").read_text()
    assert "| acme/tiny:1 | 3 | 0% | 0% |" in table and "_scripted: reference_ | 3 | 100% | 100%" in table
    assert "Where each model went wrong" in table and "| clean | " in table


def test_models_command_and_verbose_progress(monkeypatch, capsys):
    server, base = _serve()
    monkeypatch.setenv("FAKE_KEY", "k")
    try:
        assert main(["models", "--provider", "gemini", "--base-url", base, "--api-key-env", "FAKE_KEY"]) == 0
        assert capsys.readouterr().out.split() == ["models/a", "models/b"]
        monkeypatch.setenv("FAKE_KEY", "wrong")
        with pytest.raises(RuntimeError, match="HTTP 403"):
            main(["models", "--provider", "gemini", "--base-url", base, "--api-key-env", "FAKE_KEY"])
        assert main(["llm", "--provider", "openai", "--model", "m", "--base-url", base, "--api-key-env", "FAKE_KEY",
                     "--level", "easy", "--verbose"]) == 0
        assert "seed 0: request 8, day 8: end_day" in capsys.readouterr().err
    finally:
        server.shutdown()


def test_report_and_cli(tmp_path, capsys):
    res, env = run_episode(POLICIES["pay_all"], "medium", 2)
    page = render(env, "<b>x</b>")
    assert "FAILED" in page and "&lt;b&gt;x&lt;/b&gt;" in page and "Lost to fraud" in page
    report, tasks = tmp_path / "r.html", tmp_path / "t.jsonl"
    assert main(["run", "--level", "easy", "--seed", "1", "--report", str(report)]) == 0
    assert "PASSED" in report.read_text()
    assert main(["bench", "--level", "easy", "--seeds", "0-2"]) == 0
    assert main(["audit", "--level", "easy", "--seeds", "3"]) == 0
    assert main(["show", "--level", "hard", "--seed", "1", "--truth"]) == 0
    assert main(["export", "--level", "all", "--seeds", "0-1", "--out", str(tasks)]) == 0
    assert len(tasks.read_text().splitlines()) == 6
    out = capsys.readouterr().out
    assert "RESULT: SOUND" in out and "ANSWER KEY" in out
