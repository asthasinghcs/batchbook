import os
import secrets
from pathlib import Path

import click
from flask import Flask

from . import db
from .numbers import amount_text, qty_text
from .seed import seed_demo


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
    app.jinja_env.filters.update(money=amount_text, qty=qty_text)

    @app.cli.command("seed-demo")
    def seed_command():
        """Create the schema and load the benchmark's opening data."""
        click.echo("Benchmark opening data loaded." if seed_demo() else "Benchmark data already exists.")

    return app
