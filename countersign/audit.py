"""Soundness audit: evidence that the environment is solvable, deterministic and hard to game.

Checks, for every seed and level:
  1. solvable    - the reference clerk, using only public tools, gets a perfect result
  2. no shortcut - none of the shortcut policies ever passes
  3. load-bearing - a reference clerk with any single rule switched off fails every scenario
                    that contains a situation that rule exists for
  4. deterministic - the same seed gives the same world and the same trajectory
  5. sealed      - nothing the tools return contains the answer key
"""
from __future__ import annotations

import functools
import hashlib
import json

from .env import run_episode
from .generate import LEVELS
from .policies import ABLATIONS, POLICIES, reference

RELEVANT = {
    "phone_check": {"fraud_compromised", "bank_legit"},
    "duplicates": {"dup_current", "dup_history", "po_exhausted"},
    "arithmetic": {"math"},
    "price": {"price_over"},
    "goods_received": {"qty_transit", "qty_short"},
    "approval": {"clean_approval", "approval_denied", "injection"},
    "po_search": {"po_missing_ref"},
}
_SEALED_WORDS = ("fraud_lookalike", "fraud_compromised", "ceo_fraud", "dup_current", "dup_history",
                 "po_exhausted", "qty_short", "qty_transit", "price_over", "bank_legit", "approval_outcome",
                 "corrected_lines", "needs_approval", "true_bank")


def _digest(env) -> str:
    return hashlib.sha256(json.dumps(env.trajectory(), sort_keys=True).encode()).hexdigest()


def audit(seeds: int = 100, levels: tuple[str, ...] = LEVELS) -> dict:
    failures: list[str] = []
    counts = {"episodes": 0, "reference_passed": 0, "shortcut_runs": 0, "shortcut_passes": 0,
              "ablation_relevant_runs": 0, "ablation_relevant_passes": 0, "discount_ablation_fails": 0}
    for level in levels:
        for seed in range(seeds):
            res, env = run_episode(reference, level, seed)
            counts["episodes"] += 1
            counts["reference_passed"] += res["passed"]
            if not res["passed"]:
                failures.append(f"reference failed {level}/{seed}")
            blob = json.dumps(env.trajectory())
            for word in _SEALED_WORDS:
                if word in blob:
                    failures.append(f"answer key word '{word}' visible in tool output {level}/{seed}")
            if seed % 10 == 0:
                _, env2 = run_episode(reference, level, seed)
                if _digest(env) != _digest(env2):
                    failures.append(f"not deterministic {level}/{seed}")
            cases = set(env.world.s.cases)                        # type: ignore[union-attr]
            for name, pol in POLICIES.items():
                if name == "reference":
                    continue
                r, _ = run_episode(pol, level, seed)
                counts["shortcut_runs"] += 1
                if r["passed"]:
                    counts["shortcut_passes"] += 1
                    failures.append(f"shortcut '{name}' passed {level}/{seed}")
            for ab in ABLATIONS:
                r, _ = run_episode(functools.partial(reference, skip=frozenset([ab])), level, seed)
                if ab == "discount":
                    counts["discount_ablation_fails"] += not r["passed"]
                elif RELEVANT[ab] & cases:
                    counts["ablation_relevant_runs"] += 1
                    if r["passed"]:
                        counts["ablation_relevant_passes"] += 1
                        failures.append(f"clerk without '{ab}' still passed {level}/{seed}")
    if counts["discount_ablation_fails"] == 0:
        failures.append("ignoring early-payment discounts never mattered")
    return {"ok": not failures, "seeds_per_level": seeds, "levels": list(levels), **counts,
            "failures": failures[:50]}
