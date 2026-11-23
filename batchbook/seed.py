"""Section 5 opening data. Running the seed twice preserves all transactions."""

from werkzeug.security import generate_password_hash

from .db import BusinessError, atomic, initialize, today
from .inventory import move

DEMO_PASSWORD = "demo-batchbook"

ACCOUNTS = (
    ("sales-a", "Salesman A", "sales"),
    ("sales-b", "Salesman B", "sales"),
    ("admin", "Factory Admin", "admin"),
    ("production", "Production Supervisor", "production"),
    ("auditor", "External Auditor", "auditor"),
    ("owner", "Factory Owner", "owner"),
)


def seed_demo():
    initialize()
    with atomic() as connection:
        state = connection.execute("SELECT username FROM users").fetchall()
        if state:
            if not any(user["username"] == "admin" for user in state):
                raise BusinessError("Use a fresh database for the benchmark seed.")
            return False
        for index, (username, name, role) in enumerate(ACCOUNTS, 1):
            connection.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                (index, username, name, generate_password_hash(DEMO_PASSWORD), role),
            )
        connection.executemany(
            "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (1, "RM-FLOUR", "Flour", "raw", "kg", 0, 0, "", 1),
                (2, "RM-OIL", "Cooking oil", "raw", "L", 0, 0, "", 1),
                (3, "RM-SPICE", "Salt and spice mix", "raw", "kg", 0, 0, "", 1),
                (4, "PK-P200", "200 g pouch", "packaging", "piece", 0, 0, "", 1),
                (5, "PK-CT20", "Carton", "packaging", "piece", 0, 0, "", 1),
                (6, "FG-M200", "Murukku 200 g", "finished", "packet", 5000, 500, "210690", 20),
            ),
        )
        connection.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (1, "Retailer A", "Retailer", "33", "1 Demo Market, Tiruchirappalli, TN 620001",
                 "1 Demo Market, Tiruchirappalli, TN 620001", "", "retailer-a@example.test", 30),
                (2, "Distributor B", "Distributor", "29", "2 Demo Trade Road, Bengaluru, KA 560001",
                 "2 Demo Trade Road, Bengaluru, KA 560001", "29BBBBB0000B1Z0", "distributor-b@example.test", 30),
            ),
        )
        connection.execute("INSERT INTO suppliers VALUES (1, 'Demo Flour Supply', '')")
        connection.execute("INSERT INTO recipes VALUES (6, 200, 300)")
        connection.executemany(
            "INSERT INTO recipe_lines VALUES (6, ?, ?)",
            ((1, 40000), (2, 10000), (3, 1200), (4, 200), (5, 10)),
        )
        opening = today().replace(day=1).isoformat()
        for item_id, amount, value in (
            (1, 100000, 420000), (2, 30000, 420000), (3, 5000, 100000),
            (4, 500, 125000), (5, 30, 45000), (6, 50, 150000),
        ):
            move(item_id, amount, value, "opening", item_id, actor=3, date=opening)
    return True
