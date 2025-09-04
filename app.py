import os
import io
import csv
import time
import uuid
import requests
import psycopg2
from datetime import datetime
from io import BytesIO

from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from flask_bcrypt import Bcrypt

# PDF bits
from reportlab.lib.pagesizes import LETTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)
bcrypt = Bcrypt(app)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ------------------------------------------------------------------------------
# Hugging Face Inference API (no local model = no OOM on Render free tier)
# ------------------------------------------------------------------------------
HF_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "")
HF_MODEL = os.getenv(
    "HUGGINGFACE_MODEL",
    "distilbert/distilbert-base-uncased-finetuned-sst-2-english"
)
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
HF_TIMEOUT = 12  # seconds

# --- Database connection setup ---
def fGetConnection():
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise Exception("DATABASE_URL not set")
    # Render’s PostgreSQL usually includes sslmode=require already.
    return psycopg2.connect(conn_str)

# --- Health/version (helpful for verifying the right build) ---
@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/version")
def version():
    return jsonify({
        "mode": "hf-inference",
        "imports": "no-transformers",
        "model": HF_MODEL
    }), 200

# --- Route to create a new user ---
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

# --- Route to log in a user ---
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
        cur.execute("SELECT id, passwordhash FROM users WHERE email = %s", (sEmail,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "User not found"}), 404

        sUserId, sPasswordHash = row
        if bcrypt.check_password_hash(sPasswordHash, sPassword):
            return jsonify({"id": sUserId}), 200
        else:
            return jsonify({"error": "Invalid password"}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Create event ---
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
        cur.execute(
            """
            INSERT INTO events (id, user_id, trigger, compulsion, emotion, notes, timestamp)
            VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (user_id, trigger, compulsion, emotion, notes, timestamp),
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({"id": str(new_id)}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Analytics (per user) ---
@app.route("/analytics", methods=["GET"])
def fGetAnalytics():
    sUserId = request.args.get("user_id")
    if not sUserId:
        return jsonify({"error": "user_id is required"}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT trigger, COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY trigger
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
        daily_counts = [{"date": row[0].isoformat(), "count": row[1]} for row in date_rows]

        cur.close()
        conn.close()

        return jsonify({"topTriggers": top_triggers, "dailyCounts": daily_counts}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Export CSV ---
@app.route("/export/csv", methods=["GET"])
def fExportCSV():
    sUserId = request.args.get("user_id")
    if not sUserId:
        return jsonify({"error": "user_id is required"}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.email, e.trigger, e.compulsion, e.emotion, e.notes
            FROM events e
            JOIN users u ON e.user_id = u.id
            WHERE e.user_id = %s
            ORDER BY e.timestamp ASC;
            """,
            (sUserId,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()

        colnames = ["Email", "Trigger", "Compulsion", "Emotion", "Notes"]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(colnames)
        writer.writerows(rows)

        response = Response(output.getvalue(), mimetype="text/csv")
        response.headers.set(
            "Content-Disposition", "attachment", filename=f"events_{sUserId}.csv"
        )
        return response
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Export PDF ---
@app.route("/export/pdf", methods=["GET"])
def fExportPDF():
    sUserId = request.args.get("user_id")
    if not sUserId:
        return jsonify({"error": "user_id is required"}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT u.email, e.trigger, e.compulsion, e.emotion, e.notes
            FROM events e
            JOIN users u ON e.user_id = u.id
            WHERE e.user_id = %s
            ORDER BY e.timestamp ASC;
            """,
            (sUserId,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500

    try:
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=LETTER, title="OCD Events Export")
        styles = getSampleStyleSheet()

        wrapped_rows = []
        for email, trigger, compulsion, emotion, notes in rows:
            notes_para = Paragraph(notes if notes else "", styles["Normal"])
            wrapped_rows.append([email, trigger, compulsion, emotion, notes_para])

        colnames = ["Email", "Trigger", "Compulsion", "Emotion", "Notes"]
        data = [colnames] + wrapped_rows

        table = Table(data, colWidths=[120, 80, 100, 80, 200], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1ABC9C")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.HexColor("#F7F9FB")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )

        title = Paragraph(
            f"<b>OCD Tracker — Events Export</b><br/>User ID: {sUserId}", styles["Title"]
        )
        doc.build([title, table])
        buf.seek(0)
        return send_file(
            buf, mimetype="application/pdf", as_attachment=True, download_name="events.pdf"
        )
    except Exception as e:
        return jsonify({"error": f"PDF error: {e}"}), 500

# --- Debug routes you already had ---
@app.route("/debug/routes")
def debug_routes():
    return jsonify(sorted([str(r) for r in app.url_map.iter_rules()])), 200

@app.route("/debug/reportlab")
def debug_reportlab():
    try:
        from reportlab.pdfgen import canvas as C
        b = BytesIO()
        c = C.Canvas(b)
        c.drawString(72, 750, "OK")
        c.save()
        return jsonify({"reportlab": "ok"})
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500

# --- Sentiment via HF Inference API (stateless, retry while loading) ---
@app.route("/analyze", methods=["POST"])
def fAnalyze():
    if not HF_API_KEY:
        return jsonify({"error": "HUGGINGFACE_API_KEY not set on server"}), 500

    data = request.get_json(silent=True) or {}
    text = (data.get("notes") or "").strip()
    if not text:
        return jsonify({"error": "notes is required"}), 400

    payload = {"inputs": text[:600]}
    headers = {
        "Authorization": f"Bearer {HF_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(4):
        try:
            resp = requests.post(HF_API_URL, headers=headers, json=payload, timeout=HF_TIMEOUT)
            if resp.status_code in (503, 524):  # model loading or edge timeout
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code == 429:  # rate limit
                time.sleep(2.0 * (attempt + 1))
                continue
            if not resp.ok:
                return jsonify({"error": f"HuggingFace API error {resp.status_code}: {resp.text[:200]}"}), 502

            out = resp.json()
            # Expect: [ {label, score}, ... ]  OR  [[ {label, score}, ... ]]
            if isinstance(out, list):
                inner = out[0] if out and isinstance(out[0], list) else out
                if inner and isinstance(inner[0], dict) and "label" in inner[0]:
                    top = max(inner, key=lambda x: x.get("score", 0))
                    return jsonify(
                        {"label": top["label"], "score": round(float(top["score"]), 4), "model": HF_MODEL}
                    )
            return jsonify({"error": "Unexpected HF response format", "raw": out}), 502

        except requests.Timeout:
            time.sleep(1.0 * (attempt + 1))
        except Exception as e:
            return jsonify({"error": f"Analysis failed: {e}"}), 500

    return jsonify({"error": "Model is loading or rate-limited. Try again shortly."}), 503

if __name__ == "__main__":
    # Local dev only; Render uses gunicorn.
    app.run(host="127.0.0.1", port=5000, debug=True)
