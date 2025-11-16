from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
from flask_bcrypt import Bcrypt
from io import BytesIO
from reportlab.lib.pagesizes import LETTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

import psycopg2
import uuid
from datetime import datetime
import csv
import os
import io
import time
import requests
import json

app = Flask(__name__)
bcrypt = Bcrypt(app)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
HUGGINGFACE_MODEL = os.getenv(
    "HUGGINGFACE_MODEL",
    "michellejieli/emotion_text_classifier"
)
HUGGINGFACE_API_BASE = os.getenv(
    "HUGGINGFACE_API_BASE",
    "https://router.huggingface.co/hf-inference"
)


def fGetConnection():
    """Create a PostgreSQL connection."""
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(conn_str, sslmode="require")


@app.route("/users", methods=["POST"])
def fPostUser():
    data = request.json or {}
    sEmail = data.get("email")
    sPassword = data.get("password")
    if not sEmail or not sPassword:
        return jsonify({"error": "Missing fields"}), 400

    sPasswordHash = bcrypt.generate_password_hash(sPassword).decode("utf-8")
    sId = str(uuid.uuid4())
    dtCreatedAt = datetime.utcnow()

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO users (id, email, passwordhash, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (sId, sEmail, sPasswordHash, dtCreatedAt),
        )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"id": sId}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/login", methods=["POST"])
