"""Data model for the Countersign world. All money is integer cents."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

REJECT_REASONS = (
    "duplicate",
    "fraud",
    "no_po",
    "vendor_not_approved",
    "price_mismatch",
    "quantity_mismatch",
    "math_error",
    "approval_denied",
    "superseded",
)

EMAIL_INTENTS = (
    "REQUEST_CORRECTED_INVOICE",
    "DISPUTE_PRICE",
    "DISPUTE_QUANTITY",
    "CONFIRM_DELIVERY",
    "CONFIRM_BANK_DETAILS",
    "GENERAL",
)
CORRECTION_INTENTS = ("REQUEST_CORRECTED_INVOICE", "DISPUTE_PRICE", "DISPUTE_QUANTITY")


@dataclass
class Vendor:
    id: str
    name: str
    email: str
    phone: str
    bank: str
    net_days: int
    disc_bp: int = 0          # early-payment discount in basis points
    disc_days: int = 0


@dataclass
class Line:
    sku: str
    desc: str
    qty: int
    unit: int                 # cents

    @property
    def total(self) -> int:
        return self.qty * self.unit


@dataclass
class PO:
    id: str
    vendor_id: str
    requester: str
    day: int
    lines: list[Line]


@dataclass
class Receipt:
    id: str
    po_id: str
    sku: str
    qty: int
    day: int


@dataclass
class Invoice:
    id: str
    vendor_id: Optional[str]
    vendor_name: str
    number: str
    po_ref: Optional[str]
    lines: list[Line]
    stated_total: int
    bank: str
    date: int
    due_day: int
    received_day: int
    sender: str
    notes: str = ""
    status: str = "open"       # open | paid | rejected | void
    linked_po: Optional[str] = None
    approval: str = "none"     # none | pending | approved | denied
    replaces: Optional[str] = None
    paid_amount: int = 0
    paid_day: int = 0
    paid_account: str = ""
    paid_with_approval: bool = False
    paid_goods_received: bool = False
    reject_reason: str = ""

    @property
    def lines_total(self) -> int:
        return sum(l.total for l in self.lines)


@dataclass
class Email:
    id: str
    day: int
    sender: str
    to: str
    subject: str
    body: str
    invoice_id: Optional[str] = None


@dataclass
class Truth:
    """Ground truth for one document. Never exposed through a tool."""
    doc_id: str
    case: str
    expect: str                              # pay | reject | supersede
    account: str = ""
    reasons: tuple[str, ...] = ()
    needs_approval: bool = False


@dataclass
class Plan:
    """How counterparties behave about one document."""
    kind: str = "plain"        # plain | price | math | short | transit | lookalike | compromised
    corrected_lines: Optional[list[Line]] = None
    corrected_issued: bool = False
    transit_day: int = 0
    received_qty: int = 0
    ordered_qty: int = 0
    sku: str = ""
    approval_outcome: str = "approved"
    deny_note: str = ""


@dataclass
class Params:
    company: str
    domain: str
    approver: str
    horizon: int
    tolerance_bp: int
    approval_threshold: int
    late_fee_bp: int = 150
    max_steps: int = 600


@dataclass
class Scenario:
    seed: int
    level: str
    params: Params
    vendors: dict[str, Vendor] = field(default_factory=dict)
    true_bank: dict[str, str] = field(default_factory=dict)      # vendor_id -> real account
    pending_bank: dict[str, str] = field(default_factory=dict)   # legit announced changes
    compromised: dict[str, str] = field(default_factory=dict)    # vendor_id -> fraud account
    fraud_addresses: dict[str, str] = field(default_factory=dict)  # address -> fraud account
    fraud_accounts: set[str] = field(default_factory=set)
    pos: dict[str, PO] = field(default_factory=dict)
    receipts: list[Receipt] = field(default_factory=list)
    invoices: dict[str, Invoice] = field(default_factory=dict)   # every doc incl. future
    emails: list[Email] = field(default_factory=list)            # every scheduled email
    truth: dict[str, Truth] = field(default_factory=dict)
    plans: dict[str, Plan] = field(default_factory=dict)
    cases: list[str] = field(default_factory=list)


def norm_number(n: str) -> str:
    return "".join(ch for ch in n.upper() if ch.isalnum())


def discount_cents(total: int, disc_bp: int) -> int:
    return total * disc_bp // 10000
