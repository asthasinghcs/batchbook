import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from batchbook import create_app, inventory, sales
from batchbook.db import BusinessError, db
from batchbook.numbers import quantity, variance
from batchbook.seed import seed_demo

ISSUES = {
    1: ("40", "0", "kg"),
    2: ("10.5", "0", "L"),
    3: ("1.2", "0", "kg"),
    4: ("188", "6", "piece"),
    5: ("9", "0", "piece"),
}


class BusinessWorkflow(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        app = create_app({"DATABASE": Path(temp.name) / "test.sqlite3", "BUSINESS_DATE": date(2026, 11, 23)})
        context = app.app_context()
        context.push()
        self.addCleanup(context.pop)
        seed_demo()

    def test_section_five_end_to_end(self):
        inventory.purchase(3, 1, 1, "FLOUR-60", "60", "kg", "45")
        ref = str(uuid4())
        draft = inventory.start_run(4, 6, "1", ISSUES, ref)
        self.assertEqual(inventory.start_run(4, 6, "1", ISSUES, ref), draft)
        self.assertEqual(inventory.balance(1)[0], 160_000)
        self.assertEqual(inventory.balance(6)[0], 50)
        run = inventory.post_run(4, draft, "188", ISSUES, "")
        self.assertEqual(inventory.post_run(4, draft, "188", ISSUES, ""), run)
        self.assertEqual([inventory.balance(i)[0] for i in range(1, 7)],
                         [120_000, 19_500, 3_800, 306, 21, 238])
        row = db().execute("SELECT * FROM runs WHERE id = ?", (run,)).fetchone()
        self.assertEqual(variance(row["actual_packets"], row["planned_packets"], row["tolerance_bp"]),
                         ("-6.0%", True))
        self.assertEqual(variance(10_500, 10_000, 300), ("+5.0%", True))
        reference = uuid4()
        order = sales.submit(1, 1, [(6, "150", "packet")], reference)
        self.assertEqual(sales.submit(1, 1, [(6, "150", "packet")], reference), order)
        sales.review(3, order, "approve", {6: ("150", "50")}, "")
        self.assertEqual(sales.availability(6), (238, 88))
        invoice = sales.invoice(3, order)
        row = sales.invoice_for(invoice)
        self.assertEqual([row[key] for key in ("taxable", "cgst", "sgst", "igst", "total")],
                         [750_000, 18_750, 18_750, 0, 787_500])
        self.assertEqual(row["number"], "INV/26-27/00001")
        dispatch = sales.dispatch(3, invoice)
        self.assertEqual(sales.dispatch(3, invoice), dispatch)
        self.assertEqual(inventory.balance(6)[0], 88)
        order_b = sales.submit(2, 2, [(6, "6", "carton")], uuid4())
        with self.assertRaisesRegex(BusinessError, "short by 32 packets"):
            sales.review(3, order_b, "approve", {6: ("120", "50")}, "")
        self.assertEqual(sales.order_for(order_b)["status"], "submitted")
        self.assertEqual(sales.line_tax(600_000, 500, "29"), (0, 0, 30_000))
        payment = sales.payment(3, invoice, "4000", "UPI", "R-1")
        self.assertEqual(sales.payment(3, invoice, "4000", "UPI", "R-1"), payment)
        with self.assertRaisesRegex(BusinessError, "exceeds"):
            sales.payment(3, invoice, "4000", "UPI", "R-2")
        self.assertEqual(sales.invoice_for(invoice)["total"] - sales.invoice_for(invoice)["paid"], 387_500)
        inventory.update_run_cost(4, run, "1200", "600", "")
        row = db().execute("SELECT * FROM runs WHERE id = ?", (run,)).fetchone()
        self.assertEqual(row["material_paise"], 405_500)
        self.assertEqual(inventory.packet_cost(row), 3114)
        self.assertFalse(seed_demo())

    def test_run_failure_keeps_all_balances_and_records_unchanged(self):
        draft = inventory.start_run(4, 6, "1", ISSUES, str(uuid4()))
        bad = dict(ISSUES)
        bad[5] = ("31", "0", "piece")
        with self.assertRaisesRegex(BusinessError, "has only 30"):
            inventory.post_run(4, draft, "188", bad, "")
        self.assertEqual(inventory.balance(6)[0], 50)
        self.assertEqual(inventory.balance(1)[0], 100_000)
        self.assertEqual(db().execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
        self.assertIsNone(inventory.draft_for(draft)["run_id"])

    def test_draft_uses_saved_recipe_when_the_master_changes(self):
        from batchbook.masters import save_recipe

        draft = inventory.start_run(4, 6, "1", ISSUES, str(uuid4()))
        save_recipe(3, 6, "250", "3", {"item_1": "80"})
        revised = {**ISSUES, 1: ("39", "1", "kg")}
        inventory.revise_materials(4, draft, revised)
        run = inventory.post_run(4, draft, "188", ISSUES, "")
        values = db().execute("SELECT planned, used, wasted FROM run_lines WHERE run_id = ? AND item_id = 1", (run,)).fetchone()
        self.assertEqual(tuple(values), (40_000, 39_000, 1_000))
        self.assertEqual(inventory.draft_for(draft)["planned_packets"], 200)
        self.assertIsNotNone(db().execute("SELECT id FROM audit_events WHERE action = 'materials_revised'").fetchone())

    def test_credit_note_retains_invoice_and_releases_reservation(self):
        order = sales.submit(2, 2, [(6, "2", "carton")], uuid4())
        sales.review(3, order, "approve", {6: ("40", "50")}, "")
        invoice = sales.invoice(3, order)
        self.assertEqual(sales.availability(6), (50, 10))
        self.assertEqual(sales.invoice_for(invoice)["igst"], 10_000)
        credit = sales.credit(3, invoice, "Duplicate customer request")
        self.assertEqual(sales.credit(3, invoice, "Duplicate customer request"), credit)
        self.assertEqual(sales.availability(6), (50, 50))
        self.assertEqual(sales.invoice_for(invoice)["total"], 210_000)
        with self.assertRaisesRegex(BusinessError, "open invoice"):
            sales.dispatch(3, invoice)

    def test_unit_dimensions_and_precision(self):
        flour = inventory.item_for(1)
        self.assertEqual(quantity("1.2", "kg", flour), 1200)
        self.assertEqual(quantity("1200", "g", flour), 1200)
        with self.assertRaises(BusinessError):
            quantity("1", "L", flour)
        with self.assertRaises(BusinessError):
            quantity("0.0001", "kg", flour)
        with self.assertRaises(BusinessError):
            quantity("1.5", "carton", inventory.item_for(6))


if __name__ == "__main__":
    unittest.main()
