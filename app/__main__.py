import argparse
import threading

from .server import app

ap = argparse.ArgumentParser(prog="app")
ap.add_argument("--port", type=int, default=8050)
ap.add_argument("--host", default="127.0.0.1")
a = ap.parse_args()


def warm():
    """Precompute the slowest pages (all-time records) so the first visit is quick."""
    from .server import _records
    _records("all", "Regular Season")
    _records("all", "Playoffs")


threading.Thread(target=warm, daemon=True).start()
print(f"NBA stats site on http://{a.host}:{a.port}")
app.run(host=a.host, port=a.port, debug=False, threaded=True)
