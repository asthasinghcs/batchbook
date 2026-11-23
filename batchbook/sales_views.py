from urllib.parse import urlencode
from uuid import uuid4

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import sales
from .db import BusinessError, db, required
from .numbers import amount_text, scaled
from .web import AUDIT_ROLES, access, action, csrf_token

bp = Blueprint("sales", __name__)


@bp.get("/orders")
@access("sales", "admin")
def orders():
    args = []
    where = []
    mine = g.user["role"] == "sales"
    if mine:
        where.append("o.salesman_id = ?")
        args.append(g.user["id"])
    stage = request.args.get("status", "")
    allowed = ("submitted", "approved", "invoiced", "dispatched", "rejected", "cancelled")
    if stage in allowed:
        where.append("o.status = ?")
        args.append(stage)
    filter_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = db().execute(
        f"""SELECT o.*, c.name AS customer, u.display_name AS salesman,
                   (SELECT SUM(l.packets) FROM order_lines l WHERE l.order_id = o.id) AS packets
            FROM sales_orders o JOIN customers c ON c.id = o.customer_id
            JOIN users u ON u.id = o.salesman_id {filter_sql} ORDER BY o.id DESC""",
        args,
    ).fetchall()
    customers = []
    if mine:
        customers = db().execute(
            """SELECT i.customer_name, COUNT(i.id) AS invoices, SUM(i.taxable) AS net_sales
               FROM invoices i JOIN sales_orders o ON o.id = i.order_id
               WHERE o.salesman_id = ? AND o.status = 'dispatched'
               GROUP BY o.customer_id, i.customer_name ORDER BY i.customer_name""",
            (g.user["id"],),
        ).fetchall()
    return render_template("orders.html", orders=rows, stage=stage, stages=allowed, customers=customers, mine=mine)


def line_inputs(form):
    products = form.getlist("product_id")
    counts = form.getlist("count")
    units = form.getlist("unit")
    if not len(products) == len(counts) == len(units):
        raise BusinessError("Each order line needs a product, quantity and unit.")
    return [(scaled(product, 1, "Product", minimum=1), count, unit)
            for product, count, unit in zip(products, counts, units) if product or count]


@bp.route("/orders/new", methods=["GET", "POST"])
@access("sales")
def new_order():
    ref = request.form.get("client_ref", "") or str(uuid4())
    entries = [("", "", "packet")]
    if request.method == "POST":
        entries = list(zip(request.form.getlist("product_id"), request.form.getlist("count"),
                           request.form.getlist("unit")))[:50] or entries
        try:
            order_id = sales.submit(g.user["id"], request.form.get("customer_id"), line_inputs(request.form), ref)
            flash("Order sent to the factory.", "success")
            return redirect(url_for("sales.order", order_id=order_id), code=303)
        except BusinessError as error:
            flash(str(error), "error")
    customers = db().execute("SELECT * FROM customers ORDER BY name").fetchall()
    products = db().execute("SELECT * FROM items WHERE kind = 'finished' AND price_paise > 0 ORDER BY name").fetchall()
    return render_template("new_order.html", customers=customers, products=products, ref=ref, entries=entries)


@bp.get("/orders/sync-token")
@access("sales")
def sync_token():
    response = {"csrf": csrf_token(), "account": g.user["id"]}
    return response, 200, {"Cache-Control": "no-store"}


@bp.post("/orders/sync")
@access("sales")
def sync():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("account") != g.user["id"]:
        return {"error": "Sign in as the salesperson who saved this order."}, 403
    lines = data.get("lines")
    if not isinstance(lines, list) or not all(
        isinstance(line, list) and len(line) == 3 and all(isinstance(value, str) for value in line)
        for line in lines
    ):
        return {"error": "Each order line needs a product, quantity and unit."}, 400
    try:
        customer = scaled(data.get("customer_id"), 1, "Customer", minimum=1)
        order_id = sales.submit(g.user["id"], customer,
                               [(scaled(line[0], 1, "Product", minimum=1), line[1], line[2]) for line in lines],
                               data.get("client_ref"))
    except BusinessError as error:
        return {"error": str(error)}, 400
    return {"order": order_id, "url": url_for("sales.order", order_id=order_id)}


