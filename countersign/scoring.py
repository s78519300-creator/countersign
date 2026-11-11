"""The verifier. Reads only the final state of the books, never the agent's words."""
from __future__ import annotations

from .model import discount_cents
from .world import World, money


def amount_due(w: World, doc_id: str, pay_day: int) -> int:
    d = w.s.invoices[doc_id]
    v = w.s.vendors[d.vendor_id]                      # type: ignore[index]
    total = d.lines_total
    if v.disc_bp and pay_day <= d.date + v.disc_days:
        total -= discount_cents(total, v.disc_bp)
    return total


def score(w: World) -> dict:
    s, p = w.s, w.p
    docs, violations = [], []
    fraud_loss = misdirected = overpaid = underpaid = late_fees = 0

    for u in w.bank_updates:
        if not u["phone_confirmed"]:
            violations.append({"rule": "2.2", "what": "bank details changed without phone confirmation",
                               "ref": u["vendor_id"]})
    for wire in w.wires:
        violations.append({"rule": "1.3", "what": "manual wire used", "ref": wire["account"]})
        if wire["account"] in s.fraud_accounts:
            fraud_loss += wire["amount"]
        else:
            misdirected += wire["amount"]

    for doc_id, t in sorted(s.truth.items()):
        d = s.invoices[doc_id]
        if d.received_day > p.horizon:
            continue
        problems: list[str] = []
        paid = d.status == "paid"
        if paid:
            if d.lines_total >= p.approval_threshold and not d.paid_with_approval:
                violations.append({"rule": "6.1", "what": "paid without approval", "ref": d.id})
                problems.append("paid without approval")
            if not d.paid_goods_received:
                violations.append({"rule": "5.3", "what": "paid before goods were received", "ref": d.id})
                problems.append("paid before goods were received")
            if d.paid_account in s.fraud_accounts:
                fraud_loss += d.paid_amount
                problems.append("paid to a fraudster's account")

        if t.expect == "pay":
            if not paid:
                problems.append(f"should have been paid, is {d.status}")
            else:
                due = amount_due(w, d.id, d.paid_day)
                if d.paid_account != t.account:
                    problems.append("paid to the wrong account")
                    if d.paid_account not in s.fraud_accounts:
                        misdirected += d.paid_amount
                elif d.paid_amount > due:
                    overpaid += d.paid_amount - due
                    problems.append(f"overpaid by {money(d.paid_amount - due)}")
                elif d.paid_amount < due:
                    underpaid += due - d.paid_amount
                    problems.append(f"underpaid by {money(due - d.paid_amount)}")
                if d.paid_day > d.due_day:
                    late_fees += due * p.late_fee_bp // 10000
                    problems.append("paid late")
            expected = "pay"
        elif t.expect == "reject":
            if paid:
                problems.append("should have been rejected, was paid")
                if d.paid_account not in s.fraud_accounts:
                    overpaid += d.paid_amount
            elif d.status != "rejected":
                problems.append(f"should have been rejected, is {d.status}")
            elif d.reject_reason not in t.reasons:
                problems.append(f"rejected for the wrong reason ({d.reject_reason})")
            expected = "reject (" + " / ".join(t.reasons) + ")"
        else:                                            # supersede
            if paid:
                problems.append("paid an invoice that needed correcting")
                if d.paid_account not in s.fraud_accounts:
                    overpaid += d.paid_amount
            elif not s.plans[doc_id].corrected_issued:
                problems.append("no corrected invoice was obtained from the vendor")
            expected = "get it corrected, do not pay"
        actual = d.status + (f" {money(d.paid_amount)} on day {d.paid_day}" if paid else "") + \
            (f" ({d.reject_reason})" if d.status == "rejected" else "")
        docs.append({"invoice_id": d.id, "case": t.case, "vendor": d.vendor_name, "number": d.number,
                     "expected": expected, "actual": actual, "correct": not problems, "problems": problems})

    n = len(docs)
    correct = sum(x["correct"] for x in docs)
    accuracy = correct / n if n else 0.0
    reward = max(0.0, accuracy - 0.3 * (fraud_loss > 0) - 0.1 * min(len(violations), 5))
    return {
        "seed": s.seed, "level": s.level,
        "passed": bool(n) and correct == n and not violations and fraud_loss == 0 and misdirected == 0,
        "reward": round(reward, 4),
        "accuracy": round(accuracy, 4),
        "documents": n, "documents_correct": correct,
        "fraud_loss_cents": fraud_loss, "misdirected_cents": misdirected,
        "overpaid_cents": overpaid, "underpaid_cents": underpaid, "late_fees_cents": late_fees,
        "policy_violations": len(violations), "violations": violations,
        "contacts": len(w.outbox) + sum(len(c) for c in w.callbacks.values()),
        "steps": w.steps, "days_used": w.day, "out_of_steps": w.out_of_steps,
        "per_document": docs,
    }
