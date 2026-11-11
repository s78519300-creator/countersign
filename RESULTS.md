# Results

Language models run through Countersign with the built-in runner (`countersign llm`): the model gets the brief, the 20 tools and nothing else, with its full history kept in context. Scripted baselines are shown on the same seeds for comparison.

## Level: easy

| Agent | Episodes | Pass rate | Documents right | Episodes with fraud loss | Avg lost to fraud | Violations / episode | Avg tool calls |
|---|---|---|---|---|---|---|---|
| _scripted: reference_ | 1 | 100% | 100% | 0% | $0.00 | 0.0 | 83 |
| gemini-3.1-flash-lite | 1 | 0% | 67% | 0% | $0.00 | 0.0 | 45 |
| _scripted: naive_match_ | 1 | 0% | 67% | 0% | $0.00 | 1.0 | 68 |
| _scripted: pay_all_ | 1 | 0% | 33% | 100% | $1,153.32 | 3.0 | 53 |

Seeds: 0. Models with fewer episodes ran only some of these seeds.

## Where each model went wrong

**gemini-3.1-flash-lite**

| Situation | Wrong | Seen |
|---|---|---|
| price_over | 1 | 1 |
| clean | 1 | 3 |
| math | 1 | 1 |

