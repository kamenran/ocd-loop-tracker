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

app = Flask(__name__)
bcrypt = Bcrypt(app)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# --- HF config ---
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
HUGGINGFACE_MODEL = os.getenv(
    "HUGGINGFACE_MODEL",
    "distilbert/distilbert-base-uncased-finetuned-sst-2-english"
)
_HF_TIMEOUT = 12
_HF_RETRIES = 3

# --- DB connection ---
def fGetConnection():
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise Exception("DATABASE_URL not set")
    # Tip: in Render Postgres, prefer the pooled URL if you hit connection limits
    # e.g., DATABASE_URL_POOLED
    return psycopg2.connect(conn_str)

# --- Users ---
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
        cur.execute("""
            INSERT INTO users (id, email, passwordhash, created_at)
            VALUES (%s, %s, %s, %s)
        """, (sId, sEmail, sPasswordHash, dtCreatedAt))
        conn.commit()
        cur.close(); conn.close()
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
        cur.execute("SELECT id, passwordhash FROM users WHERE email = %s", (sEmail,))
        row = cur.fetchone()
        cur.close(); conn.close()

        if not row:
            return jsonify({"error": "User not found"}), 404

        sUserId, sPasswordHash = row
        if bcrypt.check_password_hash(sPasswordHash, sPassword):
            return jsonify({"id": sUserId}), 200
        return jsonify({"error": "Invalid password"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Events ---
@app.route('/events', methods=['POST'])
def create_event():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    timestamp = data.get('timestamp')
    trigger = data.get('trigger')
    compulsion = data.get('compulsion')
    emotion = data.get('emotion')
    notes = data.get('notes', '')

    if not user_id or not timestamp or not trigger:
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        new_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO events (id, user_id, "trigger", compulsion, emotion, notes, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (new_id, user_id, trigger, compulsion, emotion, notes, timestamp))
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'id': str(new_id)}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- Analytics ---
@app.route('/analytics', methods=['GET'])
def fGetAnalytics():
    sUserId = request.args.get('user_id')
    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()

        cur.execute("""
            SELECT "trigger", COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY "trigger"
            ORDER BY count DESC;
        """, (sUserId,))
        trigger_rows = cur.fetchall()
        top_triggers = {row[0]: row[1] for row in trigger_rows}

        cur.execute("""
            SELECT DATE(timestamp) AS date, COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY DATE(timestamp)
            ORDER BY date ASC;
        """, (sUserId,))
        date_rows = cur.fetchall()
        daily_counts = [{"date": row[0].isoformat(), "count": row[1]} for row in date_rows]

        cur.close(); conn.close()
        return jsonify({"topTriggers": top_triggers, "dailyCounts": daily_counts}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- Exports ---
@app.route('/export/csv', methods=['GET'])
def fExportCSV():
    sUserId = request.args.get('user_id')
    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, "trigger", compulsion, emotion, notes, timestamp
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC;
        """, (sUserId,))
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close(); conn.close()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(colnames)
        writer.writerows(rows)

        resp = Response(output.getvalue(), mimetype='text/csv')
        resp.headers.set("Content-Disposition", "attachment", filename="events.csv")
        return resp
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/export/pdf', methods=['GET'])
def fExportPDF():
    sUserId = request.args.get('user_id')
    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, "trigger", compulsion, emotion, notes, timestamp
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC;
        """, (sUserId,))
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close(); conn.close()
    except Exception as e:
        return jsonify({'error': f'DB error: {e}'}), 500

    try:
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=LETTER, title="OCD Events Export")

        data = [colnames]
        for r in rows:
            r = list(r)
            try:
                r[-1] = r[-1].strftime("%Y-%m-%d %H:%M")
            except Exception:
                r[-1] = str(r[-1]) if r[-1] is not None else ""
            if r[5] and len(str(r[5])) > 200:
                r[5] = str(r[5])[:200] + "..."
            data.append(r)

        table = Table(data, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND',      (0,0), (-1,0), colors.HexColor('#1ABC9C')),
            ('TEXTCOLOR',       (0,0), (-1,0), colors.white),
            ('FONTNAME',        (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE',        (0,0), (-1,0), 10),
            ('GRID',            (0,0), (-1,-1), 0.25, colors.grey),
            ('ROWBACKGROUNDS',  (0,1), (-1,-1), [colors.whitesmoke, colors.HexColor('#F7F9FB')]),
            ('FONTSIZE',        (0,1), (-1,-1), 9),
            ('VALIGN',          (0,0), (-1,-1), 'TOP'),
        ]))

        styles = getSampleStyleSheet()
        title = Paragraph(f"<b>OCD Tracker — Events Export</b><br/>User: {sUserId}", styles['Title'])

        doc.build([title, table])
        buf.seek(0)
        return send_file(
            buf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"events_{sUserId}.pdf"
        )
    except Exception as e:
        return jsonify({'error': f'PDF error: {e}'}), 500

# --- Health endpoints (for UI banner) ---
@app.route("/healthz", methods=["GET"])
def fHealth():
    return jsonify({"status": "ok"}), 200

@app.route("/readyz", methods=["GET"])
def fReady():
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close(); conn.close()
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        return jsonify({"status": "starting", "reason": str(e)[:160]}), 503

# --- Analyze (Hugging Face) ---
@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True, silent=True) or {}
    notes = (data.get("notes") or "").strip()
    if not notes:
        return jsonify({"error": "notes required"}), 400

    notes = notes[:500]
    url = f"https://api-inference.huggingface.co/models/{HUGGINGFACE_MODEL}"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    payload = {"inputs": notes}

    max_attempts = 12
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            if r.status_code == 503:
                time.sleep(min(2 * attempt, 10))
                continue
            r.raise_for_status()
            out = r.json()
            seq = out[0] if (isinstance(out, list) and out and isinstance(out[0], list)) else out
            if isinstance(seq, list) and seq:
                best = max(seq, key=lambda x: x.get("score", 0))
                return jsonify({
                    "label": best.get("label"),
                    "score": round(float(best.get("score", 0.0)), 4),
                    "model": HUGGINGFACE_MODEL,
                    "attempts": attempt
                }), 200
        except Exception:
            time.sleep(min(2 * attempt, 10))
            continue

    return jsonify({"available": False, "reason": "hf_unavailable"}), 503

if __name__ == "__main__":
    # Render expects the app to bind to $PORT
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
