import argparse

from .server import app

ap = argparse.ArgumentParser(prog="app")
ap.add_argument("--port", type=int, default=8050)
ap.add_argument("--host", default="127.0.0.1")
a = ap.parse_args()
print(f"NBA stats site on http://{a.host}:{a.port}")
app.run(host=a.host, port=a.port, debug=False, threaded=True)
