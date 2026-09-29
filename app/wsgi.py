"""WSGI entry point for production servers.

    gunicorn -w 2 --threads 4 -b 0.0.0.0:8050 app.wsgi:app      (Linux / Docker)
    waitress-serve --port=8050 app.wsgi:app                     (Windows)
"""
import logging
import threading

from .server import _records, app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _warm():
    """Precompute the slowest page (all-time records) so the first visitor doesn't wait.
    Skipped when the database already carries precomputed results."""
    from .server import precomputed
    if precomputed("records:all:Regular Season") is not None:
        return
    try:
        for stype in ("Regular Season", "Playoffs"):
            _records("all", stype)
    except Exception:  # noqa: BLE001 - warming is best effort
        logging.getLogger("hoopslab").exception("cache warm-up failed")


threading.Thread(target=_warm, daemon=True).start()

__all__ = ["app"]
