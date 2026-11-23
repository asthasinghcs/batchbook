from uuid import uuid4

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import inventory, masters
from .db import BusinessError, db, required
from .numbers import UNIT_MAP, variance
from .web import STOCK_ROLES, access, action

bp = Blueprint("factory", __name__)
OPERATORS = ("admin", "production")
MASTER_FORMS = {"items": masters.add_item, "customers": masters.add_customer, "suppliers": masters.add_supplier}


def item_quantities(item):
    return tuple(UNIT_MAP[item["unit"]]) + (("carton",) if item["unit"] == "packet" else ())


def material_inputs(form, items, kind):
    return {item["id"]: (form.get(f"used_{item['id']}"), form.get(f"waste_{item['id']}"),
                         form.get(f"unit_{item['id']}")) for item in items if item["kind"] == kind}


@bp.get("/stock")
@access(*STOCK_ROLES)
def stock():
    connection = db()
    items = []
    for item in connection.execute(
        """SELECT i.*, COALESCE(SUM(m.quantity), 0) AS on_hand, COALESCE(SUM(m.value_paise), 0) AS value
           FROM items i LEFT JOIN stock_moves m ON m.item_id = i.id GROUP BY i.id ORDER BY i.kind, i.name"""
    ):
        reservations = inventory.reserved(item["id"]) if item["kind"] == "finished" else 0
        items.append(dict(item, reserved=reservations, available=item["on_hand"] - reservations,
                          units=item_quantities(item)))
    return render_template("stock.html", items=items, admin=g.user["role"] == "admin")


@bp.get("/stock/<int:item_id>")
@access(*STOCK_ROLES)
def item_ledger(item_id):
    item = inventory.item_for(item_id)
    moves = db().execute(
        """SELECT m.*, SUM(m.quantity) OVER (ORDER BY m.id) AS running,
                  u.display_name, p.reference, i.number, i.id AS invoice_id, a.reason
           FROM stock_moves m JOIN users u ON u.id = m.actor_id
           LEFT JOIN purchases p ON m.source = 'purchase' AND p.id = m.source_id
           LEFT JOIN dispatches d ON m.source = 'dispatch' AND d.id = m.source_id
           LEFT JOIN invoices i ON i.id = d.invoice_id
           LEFT JOIN adjustments a ON m.source = 'adjustment' AND a.id = m.source_id
           WHERE m.item_id = ? ORDER BY m.id DESC""",
        (item_id,),
    ).fetchall()
    return render_template("item_ledger.html", item=item, moves=moves, closing=inventory.balance(item_id)[0])


@bp.post("/stock/adjust")
@access("admin")
def adjust_stock():
    form = request.form
    return action(url_for("factory.stock"), "Stock adjustment posted.", inventory.adjust, g.user["id"],
                  form.get("item_id"), form.get("direction"), form.get("amount"), form.get("unit"),
                  form.get("rate", "0"), form.get("reason", ""))


@bp.route("/purchases", methods=["GET", "POST"])
@access(*OPERATORS)
def purchases():
    form = request.form
    if request.method == "POST":
        try:
            inventory.purchase(g.user["id"], form.get("supplier_id"), form.get("item_id"),
                               form.get("reference", ""), form.get("amount"), form.get("unit"), form.get("rate"))
            flash("Purchase received. Material stock updated.", "success")
            return redirect(url_for("factory.purchases"), code=303)
        except BusinessError as error:
            flash(str(error), "error")
    connection = db()
    receipts = connection.execute(
        """SELECT p.*, i.name AS item, i.unit, s.name AS supplier FROM purchases p
           JOIN items i ON i.id = p.item_id JOIN suppliers s ON s.id = p.supplier_id ORDER BY p.id DESC"""
    ).fetchall()
    items = [dict(item, units=item_quantities(item)) for item in connection.execute(
        "SELECT * FROM items WHERE kind <> 'finished' ORDER BY kind DESC, id"
    )]
    suppliers = connection.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    return render_template("purchases.html", receipts=receipts, items=items, suppliers=suppliers)


