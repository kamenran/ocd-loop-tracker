import os
import io
import csv
import uuid
import time
import psycopg2
import requests
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
from flask_bcrypt import Bcrypt

# PDF (ReportLab)
from reportlab.lib.pagesizes import LETTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

APP_VERSION = "2025-09-04"
HF_DEFAULT_MODEL = "distilbert/distilbert-base-uncased-finetuned-sst-2-english"
HF_TIMEOUT_SEC = 20
HF_RETRIES = 4

app = Flask(__name__)
bcrypt = Bcrypt(app)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

DATABASE_URL = os.getenv("DATABASE_URL")
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
HUGGINGFACE_MODEL = os.getenv("HUGGINGFACE_MODEL", HF_DEFAULT_MODEL)

# ------------------------------- DB -------------------------------
def db():
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    conn.autocommit = True
    return conn

def init_db():
    with db() as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users(
          id UUID PRIMARY KEY,
          email TEXT UNIQUE NOT NULL,
          passwordhash TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS events(
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id UUID REFERENCES users(id) ON DELETE CASCADE,
          trigger TEXT NOT NULL,
          compulsion TEXT,
          emotion TEXT,
          notes TEXT,
          timestamp TIMESTAMPTZ NOT NULL
        );
        """)

# Flask 3: run init at import (no before_first_request)
try:
    init_db()
except Exception as e:
    print(f"[init_db] warning: {e}")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def json_error(msg, code=400):
    return jsonify({"error": msg}), code

def list_routes():
    out = []
    for rule in app.url_map.iter_rules():
        methods = sorted(m for m in rule.methods if m not in ("HEAD", "OPTIONS"))
        out.append({"rule": str(rule), "methods": methods, "endpoint": rule.endpoint})
    return out

# ----------------------- Health & Debug -----------------------
@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/version")
def version():
    return jsonify({
        "version": APP_VERSION,
        "mode": "hf-inference",
        "hf_model": HUGGINGFACE_MODEL
    })

@app.route("/debug/routes")
def debug_routes():
    return jsonify(list_routes())

@app.route("/debug/reportlab")
def debug_reportlab():
    try:
        _ = SimpleDocTemplate(io.BytesIO(), pagesize=LETTER)
        return jsonify({"reportlab": "ok"})
    except Exception as e:
        return json_error(f"reportlab_error: {e}", 500)

@app.route("/debug/hf")
def debug_hf():
    probe = hf_sentiment("I feel okay.")
    return jsonify({
        "env": {
            "has_api_key": bool(HUGGINGFACE_API_KEY),
            "model": HUGGINGFACE_MODEL,
            "timeout": HF_TIMEOUT_SEC,
            "retries": HF_RETRIES
        },
        "probe": probe
    })

# ----------------------------- Auth -----------------------------
@app.route("/users", methods=["POST"])
def create_user():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return json_error("email and password required")

    uid = uuid.uuid4()
    pwdhash = bcrypt.generate_password_hash(password).decode("utf-8")
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users(id,email,passwordhash,created_at) VALUES(%s,%s,%s,%s)",
                (str(uid), email, pwdhash, datetime.now(timezone.utc))
            )
        return jsonify({"id": str(uid), "email": email, "created_at": now_iso()})
    except psycopg2.Error:
        # Unique violation or other pg error -> treat as conflict
        return json_error("email already exists", 409)

@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return json_error("email and password required")

    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, passwordhash FROM users WHERE email=%s", (email,))
        row = cur.fetchone()
        if not row:
            return json_error("invalid credentials", 401)
        uid, pwdhash = row
        if not bcrypt.check_password_hash(pwdhash, password):
            return json_error("invalid credentials", 401)
        return jsonify({"id": str(uid), "email": email})

# ---------------------------- Events ----------------------------
@app.route("/events", methods=["POST"])
def add_event():
    data = request.get_json(force=True, silent=True) or {}
    try:
        user_id = data["user_id"]
        trig = (data.get("trigger") or "").strip()
        comp = (data.get("compulsion") or "").strip() or None
        emo = (data.get("emotion") or "").strip() or None
        notes = (data.get("notes") or "").strip() or None
        ts = data.get("timestamp")
    except Exception:
        return json_error("invalid payload")

    if not user_id or not trig:
        return json_error("user_id and trigger required")

    try:
        event_ts = datetime.fromisoformat(ts.replace("Z","+00:00")) if ts else datetime.now(timezone.utc)
    except Exception:
        return json_error("timestamp must be ISO-8601")

    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO events(user_id,trigger,compulsion,emotion,notes,timestamp) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
            (user_id, trig, comp, emo, notes, event_ts)
        )
        eid = cur.fetchone()[0]
    return jsonify({"id": str(eid), "ok": True})

@app.route("/analytics")
def analytics():
    user_id = request.args.get("user_id")
    if not user_id:
        return json_error("user_id required")

    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT timestamp::date, COUNT(*) FROM events WHERE user_id=%s GROUP BY 1 ORDER BY 1", (user_id,))
        daily = [{"date": d.isoformat(), "count": c} for d, c in cur.fetchall()]

        cur.execute("SELECT trigger, COUNT(*) FROM events WHERE user_id=%s GROUP BY 1 ORDER BY 2 DESC LIMIT 10", (user_id,))
        top = {t: c for t, c in cur.fetchall()}

    return jsonify({"dailyCounts": daily, "topTriggers": top})

# ---------------------------- Exports ---------------------------
@app.route("/export/csv")
def export_csv():
    user_id = request.args.get("user_id")
    if not user_id:
        return json_error("user_id required")

    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, user_id, trigger, compulsion, emotion, notes, timestamp
            FROM events WHERE user_id=%s ORDER BY timestamp DESC
        """, (user_id,))
        rows = cur.fetchall()

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id","user_id","trigger","compulsion","emotion","notes","timestamp"])
        yield output.getvalue()
        output.seek(0); output.truncate(0)
        for r in rows:
            writer.writerow([str(r[0]), r[1], r[2], r[3] or "", r[4] or "", r[5] or "", r[6].isoformat()])
            yield output.getvalue()
            output.seek(0); output.truncate(0)

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="events.csv"'})

