CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('sales', 'admin', 'production', 'auditor', 'owner'))
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    sku TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('raw', 'packaging', 'finished')),
    unit TEXT NOT NULL CHECK (unit IN ('kg', 'L', 'piece', 'packet')),
    price_paise INTEGER NOT NULL DEFAULT 0 CHECK (price_paise >= 0),
    gst_bp INTEGER NOT NULL DEFAULT 0 CHECK (gst_bp BETWEEN 0 AND 10000),
    hsn TEXT NOT NULL DEFAULT '',
    packets_per_carton INTEGER NOT NULL DEFAULT 1 CHECK (packets_per_carton > 0)
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('Retailer', 'Distributor')),
    state_code TEXT NOT NULL,
    billing_address TEXT NOT NULL,
    delivery_address TEXT NOT NULL,
    gstin TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    credit_days INTEGER NOT NULL DEFAULT 30 CHECK (credit_days BETWEEN 0 AND 365)
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    gstin TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS recipes (
    product_id INTEGER PRIMARY KEY REFERENCES items(id),
    expected_packets INTEGER NOT NULL CHECK (expected_packets > 0),
    tolerance_bp INTEGER NOT NULL CHECK (tolerance_bp BETWEEN 0 AND 10000)
);

CREATE TABLE IF NOT EXISTS recipe_lines (
    product_id INTEGER NOT NULL REFERENCES recipes(product_id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (product_id, item_id),
    CHECK (product_id <> item_id)
);

CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    reference TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    rate_paise INTEGER NOT NULL CHECK (rate_paise >= 0),
    total_paise INTEGER NOT NULL CHECK (total_paise >= 0),
    on_date TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    UNIQUE(supplier_id, reference, item_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES items(id),
    on_date TEXT NOT NULL,
    batch_milli INTEGER NOT NULL CHECK (batch_milli > 0),
    planned_packets INTEGER NOT NULL CHECK (planned_packets > 0),
    actual_packets INTEGER NOT NULL CHECK (actual_packets >= 0),
    tolerance_bp INTEGER NOT NULL,
    material_paise INTEGER NOT NULL CHECK (material_paise >= 0),
    labour_paise INTEGER NOT NULL DEFAULT 0 CHECK (labour_paise >= 0),
    energy_paise INTEGER NOT NULL DEFAULT 0 CHECK (energy_paise >= 0),
    notes TEXT NOT NULL DEFAULT '',
    actor_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS run_lines (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    planned INTEGER NOT NULL CHECK (planned >= 0),
    used INTEGER NOT NULL CHECK (used >= 0),
    wasted INTEGER NOT NULL CHECK (wasted >= 0),
    cost_paise INTEGER NOT NULL CHECK (cost_paise >= 0),
    PRIMARY KEY (run_id, item_id)
);

CREATE TABLE IF NOT EXISTS run_drafts (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES items(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    started_on TEXT NOT NULL,
    batch_milli INTEGER NOT NULL CHECK (batch_milli > 0),
    planned_packets INTEGER NOT NULL CHECK (planned_packets > 0),
    tolerance_bp INTEGER NOT NULL,
    client_ref TEXT NOT NULL,
    run_id INTEGER UNIQUE REFERENCES runs(id),
    UNIQUE(actor_id, client_ref)
);

CREATE TABLE IF NOT EXISTS run_draft_lines (
    draft_id INTEGER NOT NULL REFERENCES run_drafts(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    planned INTEGER NOT NULL CHECK (planned >= 0),
    used INTEGER NOT NULL CHECK (used >= 0),
    wasted INTEGER NOT NULL CHECK (wasted >= 0),
    PRIMARY KEY (draft_id, item_id)
);

CREATE TABLE IF NOT EXISTS sales_orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    salesman_id INTEGER NOT NULL REFERENCES users(id),
    client_ref TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('submitted', 'approved', 'rejected', 'invoiced', 'dispatched', 'cancelled')),
    on_date TEXT NOT NULL,
    review_note TEXT NOT NULL DEFAULT '',
    UNIQUE(salesman_id, client_ref)
);

CREATE TABLE IF NOT EXISTS order_lines (
    order_id INTEGER NOT NULL REFERENCES sales_orders(id),
    product_id INTEGER NOT NULL REFERENCES items(id),
    packets INTEGER NOT NULL CHECK (packets > 0),
    price_paise INTEGER NOT NULL CHECK (price_paise > 0),
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE IF NOT EXISTS sequences (
    series TEXT NOT NULL,
    fiscal_start INTEGER NOT NULL,
    last_number INTEGER NOT NULL CHECK (last_number BETWEEN 1 AND 999999),
    PRIMARY KEY (series, fiscal_start)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    number TEXT UNIQUE NOT NULL,
    order_id INTEGER UNIQUE NOT NULL REFERENCES sales_orders(id),
    on_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    gstin TEXT NOT NULL,
    billing_address TEXT NOT NULL,
    delivery_address TEXT NOT NULL,
    place_of_supply TEXT NOT NULL,
    taxable INTEGER NOT NULL CHECK(taxable >= 0),
    cgst INTEGER NOT NULL CHECK(cgst >= 0),
    sgst INTEGER NOT NULL CHECK(sgst >= 0),
    igst INTEGER NOT NULL CHECK(igst >= 0),
    total INTEGER NOT NULL CHECK(total = taxable + cgst + sgst + igst),
    actor_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    product_id INTEGER NOT NULL REFERENCES items(id),
    description TEXT NOT NULL,
    hsn TEXT NOT NULL,
    packets INTEGER NOT NULL CHECK(packets > 0),
    price_paise INTEGER NOT NULL CHECK(price_paise > 0),
    gst_bp INTEGER NOT NULL,
    taxable INTEGER NOT NULL,
    cgst INTEGER NOT NULL,
    sgst INTEGER NOT NULL,
    igst INTEGER NOT NULL,
    PRIMARY KEY (invoice_id, product_id)
);

CREATE TABLE IF NOT EXISTS dispatches (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER UNIQUE NOT NULL REFERENCES invoices(id),
    on_date TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    reference TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('Cash', 'UPI', 'Bank transfer', 'Cheque')),
    on_date TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    UNIQUE(invoice_id, reference)
);

CREATE TABLE IF NOT EXISTS credit_notes (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER UNIQUE NOT NULL REFERENCES invoices(id),
    number TEXT UNIQUE NOT NULL,
    reason TEXT NOT NULL,
    on_date TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS stock_moves (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    quantity INTEGER NOT NULL CHECK (quantity <> 0),
    value_paise INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('opening', 'purchase', 'production', 'dispatch', 'adjustment')),
    source_id INTEGER NOT NULL,
    on_date TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    UNIQUE(item_id, source, source_id)
);

CREATE TABLE IF NOT EXISTS adjustments (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id),
    reason TEXT NOT NULL,
    on_date TEXT NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor_id INTEGER NOT NULL REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS stock_by_item ON stock_moves(item_id, on_date);
CREATE INDEX IF NOT EXISTS orders_by_salesman ON sales_orders(salesman_id, id);
CREATE INDEX IF NOT EXISTS orders_by_status ON sales_orders(status, id);
CREATE INDEX IF NOT EXISTS payments_by_invoice ON payments(invoice_id, on_date);
CREATE INDEX IF NOT EXISTS audit_by_entity ON audit_events(entity, entity_id, id);

CREATE TRIGGER IF NOT EXISTS stock_no_update BEFORE UPDATE ON stock_moves
BEGIN SELECT RAISE(ABORT, 'Stock movements are append-only'); END;
CREATE TRIGGER IF NOT EXISTS stock_no_delete BEFORE DELETE ON stock_moves
BEGIN SELECT RAISE(ABORT, 'Stock movements are append-only'); END;
CREATE TRIGGER IF NOT EXISTS invoice_no_update BEFORE UPDATE ON invoices
BEGIN SELECT RAISE(ABORT, 'Posted invoices are immutable'); END;
CREATE TRIGGER IF NOT EXISTS invoice_no_delete BEFORE DELETE ON invoices
BEGIN SELECT RAISE(ABORT, 'Posted invoices are immutable'); END;
CREATE TRIGGER IF NOT EXISTS invoice_lines_no_update BEFORE UPDATE ON invoice_lines
BEGIN SELECT RAISE(ABORT, 'Posted invoice lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS invoice_lines_no_delete BEFORE DELETE ON invoice_lines
BEGIN SELECT RAISE(ABORT, 'Posted invoice lines are immutable'); END;