@bp.get("/purchases/<int:receipt_id>")
@access(*STOCK_ROLES)
def receipt(receipt_id):
    row = required(
        """SELECT p.*, i.name AS item, i.unit, s.name AS supplier, u.display_name
           FROM purchases p JOIN items i ON i.id = p.item_id JOIN suppliers s ON s.id = p.supplier_id
           JOIN users u ON u.id = p.actor_id WHERE p.id = ?""",
        (receipt_id,),
    )
    return render_template("receipt.html", receipt=row)


@bp.get("/production")
@access(*STOCK_ROLES)
def runs():
    rows = db().execute(
        """SELECT r.*, i.name AS product, i.price_paise FROM runs r
           JOIN items i ON i.id = r.product_id ORDER BY r.id DESC"""
    ).fetchall()
    work = [dict(row, yield_variance=variance(row["actual_packets"], row["planned_packets"], row["tolerance_bp"]),
                 unit_cost=inventory.packet_cost(row)) for row in rows]
    drafts = []
    if g.user["role"] in OPERATORS:
        drafts = db().execute(
            """SELECT d.*, i.name AS product, u.display_name AS started_by FROM run_drafts d
               JOIN items i ON i.id = d.product_id JOIN users u ON u.id = d.actor_id
               WHERE d.run_id IS NULL ORDER BY d.id DESC"""
        ).fetchall()
    return render_template("runs.html", runs=work, drafts=drafts, operator=g.user["role"] in OPERATORS)


@bp.route("/production/new", methods=["GET", "POST"])
@access(*OPERATORS)
def new_run():
    products = db().execute(
        """SELECT i.id, i.name FROM items i JOIN recipes r ON r.product_id = i.id ORDER BY i.name"""
    ).fetchall()
    form = request.form
    product_id = form.get("product_id", type=int) if request.method == "POST" else request.args.get("product", type=int)
    recipe, components = None, []
    ref = form.get("client_ref") or str(uuid4())
    if products:
        product_id = product_id or products[0]["id"]
        recipe, lines = inventory.recipe_for(product_id)
        components = [dict(item, units=item_quantities(item), on_hand=inventory.balance(item["id"])[0])
                      for item in lines]
        if request.method == "POST":
            try:
                draft_id = inventory.start_run(g.user["id"], product_id, form.get("batches"),
                                               material_inputs(form, components, "raw"), ref)
                return redirect(url_for("factory.draft", draft_id=draft_id), code=303)
            except BusinessError as error:
                flash(str(error), "error")
    return render_template("new_run.html", products=products, recipe=recipe, product_id=product_id,
                           components=components, ref=ref)


@bp.route("/production/drafts/<int:draft_id>", methods=["GET", "POST"])
@access(*OPERATORS)
def draft(draft_id):
    header = inventory.draft_for(draft_id)
    if header["run_id"]:
        return redirect(url_for("factory.run", run_id=header["run_id"]), code=303)
    lines = [dict(item, units=item_quantities(item), on_hand=inventory.balance(item["id"])[0])
             for item in inventory.draft_lines(draft_id)]
    form = request.form
    if request.method == "POST":
        try:
            if form.get("action") == "revise":
                inventory.revise_materials(g.user["id"], draft_id, material_inputs(form, lines, "raw"))
                flash("Material quantities updated.", "success")
                return redirect(url_for("factory.draft", draft_id=draft_id), code=303)
            run_id = inventory.post_run(g.user["id"], draft_id, form.get("good_packets"),
                                       material_inputs(form, lines, "packaging"), form.get("notes", ""))
            flash("Batch posted. Material issues and good packets are in the stock ledger.", "success")
            return redirect(url_for("factory.run", run_id=run_id), code=303)
        except BusinessError as error:
            flash(str(error), "error")
    materials = [item for item in lines if item["kind"] == "raw"]
    packaging = [item for item in lines if item["kind"] == "packaging"]
    return render_template("draft.html", draft=header, materials=materials, packaging=packaging)


