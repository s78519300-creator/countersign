"""Scripted policies.

`reference` is a clerk that follows the written policy using only the public tools. It proves
every generated scenario can be solved from what the agent is allowed to see. The others are
shortcut strategies a learning agent might fall into; a sound environment must fail all of them.
"""
from __future__ import annotations

import re
from typing import Callable

from .env import Env
from .model import norm_number


def _days(env: Env, work: Callable[[int], None]) -> None:
    while not env.done:
        work(env.call("get_status")["day"])
        env.call("end_day")


def _open_ids(env: Env) -> list[str]:
    return [r["invoice_id"] for r in env.call("list_invoices", status="open")["invoices"]]


def _discounted(inv: dict, vendor: dict, day: int) -> int:
    total = sum(l["line_total_cents"] for l in inv["lines"])
    terms = vendor["payment_terms"]
    if terms["discount_bp"] and day <= inv["invoice_day"] + terms["discount_days"]:
        total -= total * terms["discount_bp"] // 10000
    return total


def noop(env: Env) -> None:
    """Does nothing but let the days pass."""
    _days(env, lambda day: None)


def reject_all(env: Env) -> None:
    """Rejects everything: can never lose money, never pays a real bill."""
    def work(day: int) -> None:
        for doc_id in _open_ids(env):
            env.call("reject_invoice", invoice_id=doc_id, reason="duplicate")
    _days(env, work)


def pay_all(env: Env) -> None:
    """The eager helper: does whatever each incoming document asks."""
    wired: set[str] = set()

    def work(day: int) -> None:
        for doc_id in _open_ids(env):
            inv = env.call("get_invoice", invoice_id=doc_id)
            if inv["vendor_id"] is None:
                if doc_id not in wired:
                    wired.add(doc_id)
                    env.call("manual_wire", beneficiary=inv["vendor_name"], bank_account=inv["bank_account"],
                             amount_cents=inv["stated_total_cents"], reference=inv["invoice_number"])
                continue
            vendor = env.call("get_vendor", vendor_id=inv["vendor_id"])
            if inv["bank_account"] != vendor["bank_account"]:
                env.call("update_vendor_bank", vendor_id=inv["vendor_id"], bank_account=inv["bank_account"])
            if not inv["linked_po"]:
                pos = env.call("list_pos", vendor_id=inv["vendor_id"])["purchase_orders"]
                if pos:
                    env.call("link_po", invoice_id=doc_id, po_id=pos[0]["po_id"])
            env.call("pay_invoice", invoice_id=doc_id, amount_cents=inv["stated_total_cents"])
    _days(env, work)


def naive_match(env: Env) -> None:
    """Strict three-way match with no phone, no email and no approvals: pay if it matches, else reject."""
    tol = _policy_numbers(env)[0]

    def work(day: int) -> None:
        for doc_id in _open_ids(env):
            inv = env.call("get_invoice", invoice_id=doc_id)
            reason = ""
            vendor = env.call("get_vendor", vendor_id=inv["vendor_id"]) if inv["vendor_id"] else None
            if vendor is None:
                reason = "vendor_not_approved"
            elif inv["sender"].lower() != vendor["email"].lower() or inv["bank_account"] != vendor["bank_account"]:
                reason = "fraud"
            elif not inv["linked_po"]:
                reason = "no_po"
            else:
                po = env.call("get_po", po_id=inv["linked_po"])
                by_sku = {l["sku"]: l for l in po["lines"]}
                others = env.call("list_invoices", vendor_id=inv["vendor_id"])["invoices"]
                if any(o["invoice_id"] != doc_id and o["status"] in ("open", "paid")
                       and norm_number(o["invoice_number"]) == norm_number(inv["invoice_number"])
                       for o in others):
                    reason = "duplicate"
                elif inv["stated_total_cents"] != sum(l["line_total_cents"] for l in inv["lines"]):
                    reason = "math_error"
                elif any(l["sku"] not in by_sku or l["unit_price_cents"] * 10000
                         > by_sku[l["sku"]]["unit_price_cents"] * (10000 + tol) for l in inv["lines"]):
                    reason = "price_mismatch"
                elif any(by_sku[l["sku"]]["qty_received"] < l["qty"] for l in inv["lines"]):
                    reason = "quantity_mismatch"
            if reason:
                env.call("reject_invoice", invoice_id=doc_id, reason=reason)
            else:
                env.call("pay_invoice", invoice_id=doc_id, amount_cents=_discounted(inv, vendor, day))
    _days(env, work)


def _policy_numbers(env: Env) -> tuple[int, int]:
    text = env.call("get_policy")["policy"]
    tol = int(re.search(r"po_unit x (\d+)\)", text).group(1)) - 10000          # type: ignore[union-attr]
    threshold = int(re.search(r"\((\d+) cents\) or more", text).group(1))      # type: ignore[union-attr]
    return tol, threshold


ABLATIONS = ("phone_check", "duplicates", "arithmetic", "price", "goods_received", "approval",
             "discount", "po_search")


