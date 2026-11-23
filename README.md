# Batchbook

Batchbook is the order-to-audit desk for Kaveri Snack Works, a fictional snack factory in Tamil Nadu. A supervisor records ingredients against a recipe, counts good packets and posts the batch. The same stock ledger backs order approval, dispatch and the month-end reports.

## Run the demo

From the repository root, run `docker compose up --build` and open [localhost:8000](http://localhost:8000). Docker stores the demo data in a named volume so a restart keeps your work.

For a local Python 3.12 setup, run these commands.

```text
python -m venv .venv
# Windows PowerShell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m batchbook
# macOS/Linux: use .venv/bin/python for both commands
```

The launcher loads the benchmark opening balances into a fresh database. To replay the whole benchmark, point `BATCHBOOK_DB` at a different SQLite file before starting the Python launcher. Alternatively, start Compose with a new project name, such as `docker compose -p batchbook-replay up --build`, after stopping the first instance. Both approaches preserve the original data. `flask --app batchbook seed-demo` loads the same seed explicitly.

| Username | Role and starting view |
| --- | --- |
| `sales-a` | Salesman A, mobile orders |
| `sales-b` | Salesman B, mobile orders |
| `admin` | Factory admin, orders and billing |
| `production` | Production supervisor, runs and receipts |
| `auditor` | External auditor, read-only reports |
| `owner` | Owner, operational overview |

All demo users have the password `demo-batchbook`. The local login screen offers shortcuts for each role. The demo contains fictional data and is intended for the local evaluator environment.

## Benchmark walkthrough

Use the [benchmark walkthrough and rehearsal checklist](docs/benchmark.md) to replay the exact scenario. The [design note](docs/design.md) explains the data model and calculations.

| Section 5 check | Result |
| --- | --- |
| Flour, oil and spice after production | 120 kg, 19.5 L and 3.8 kg |
| Pouches and cartons after production | 306 pouches and 21 cartons |
| Finished stock | 238 after production, 88 after dispatch |
| Yield | 188 / 200 packets, −6.0%, flagged |
| Oil consumption | 10.5 / 10 L, +5.0%, flagged |
| Retailer A | ₹7,500 taxable + ₹187.50 CGST + ₹187.50 SGST = ₹7,875 |
| Retailer A collection | ₹4,000 received, ₹3,875 outstanding |
| Distributor B | 120 requested, 88 available, 32 short. Karnataka supply would use IGST. |
| Cost and unit margin | ₹31.14 per good packet, ₹18.86 per packet at the ₹50 pre-tax list price |

Materials cost ₹4,055. That includes ₹1,725 flour at a moving weighted-average rate of ₹43.125/kg, ₹1,470 oil, ₹240 spice, ₹485 pouches (including six damaged) and ₹135 cartons. Add ₹1,800 in directly assigned labour and energy, then divide ₹5,855 by 188 good packets.

## Scope

| Problem-statement tier | Implemented |
| --- | --- |
| Must have | Server-checked roles, product/customer/material/BOM masters, mobile orders, admin approval and stock reservation, GST invoices as PDFs, saved batch preparation and atomic stock posting, yield and material variances, traceable stock ledger, and sales/stock exports |
| Should have | Suppliers and material purchases, payment and customer ageing, run cost and margin, outward GST reporting, variance alerts, owner overview |
| Stretch | Offline queue and retry from an already-open sales form, item-level stock adjustments, full monthly Excel workbook |

Reports cover sales register, sales by customer and product, outward GST, purchases, stock summary and ledger, production and waste, collections, and outstanding balances. Each report has CSV, Excel and PDF output. Sales by customer inside the salesperson workspace counts that salesperson’s dispatched, pre-tax sales. Auditor sales figures use the invoice and credit-note dates.

## Design choices and limits

- Flask with server-rendered Jinja keeps a user action traceable from the form to one Python function. SQLite makes a single-factory demo easy to run and provides transactions for multi-step writes. Openpyxl and ReportLab produce portable, readable exports.
- Posting dates use IST. Seed opening quantities are dated the first day of that month. The problem supplies no value for opening finished stock, so the seed assumes ₹30 per packet for stock valuation. Production transfers material cost into finished stock. The run absorbs labour and energy for management costing.
- The material sheet can stay open across a shift. It saves the quantities and the recipe plan as a draft. Posting the finished count commits material issues, packaging waste and finished stock together. The stock check runs again at posting because other batches can consume the same supplies.
- The benchmark uses the supplied 5% GST rate and a sample HSN, with the customer’s state as place of supply. The PDF uses the fields in Rule 46 as a reference. These are demo documents with illustrative GSTINs and addresses. The GST report is a reconciliation aid for GSTR-1 and GSTR-3B, with no government upload or e-invoicing integration. [CBIC Rule 46](https://taxinformation.cbic.gov.in/content-page/explore-rules/1000136/1000001)
- This prototype handles full dispatches. An unpaid and undispatched invoice can be cancelled through a full credit note. Returns, partial fulfilment, work in progress, batch expiry and Tally integration are outside this release.
- An open mobile order form can capture and retry through a connection drop. The queue stays in that browser and is synced under the original account. Fresh login or reopening the form requires a connection. Email sharing opens a draft, with the downloaded PDF attached by the sender.

## Verify

```text
python -m unittest discover -s tests -v
```

The tests run the benchmark through the business functions and HTTP routes. They check that drafts leave stock alone, recipe edits leave an open batch’s plan intact, a failed posting leaves balances unchanged, and ledger entries link to their source records. They also cover unit conversions, tax, credit notes, order retry, roles and file exports.

## Photograph

The sign-in photo is Keerthiga’s *Murukku or Chakli*, converted to WebP from the Wikimedia preview and displayed cropped. [Source photograph](https://commons.wikimedia.org/wiki/File:Murukku_or_Chakli.jpg) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
