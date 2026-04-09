"""Run the IG phone poster Flask app: python -m ig_poster."""

from ig_poster.server import PORT, app, init_db

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT)