@bp.get("/orders/<int:order_id>")
@access("admin", "sales")
def order(order_id):
    header = sales.order_for(order_id)
    if g.user["role"] == "sales" and g.user["id"] != header["salesman_id"]:
        abort(404)
    lines = []
    admin = g.user["role"] == "admin"
    for row in sales.order_lines(order_id):
        on_hand, available = sales.availability(row["product_id"], order_id) if admin else (0, 0)
        lines.append(dict(row, on_hand=on_hand, available=available, short=max(0, row["packets"] - available)))
    invoice = db().execute("SELECT id, number FROM invoices WHERE order_id = ?", (order_id,)).fetchone()
    return render_template("order.html", order=header, lines=lines, invoice=invoice, admin=admin)


@bp.post("/orders/<int:order_id>/review")
@access("admin")
def review(order_id):
    decision = request.form.get("decision")
    if decision not in {"approve", "reject"}:
        abort(400)
    edits = {row["product_id"]: (request.form.get(f"qty_{row['product_id']}"),
                                 request.form.get(f"price_{row['product_id']}"))
             for row in sales.order_lines(order_id)}
    return action(url_for("sales.order", order_id=order_id),
                  "Order approved and stock reserved." if decision == "approve" else "Order rejected.",
                  sales.review, g.user["id"], order_id, decision, edits, request.form.get("note", ""))


@bp.post("/orders/<int:order_id>/invoice")
@access("admin")
def issue_invoice(order_id):
    try:
        invoice_id = sales.invoice(g.user["id"], order_id)
    except BusinessError as error:
        flash(str(error), "error")
        return redirect(url_for("sales.order", order_id=order_id), code=303)
    flash("GST invoice posted.", "success")
    return redirect(url_for("sales.invoice", invoice_id=invoice_id), code=303)


@bp.get("/invoices")
@access(*AUDIT_ROLES)
def invoices():
    invoices = db().execute(
        """SELECT i.*, o.status, COALESCE((SELECT SUM(p.amount) FROM payments p
           WHERE p.invoice_id = i.id), 0) AS paid
           FROM invoices i JOIN sales_orders o ON o.id = i.order_id ORDER BY i.id DESC"""
    ).fetchall()
    return render_template("invoices.html", invoices=invoices)


@bp.get("/invoices/<int:invoice_id>")
@access(*AUDIT_ROLES)
def invoice(invoice_id):
    header = sales.invoice_for(invoice_id)
    lines = db().execute("SELECT * FROM invoice_lines WHERE invoice_id = ?", (invoice_id,)).fetchall()
    paid = db().execute("SELECT * FROM payments WHERE invoice_id = ? ORDER BY id", (invoice_id,)).fetchall()
    email = urlencode({
        "subject": f"Invoice {header['number']} from {sales.FACTORY_NAME}",
        "body": (f"Hello {header['customer_name']},\n\nPlease find invoice {header['number']} attached. "
                 f"The invoice total is INR {amount_text(header['total'])}.\n\n{sales.FACTORY_NAME}"),
    }).replace("+", "%20")
    return render_template("invoice.html", invoice=header, lines=lines, payments=paid, mail_query=email,
                           factory_name=sales.FACTORY_NAME, factory_gstin=sales.FACTORY_GSTIN,
                           factory_address=sales.FACTORY_ADDRESS, admin=g.user["role"] == "admin")


@bp.post("/invoices/<int:invoice_id>/<operation>")
@access("admin")
def invoice_action(invoice_id, operation):
    destination = url_for("sales.invoice", invoice_id=invoice_id)
    actor = g.user["id"]
    if operation == "dispatch":
        return action(destination, "Dispatch posted. Finished stock updated.", sales.dispatch, actor, invoice_id)
    if operation == "payment":
        return action(destination, "Payment recorded.", sales.payment, actor, invoice_id,
                      request.form.get("amount"), request.form.get("mode"), request.form.get("reference", ""))
    if operation == "credit":
        return action(destination, "Credit note issued. The original invoice is retained.", sales.credit,
                      actor, invoice_id, request.form.get("reason", ""))
    abort(404)


@bp.get("/invoices/<int:invoice_id>/document.pdf")
@access(*AUDIT_ROLES)
def invoice_pdf(invoice_id):
    from .exports import invoice_document, pdf_response

    header = sales.invoice_for(invoice_id)
    lines = db().execute("SELECT * FROM invoice_lines WHERE invoice_id = ?", (invoice_id,)).fetchall()
    credit = None
    if request.args.get("credit") == "1":
        credit = required("SELECT * FROM credit_notes WHERE invoice_id = ?", (invoice_id,), "No credit note exists.")
    return pdf_response(invoice_document(header, lines, credit), (credit or header)["number"].replace("/", "-"))
