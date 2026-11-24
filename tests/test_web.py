import re
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from openpyxl import load_workbook

from batchbook import create_app
from batchbook.db import db
from batchbook.seed import DEMO_PASSWORD, seed_demo


class WebWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.app = create_app({"TESTING": True, "DATABASE": Path(temp.name) / "web.sqlite3",
                              "BUSINESS_DATE": date(2026, 11, 23), "DEMO_MODE": True})
        with cls.app.app_context():
            seed_demo()

    def session(self, username):
        client = self.app.test_client()
        page = client.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Batchbook", page.data)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', page.data).group(1).decode()
        response = client.post("/login", data={"username": username, "password": DEMO_PASSWORD, "csrf": csrf})
        self.assertEqual(response.status_code, 303)
        client.get(response.location, follow_redirects=True)
        return client

    @staticmethod
    def post(client, path, payload):
        with client.session_transaction() as session:
            csrf = session.get("csrf")
        return client.post(path, data=dict(payload, csrf=csrf), follow_redirects=True)

    def test_benchmark_through_every_role(self):
        admin = self.session("admin")
        production = self.session("production")
        sales_a = self.session("sales-a")
        sales_b = self.session("sales-b")
        auditor = self.session("auditor")
        owner = self.session("owner")
        receipt = self.post(admin, "/purchases", {"supplier_id": 1, "item_id": 1, "reference": "NOV-60",
                                                  "amount": 60, "unit": "kg", "rate": 45})
        self.assertIn(b"Material stock updated", receipt.data)
        self.assertIn(b"Flour used", production.get("/production/new").data)
        self.assertEqual(production.get("/static/murukku.webp", buffered=True).status_code, 200)
        prep = {"product_id": 6, "batches": 1, "client_ref": str(uuid4())}
        for item, (used, unit) in {1: (40000, "g"), 2: (10.5, "L"), 3: (1.2, "kg")}.items():
            prep.update({f"used_{item}": used, f"unit_{item}": unit})
        draft = self.post(production, "/production/new", prep)
        self.assertEqual(draft.request.path, "/production/drafts/1")
        self.assertIn(b"Good packets", draft.data)
        self.assertIn(b"Awaiting yield", production.get("/production").data)
        pack = {"good_packets": 188, "used_4": 188, "waste_4": 6, "unit_4": "piece", "used_5": 9, "unit_5": "piece"}
        run = self.post(production, "/production/drafts/1", pack)
        self.assertIn(b"-6.0%", run.data)
        self.assertIn(b"+5.0%", run.data)
        self.assertIn(b"238", admin.get("/stock").data)
        duplicate = self.post(production, "/production/new", prep)
        self.assertIn(b"RUN-0001", duplicate.data)
        flour_history = production.get("/stock/1").data
        self.assertIn(b'href="/purchases/1"', flour_history)
        self.assertIn(b'href="/production/1"', flour_history)
        self.assertIn(b"120", flour_history)
        order_a = self.post(sales_a, "/orders/new", {"customer_id": 1, "product_id": 6, "count": 150,
                                                   "unit": "packet", "client_ref": str(uuid4())})
        self.assertIn(b"SO-0001", order_a.data)
        self.assertEqual(sales_b.get("/orders/1").status_code, 404)
        self.assertIn(b"stock reserved", self.post(admin, "/orders/1/review", {
            "decision": "approve", "qty_6": 150, "price_6": 50,
        }).data)
        bill = self.post(admin, "/orders/1/invoice", {})
        self.assertIn(b"7,875.00", bill.data)
        pdf = admin.get("/invoices/1/document.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertIn(b"Finished stock updated", self.post(admin, "/invoices/1/dispatch", {}).data)
        self.assertIn(b'href="/invoices/1"', admin.get("/stock/6").data)
        sync = sales_b.get("/orders/sync-token").json
        queued = {"account": sync["account"], "customer_id": "2", "lines": [["6", "120", "packet"]],
                  "client_ref": str(uuid4())}
        for _ in range(2):
            accepted = sales_b.post("/orders/sync", json=queued, headers={"X-CSRF-Token": sync["csrf"]})
            self.assertEqual(accepted.json["order"], 2)
        self.assertIn(b"32-packet shortage", admin.get("/orders/2").data)
        self.assertIn(b"IGST if invoiced", admin.get("/orders/2").data)
        plan = production.get("/production/plan")
        self.assertEqual(plan.status_code, 200)
        self.assertIn(b"1 batch to make", plan.data)
        self.assertIn(b"40 kg", plan.data)
        self.assertIn(b"/production/new?product=6&amp;batches=1", plan.data)
        self.assertEqual(sales_a.get("/production/plan").status_code, 403)
        paid = self.post(admin, "/invoices/1/payment", {"amount": 4000, "mode": "UPI", "reference": "R-A1"})
        self.assertIn(b"3,875.00", paid.data)
        costing = self.post(production, "/production/1/cost", {"labour": 1200, "energy": 600})
        self.assertIn(b"31.14", costing.data)
        self.assertIn(b"31.14", owner.get("/").data)
        self.assertEqual(self.post(auditor, "/invoices/1/dispatch", {}).status_code, 403)
        self.assertEqual(admin.post("/invoices/1/dispatch").status_code, 403)
        self.assertEqual(sales_a.get("/reports").status_code, 403)
        self.assertEqual(production.get("/reports?report=gst").status_code, 403)

        for key in ["sales", "customer-sales", "product-sales", "gst", "purchases", "stock",
                    "ledger", "production", "collections", "outstanding"]:
            response = auditor.get("/reports", query_string={"report": key})
            self.assertEqual(response.status_code, 200, key)
            self.assertNotIn(b"No posted records", response.data, key)
        workbook = auditor.get("/reports?report=all&format=xlsx")
        book = load_workbook(BytesIO(workbook.data), read_only=True)
        self.assertEqual(len(book.sheetnames), 10)
        outstanding = list(book["Outstanding and ageing"].values)
        self.assertEqual(outstanding[1][0], "As of 2026-11-23")
        self.assertEqual(outstanding[3][-1], 3875)
        book.close()
        stock_csv = auditor.get("/reports?report=stock&format=csv")
        self.assertIn(b"Closing", stock_csv.data)
        self.assertIn(b"88,", stock_csv.data)
        self.assertTrue(auditor.get("/reports?report=production&format=pdf").data.startswith(b"%PDF"))
        for section in ("items", "customers", "suppliers", "recipes"):
            self.assertEqual(admin.get(f"/masters/{section}").status_code, 200)
        with self.app.app_context():
            self.assertEqual(db().execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(db().execute("SELECT COUNT(*) FROM sales_orders").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
