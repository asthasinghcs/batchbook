from flask import Blueprint, abort, g, render_template, request

from .db import db, today
from .reports import REPORTS, between
from .web import AUDIT_ROLES, STOCK_ROLES, access

bp = Blueprint("audit", __name__)


@bp.get("/reports")
@access(*STOCK_ROLES)
def reports():
    day = today()
    start, end = between(request.args.get("start", day.replace(day=1).isoformat()),
                         request.args.get("end", day.isoformat()))
    role = g.user["role"]
    choices = REPORTS if role in AUDIT_ROLES else {key: REPORTS[key] for key in ("stock", "ledger", "production")}
    key = request.args.get("report", next(iter(choices)))
    output = request.args.get("format")
    item_id = request.args.get("item_id", type=int) if key in {"stock", "ledger"} else None
    period = f"As of {end}" if key == "outstanding" else f"{start} to {end}"
    if key == "all":
        if role not in AUDIT_ROLES or output != "xlsx":
            abort(403)
        from .exports import workbook_response

        return workbook_response(
            [build(start, end, None) for _, build in choices.values()],
            f"batchbook-monthly-{start}_{end}", period,
        )
    if key not in choices:
        abort(403)
    report = choices[key][1](start, end, item_id)
    if output:
        from .exports import csv_response, pdf_response, report_pdf, workbook_response

        name = f"batchbook-{key}-{start}_{end}"
        if output == "csv":
            return csv_response(report, name)
        if output == "xlsx":
            return workbook_response([report], name, period)
        if output == "pdf":
            return pdf_response(report_pdf(report, period), name)
        abort(404)
    items = db().execute("SELECT id, name FROM items ORDER BY kind, name").fetchall() if key in {"stock", "ledger"} else ()
    return render_template("reports.html", report=report, key=key, choices=choices, start=start, end=end,
                           items=items, item_id=item_id, period=period, full=role in AUDIT_ROLES)
