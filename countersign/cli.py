"""Command line: countersign <command>."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .audit import audit
from .env import Env, run_episode
from .generate import LEVELS, generate
from .policies import POLICIES
from .report import render
from .tools import TOOLS
from .world import brief, money, policy_text


def _seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _summary(r: dict) -> str:
    return (f"{'PASSED' if r['passed'] else 'FAILED'}  reward {r['reward']:.2f}  "
            f"documents {r['documents_correct']}/{r['documents']}  fraud loss {money(r['fraud_loss_cents'])}  "
            f"overpaid {money(r['overpaid_cents'])}  violations {r['policy_violations']}  steps {r['steps']}")


def cmd_run(a: argparse.Namespace) -> int:
    res, env = run_episode(POLICIES[a.policy], a.level, a.seed)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(render(env, f"{a.policy} policy"))
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(_summary(res))
        for d in res["per_document"]:
            if not d["correct"]:
                print(f"  WRONG {d['invoice_id']} {d['case']}: {'; '.join(d['problems'])}")
        if a.report:
            print(f"report written to {a.report}")
    return 0


def bench(policies: list[str], levels: list[str], seeds: list[int]) -> list[dict]:
    rows = []
    for level in levels:
        for name in policies:
            rs = [run_episode(POLICIES[name], level, s)[0] for s in seeds]
            n = len(rs)
            rows.append({
                "level": level, "policy": name, "episodes": n,
                "pass_rate": sum(r["passed"] for r in rs) / n,
                "reward": sum(r["reward"] for r in rs) / n,
                "accuracy": sum(r["accuracy"] for r in rs) / n,
                "fraud_episodes": sum(r["fraud_loss_cents"] > 0 for r in rs) / n,
                "fraud_loss_cents": sum(r["fraud_loss_cents"] for r in rs) // n,
                "overpaid_cents": sum(r["overpaid_cents"] for r in rs) // n,
                "violations": sum(r["policy_violations"] for r in rs) / n,
            })
    return rows


def cmd_bench(a: argparse.Namespace) -> int:
    levels = list(LEVELS) if a.level == "all" else [a.level]
    rows = bench(a.policies.split(","), levels, _seeds(a.seeds))
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    print("| Level | Policy | Pass rate | Documents right | Episodes with fraud loss | Avg lost to fraud | Avg overpaid | Violations / episode |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['level']} | `{r['policy']}` | {r['pass_rate']:.0%} | {r['accuracy']:.0%} | "
              f"{r['fraud_episodes']:.0%} | {money(r['fraud_loss_cents'])} | {money(r['overpaid_cents'])} | "
              f"{r['violations']:.1f} |")
    return 0


def cmd_audit(a: argparse.Namespace) -> int:
    levels = LEVELS if a.level == "all" else (a.level,)
    out = audit(a.seeds, levels)
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        print(f"Countersign {__version__} soundness audit: {a.seeds} seeds x {len(levels)} levels")
        print(f"  solvable       reference clerk passed {out['reference_passed']}/{out['episodes']} episodes")
        print(f"  no shortcut    shortcut policies passed {out['shortcut_passes']}/{out['shortcut_runs']} runs")
        print(f"  load-bearing   single-rule ablations passed {out['ablation_relevant_passes']}/"
              f"{out['ablation_relevant_runs']} relevant runs")
        print(f"  discounts      ignoring discounts failed {out['discount_ablation_fails']} episodes")
        for f in out["failures"]:
            print("  FAILURE:", f)
        print("RESULT:", "SOUND" if out["ok"] else "NOT SOUND")
    return 0 if out["ok"] else 1


def cmd_show(a: argparse.Namespace) -> int:
    s = generate(a.seed, a.level)
    print(brief(s), "\n")
    print(policy_text(s))
    if a.truth:
        print("ANSWER KEY (never shown to the agent)")
        for doc_id, t in sorted(s.truth.items()):
            d = s.invoices[doc_id]
            what = t.expect + (f" ({'/'.join(t.reasons)})" if t.reasons else "")
            print(f"  {doc_id}  day {d.received_day:>2}  {t.case:20s} {what:28s} {d.vendor_name}")
        print("  (corrected invoices are added to the key when a vendor issues them)")
    return 0


def cmd_play(a: argparse.Namespace) -> int:
    env = Env(a.level, a.seed)
    print(env.reset()["brief"])
    print("\nType: tool_name {json arguments}. Tools:", ", ".join(t["name"] for t in TOOLS), "\n")
    while not env.done:
        try:
            line = input(f"day {env.world.day}> ").strip()       # type: ignore[union-attr]
        except EOFError:
            break
        if not line:
            continue
        name, _, rest = line.partition(" ")
        try:
            args = json.loads(rest) if rest.strip() else {}
        except json.JSONDecodeError as e:
            print("bad JSON:", e)
            continue
        out = env.step({"tool": name, "args": args})[0]
        print(out["policy"] if "policy" in out else json.dumps(out, indent=1))
    while not env.done:
        env.call("end_day")
    print(_summary(env.result()))
    return 0


def cmd_mcp(a: argparse.Namespace) -> int:
    from .mcp import serve
    serve(a.level, a.seed, a.result)
    return 0


def cmd_llm(a: argparse.Namespace) -> int:
    from .llm import AnthropicBackend, OpenAIBackend, run_llm
    results = []
    for seed in _seeds(a.seeds):
        if a.provider == "anthropic":
            backend = AnthropicBackend(a.model, base_url=a.base_url or "https://api.anthropic.com")
        else:
            backend = OpenAIBackend(a.model, base_url=a.base_url or "https://api.openai.com/v1")
        env = Env(a.level, seed)
        res = run_llm(env, backend, max_turns=a.max_turns)
        results.append(res)
        print(f"seed {seed}: {_summary(res)}", file=sys.stderr)
        if a.report_dir:
            with open(f"{a.report_dir}/{a.level}-{seed}.html", "w", encoding="utf-8") as f:
                f.write(render(env, a.model))
    n = len(results)
    print(json.dumps({"model": a.model, "level": a.level, "episodes": n,
                      "pass_rate": sum(r["passed"] for r in results) / n,
                      "reward": sum(r["reward"] for r in results) / n,
                      "fraud_episodes": sum(r["fraud_loss_cents"] > 0 for r in results) / n,
                      "results": results}, indent=1))
    return 0


def cmd_export(a: argparse.Namespace) -> int:
    levels = list(LEVELS) if a.level == "all" else [a.level]
    with open(a.out, "w", encoding="utf-8") as f:
        for level in levels:
            for seed in _seeds(a.seeds):
                s = generate(seed, level)
                f.write(json.dumps({
                    "task_id": f"countersign-{level}-{seed:05d}", "level": level, "seed": seed,
                    "instruction": brief(s), "horizon_days": s.params.horizon,
                    "max_steps": s.params.max_steps,
                    "mcp_command": ["python", "-m", "countersign", "mcp", "--level", level, "--seed", str(seed),
                                    "--result", "result.json"],
                    "success": "result.json: result.passed is true",
                }) + "\n")
    print(f"wrote {a.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="countersign", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser, level_all: bool = False) -> None:
        p.add_argument("--level", default="medium", choices=list(LEVELS) + (["all"] if level_all else []))

    p = sub.add_parser("run", help="run a scripted policy for one episode")
    common(p)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--policy", default="reference", choices=list(POLICIES))
    p.add_argument("--report", help="write an HTML trajectory report here")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("bench", help="compare scripted policies over many seeds")
    common(p, True)
    p.add_argument("--seeds", default="0-99")
    p.add_argument("--policies", default=",".join(POLICIES))
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_bench, level="all")

    p = sub.add_parser("audit", help="soundness audit: solvable, no shortcuts, deterministic, sealed")
    common(p, True)
    p.add_argument("--seeds", type=int, default=100)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_audit, level="all")

    p = sub.add_parser("show", help="print the brief and policy of a scenario")
    common(p)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--truth", action="store_true", help="also print the answer key")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("play", help="play an episode yourself in the terminal")
    common(p)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=cmd_play)

    p = sub.add_parser("mcp", help="serve one episode as an MCP server on stdio")
    common(p)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--result", help="write result and trajectory JSON here when the period closes")
    p.set_defaults(fn=cmd_mcp)

    p = sub.add_parser("llm", help="run a tool-calling model through episodes")
    common(p)
    p.add_argument("--provider", choices=["anthropic", "openai"], required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--base-url", help="override the API base URL (for example a local server)")
    p.add_argument("--seeds", default="0")
    p.add_argument("--max-turns", type=int, default=400)
    p.add_argument("--report-dir", help="write one HTML report per episode into this folder")
    p.set_defaults(fn=cmd_llm)

    p = sub.add_parser("export", help="write a JSONL task manifest for a harness")
    common(p, True)
    p.add_argument("--seeds", default="0-9")
    p.add_argument("--out", default="tasks.jsonl")
    p.set_defaults(fn=cmd_export)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
