"""Shared views, authentication and role checks."""

import hmac
import secrets
from functools import wraps

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from . import inventory
from .db import BusinessError, db, today
from .numbers import variance
from .sales import STATES
from .seed import ACCOUNTS, DEMO_PASSWORD

bp = Blueprint("web", __name__)
ROLE_LABELS = {
    "sales": "Field Sales", "admin": "Factory Admin", "production": "Production",
    "auditor": "Auditor", "owner": "Owner",
}
STOCK_ROLES = ("admin", "production", "auditor", "owner")
AUDIT_ROLES = ("admin", "auditor", "owner")
DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def load_session():
    user_id = session.get("user_id")
    g.user = db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None
    if request.method == "POST":
        token = request.form.get("csrf", "") or request.headers.get("X-CSRF-Token", "")
        server_token = session.get("csrf")
        if not server_token or not hmac.compare_digest(token.encode(), server_token.encode()):
            abort(403, "This form expired. Reload the page and try again.")


def secure_response(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "no-store"
    return response


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def template_context():
    return {
        "role_labels": ROLE_LABELS, "states": STATES, "business_date": today(),
        "demo_mode": current_app.config["DEMO_MODE"],
    }


def access(*roles):
    def decorator(view):
        @wraps(view)
        def protected(*args, **kwargs):
            if not g.user:
                if request.is_json:
                    return {"error": "Sign in as the salesperson who saved this order."}, 401
                return redirect(url_for("web.login"))
            if roles and g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return protected
    return decorator


def bad_request(error):
    return render_template("error.html", title="Check the request", message=str(error)), 400


def forbidden(error):
    if request.is_json:
        return {"error": error.description}, 403
    return render_template("error.html", title="Access restricted", message=error.description), 403


def missing(_error):
    return render_template("error.html", title="Page unavailable", message="Check the address or return to the workspace."), 404


def action(destination, message, service, *args):
    try:
        service(*args)
        flash(message, "success")
    except BusinessError as error:
        flash(str(error), "error")
    return redirect(destination, code=303)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("web.home"))
    if request.method == "POST":
        login = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = db().execute("SELECT * FROM users WHERE username = ?", (login,)).fetchone()
        checksum = user["password_hash"] if user else DUMMY_HASH
        if len(password) <= 200 and check_password_hash(checksum, password) and user:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("web.home"), code=303)
        flash("Check your username and password.", "error")
    accounts = ACCOUNTS if current_app.config["DEMO_MODE"] else ()
    return render_template("login.html", accounts=accounts, demo_password=DEMO_PASSWORD)


@bp.post("/logout")
@access()
def logout():
    session.clear()
    return redirect(url_for("web.login"), code=303)


@bp.get("/")
@access()
def home():
    role = g.user["role"]
    if role == "sales":
        return redirect(url_for("sales.orders"))
    if role == "production":
        return redirect(url_for("factory.runs"))
    if role == "auditor":
        return redirect(url_for("audit.reports"))
    connection = db()
    date = today().isoformat()
    work = connection.execute(
        """SELECT o.*, c.name AS customer, u.display_name AS salesman,
                  (SELECT SUM(l.packets) FROM order_lines l WHERE l.order_id = o.id) AS packets
           FROM sales_orders o JOIN customers c ON c.id = o.customer_id JOIN users u ON u.id = o.salesman_id
           WHERE (? <> 'admin' OR o.status IN ('submitted', 'approved', 'invoiced'))
           ORDER BY o.id DESC LIMIT 6""",
        (role,),
    ).fetchall()
    finished = []
    for product in connection.execute("SELECT * FROM items WHERE kind = 'finished' ORDER BY name"):
        on_hand = inventory.balance(product["id"])[0]
        finished.append(dict(product, on_hand=on_hand, available=on_hand - inventory.reserved(product["id"])))
    due = connection.execute(
        """SELECT COALESCE(SUM(i.total - COALESCE((
               SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id = i.id), 0)), 0)
           FROM invoices i WHERE NOT EXISTS (SELECT 1 FROM credit_notes n WHERE n.invoice_id = i.id)"""
    ).fetchone()[0]
    today_count = connection.execute("SELECT COUNT(*) FROM sales_orders WHERE on_date = ?", (date,)).fetchone()[0]
    pending = connection.execute("SELECT COUNT(*) FROM sales_orders WHERE status = 'submitted'").fetchone()[0]
    to_dispatch = connection.execute("SELECT COUNT(*) FROM sales_orders WHERE status = 'invoiced'").fetchone()[0]
    runs = []
    for run in connection.execute(
        """SELECT r.*, i.name AS product, i.price_paise FROM runs r JOIN items i ON i.id = r.product_id
           ORDER BY r.id DESC LIMIT 6"""
    ):
        yield_pct, alert = variance(run["actual_packets"], run["planned_packets"], run["tolerance_bp"])
        issues = connection.execute("SELECT * FROM run_lines WHERE run_id = ?", (run["id"],)).fetchall()
        flagged = sum(variance(line["used"] + line["wasted"], line["planned"], run["tolerance_bp"])[1] for line in issues)
        runs.append(dict(run, yield_pct=yield_pct, alerts=flagged + int(alert), cost=inventory.packet_cost(run)))
    return render_template("home.html", orders=work, finished=finished, due=due, today_count=today_count,
                           pending=pending, to_dispatch=to_dispatch, runs=runs)
