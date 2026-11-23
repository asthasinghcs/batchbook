# Section 5 rehearsal

Start with a fresh database. Use one browser profile per role, or sign out between steps. The given opening balances are loaded automatically.

1. As **Factory Admin**, open **Purchases**. Choose Demo Flour Supply and Flour, enter 60 kg at ₹45 per kg and use receipt reference `FLOUR-60`.
2. As **Production Supervisor**, open **Production → New production run**. Choose Murukku 200 g and one batch. Enter 40 kg flour, 10.5 L oil and 1.2 kg spice mix. Put those amounts in **Used**.
3. Enter 188 good packets. Enter 188 used and 6 wasted for the 200 g pouch, and 9 used cartons. Post. Show the −6% yield and +5% oil flags. Open **Stock** to show 120 kg flour, 19.5 L oil, 306 pouches and 238 finished packets.
4. At phone width as **Salesman A**, open **New order**. Select Retailer A and order 150 Murukku packets.
5. As **Factory Admin**, review the order, approve and reserve it, and raise the invoice. Show the ₹7,875 PDF total with CGST and SGST of ₹187.50 each. Record dispatch. Show 88 finished packets in **Stock**.
6. At phone width as **Salesman B**, submit 120 packets for Distributor B. As admin, open the order and show the 32-packet shortage and the IGST indicator. The order stays submitted and can be revisited after replenishment.
7. On Retailer A’s invoice, record ₹4,000 paid by UPI with reference `RETAIL-A-01`. Show ₹3,875 outstanding.
8. In the production run, save ₹1,200 labour and ₹600 gas and power. Show the ₹5,855 total, ₹31.14 cost per good packet and ₹18.86 margin.
9. As **External Auditor**, open **Reports**. Use the current month and show the sales register, GST, purchases, stock and ledger, production variance, collections and ageing. Open the ten-sheet Excel export and show the outstanding balance.

## Practice for the interview

- Explain why approval reserves stock and dispatch issues stock. The same 150 packets cannot be promised to a second customer.
- Trace a purchase into `stock_moves`, then the flour issue into `run_lines`. Show how the posting function commits the rows together.
- Work the flour valuation on paper. `(100 × 42 + 60 × 45) ÷ 160 = ₹43.125/kg`. That rate times 40 kg is ₹1,725.
- Trace the Karnataka order. It stays submitted with 88 available, so an IGST invoice is never posted in the benchmark.
- Rehearse adding a 500 g pack. Create a finished item and configure its yield and ingredient quantities under **Master data → Recipes (BOM)**. The original pack remains a separate stock item. Discuss how you would share a formula across pack sizes if asked to extend the schema.

The problem statement permits AI coding assistants. Be ready to explain the behavior, run the benchmark independently and make a small change live.

## Read the code in this order

1. Start with [the schema](../batchbook/schema.sql). Find `items`, `stock_moves`, `runs`, `run_lines` and `invoices`.
2. Read `move()` and `run()` in [inventory.py](../batchbook/inventory.py). Point to the lines that issue flour and receive finished packets within one transaction.
3. Read `review()`, `invoice()` and `dispatch()` in [sales.py](../batchbook/sales.py). Follow order SO-0001 across the three steps.
4. Open [test_workflow.py](../tests/test_workflow.py) and read the assertions under `test_section_five_end_to_end`. Each assertion checks the same result the reviewer will see in the video.
