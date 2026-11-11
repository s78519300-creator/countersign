# Results

Language models run through Countersign with the built-in runner (`countersign llm`): the model gets the brief, the 20 tools and nothing else, with its full history kept in context. Scripted baselines are shown on the same seeds for comparison.

## Level: easy

| Agent | Episodes | Pass rate | Documents right | Episodes with fraud loss | Avg lost to fraud | Violations / episode | Avg tool calls |
|---|---|---|---|---|---|---|---|
| _scripted: reference_ | 5 | 100% | 100% | 0% | $0.00 | 0.0 | 80 |
| _scripted: naive_match_ | 5 | 0% | 73% | 0% | $0.00 | 1.0 | 68 |
| gemini-3.1-flash-lite | 5 | 0% | 67% | 0% | $0.00 | 0.0 | 50 |
| _scripted: pay_all_ | 5 | 0% | 31% | 100% | $2,328.00 | 3.0 | 53 |

Seeds: 0, 1, 2, 3, 4. Models with fewer episodes ran only some of these seeds.

## Where each model went wrong

**gemini-3.1-flash-lite**

| Situation | Wrong | Seen |
|---|---|---|
| clean | 3 | 15 |
| dup_history | 3 | 3 |
| price_over | 2 | 5 |
| genuine_after_fraud | 2 | 5 |
| math | 2 | 2 |
| dup_current | 2 | 5 |
| clean_approval | 1 | 5 |
| price_over (corrected invoice) | 1 | 3 |

