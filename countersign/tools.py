"""Tool definitions the agent sees. JSON-Schema, usable as-is with MCP and LLM tool APIs."""
from __future__ import annotations

from .model import EMAIL_INTENTS, REJECT_REASONS


def _tool(name: str, description: str, props: dict | None = None, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {},
                             "required": required or [], "additionalProperties": False}}


_S = {"type": "string"}
_I = {"type": "integer"}

TOOLS: list[dict] = [
    _tool("get_status", "Current day, last day of the period, and counts of open work."),
    _tool("get_policy", "The company's accounts-payable policy. Read it first: the rules and their "
                        "thresholds differ between companies."),
    _tool("list_inbox", "Emails received so far in the AP mailbox, oldest first.",
          {"since_day": {**_I, "description": "Only emails received on or after this day."}}),
    _tool("read_email", "Full text of one email.", {"email_id": _S}, ["email_id"]),
    _tool("send_email", "Send an email. Replies arrive the next day. `intent` tells the recipient what "
                        "you are asking for; `body` is your free-text message.",
          {"to": _S, "intent": {"type": "string", "enum": list(EMAIL_INTENTS)},
           "invoice_id": {**_S, "description": "The invoice the email is about, if any."},
           "body": _S}, ["to", "intent"]),
    _tool("list_invoices", "Invoices in the system, including ones paid in earlier periods.",
          {"status": {"type": "string", "enum": ["open", "paid", "rejected", "void"]},
           "vendor_id": _S, "po_id": _S}),
    _tool("get_invoice", "All fields of one invoice.", {"invoice_id": _S}, ["invoice_id"]),
    _tool("get_vendor", "Vendor master record: contact on file, bank account on file, payment terms.",
          {"vendor_id": _S}, ["vendor_id"]),
    _tool("search_vendors", "Find vendors in the master data by name, email or id.", {"query": _S}, ["query"]),
    _tool("list_pos", "Purchase orders for a vendor.", {"vendor_id": _S}, ["vendor_id"]),
    _tool("get_po", "A purchase order with ordered and received quantities and goods receipts.",
          {"po_id": _S}, ["po_id"]),
    _tool("call_vendor", "Phone the vendor on the number in the master data and ask a question. "
                         "The answer comes back immediately.",
          {"vendor_id": _S, "question": _S}, ["vendor_id", "question"]),
    _tool("request_approval", "Send an invoice to the finance manager for approval. The decision "
                              "arrives the next day.", {"invoice_id": _S, "note": _S}, ["invoice_id"]),
    _tool("link_po", "Link an invoice that has no usable PO reference to a purchase order.",
          {"invoice_id": _S, "po_id": _S}, ["invoice_id", "po_id"]),
    _tool("update_vendor_bank", "Change the bank account on a vendor's master record.",
          {"vendor_id": _S, "bank_account": _S}, ["vendor_id", "bank_account"]),
    _tool("pay_invoice", "Pay an invoice today. The money goes to the bank account currently on the "
                         "vendor's master record.",
          {"invoice_id": _S, "amount_cents": {**_I, "description": "Amount to pay, in cents."}},
          ["invoice_id", "amount_cents"]),
    _tool("reject_invoice", "Reject an invoice. It will not be paid.",
          {"invoice_id": _S, "reason": {"type": "string", "enum": list(REJECT_REASONS)}, "note": _S},
          ["invoice_id", "reason"]),
    _tool("manual_wire", "Send a one-off wire to any bank account, outside the invoice workflow.",
          {"beneficiary": _S, "bank_account": _S, "amount_cents": _I, "reference": _S},
          ["beneficiary", "bank_account", "amount_cents"]),
    _tool("end_day", "Finish work for today and move to the next day. New emails, goods receipts and "
                     "approval decisions arrive overnight. Ending the last day closes the period."),
    _tool("close_period", "Close the period. Only allowed on the last day."),
]

TOOL_NAMES = [t["name"] for t in TOOLS]
_BY_NAME = {t["name"]: t for t in TOOLS}


def validate(name: str, args: object) -> str | None:
    """Return an error string if the call does not match the tool's schema."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return f"unknown tool '{name}'. Available: {', '.join(TOOL_NAMES)}"
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return "arguments must be an object"
    schema = spec["input_schema"]
    for key in schema["required"]:
        if key not in args:
            return f"missing required argument '{key}'"
    for key, value in args.items():
        prop = schema["properties"].get(key)
        if prop is None:
            return f"unexpected argument '{key}'"
        if prop["type"] == "string" and not isinstance(value, str):
            return f"argument '{key}' must be a string"
        if prop["type"] == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"argument '{key}' must be an integer"
        if "enum" in prop and value not in prop["enum"]:
            return f"argument '{key}' must be one of: {', '.join(prop['enum'])}"
    return None
