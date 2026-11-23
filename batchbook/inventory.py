"""Receipts, production and dispatch all write to the same stock ledger."""

from .db import BusinessError, atomic, audit, db, required, today
from .numbers import qty_text, quantity, round_ratio, scaled, unit_scale


def item_for(item_id):
    return required("SELECT * FROM items WHERE id = ?", (item_id,), "The selected item is unavailable.")


def balance(item_id):
    row = db().execute(
        """SELECT COALESCE(SUM(quantity), 0) AS quantity, COALESCE(SUM(value_paise), 0) AS value
           FROM stock_moves WHERE item_id = ?""",
        (item_id,),
    ).fetchone()
    return row["quantity"], row["value"]


def reserved(item_id, except_order=None):
    return db().execute(
        """SELECT COALESCE(SUM(l.packets), 0)
           FROM order_lines l JOIN sales_orders o ON o.id = l.order_id
           WHERE l.product_id = ? AND o.status IN ('approved', 'invoiced') AND o.id <> ?""",
        (item_id, except_order or 0),
    ).fetchone()[0]


def move(item_id, amount, value, source, source_id, actor, date=None):
    db().execute(
        """INSERT INTO stock_moves (item_id, quantity, value_paise, source, source_id, on_date, actor_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (item_id, amount, value, source, source_id, date or today().isoformat(), actor),
    )


def issue_cost(item, amount):
    on_hand, value = balance(item["id"])
    if amount > on_hand:
        available = qty_text(on_hand, item["unit"])
        raise BusinessError(f"{item['name']} has only {available} {item['unit']} in stock.")
    return round_ratio(value * amount, on_hand) if amount else 0


def purchase(actor, supplier_id, item_id, reference, amount, unit, rate):
    reference = str(reference).strip()
    if not 1 <= len(reference) <= 60:
        raise BusinessError("Enter a supplier invoice reference of up to 60 characters.")
    rate_paise = scaled(rate, 100, "Unit rate", minimum=1)
    with atomic() as connection:
        required("SELECT id FROM suppliers WHERE id = ?", (supplier_id,), "Choose a supplier.")
        item = item_for(item_id)
        if item["kind"] == "finished":
            raise BusinessError("Select raw material or packaging.")
        amount = quantity(amount, unit, item)
        existing = connection.execute(
            "SELECT * FROM purchases WHERE supplier_id = ? AND item_id = ? AND reference = ?",
            (supplier_id, item_id, reference),
        ).fetchone()
        if existing:
            if (existing["quantity"], existing["rate_paise"]) == (amount, rate_paise):
                return existing["id"]
            raise BusinessError("That reference already has a different receipt for this item.")
        total = round_ratio(amount * rate_paise, unit_scale(item))
        receipt_id = connection.execute(
            """INSERT INTO purchases
               (supplier_id, item_id, reference, quantity, rate_paise, total_paise, on_date, actor_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (supplier_id, item_id, reference, amount, rate_paise, total, today().isoformat(), actor),
        ).lastrowid
        move(item_id, amount, total, "purchase", receipt_id, actor)
        audit("purchase", receipt_id, "received", actor)
        return receipt_id


def recipe_for(product_id):
    recipe = required("SELECT * FROM recipes WHERE product_id = ?", (product_id,), "This product needs a BOM.")
    lines = db().execute(
        """SELECT i.*, l.quantity AS per_batch FROM recipe_lines l
           JOIN items i ON i.id = l.item_id WHERE l.product_id = ? ORDER BY i.kind DESC, i.id""",
        (product_id,),
    ).fetchall()
    return recipe, lines


def run(actor, product_id, batches, good_packets, amounts, notes):
    batch_milli = scaled(batches, 1000, "Batches", minimum=1, maximum=100_000)
    actual = scaled(good_packets, 1, "Good packets")
    notes = notes.strip()
    if len(notes) > 400:
        raise BusinessError("Keep the run note within 400 characters.")
    with atomic() as connection:
        recipe, lines = recipe_for(product_id)
        planned = round_ratio(recipe["expected_packets"] * batch_milli, 1000)
        if planned == 0:
            raise BusinessError("This batch size needs to yield at least one planned packet.")
        consumption = []
        for item in lines:
            used_text, waste_text, unit = amounts.get(item["id"], ("", "", item["unit"]))
            used = quantity(used_text, unit, item, allow_zero=True)
            wasted = quantity(waste_text or "0", unit, item, allow_zero=True)
            standard = round_ratio(item["per_batch"] * batch_milli, 1000)
            cost = issue_cost(item, used + wasted)
            consumption.append((item["id"], standard, used, wasted, cost))
        material_cost = sum(line[4] for line in consumption)
        run_id = connection.execute(
            """INSERT INTO runs
               (product_id, on_date, batch_milli, planned_packets, actual_packets, tolerance_bp,
                material_paise, notes, actor_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (product_id, today().isoformat(), batch_milli, planned, actual, recipe["tolerance_bp"],
             material_cost, notes, actor),
        ).lastrowid
        for item_id, standard, used, wasted, cost in consumption:
            connection.execute(
                "INSERT INTO run_lines VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, item_id, standard, used, wasted, cost),
            )
            if used + wasted:
                move(item_id, -(used + wasted), -cost, "production", run_id, actor)
        if actual:
            move(product_id, actual, material_cost, "production", run_id, actor)
        audit("run", run_id, "posted", actor, {"expected_packets": planned, "good_packets": actual})
        return run_id


def update_run_cost(actor, run_id, labour, energy, notes):
    labour_paise = scaled(labour, 100, "Labour cost")
    energy_paise = scaled(energy, 100, "Gas and power cost")
    notes = notes.strip()
    if len(notes) > 400:
        raise BusinessError("Keep the run note within 400 characters.")
    with atomic() as connection:
        run = required("SELECT * FROM runs WHERE id = ?", (run_id,))
        changed = {"labour_paise": labour_paise, "energy_paise": energy_paise, "notes": notes}
        detail = {key: {"from": run[key], "to": value} for key, value in changed.items() if run[key] != value}
        if detail:
            connection.execute(
                "UPDATE runs SET labour_paise = ?, energy_paise = ?, notes = ? WHERE id = ?",
                (labour_paise, energy_paise, notes, run_id),
            )
            audit("run", run_id, "cost_updated", actor, detail)


def packet_cost(run):
    cost = run["material_paise"] + run["labour_paise"] + run["energy_paise"]
    return round_ratio(cost, run["actual_packets"]) if run["actual_packets"] else None


def adjust(actor, item_id, direction, amount, unit, rate, reason):
    reason = reason.strip()
    if not 1 <= len(reason) <= 200:
        raise BusinessError("Enter a stock count reason of up to 200 characters.")
    if direction not in {"add", "remove"}:
        raise BusinessError("Choose whether stock was added or removed.")
    with atomic() as connection:
        item = item_for(item_id)
        amount = quantity(amount, unit, item)
        if direction == "remove":
            if item["kind"] == "finished" and amount > balance(item_id)[0] - reserved(item_id):
                raise BusinessError("Removing this quantity would use reserved stock.")
            value = -issue_cost(item, amount)
            amount = -amount
        else:
            value = round_ratio(amount * scaled(rate, 100, "Unit rate"), unit_scale(item))
        adjustment_id = connection.execute(
            "INSERT INTO adjustments (item_id, reason, on_date, actor_id) VALUES (?, ?, ?, ?)",
            (item_id, reason, today().isoformat(), actor),
        ).lastrowid
        move(item_id, amount, value, "adjustment", adjustment_id, actor)
        audit("adjustment", adjustment_id, "posted", actor, {"reason": reason})
        return adjustment_id
