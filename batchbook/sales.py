"""The sales order owns the workflow. Invoices snapshot the approved lines."""

from datetime import timedelta
from uuid import UUID

from .db import BusinessError, atomic, audit, db, required, today
from .inventory import balance, issue_cost, item_for, move, reserved
from .numbers import quantity, round_ratio, scaled

FACTORY_STATE = "33"
FACTORY_NAME = "Kaveri Snack Works"
FACTORY_ADDRESS = "1 Demo Industrial Estate, Tiruchirappalli, Tamil Nadu 620001"
FACTORY_GSTIN = "33AAAAA0000A1Z0"
STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}


def order_for(order_id):
    return required(
        """SELECT o.*, c.name AS customer, c.state_code, c.email, u.display_name AS salesman
           FROM sales_orders o JOIN customers c ON c.id = o.customer_id
           JOIN users u ON u.id = o.salesman_id WHERE o.id = ?""",
        (order_id,),
        "The order could not be found.",
    )


def order_lines(order_id):
    return db().execute(
        """SELECT l.*, i.name, i.sku, i.hsn, i.gst_bp, i.packets_per_carton
           FROM order_lines l JOIN items i ON i.id = l.product_id WHERE l.order_id = ?
           ORDER BY i.name""",
        (order_id,),
    ).fetchall()


def availability(product_id, order_id=None):
    on_hand = balance(product_id)[0]
    return on_hand, on_hand - reserved(product_id, order_id)


def submit(actor, customer_id, lines, client_ref):
    try:
        client_ref = str(UUID(str(client_ref)))
    except ValueError:
        raise BusinessError("Refresh the order form and try again.") from None
    if not 1 <= len(lines) <= 50:
        raise BusinessError("Add between 1 and 50 order lines.")
    with atomic() as connection:
        existing = connection.execute(
            "SELECT id FROM sales_orders WHERE salesman_id = ? AND client_ref = ?",
            (actor, client_ref),
        ).fetchone()
        if existing:
            return existing["id"]
        required("SELECT id FROM customers WHERE id = ?", (customer_id,), "Choose a customer.")
        order_id = connection.execute(
            """INSERT INTO sales_orders (customer_id, salesman_id, client_ref, status, on_date)
               VALUES (?, ?, ?, 'submitted', ?)""",
            (customer_id, actor, client_ref, today().isoformat()),
        ).lastrowid
        totals = {}
        for product_id, count, unit in lines:
            item = item_for(product_id)
            if item["kind"] != "finished" or item["price_paise"] <= 0:
                raise BusinessError("Choose a finished product with an active selling price.")
            packets = quantity(count, unit, item)
            previous = totals.get(product_id, (0, item["price_paise"]))
            totals[product_id] = (previous[0] + packets, item["price_paise"])
        connection.executemany(
            "INSERT INTO order_lines VALUES (?, ?, ?, ?)",
            [(order_id, product, count, price) for product, (count, price) in totals.items()],
        )
        audit("order", order_id, "submitted", actor)
        return order_id


def review(actor, order_id, decision, edits, note):
    note = note.strip()
    if len(note) > 400:
        raise BusinessError("Keep the review note within 400 characters.")
    with atomic() as connection:
        order = order_for(order_id)
        if order["status"] != "submitted":
            raise BusinessError("An order can only be reviewed while it is submitted.")
        if decision == "reject":
            if not note:
                raise BusinessError("Enter a reason for rejection.")
            connection.execute(
                "UPDATE sales_orders SET status = 'rejected', review_note = ? WHERE id = ?", (note, order_id)
            )
            audit("order", order_id, "rejected", actor, {"note": note})
            return
        changes = []
        for line in order_lines(order_id):
            product_id = line["product_id"]
            count_text, price_text = edits.get(product_id, ("", ""))
            count = scaled(count_text, 1, "Packets", minimum=1)
            price = scaled(price_text, 100, "Price", minimum=1)
            available = availability(product_id, order_id)[1]
            if count > available:
                raise BusinessError(f"{line['name']} is short by {count - available} packets.")
            if (line["packets"], line["price_paise"]) != (count, price):
                changes.append({"product_id": product_id, "original_packets": line["packets"],
                                "packets": count, "original_price_paise": line["price_paise"], "price_paise": price})
            connection.execute(
                "UPDATE order_lines SET packets = ?, price_paise = ? WHERE order_id = ? AND product_id = ?",
                (count, price, order_id, product_id),
            )
        connection.execute(
            "UPDATE sales_orders SET status = 'approved', review_note = ? WHERE id = ?", (note, order_id)
        )
        audit("order", order_id, "approved", actor, {"note": note, "line_changes": changes})


def line_tax(taxable, gst_bp, state_code):
    if state_code == FACTORY_STATE:
        half = round_ratio(taxable * gst_bp, 20000)
        return half, half, 0
    return 0, 0, round_ratio(taxable * gst_bp, 10000)


def next_number(series, on_date):
    start = on_date.year - int(on_date.month < 4)
    row = db().execute(
        """INSERT INTO sequences VALUES (?, ?, 1)
           ON CONFLICT (series, fiscal_start) DO UPDATE SET last_number = last_number + 1
           RETURNING last_number""",
        (series, start),
    ).fetchone()
    return f"{series}/{start % 100:02d}-{(start + 1) % 100:02d}/{row[0]:05d}"