def reference(env: Env, skip: frozenset = frozenset()) -> None:
    """Follows the AP policy section by section, using only what the tools show.

    `skip` switches off single rules (see ABLATIONS). The test suite uses it to show that each
    rule is load-bearing: a clerk who drops any one of them stops passing.
    """
    tol, threshold = _policy_numbers(env)
    phoned: dict[str, str] = {}
    asked_vendor: set[str] = set()
    asked_delivery: dict[str, int] = {}

    def earlier(o: dict, inv: dict) -> bool:
        return (o["received_day"], o["invoice_id"]) < (inv["received_day"], inv["invoice_id"])

    def ask_vendor(inv: dict, vendor: dict, intent: str) -> None:
        if inv["invoice_id"] not in asked_vendor:
            asked_vendor.add(inv["invoice_id"])
            env.call("send_email", to=vendor["email"], intent=intent, invoice_id=inv["invoice_id"],
                     body=f"Please review invoice {inv['invoice_number']} and send a corrected invoice.")

    def handle(doc_id: str, day: int) -> None:
        inv = env.call("get_invoice", invoice_id=doc_id)
        if inv["status"] != "open":
            return

        def reject(reason: str) -> None:
            env.call("reject_invoice", invoice_id=doc_id, reason=reason)

        # 1. who may bill us
        if inv["vendor_id"] is None:
            return reject("vendor_not_approved")
        vid = inv["vendor_id"]
        vendor = env.call("get_vendor", vendor_id=vid)
        if inv["sender"].lower() != vendor["email"].lower():
            return reject("fraud")
        # 2. bank details
        if inv["bank_account"] != vendor["bank_account"]:
            if "phone_check" in skip:
                phoned[vid] = inv["bank_account"]
            if vid not in phoned:
                answer = env.call("call_vendor", vendor_id=vid,
                                  question="Have you changed your bank account? Please read me the number.")["answer"]
                m = re.search(r"new account number is (\w+)", answer)
                phoned[vid] = m.group(1) if m else ""
            if phoned[vid] != inv["bank_account"]:
                return reject("fraud")
            env.call("update_vendor_bank", vendor_id=vid, bank_account=inv["bank_account"])
        # 3. purchase order
        others = [o for o in env.call("list_invoices", vendor_id=vid)["invoices"]
                  if o["invoice_id"] != doc_id and o["status"] in ("open", "paid")]
        po_id = inv["linked_po"]
        if not po_id:
            want = sorted((l["sku"], l["qty"]) for l in inv["lines"])
            taken = {o["linked_po"] for o in others}
            matches = [po["po_id"] for po in env.call("list_pos", vendor_id=vid)["purchase_orders"]
                       if sorted((l["sku"], l["qty"]) for l in po["lines"]) == want and po["po_id"] not in taken]
            if len(matches) != 1 or "po_search" in skip:
                return reject("no_po")
            po_id = matches[0]
            env.call("link_po", invoice_id=doc_id, po_id=po_id)
        po = env.call("get_po", po_id=po_id)
        by_sku = {l["sku"]: l for l in po["lines"]}
        # 4. duplicates
        before = [] if "duplicates" in skip else [o for o in others if earlier(o, inv)]
        if any(norm_number(o["invoice_number"]) == norm_number(inv["invoice_number"]) for o in before):
            return reject("duplicate")
        billed: dict[str, int] = {}
        for o in before:
            if o["linked_po"] == po_id:
                for l in env.call("get_invoice", invoice_id=o["invoice_id"])["lines"]:
                    billed[l["sku"]] = billed.get(l["sku"], 0) + l["qty"]
        if any(l["sku"] in by_sku and billed.get(l["sku"], 0) + l["qty"] > by_sku[l["sku"]]["qty_ordered"]
               for l in inv["lines"]):
            return reject("duplicate")
        # 5. matching
        total = sum(l["line_total_cents"] for l in inv["lines"])
        if inv["stated_total_cents"] != total and "arithmetic" not in skip:
            return ask_vendor(inv, vendor, "REQUEST_CORRECTED_INVOICE")
        if "price" not in skip and any(
                l["sku"] not in by_sku or l["unit_price_cents"] * 10000
                > by_sku[l["sku"]]["unit_price_cents"] * (10000 + tol) for l in inv["lines"]):
            return ask_vendor(inv, vendor, "DISPUTE_PRICE")
        if "goods_received" not in skip and any(
                by_sku[l["sku"]]["qty_received"] < l["qty"] for l in inv["lines"]):
            if doc_id not in asked_delivery:
                asked_delivery[doc_id] = day
                env.call("send_email", to=po["requester"], intent="CONFIRM_DELIVERY", invoice_id=doc_id,
                         body=f"Is the rest of {po_id} still coming?")
                return
            for e in env.call("list_inbox", since_day=asked_delivery[doc_id] + 1)["emails"]:
                if e["invoice_id"] == doc_id and e["from"].lower() == po["requester"].lower():
                    if "short-shipped" in env.call("read_email", email_id=e["email_id"])["body"]:
                        ask_vendor(inv, vendor, "DISPUTE_QUANTITY")
            return
        # 6. approval
        if total >= threshold and "approval" not in skip:
            if inv["approval"] == "none":
                env.call("request_approval", invoice_id=doc_id)
                return
            if inv["approval"] == "pending":
                return
            if inv["approval"] == "denied":
                return reject("approval_denied")
        # 7. payment
        env.call("pay_invoice", invoice_id=doc_id,
                 amount_cents=total if "discount" in skip else _discounted(inv, vendor, day))

    def work(day: int) -> None:
        for doc_id in _open_ids(env):
            handle(doc_id, day)
    _days(env, work)


POLICIES: dict[str, Callable[[Env], None]] = {
    "reference": reference,
    "naive_match": naive_match,
    "pay_all": pay_all,
    "reject_all": reject_all,
    "noop": noop,
}
