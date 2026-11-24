"""Connection lifecycle and transaction boundaries."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g

IST = timezone(timedelta(hours=5, minutes=30))


class BusinessError(ValueError):
    """A form can show this validation error directly."""


def today():
    configured = current_app.config.get("BUSINESS_DATE")
    return configured or datetime.now(IST).date()


def db():
    if "db" not in g:
        database = Path(current_app.config["DATABASE"])
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database, isolation_level=None, timeout=8)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        g.db = connection
    return g.db


def close(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def initialize():
    connection = db()
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))


@contextmanager
def atomic(*, write=True):
    connection = db()
    connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def required(sql, args=(), message="That record could not be found."):
    row = db().execute(sql, args).fetchone()
    if row is None:
        raise BusinessError(message)
    return row


def audit(entity, entity_id, action, actor, detail=None):
    db().execute(
        """INSERT INTO audit_events (entity, entity_id, action, actor_id, detail)
           VALUES (?, ?, ?, ?, ?)""",
        (entity, entity_id, action, actor, json.dumps(detail or {}, sort_keys=True)),
    )