def fLogin():
    data = request.json or {}
    sEmail = data.get("email")
    sPassword = data.get("password")
    if not sEmail or not sPassword:
        return jsonify({"error": "Missing fields"}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, passwordhash FROM users WHERE email = %s",
            (sEmail,),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "User not found"}), 404

        sUserId, sPasswordHash = row
        if bcrypt.check_password_hash(sPasswordHash, sPassword):
            return jsonify({"id": sUserId}), 200
        return jsonify({"error": "Invalid password"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/events", methods=["POST"])
def create_event():
    data = request.get_json() or {}
    user_id = data.get("user_id")
    timestamp = data.get("timestamp")
    trigger = data.get("trigger")
    compulsion = data.get("compulsion")
    emotion = data.get("emotion")
    notes = data.get("notes", "")

    if not user_id or not timestamp or not trigger:
        return jsonify({"error": "Missing required fields"}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        new_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO events (id, user_id, "trigger", compulsion, emotion, notes, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (new_id, user_id, trigger, compulsion, emotion, notes, timestamp),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"id": str(new_id)}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/analytics", methods=["GET"])
def fGetAnalytics():
    """Return summary analytics for a user."""
    sUserId = request.args.get("user_id")
    if not sUserId:
        return jsonify({"error": "user_id is required"}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT "trigger", COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY "trigger"
            ORDER BY count DESC;
            """,
            (sUserId,),
        )
        trigger_rows = cur.fetchall()
        top_triggers = {row[0]: row[1] for row in trigger_rows}

        cur.execute(
            """
            SELECT DATE(timestamp) AS date, COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY DATE(timestamp)
            ORDER BY date ASC;
            """,
            (sUserId,),
        )
        date_rows = cur.fetchall()
        daily_counts = [
            {"date": row[0].isoformat(), "count": row[1]} for row in date_rows
        ]

        cur.execute(
            """
            SELECT ai_emotion, COUNT(*) AS c
            FROM events
            WHERE user_id = %s AND ai_emotion IS NOT NULL
            GROUP BY ai_emotion
            ORDER BY c DESC;
            """,
            (sUserId,),
        )
        ai_rows = cur.fetchall()
        ai_emotions = {row[0]: row[1] for row in ai_rows}

        cur.close()
        conn.close()
        return jsonify(
            {
                "topTriggers": top_triggers,
                "dailyCounts": daily_counts,
                "aiEmotions": ai_emotions,
            }
        ), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/export/csv", methods=["GET"])
def fExportCSV():
    """Export events as CSV."""
    sUserId = request.args.get("user_id")
    if not sUserId:
        return jsonify({"error": "user_id is required"}), 400
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, "trigger", compulsion, emotion, notes, timestamp, ai_emotion
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC;
            """,
            (sUserId,),
        )
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(colnames)
        writer.writerows(rows)

        resp = Response(output.getvalue(), mimetype="text/csv")
        resp.headers.set(
            "Content-Disposition", "attachment", filename="events.csv"
        )
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/export/pdf", methods=["GET"])
def fExportPDF():
    """Export events as PDF."""
    sUserId = request.args.get("user_id")
    if not sUserId:
        return jsonify({"error": "user_id is required"}), 400
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, "trigger", compulsion, emotion, notes, timestamp, ai_emotion
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC;
            """,
            (sUserId,),
        )
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500

    try:
        buf = BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=LETTER, title="OCD Events Export"
        )

        data = [colnames]
        for r in rows:
            r = list(r)
            try:
                r[6] = r[6].strftime("%Y-%m-%d %H:%M")
            except Exception:
                r[6] = str(r[6]) if r[6] is not None else ""
            if r[5] and len(str(r[5])) > 200:
                r[5] = str(r[5])[:200] + "..."
            data.append(r)

        table = Table(data, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1ABC9C")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.whitesmoke, colors.HexColor("#F7F9FB")],
                    ),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )

        styles = getSampleStyleSheet()
        title = Paragraph(
            f"<b>OCD Tracker — Events Export</b><br/>User: {sUserId}",
            styles["Title"],
        )

        doc.build([title, table])
        buf.seek(0)
        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"events_{sUserId}.pdf",
        )
    except Exception as e:
        return jsonify({"error": f"PDF error: {e}"}), 500


@app.route("/healthz", methods=["GET"])
def fHealth():
    return jsonify({"status": "ok"}), 200


@app.route("/readyz", methods=["GET"])
def fReady():
    """Database readiness check."""
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        return jsonify({"status": "starting", "reason": str(e)[:160]}), 503


@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Run emotion analysis on notes text and optionally store results.
    """
    data = request.get_json(force=True, silent=True) or {}
    notes = (data.get("notes") or "").strip()
    event_id = data.get("event_id")

    if not notes:
        return jsonify({"error": "notes required"}), 400

    notes = notes[:500]

    base = (HUGGINGFACE_API_BASE or "").rstrip("/")
    model = (HUGGINGFACE_MODEL or "").strip()
    if not base or not model:
        return (
            jsonify(
                {
                    "available": False,
                    "reason": "HuggingFace config missing (API base or model)",
                }
            ),
            502,
        )

    url = f"{base}/models/{model}"
    headers = {}
    if HUGGINGFACE_API_KEY:
        headers["Authorization"] = f"Bearer {HUGGINGFACE_API_KEY}"
    headers["Content-Type"] = "application/json"

    payload = {"inputs": notes}

    max_attempts = 6
    attempt = 0
    out_json = None
    last_err = None

    while attempt < max_attempts:
        attempt += 1
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)

            if r.status_code in (502, 503, 504, 429):
                last_err = f"HF HTTP {r.status_code}: {r.text[:200]}"
                time.sleep(min(1.5 * attempt, 8))
                continue

            if r.status_code >= 400:
                last_err = f"HF HTTP {r.status_code}: {r.text[:200]}"
                break

            out_json = r.json()
            break

        except Exception as e:
            last_err = str(e)
            time.sleep(min(1.5 * attempt, 8))
            continue

    if out_json is None:
        return (
            jsonify(
                {
                    "available": False,
                    "reason": f"hf_unavailable: {last_err}",
                }
            ),
            502,
        )

    if isinstance(out_json, list) and out_json and isinstance(out_json[0], list):
        seq = out_json[0]
    else:
        seq = out_json

    if not isinstance(seq, list) or not seq:
        return (
            jsonify(
                {
                    "available": False,
                    "reason": f"unexpected_response: {out_json}",
                }
            ),
            502,
        )

    scores_sorted = sorted(
        [
            {
                "label": s.get("label"),
                "score": float(s.get("score", 0.0)),
            }
            for s in seq
            if "label" in s
        ],
        key=lambda x: x["score"],
        reverse=True,
    )
    if not scores_sorted:
        return (
            jsonify(
                {
                    "available": False,
                    "reason": "no_scores_from_model",
                }
            ),
            502,
        )

    top = scores_sorted[0]
    resp = {
        "label": top["label"],
        "score": round(top["score"], 4),
        "scores": scores_sorted,
        "model": model,
        "attempts": attempt,
    }

    if event_id:
        try:
            conn = fGetConnection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE events SET ai_emotion = %s, ai_emotion_scores = %s WHERE id = %s",
                (top["label"], json.dumps(scores_sorted), event_id),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            resp["persist_warning"] = f"Could not save ai_emotion: {e}"

    return jsonify(resp), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
