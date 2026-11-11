"""Write your own policy in a few lines and score it.

    python examples/quickstart.py
"""
from countersign import Env


def my_policy(env: Env) -> None:
    """Pays invoices that come from the address on file. Not good enough: see the result."""
    while not env.done:
        for row in env.call("list_invoices", status="open")["invoices"]:
            inv = env.call("get_invoice", invoice_id=row["invoice_id"])
            if inv["vendor_id"] is None:
                env.call("reject_invoice", invoice_id=inv["invoice_id"], reason="vendor_not_approved")
                continue
            vendor = env.call("get_vendor", vendor_id=inv["vendor_id"])
            if inv["sender"] != vendor["email"]:
                env.call("reject_invoice", invoice_id=inv["invoice_id"], reason="fraud")
            else:
                env.call("pay_invoice", invoice_id=inv["invoice_id"], amount_cents=inv["stated_total_cents"])
        env.call("end_day")


if __name__ == "__main__":
    env = Env(level="medium", seed=0)
    env.reset()
    my_policy(env)
    result = env.result()
    print("passed:", result["passed"], "| reward:", result["reward"],
          "| documents:", f"{result['documents_correct']}/{result['documents']}",
          "| violations:", result["policy_violations"])
    for doc in result["per_document"]:
        if not doc["correct"]:
            print(" ", doc["invoice_id"], doc["case"], "->", "; ".join(doc["problems"]))
