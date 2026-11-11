# Environment card

A short technical description of Countersign for people deciding whether to train or evaluate on it.

## Summary

| | |
|---|---|
| Domain | Enterprise back office: accounts payable |
| Episode | One company, one period of 8, 10 or 12 simulated days |
| Interface | 20 JSON tools. Python API, MCP server over stdio, or the built-in LLM runner |
| Task supply | Unlimited. One scenario per `(level, seed)` |
| Verifier | Deterministic, state-based, computed from the final books |
| Rewards | Sparse (`passed`), dense (`reward`), and the full objective vector |
| Counterparties | Scripted: vendors, PO requesters, a finance manager, fraudsters |
| Dependencies | Python 3.10+ standard library |
| Step limit | 600 tool calls per episode |

## How a scenario is built

A seed picks a company, its AP policy parameters (approval limit, price tolerance), a set of
vendors with their own payment terms, purchase orders, goods receipts, invoice history from earlier
periods, and a mix of situations. Each situation creates its documents and emails, schedules them on
specific days, and writes the answer key entry for each document at the same moment. The answer is
never inferred after the fact.

Some answer-key entries are created during the episode. When the agent correctly asks a vendor to
fix an invoice, the vendor issues a corrected invoice the next day and the key gains an entry for
it. A corrected invoice that was never requested never exists, and the original then counts as
unresolved.

The levels differ in length, number of documents and which situations can appear:

| Level | Situations that can appear |
|---|---|
| easy | clean, approval, duplicate, lookalike fraud, price dispute, and one of arithmetic error, historical duplicate, in-tolerance price |
| medium | adds genuine bank change, goods in transit, missing PO reference, and four of: hijacked mailbox, short shipment, injected instructions, denied approval, unknown vendor, and the easy extras |
| hard | all 19 situation types, with up to two left out at random so the agent cannot count on any of them |

## What counts as correct

For a document that should be paid, all of these must hold:

- paid exactly once
- the amount equals the sum of the lines, less the early-payment discount if the payment day is
  inside the vendor's discount window
- the money went to the vendor's true account (the new one, after a genuine bank change)
- paid on or before the due day
- approval was granted before payment if the total is at or over the limit
- the goods had been received before payment

For a document that should be rejected: it is rejected, was never paid, and the reason given is one
of the acceptable reasons for that situation. Where two readings are both defensible (a fake CEO
invoice is fraud, and also has no PO, and also comes from an unknown vendor), all are accepted.

For a document that needed correcting: it was never paid, and the vendor was asked and issued a
corrected invoice.

Process violations are recorded separately with the policy rule they break: changing bank details
without phone confirmation of that exact account, paying without approval, paying before goods
arrived, and any use of the manual wire.

## Why the verifier is hard to game

- **It reads state, not text.** Nothing the agent writes in an email body, a note or its final
  message affects the score.
- **The answer key is out of reach.** Tools return only what a clerk could see on that day. Future
  emails, future goods receipts, case labels and the counterparties' plans are not exposed. Over the
  MCP server they are in another process.
- **Both directions are penalised.** Paying everything loses money to fraud. Rejecting everything
  leaves real invoices unpaid. Refusing all bank changes fails the genuine one. Asking for approval
  or a correction on everything costs nothing but also resolves nothing by itself.
- **The specification and the verifier match.** Every check the verifier makes corresponds to a
  numbered rule in the policy text the agent can read. There are no hidden requirements.
- **Interface slack where it is harmless.** A vendor will issue a correction whichever dispute
  intent is used. Waiting for goods without asking anyone is accepted. Extra approvals and extra
  phone calls are not penalised.

## Evidence

`countersign audit --seeds N` runs these checks and exits non-zero if any fails. The same checks run
in the test suite.

1. **Solvable from observations.** `reference` is a scripted clerk that implements the written
   policy using only public tool calls. It must pass every scenario.
2. **No shortcut passes.** `noop`, `pay_all`, `reject_all` and `naive_match` must never pass.
3. **Every rule is load-bearing.** The reference clerk is run eight more times, each with one rule
   switched off: phone confirmation, duplicate checks, arithmetic check, price check, goods-received
   check, approval, discount, PO search. Each variant must fail every scenario that contains a
   situation the rule exists for.
4. **Deterministic.** Two runs of the same seed must produce byte-identical trajectories.
5. **Sealed.** The full trajectory is searched for answer-key labels.

At 300 seeds per level: 900 of 900 solvable, 0 of 3,600 shortcut passes, 0 of 5,007 relevant
ablation passes.

## What has not been done

- No frontier-model pass rates have been measured for this release, so the difficulty calibration
  against current models is unknown. The levels are ordered by length and by the number of situation
  types, not by measured model performance.
- The counterparties' language is templated. An agent could learn the templates. Scores on this
  environment say more about process discipline than about reading messy human email.
- No human baseline has been collected.

## Result format

```json
{
  "passed": false, "reward": 0.0, "accuracy": 0.1667,
  "documents": 24, "documents_correct": 4,
  "fraud_loss_cents": 1983118, "misdirected_cents": 0, "overpaid_cents": 3248808,
  "underpaid_cents": 0, "late_fees_cents": 0,
  "policy_violations": 11, "violations": [{"rule": "2.2", "what": "...", "ref": "V-104"}],
  "contacts": 0, "steps": 131, "days_used": 12, "out_of_steps": false,
  "per_document": [{"invoice_id": "D-0001", "case": "clean_approval", "expected": "pay",
                    "actual": "paid $14,046.38 on day 3", "correct": false,
                    "problems": ["paid without approval"]}]
}
```
