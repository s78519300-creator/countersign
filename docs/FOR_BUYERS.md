# Countersign for data buyers

A one-page brief for people who buy or license agent environments: labs, data vendors and teams
building finance agents.

## What it is

An accounts-payable world for tool-using agents. The agent is the AP clerk of a simulated company
for 8 to 12 simulated days and has to pay what is owed and refuse what is not, while duplicates,
billing errors and several kinds of payment fraud arrive in its inbox. A deterministic verifier
scores the final state of the books.

| | |
|---|---|
| Domain | Enterprise back office, finance operations |
| Horizon | Long: 80 to 215+ tool calls per episode for a scripted expert, across 8 to 12 simulated days |
| Interaction | Multi-turn with scripted counterparties whose replies arrive on later days |
| Objectives | Several at once: correctness, fraud loss, overpayment, timeliness, process compliance |
| Supply | Procedural. One scenario per seed, three difficulty levels, 19 situation types |
| Rewards | Sparse pass/fail, a dense scalar, and the full objective vector |
| Interfaces | Python API, MCP server, JSONL task manifest, built-in runner for common model APIs |
| Footprint | Python standard library only. No Docker image, GPU, network or API keys needed to run the environment |

## Check it yourself in ten minutes

```bash
git clone https://github.com/s78519300-creator/countersign && cd countersign
pip install -e ".[dev]"
pytest -q                           # the test suite
countersign audit --seeds 300       # soundness: solvable, no shortcuts, every rule load-bearing
countersign show --level hard --seed 1 --truth     # one scenario with its answer key
countersign run --level hard --seed 1 --policy pay_all --report report.html   # open report.html
```

What the audit establishes, on 900 scenarios:

- A reference policy that sees only what the agent sees solves every scenario, so no task is
  impossible or underspecified.
- Four shortcut policies (do nothing, pay everything, reject everything, match-and-pay with no
  communication) never pass.
- Removing any single rule from the reference policy makes it fail every scenario that contains a
  situation that rule exists for (0 passes in 5,007 runs). No rule is decorative.
- Same seed, same episode. No answer-key label appears in any tool output.

The verifier's checks map one to one to numbered rules in the policy text the agent can read. See
[ENVIRONMENT_CARD.md](ENVIRONMENT_CARD.md) for the scoring rules and known limits.

## Model results

See [RESULTS.md](../RESULTS.md) for the runs published so far, with raw per-episode files under
`results/`. If your model is not there, `countersign llm` or the "Benchmark a model" workflow will
produce the same table for it.

## What is public and what is licensed

| | Public repository | Commercial license |
|---|---|---|
| Environment engine, verifier, tools, audit | Yes | Yes |
| The 19 public situation types | Yes | Yes |
| Use for research, teaching and personal projects | Yes | Yes |
| Use at or for a company, including model training and evaluation | No | Yes |
| Held-out situation types that are not in any public repository | No | Yes |
| Exclusive packs | No | On request |
| New workflows built to order (for example expense audit, vendor onboarding, month-end close) | No | On request |

The public generator should be treated as seen data. Held-out packs exist so that evaluation and
training on unseen situations stays possible.

## Limits, stated plainly

- Counterparties are scripted and their wording is templated.
- The company has unlimited cash, and there is no tax, currency or partial payment yet.
- Difficulty has not been calibrated against a human baseline.

## Contact

Open an issue on the repository, or reach the author through their
[GitHub profile](https://github.com/s78519300-creator).
