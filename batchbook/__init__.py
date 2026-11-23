import os
import secrets
from decimal import Decimal
from pathlib import Path

import click
from flask import Flask

from . import db
from .numbers import amount_text, qty_text
from .seed import seed_demo


def report_cell(value):
    if value is None:
        return "—"
    return format(value, ",") if isinstance(value, (int, Decimal)) else value


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get("BATCHBOOK_DB", str(Path(app.instance_path, "batchbook.sqlite3"))),
        SECRET_KEY=os.environ.get("BATCHBOOK_SECRET") or secrets.token_hex(32),
        DEMO_MODE=os.environ.get("BATCHBOOK_DEMO") == "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=128 * 1024,
    )
    app.config.update(config or {})
    app.teardown_appcontext(db.close)
    app.jinja_env.filters.update(money=amount_text, qty=qty_text, report_cell=report_cell)
    from . import audit_views, factory_views, sales_views, web

    app.register_blueprint(web.bp)
    app.register_blueprint(sales_views.bp)
    app.register_blueprint(factory_views.bp)
    app.register_blueprint(audit_views.bp)
    app.before_request(web.load_session)
    app.after_request(web.secure_response)
    app.jinja_env.globals["csrf_token"] = web.csrf_token
    app.context_processor(web.template_context)
    app.register_error_handler(db.BusinessError, web.bad_request)
    app.register_error_handler(403, web.forbidden)
    app.register_error_handler(404, web.missing)

    @app.cli.command("seed-demo")
    def seed_command():
        """Create the schema and load the benchmark's opening data."""
        click.echo("Benchmark opening data loaded." if seed_demo() else "Benchmark data already exists.")

    return app