@app.route("/export/pdf")
def export_pdf():
    user_id = request.args.get("user_id")
    if not user_id:
        return json_error("user_id required")

    with db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT timestamp, trigger, compulsion, emotion, notes
            FROM events WHERE user_id=%s ORDER BY timestamp DESC LIMIT 150
        """, (user_id,))
        rows = cur.fetchall()

        cur.execute("SELECT COUNT(*) FROM events WHERE user_id=%s", (user_id,))
        total_events = cur.fetchone()[0]

        cur.execute("SELECT trigger, COUNT(*) FROM events WHERE user_id=%s GROUP BY 1 ORDER BY 2 DESC LIMIT 5", (user_id,))
        top = cur.fetchall()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("OCD Tracker — Report", styles["Title"]))
    story.append(Paragraph(f"Generated: {now_iso()}", styles["Normal"]))
    story.append(Paragraph(f"Total Events: {total_events}", styles["Normal"]))
    story.append(Spacer(1, 8))

    if top:
        data = [["Top Triggers", "Count"]] + [[t, c] for t, c in top]
        table = Table(data, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ]))
        story.append(table)
        story.append(Spacer(1, 12))

    data = [["Timestamp", "Trigger", "Compulsion", "Emotion", "Notes"]]
    for ts, trig, comp, emo, notes in rows:
        data.append([ts.isoformat(), trig, comp or "", emo or "", (notes or "")[:200]])

    table = Table(data, colWidths=[120, 100, 90, 70, 150])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
        ("FONTSIZE", (0,0), (-1,-1), 8),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
    ]))
    story.append(table)

    doc.build(story)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="ocd_report.pdf", mimetype="application/pdf")

# ----------------------- HF Inference API -----------------------
def hf_sentiment(text: str):
    if not HUGGINGFACE_API_KEY:
        return {"available": False, "reason": "missing_api_key"}

    url = f"https://api-inference.huggingface.co/models/{HUGGINGFACE_MODEL}"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    payload = {"inputs": text}

    for attempt in range(1, HF_RETRIES + 1):
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=HF_TIMEOUT_SEC)
            if res.status_code == 503:
                time.sleep(min(2 * attempt, 6))
                continue
            res.raise_for_status()
            out = res.json()
            # [[{label,score},...]] or [{label,score},...]
            if isinstance(out, list) and out:
                seq = out[0] if (isinstance(out[0], list) and out[0]) else out
                if isinstance(seq, list) and seq:
                    best = max(seq, key=lambda x: x.get("score", 0))
                    return {
                        "available": True,
                        "label": best.get("label"),
                        "score": best.get("score"),
                        "model": HUGGINGFACE_MODEL
                    }
            return {"available": False, "reason": "unexpected_response", "raw": out}
        except requests.RequestException as e:
            if attempt == HF_RETRIES:
                return {"available": False, "reason": f"network_error: {e}"}
            time.sleep(min(2 * attempt, 6))
    return {"available": False, "reason": "unknown"}

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True, silent=True) or {}
    notes = (data.get("notes") or "").strip()
    if not notes:
        return jsonify({"available": False, "reason": "notes required"}), 400

    result = hf_sentiment(notes)
    if (isinstance(result, dict) and result.get("available")) or ("label" in result):
        return jsonify({
            "available": True,
            "label": result.get("label"),
            "score": result.get("score"),
            "model": result.get("model", HUGGINGFACE_MODEL)
        })
    return jsonify({"available": False, "reason": result.get("reason", "unknown")})

# ------------------------------ Root ------------------------------
@app.route("/")
def root():
    return jsonify({"ok": True, "message": "OCD Tracker API"}), 200

# ------------------------------ Main ------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
