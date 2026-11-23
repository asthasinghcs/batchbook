"""Run the seeded evaluator demo with python -m batchbook."""

import os

from . import create_app
from .seed import seed_demo


def main():
    app = create_app({"DEMO_MODE": True})
    with app.app_context():
        seed_demo()
    app.run(host=os.environ.get("BATCHBOOK_HOST", "127.0.0.1"), port=int(os.environ.get("BATCHBOOK_PORT", 8000)))


if __name__ == "__main__":
    main()