@bp.get("/production/<int:run_id>")
@access(*STOCK_ROLES)
def run(run_id):
    header = required(
        """SELECT r.*, i.name AS product, i.price_paise, u.display_name AS operator FROM runs r
           JOIN items i ON i.id = r.product_id JOIN users u ON u.id = r.actor_id WHERE r.id = ?""",
        (run_id,),
    )
    lines = [dict(line, total=line["used"] + line["wasted"],
                  variance=variance(line["used"] + line["wasted"], line["planned"], header["tolerance_bp"]))
             for line in db().execute(
                 """SELECT l.*, i.name, i.unit FROM run_lines l JOIN items i ON i.id = l.item_id
                    WHERE l.run_id = ? ORDER BY i.kind DESC, i.id""",
                 (run_id,),
             )]
    events = db().execute(
        """SELECT a.*, u.display_name FROM audit_events a JOIN users u ON u.id = a.actor_id
           WHERE (a.entity = 'run' AND a.entity_id = ?)
              OR (a.entity = 'draft' AND a.entity_id IN (SELECT id FROM run_drafts WHERE run_id = ?))
           ORDER BY a.id DESC""",
        (run_id, run_id),
    ).fetchall()
    movements = db().execute(
        """SELECT m.*, i.name, i.unit,
                  (SELECT SUM(s.quantity) FROM stock_moves s WHERE s.item_id = m.item_id AND s.id <= m.id) AS after
           FROM stock_moves m JOIN items i ON i.id = m.item_id
           WHERE m.source = 'production' AND m.source_id = ? ORDER BY m.quantity > 0 DESC, m.id""",
        (run_id,),
    ).fetchall()
    return render_template("run.html", run=header, lines=lines, unit_cost=inventory.packet_cost(header),
                           yield_variance=variance(header["actual_packets"], header["planned_packets"], header["tolerance_bp"]),
                           events=events, movements=movements, operator=g.user["role"] in OPERATORS)


@bp.post("/production/<int:run_id>/cost")
@access(*OPERATORS)
def run_cost(run_id):
    return action(url_for("factory.run", run_id=run_id), "Run costs saved.", inventory.update_run_cost,
                  g.user["id"], run_id, request.form.get("labour"), request.form.get("energy"),
                  request.form.get("notes", ""))


@bp.route("/masters/<section>", methods=["GET", "POST"])
@access("admin")
def master(section):
    if section not in MASTER_FORMS and section != "recipes":
        abort(404)
    connection = db()
    product_id = request.values.get("product_id", type=int)
    if request.method == "POST":
        try:
            if section == "recipes":
                masters.save_recipe(g.user["id"], product_id, request.form.get("expected"),
                                    request.form.get("tolerance"), request.form)
            else:
                MASTER_FORMS[section](g.user["id"], request.form)
            flash("Master record saved.", "success")
            return redirect(url_for("factory.master", section=section, product_id=product_id), code=303)
        except BusinessError as error:
            flash(str(error), "error")
    if section == "recipes":
        products = connection.execute("SELECT * FROM items WHERE kind = 'finished' ORDER BY name").fetchall()
        materials = connection.execute("SELECT * FROM items WHERE kind <> 'finished' ORDER BY kind DESC, id").fetchall()
        product_id = product_id or (products[0]["id"] if products else None)
        recipe = connection.execute("SELECT * FROM recipes WHERE product_id = ?", (product_id,)).fetchone()
        current = {row["item_id"]: row["quantity"] for row in connection.execute(
            "SELECT * FROM recipe_lines WHERE product_id = ?", (product_id,)
        )}
        return render_template("masters.html", section=section, products=products, materials=materials,
                               product_id=product_id, recipe=recipe, current=current)
    rows = connection.execute(f"SELECT * FROM {section} ORDER BY name").fetchall()
    return render_template("masters.html", section=section, records=rows)
