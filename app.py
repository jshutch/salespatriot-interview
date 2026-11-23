"""Flask backend: serves the RFQ browser UI and a JSON API over dibbs.db."""
import sqlite3

from flask import Flask, jsonify, request, send_from_directory

DB_PATH = "dibbs.db"
app = Flask(__name__, static_folder="static")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/dates")
def dates():
    with db() as con:
        rows = con.execute(
            "SELECT post_date, COUNT(*) n FROM rfqs GROUP BY post_date ORDER BY post_date DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/rfqs")
def rfqs():
    date = request.args.get("date")
    with db() as con:
        if date:
            rows = con.execute(
                "SELECT * FROM rfqs WHERE post_date=? ORDER BY solicitation", (date,)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM rfqs ORDER BY solicitation").fetchall()
    return jsonify([dict(r) for r in rows])


if __name__ == "__main__":
    app.run(debug=True, port=5000)