def invoice(actor, order_id):
    with atomic() as connection:
        order = order_for(order_id)
        existing = connection.execute("SELECT id FROM invoices WHERE order_id = ?", (order_id,)).fetchone()
        if existing:
            return existing["id"]
        if order["status"] != "approved":
            raise BusinessError("Approve the order before raising its invoice.")
        customer = required("SELECT * FROM customers WHERE id = ?", (order["customer_id"],))
        lines = []
        for line in order_lines(order_id):
            taxable = line["price_paise"] * line["packets"]
            taxes = line_tax(taxable, line["gst_bp"], customer["state_code"])
            lines.append((line["product_id"], line["name"], line["hsn"], line["packets"],
                          line["price_paise"], line["gst_bp"], taxable, *taxes))
        taxable, cgst, sgst, igst = (sum(line[index] for line in lines) for index in range(6, 10))
        date = today()
        number = next_number("INV", date)
        invoice_id = connection.execute(
            """INSERT INTO invoices
               (number, order_id, on_date, due_date, customer_name, gstin, billing_address,
                delivery_address, place_of_supply, taxable, cgst, sgst, igst, total, actor_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (number, order_id, date.isoformat(), (date + timedelta(days=customer["credit_days"])).isoformat(),
             customer["name"], customer["gstin"], customer["billing_address"], customer["delivery_address"],
             customer["state_code"], taxable, cgst, sgst, igst, taxable + cgst + sgst + igst, actor),
        ).lastrowid
        connection.executemany(
            "INSERT INTO invoice_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(invoice_id, *line) for line in lines],
        )
        connection.execute("UPDATE sales_orders SET status = 'invoiced' WHERE id = ?", (order_id,))
        audit("invoice", invoice_id, "issued", actor, {"number": number, "order_id": order_id})
        return invoice_id


def dispatch(actor, invoice_id):
    with atomic() as connection:
        invoice = required("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
        order = order_for(invoice["order_id"])
        existing = connection.execute("SELECT id FROM dispatches WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if existing:
            return existing["id"]
        if order["status"] != "invoiced":
            raise BusinessError("Only an open invoice can be dispatched.")
        dispatch_id = connection.execute(
            "INSERT INTO dispatches (invoice_id, on_date, actor_id) VALUES (?, ?, ?)",
            (invoice_id, today().isoformat(), actor),
        ).lastrowid
        lines = connection.execute("SELECT * FROM invoice_lines WHERE invoice_id = ?", (invoice_id,))
        for line in lines:
            item = item_for(line["product_id"])
            cost = issue_cost(item, line["packets"])
            move(item["id"], -line["packets"], -cost, "dispatch", dispatch_id, actor)
        connection.execute("UPDATE sales_orders SET status = 'dispatched' WHERE id = ?", (order["id"],))
        audit("invoice", invoice_id, "dispatched", actor, {"dispatch_id": dispatch_id})
        return dispatch_id


def invoice_for(invoice_id):
    return required(
        """SELECT i.*, o.status, o.customer_id, c.email, n.id AS credit_id, n.number AS credit_number,
                  COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0) AS paid
           FROM invoices i JOIN sales_orders o ON o.id = i.order_id
           JOIN customers c ON c.id = o.customer_id
           LEFT JOIN credit_notes n ON n.invoice_id = i.id
           WHERE i.id = ?""",
        (invoice_id,),
    )


def payment(actor, invoice_id, amount, mode, reference):
    value = scaled(amount, 100, "Payment", minimum=1)
    reference = reference.strip()
    if mode not in {"Cash", "UPI", "Bank transfer", "Cheque"} or not 1 <= len(reference) <= 60:
        raise BusinessError("Choose a payment method and enter a receipt reference.")
    with atomic() as connection:
        invoice = invoice_for(invoice_id)
        existing = connection.execute(
            "SELECT * FROM payments WHERE invoice_id = ? AND reference = ?", (invoice_id, reference)
        ).fetchone()
        if existing:
            if (existing["amount"], existing["mode"]) == (value, mode):
                return existing["id"]
            raise BusinessError("That receipt reference is already used for this invoice.")
        if invoice["credit_id"]:
            raise BusinessError("The invoice is cancelled.")
        if value > invoice["total"] - invoice["paid"]:
            raise BusinessError("The payment exceeds the invoice's outstanding amount.")
        payment_id = connection.execute(
            """INSERT INTO payments (invoice_id, amount, reference, mode, on_date, actor_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (invoice_id, value, reference, mode, today().isoformat(), actor),
        ).lastrowid
        audit("invoice", invoice_id, "payment_recorded", actor, {"amount_paise": value, "payment_id": payment_id})
        return payment_id


def credit(actor, invoice_id, reason):
    reason = reason.strip()
    if not 1 <= len(reason) <= 200:
        raise BusinessError("Enter a reason of up to 200 characters for the credit note.")
    with atomic() as connection:
        invoice = invoice_for(invoice_id)
        if invoice["credit_id"]:
            return invoice["credit_id"]
        if invoice["status"] != "invoiced" or invoice["paid"]:
            raise BusinessError("A full credit requires an unpaid, undelivered invoice.")
        date = today()
        number = next_number("CN", date)
        credit_id = connection.execute(
            "INSERT INTO credit_notes (invoice_id, number, reason, on_date, actor_id) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, number, reason, date.isoformat(), actor),
        ).lastrowid
        connection.execute("UPDATE sales_orders SET status = 'cancelled' WHERE id = ?", (invoice["order_id"],))
        audit("invoice", invoice_id, "credited", actor, {"number": number, "reason": reason})
        return credit_id
