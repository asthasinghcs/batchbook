"""Date-bounded views of posted records for the accountant."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .db import BusinessError, db
from .inventory import packet_cost
from .numbers import qty_text, variance
from .sales import STATES


@dataclass
class Report:
    title: str
    headers: tuple
    rows: list


def rupees(paise):
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def between(start, end):
    try:
        dates = (date.fromisoformat(start), date.fromisoformat(end))
    except (TypeError, ValueError):
        raise BusinessError("Choose a valid start and end date.") from None
    if dates[0] > dates[1]:
        raise BusinessError("The report end date must be on or after the start.")
    return tuple(day.isoformat() for day in dates)


SALES_DOCUMENTS = """
    WITH docs AS (
        SELECT i.id AS invoice_id, i.on_date, i.number, 'Invoice' AS type, 1 AS sign
        FROM invoices i WHERE i.on_date BETWEEN ? AND ?
        UNION ALL
        SELECT n.invoice_id, n.on_date, n.number, 'Credit note', -1
        FROM credit_notes n WHERE n.on_date BETWEEN ? AND ?
    )
"""


def sales_register(start, end, _item):
    rows = db().execute(
        SALES_DOCUMENTS + """
        SELECT d.*, i.customer_name, i.gstin, i.place_of_supply, i.taxable, i.cgst, i.sgst, i.igst, i.total
        FROM docs d JOIN invoices i ON i.id = d.invoice_id ORDER BY d.on_date, d.number
        """,
        (start, end, start, end),
    )
    return Report("Sales register", ("Date", "Document", "Type", "Customer", "GSTIN", "Place of supply",
                                     "Taxable (INR)", "CGST", "SGST", "IGST", "Total"), [
        (r["on_date"], r["number"], r["type"], r["customer_name"], r["gstin"] or "Unregistered",
         STATES[r["place_of_supply"]], *(rupees(r[key] * r["sign"]) for key in ("taxable", "cgst", "sgst", "igst", "total")))
        for r in rows
    ])


def sales_group(start, end, by):
    dimensions = {
        "customer": ("i.customer_name", "o.customer_id", "Customer-wise sales", "Customer"),
        "product": ("l.description", "l.product_id", "Product-wise sales", "Product"),
    }
    column, key, title, label = dimensions[by]
    rows = db().execute(
        SALES_DOCUMENTS + f"""
        SELECT {column} AS label, SUM(l.packets * d.sign) AS packets,
               SUM(l.taxable * d.sign) AS taxable, SUM((l.cgst+l.sgst+l.igst)*d.sign) AS tax
        FROM docs d JOIN invoices i ON i.id = d.invoice_id JOIN sales_orders o ON o.id = i.order_id
        JOIN invoice_lines l ON l.invoice_id = i.id GROUP BY {key}, {column} ORDER BY label
        """,
        (start, end, start, end),
    )
    return Report(title, (label, "Net packets", "Taxable (INR)", "GST (INR)", "Gross (INR)"),
                  [(r["label"], r["packets"], rupees(r["taxable"]), rupees(r["tax"]), rupees(r["taxable"] + r["tax"]))
                   for r in rows])


def gst_report(start, end, _item):
    rows = db().execute(
        SALES_DOCUMENTS + """
        SELECT CASE WHEN i.gstin = '' THEN 'Unregistered' ELSE 'Registered' END AS category,
               i.place_of_supply, l.gst_bp, l.hsn, SUM(l.taxable * d.sign) AS taxable,
               SUM(l.cgst * d.sign) AS cgst, SUM(l.sgst * d.sign) AS sgst, SUM(l.igst * d.sign) AS igst
        FROM docs d JOIN invoices i ON i.id = d.invoice_id JOIN invoice_lines l ON l.invoice_id = i.id
        GROUP BY category, i.place_of_supply, l.gst_bp, l.hsn ORDER BY category, i.place_of_supply, l.gst_bp
        """,
        (start, end, start, end),
    )
    return Report("Outward GST summary", ("Recipient", "Place of supply", "State code", "GST rate", "HSN",
                                           "Taxable (INR)", "CGST (INR)", "SGST (INR)", "IGST (INR)"), [
        (r["category"], STATES[r["place_of_supply"]], r["place_of_supply"], f"{rupees(r['gst_bp'])}%", r["hsn"],
         *(rupees(r[key]) for key in ("taxable", "cgst", "sgst", "igst"))) for r in rows
    ])


def purchase_report(start, end, _item):
    rows = db().execute(
        """SELECT p.*, i.name AS item, i.unit, s.name AS supplier FROM purchases p
           JOIN items i ON i.id = p.item_id JOIN suppliers s ON s.id = p.supplier_id
           WHERE p.on_date BETWEEN ? AND ? ORDER BY p.on_date, p.id""",
        (start, end),
    )
    return Report("Purchase register", ("Date", "Reference", "Supplier", "Item", "Quantity", "Unit", "Rate (INR)", "Value (INR)"),
                  [(r["on_date"], r["reference"], r["supplier"], r["item"],
                    Decimal(qty_text(r["quantity"], r["unit"])), r["unit"], rupees(r["rate_paise"]), rupees(r["total_paise"]))
                   for r in rows])


def stock_summary(start, end, item_id):
    rows = db().execute(
        """SELECT i.name, i.sku, i.kind, i.unit,
           COALESCE(SUM(CASE WHEN (m.on_date < ? OR (m.on_date = ? AND m.source = 'opening'))
                        THEN m.quantity ELSE 0 END), 0) AS opening,
           COALESCE(SUM(CASE WHEN m.on_date >= ? AND m.source <> 'adjustment'
                        AND (m.source <> 'opening' OR m.on_date > ?) AND m.quantity > 0
                        THEN m.quantity ELSE 0 END), 0) AS receipts,
           COALESCE(SUM(CASE WHEN m.on_date >= ? AND m.source <> 'adjustment'
                        AND m.quantity < 0 THEN -m.quantity ELSE 0 END), 0) AS issues,
           COALESCE(SUM(CASE WHEN m.on_date >= ? AND m.source = 'adjustment' THEN m.quantity ELSE 0 END), 0) AS adjustments,
           COALESCE(SUM(m.quantity), 0) AS closing, COALESCE(SUM(m.value_paise), 0) AS value
           FROM items i LEFT JOIN stock_moves m ON i.id = m.item_id AND m.on_date <= ?
           WHERE (? IS NULL OR i.id = ?) GROUP BY i.id ORDER BY i.kind, i.name""",
        (start, start, start, start, start, start, end, item_id, item_id),
    )
    return Report("Stock summary", ("SKU", "Item", "Type", "Unit", "Opening", "Receipts",
                                    "Issues", "Adjustments", "Closing", "Closing value (INR)"), [
        (r["sku"], r["name"], r["kind"].title(), r["unit"],
         *(Decimal(qty_text(r[key], r["unit"])) for key in ("opening", "receipts", "issues", "adjustments", "closing")),
         rupees(r["value"])) for r in rows
    ])


def ledger(start, end, item_id):
    rows = db().execute(
        """WITH movements AS (
               SELECT m.*, SUM(m.quantity) OVER (PARTITION BY m.item_id ORDER BY m.on_date, m.id) AS balance
               FROM stock_moves m WHERE m.on_date <= ? AND (? IS NULL OR m.item_id = ?)
           )
           SELECT m.*, i.name, i.unit, u.display_name, p.reference, inv.number, a.reason
           FROM movements m JOIN items i ON i.id = m.item_id JOIN users u ON u.id = m.actor_id
           LEFT JOIN purchases p ON m.source = 'purchase' AND p.id = m.source_id
           LEFT JOIN dispatches d ON m.source = 'dispatch' AND d.id = m.source_id
           LEFT JOIN invoices inv ON inv.id = d.invoice_id
           LEFT JOIN adjustments a ON m.source = 'adjustment' AND a.id = m.source_id
           WHERE m.on_date >= ? ORDER BY m.on_date, m.id""",
        (end, item_id, item_id, start),
    )
    return Report("Stock ledger", ("Date", "Item", "Movement", "Source reference", "Change", "Unit",
                                    "Balance", "Value change (INR)", "Posted by"), [
        (r["on_date"], r["name"], r["source"].title(),
         r["reference"] or r["number"] or (f"ADJ-{r['source_id']} · {r['reason']}" if r["reason"] else
                                           f"RUN-{r['source_id']:04d}" if r["source"] == "production" else "Opening stock"),
         Decimal(qty_text(r["quantity"], r["unit"])), r["unit"], Decimal(qty_text(r["balance"], r["unit"])),
         rupees(r["value_paise"]), r["display_name"]) for r in rows
    ])


def production_report(start, end, _item):
    runs = db().execute(
        """SELECT r.*, i.name FROM runs r JOIN items i ON i.id = r.product_id
           WHERE r.on_date BETWEEN ? AND ? ORDER BY r.id""",
        (start, end),
    ).fetchall()
    data = []
    for run in runs:
        label = f"RUN-{run['id']:04d}"
        change, flagged = variance(run["actual_packets"], run["planned_packets"], run["tolerance_bp"])
        cost = packet_cost(run)
        data.append((run["on_date"], label, run["name"], "Yield", "packet", run["planned_packets"],
                     run["actual_packets"], None, change, "Review" if flagged else "Within", rupees(cost) if cost is not None else None))
        lines = db().execute(
            """SELECT l.*, i.name, i.unit FROM run_lines l JOIN items i ON i.id = l.item_id
               WHERE l.run_id = ? ORDER BY i.kind DESC, i.id""",
            (run["id"],),
        )
        for line in lines:
            change, flagged = variance(line["used"] + line["wasted"], line["planned"], run["tolerance_bp"])
            data.append((run["on_date"], label, run["name"], line["name"], line["unit"],
                         *(Decimal(qty_text(value, line["unit"]))
                           for value in (line["planned"], line["used"] + line["wasted"], line["wasted"])),
                         change, "Review" if flagged else "Within", None))
    return Report("Production and variance", ("Date", "Run", "Product", "Measure", "Unit", "Planned",
                                               "Actual", "Waste", "Variance", "Tolerance", "Cost/packet (INR)"), data)


def collections(start, end, _item):
    rows = db().execute(
        """SELECT p.*, i.number, i.customer_name FROM payments p JOIN invoices i ON i.id = p.invoice_id
           WHERE p.on_date BETWEEN ? AND ? ORDER BY p.on_date, p.id""",
        (start, end),
    )
    return Report("Collections", ("Date", "Invoice", "Customer", "Method", "Receipt", "Amount (INR)"),
                  [(r["on_date"], r["number"], r["customer_name"], r["mode"], r["reference"], rupees(r["amount"]))
                   for r in rows])


def outstanding(_start, end, _item):
    rows = db().execute(
        """SELECT i.*, o.customer_id, COALESCE((SELECT SUM(p.amount) FROM payments p
           WHERE p.invoice_id = i.id AND p.on_date <= ?), 0) AS paid
           FROM invoices i JOIN sales_orders o ON o.id = i.order_id
           WHERE i.on_date <= ? AND NOT EXISTS (
             SELECT 1 FROM credit_notes n WHERE n.invoice_id = i.id AND n.on_date <= ?)
           ORDER BY i.customer_name, i.due_date""",
        (end, end, end),
    )
    as_of = date.fromisoformat(end)
    totals = defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    for row in rows:
        remaining = row["total"] - row["paid"]
        if remaining <= 0:
            continue
        days = (as_of - date.fromisoformat(row["due_date"])).days
        bucket = sum(days > edge for edge in (0, 30, 60, 90))
        customer = totals[(row["customer_id"], row["customer_name"])]
        customer[bucket] += remaining
        customer[5] += remaining
    return Report("Outstanding and ageing", ("Customer", "Current", "1–30 days late", "31–60 days late",
                                             "61–90 days late", "90+ days late", "Total (INR)"),
                  [(name, *(rupees(value) for value in buckets)) for (_, name), buckets in totals.items()])


REPORTS = {
    "sales": ("Sales register", sales_register),
    "customer-sales": ("Customer-wise sales", lambda start, end, _: sales_group(start, end, "customer")),
    "product-sales": ("Product-wise sales", lambda start, end, _: sales_group(start, end, "product")),
    "gst": ("Outward GST summary", gst_report),
    "purchases": ("Purchase register", purchase_report),
    "stock": ("Stock summary", stock_summary),
    "ledger": ("Stock ledger", ledger),
    "production": ("Production and variance", production_report),
    "collections": ("Collections", collections),
    "outstanding": ("Outstanding and ageing", outstanding),
}
