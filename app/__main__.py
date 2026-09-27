"""Run the website.

    python -m app                 # development server on http://127.0.0.1:8050
    python -m app --prod          # production server (waitress), still one command
"""
import argparse
import os

ap = argparse.ArgumentParser(prog="app")
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8050)))
ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
ap.add_argument("--prod", action="store_true", help="serve with waitress (pip install waitress)")
ap.add_argument("--threads", type=int, default=8)
a = ap.parse_args()

from .wsgi import app  # noqa: E402  (after arg parsing so --help is instant)

print(f"Hoops Lab on http://{a.host}:{a.port}")
if a.prod:
    try:
        from waitress import serve
    except ImportError:
        raise SystemExit("waitress is not installed: pip install waitress (or use gunicorn app.wsgi:app)")
    serve(app, host=a.host, port=a.port, threads=a.threads)
else:
    app.run(host=a.host, port=a.port, debug=False, threaded=True)
