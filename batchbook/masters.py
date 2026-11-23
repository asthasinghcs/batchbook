"""Validated master data changes. Existing posted documents keep their snapshots."""

import re
import sqlite3

from .db import BusinessError, atomic, audit
from .inventory import item_for
from .numbers import quantity, scaled
from .sales import STATES


def text_field(value, name, limit, *, optional=False):
    value = value.strip()
    if len(value) > limit or (not value and not optional):
        raise BusinessError(f"Enter {name} using up to {limit} characters.")
    return value


def gstin(value, state=None):
    value = value.strip().upper()
    if value and (
        not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]", value)
        or value[:2] not in STATES or (state and value[:2] != state)
    ):
        raise BusinessError("Enter a GSTIN with a valid state prefix and 15-character format.")
    return value


def add_item(actor, form):
    sku = text_field(form.get("sku", ""), "an SKU", 24).upper()
    name = text_field(form.get("name", ""), "an item name", 80)
    kind, unit = form.get("kind"), form.get("unit")
    options = {"raw": {"kg", "L", "piece"}, "packaging": {"piece"}, "finished": {"packet"}}
    if kind not in options or unit not in options[kind]:
        raise BusinessError("Select a base unit that matches the item type.")
    price, gst, hsn, pack = 0, 0, "", 1
    if kind == "finished":
        price = scaled(form.get("price"), 100, "Selling price", minimum=1)
        gst = scaled(form.get("gst"), 100, "GST percentage", maximum=10000)
        hsn = form.get("hsn", "").strip()
        if not re.fullmatch(r"(?:\d{4}|\d{6}|\d{8})", hsn):
            raise BusinessError("Enter a 4-, 6- or 8-digit HSN code.")
        pack = scaled(form.get("pack"), 1, "Packets per carton", minimum=1, maximum=100_000)
    try:
        with atomic() as connection:
            item_id = connection.execute(
                """INSERT INTO items (sku, name, kind, unit, price_paise, gst_bp, hsn, packets_per_carton)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (sku, name, kind, unit, price, gst, hsn, pack),
            ).lastrowid
            audit("item", item_id, "created", actor, {"sku": sku})
            return item_id
    except sqlite3.IntegrityError:
        raise BusinessError("That SKU is already in use.") from None


def add_customer(actor, form):
    name = text_field(form.get("name", ""), "a customer name", 80)
    kind, state = form.get("kind"), form.get("state_code")
    if kind not in {"Retailer", "Distributor"} or state not in STATES:
        raise BusinessError("Choose the customer type and state.")
    address = text_field(form.get("billing_address", ""), "a billing address", 180)
    delivery = text_field(form.get("delivery_address", "") or address, "a delivery address", 180)
    email = text_field(form.get("email", ""), "an email", 120, optional=True)
    if email and not re.fullmatch(r"[^\s@?&]+@[^\s@?&]+\.[^\s@?&]+", email):
        raise BusinessError("Enter a valid email address.")
    credit = scaled(form.get("credit_days"), 1, "Credit days", maximum=365)
    try:
        with atomic() as connection:
            customer_id = connection.execute(
                """INSERT INTO customers (name, kind, state_code, billing_address, delivery_address, gstin,
                   email, credit_days) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, kind, state, address, delivery, gstin(form.get("gstin", ""), state), email, credit),
            ).lastrowid
            audit("customer", customer_id, "created", actor)
            return customer_id
    except sqlite3.IntegrityError:
        raise BusinessError("A customer with that name already exists.") from None


def add_supplier(actor, form):
    name = text_field(form.get("name", ""), "a supplier name", 80)
    try:
        with atomic() as connection:
            supplier_id = connection.execute(
                "INSERT INTO suppliers (name, gstin) VALUES (?, ?)", (name, gstin(form.get("gstin", "")))
            ).lastrowid
            audit("supplier", supplier_id, "created", actor)
            return supplier_id
    except sqlite3.IntegrityError:
        raise BusinessError("A supplier with that name already exists.") from None


def save_recipe(actor, product_id, expected, tolerance, form):
    expected = scaled(expected, 1, "Expected packets", minimum=1)
    tolerance = scaled(tolerance, 100, "Tolerance percentage", maximum=10000)
    with atomic() as connection:
        product = item_for(product_id)
        if product["kind"] != "finished":
            raise BusinessError("A recipe must belong to a finished product.")
        components = []
        for item in connection.execute("SELECT * FROM items WHERE kind <> 'finished'"):
            amount = form.get(f"item_{item['id']}", "")
            if amount:
                units = quantity(amount, item["unit"], item, allow_zero=True)
                if units:
                    components.append((product_id, item["id"], units))
        if not components:
            raise BusinessError("Add at least one material to the recipe.")
        connection.execute(
            """INSERT INTO recipes VALUES (?, ?, ?)
               ON CONFLICT (product_id) DO UPDATE SET
               expected_packets = excluded.expected_packets, tolerance_bp = excluded.tolerance_bp""",
            (product_id, expected, tolerance),
        )
        connection.execute("DELETE FROM recipe_lines WHERE product_id = ?", (product_id,))
        connection.executemany("INSERT INTO recipe_lines VALUES (?, ?, ?)", components)
        audit("recipe", product_id, "saved", actor, {"expected_packets": expected, "tolerance_bp": tolerance})
