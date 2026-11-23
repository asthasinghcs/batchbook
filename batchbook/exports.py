"""Render the same report data for the web, spreadsheet and PDF."""

import csv
import re
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

from flask import send_file
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .numbers import amount_text
from .sales import FACTORY_ADDRESS, FACTORY_GSTIN, FACTORY_NAME, FACTORY_STATE, STATES

INK = colors.HexColor("#173329")
MUTED = colors.HexColor("#526960")
PALE = colors.HexColor("#EDF4EF")


def download(stream, name, mime):
    stream.seek(0)
    return send_file(stream, mimetype=mime, as_attachment=True, download_name=name, max_age=0)


def csv_text(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        if not re.fullmatch(r"[+-][0-9]+\.[0-9]+%", value):
            return "'" + value
    return value


def csv_response(report, name):
    text = StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(report.headers)
    writer.writerows([csv_text(value) for value in row] for row in report.rows)
    return download(BytesIO(text.getvalue().encode("utf-8-sig")), name + ".csv", "text/csv")


def workbook_response(reports, name, period):
    book = Workbook()
    book.remove(book.active)
    for report in reports:
        sheet = book.create_sheet(report.title[:31])
        sheet.append([report.title])
        sheet.append([period])
        sheet.append(report.headers)
        for row in report.rows:
            sheet.append(row)
        for cell in sheet[3]:
            cell.fill = PatternFill("solid", fgColor="173329")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        sheet["A1"].font = Font(color="173329", bold=True, size=15)
        sheet.row_dimensions[3].height = 32
        for column in sheet.iter_cols(min_row=4) if sheet.max_row > 3 else ():
            for cell in column:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        for index, header in enumerate(report.headers, 1):
            lengths = [len(str(row[index - 1] or "")) for row in report.rows]
            sheet.column_dimensions[get_column_letter(index)].width = min(44, max(12, len(header) + 2, *lengths))
        sheet.freeze_panes = "A4"
        sheet.auto_filter.ref = f"A3:{get_column_letter(len(report.headers))}{sheet.max_row}"
        sheet.sheet_view.showGridLines = False
    stream = BytesIO()
    book.save(stream)
    return download(stream, name + ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def paragraph(text, style):
    return Paragraph(escape("" if text is None else str(text)).replace("\n", "<br/>"), style)


def document(stream, pagesize):
    return SimpleDocTemplate(stream, pagesize=pagesize, leftMargin=35, rightMargin=35, topMargin=36, bottomMargin=42)


def styles():
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("Document title", parent=sample["Heading1"], textColor=INK, fontSize=24, leading=29),
        "body": ParagraphStyle("Document copy", parent=sample["Normal"], textColor=INK, fontSize=9, leading=12),
        "meta": ParagraphStyle("Document details", parent=sample["Normal"], textColor=MUTED, fontSize=9, leading=13),
        "header": ParagraphStyle("Table heading", parent=sample["Normal"], textColor=colors.white,
                                 fontName="Helvetica-Bold", fontSize=8, leading=10),
        "cell": ParagraphStyle("Table cell", parent=sample["Normal"], textColor=INK, fontSize=8, leading=10),
        "total": ParagraphStyle("Invoice total", parent=sample["Normal"], textColor=INK, alignment=TA_RIGHT,
                                fontName="Helvetica-Bold", fontSize=11, leading=15),
    }


def table(headers, rows, widths, style):
    content = [[paragraph(value, style["header"]) for value in headers]]
    content.extend([paragraph(value, style["cell"]) for value in row] for row in rows)
    result = LongTable(content, colWidths=widths, repeatRows=1, hAlign="LEFT")
    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return result


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 24, "Batchbook   /   Kaveri Snack Works")
    canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 24, f"Page {doc.page}")
    canvas.restoreState()


def report_pdf(report, period):
    stream = BytesIO()
    doc = document(stream, landscape(A4))
    style = styles()
    body = [paragraph(report.title, style["title"]), paragraph(period, style["meta"]), Spacer(1, 14)]
    if report.rows:
        weights = [
            min(24, max(8, len(header), max(len(str(row[index] or "")) for row in report.rows)))
            for index, header in enumerate(report.headers)
        ]
        widths = [doc.width * weight / sum(weights) for weight in weights]
        body.append(table(report.headers, report.rows, widths, style))
    else:
        body.append(paragraph("No posted records for this period.", style["body"]))
    doc.build(body, onFirstPage=footer, onLaterPages=footer)
    return stream


def invoice_document(invoice, lines, credit=None):
    stream = BytesIO()
    doc = document(stream, A4)
    style = styles()
    place = invoice["place_of_supply"]
    name = "Credit note" if credit else "Tax invoice"
    record = credit or invoice
    sign = -1 if credit else 1
    address_blocks = [
        paragraph(f"SUPPLIER\n{FACTORY_NAME}\n{FACTORY_ADDRESS}\nGSTIN {FACTORY_GSTIN} · State {FACTORY_STATE}", style["body"]),
        paragraph(f"BILL TO\n{invoice['customer_name']}\n{invoice['billing_address']}\n"
                  f"GSTIN {invoice['gstin'] or 'Unregistered'}", style["body"]),
    ]
    addresses = Table([address_blocks], colWidths=[doc.width / 2] * 2)
    addresses.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
    ]))
    details = (
        f"Original invoice {invoice['number']} ({invoice['on_date']}) · Reason: {credit['reason']}"
        if credit else f"Payment due {invoice['due_date']}"
    )
    body = [
        paragraph("DEMO DOCUMENT  /  VALUES IN INR", style["meta"]),
        paragraph(name, style["title"]),
        paragraph(f"{record['number']}  ·  Issued {record['on_date']}", style["body"]),
        paragraph(details, style["meta"]), Spacer(1, 14), addresses, Spacer(1, 10),
        paragraph(f"Ship to: {invoice['delivery_address']}\nPlace of supply: {STATES[place]} ({place})\n"
                  "Reverse charge: No  ·  Unit: packet (PAC)", style["meta"]), Spacer(1, 12),
    ]
    columns = ("Product", "HSN", "Qty", "Rate", "GST rate", "Taxable", "CGST", "SGST", "IGST")
    weights = (25, 9, 6, 10, 9, 13, 10, 10, 10)
    data = [
        (line["description"], line["hsn"], line["packets"] * sign, amount_text(line["price_paise"]),
         f"{amount_text(line['gst_bp'])}%", *(amount_text(line[field] * sign)
                                            for field in ("taxable", "cgst", "sgst", "igst")))
        for line in lines
    ]
    body.append(table(columns, data, [doc.width * weight / sum(weights) for weight in weights], style))
    body.extend([Spacer(1, 14), paragraph(
        f"Taxable {amount_text(invoice['taxable'] * sign)}   ·   CGST {amount_text(invoice['cgst'] * sign)}   ·   "
        f"SGST {amount_text(invoice['sgst'] * sign)}   ·   IGST {amount_text(invoice['igst'] * sign)}", style["meta"]),
        paragraph(f"Total  INR {amount_text(invoice['total'] * sign)}", style["total"]),
        Spacer(1, 32), paragraph("Authorised signatory __________________________________", style["meta"]),
    ])
    doc.build(body, onFirstPage=footer, onLaterPages=footer)
    return stream


def pdf_response(stream, name):
    return download(stream, name + ".pdf", "application/pdf")
