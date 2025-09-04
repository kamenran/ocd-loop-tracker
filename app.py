from flask import send_file
from io import BytesIO
from reportlab.lib.pagesizes import LETTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_bcrypt import Bcrypt
import psycopg2
import uuid
from datetime import datetime
import csv
import os
import io
import time
import requests
from flask import Response
app = Flask(__name__)
bcrypt = Bcrypt(app)
CORS(app)  #Allows frontend (dashboard.html) to access this backend
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")
HUGGINGFACE_MODEL = os.getenv(
    "HUGGINGFACE_MODEL",
    "distilbert/distilbert-base-uncased-finetuned-sst-2-english"
)
_HF_TIMEOUT = 12
_HF_RETRIES = 3
# --- Database connection setup ---


def fGetConnection():
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(conn_str)
# --- Route to create a new user ---
@app.route("/users", methods=["POST"])
def fPostUser():
    data = request.json
    sEmail = data.get("email")
    sPassword = data.get("password")  # now plain text, not pre-hashed

    if not sEmail or not sPassword:
        return jsonify({"error": "Missing fields"}), 400

    # Hash the password securely
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
        cur.close()
        conn.close()
        return jsonify({"id": sId}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500# --- Route to create a new OCD event ---
# --- Route to log in a user ---
@app.route("/login", methods=["POST"])
def fLogin():
    data = request.json
    sEmail = data.get("email")
    sPassword = data.get("password")

    if not sEmail or not sPassword:
        return jsonify({"error": "Missing fields"}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()

        # Look up the user by email
        cur.execute("SELECT id, passwordhash FROM users WHERE email = %s", (sEmail,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "User not found"}), 404

        sUserId, sPasswordHash = row

        # Check if the password matches the stored hash
        if bcrypt.check_password_hash(sPasswordHash, sPassword):
            return jsonify({"id": sUserId}), 200
        else:
            return jsonify({"error": "Invalid password"}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/events', methods=['POST'])
def create_event():
    data = request.get_json()
    print("Raw incoming data:", data)

    user_id = data.get('user_id')
    timestamp = data.get('timestamp')
    trigger = data.get('trigger')
    compulsion = data.get('compulsion')
    emotion = data.get('emotion')
    notes = data.get('notes', '')

    print(f"Parsed: {user_id=}, {timestamp=}, {trigger=}, {compulsion=}, {emotion=}, {notes=}")

    if not user_id or not timestamp or not trigger:
        print("Missing required field")
        return jsonify({'error': 'Missing required fields'}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO events (id, user_id, trigger, compulsion, emotion, notes, timestamp)
            VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s)
            RETURNING id;
        """, (user_id, trigger, compulsion, emotion, notes, timestamp))

        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        print("Insert successful:", new_id)
        return jsonify({'id': str(new_id)}), 201

    except Exception as e:
        print("Error inserting:", e)
        return jsonify({'error': str(e)}), 500


# --- Route to get analytics data (per user) ---
@app.route('/analytics', methods=['GET'])
def fGetAnalytics():
    sUserId = request.args.get('user_id')  # ?user_id=<uuid>

    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()

        # Top trigger frequencies for this user
        cur.execute("""
            SELECT trigger, COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY trigger
            ORDER BY count DESC;
        """, (sUserId,))
        trigger_rows = cur.fetchall()
        top_triggers = {row[0]: row[1] for row in trigger_rows}

        # Daily counts for this user
        cur.execute("""
            SELECT DATE(timestamp) AS date, COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY DATE(timestamp)
            ORDER BY date ASC;
        """, (sUserId,))
        date_rows = cur.fetchall()
        daily_counts = [{"date": row[0].isoformat(), "count": row[1]} for row in date_rows]

        cur.close()
        conn.close()

        return jsonify({
            "topTriggers": top_triggers,
            "dailyCounts": daily_counts
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
# --- Route to export events as CSV ---
@app.route('/export/csv', methods=['GET'])
def fExportCSV():
    sUserId = request.args.get('user_id')

    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, trigger, compulsion, emotion, notes, timestamp
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC;
        """, (sUserId,))
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()

        # Write rows to CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(colnames)   # header
        writer.writerows(rows)

        response = Response(output.getvalue(), mimetype='text/csv')
        response.headers.set("Content-Disposition", "attachment", filename="events.csv")
        return response

    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/export/pdf', methods=['GET'])
def fExportPDF():
    sUserId = request.args.get('user_id')
    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400

    # --- fetch rows ---
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, trigger, compulsion, emotion, notes, timestamp
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC;
        """, (sUserId,))
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({'error': f'DB error: {e}'}), 500

    # --- build PDF in memory ---
    try:
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=LETTER, title="OCD Events Export")

        # table data
        data = [colnames]
        for r in rows:
            r = list(r)
            # timestamp → string, defensively
            try:
                r[-1] = r[-1].strftime("%Y-%m-%d %H:%M")
            except Exception:
                r[-1] = str(r[-1]) if r[-1] is not None else ""
            # trim very long notes
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

        # optional title
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

def _hf_sentiment(text: str):
    """
    Calls Hugging Face Inference API for sentiment. Returns dict:
    { available: bool, label?: str, score?: float, reason?: str }
    """
    if not HUGGINGFACE_API_KEY:
        return {"available": False, "reason": "missing_api_key"}

    url = f"https://api-inference.huggingface.co/models/{HUGGINGFACE_MODEL}"
    headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
    payload = {"inputs": text}

    for attempt in range(1, _HF_RETRIES + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=_HF_TIMEOUT)
            if r.status_code == 503:  # cold start
                time.sleep(min(2 * attempt, 6))
                continue
            r.raise_for_status()
            out = r.json()
            # response can be [[...]] or [...]
            seq = out[0] if (isinstance(out, list) and out and isinstance(out[0], list)) else out
            if isinstance(seq, list) and seq:
                best = max(seq, key=lambda x: x.get("score", 0))
                return {
                    "available": True,
                    "label": best.get("label"),
                    "score": float(best.get("score", 0.0))
                }
            return {"available": False, "reason": "unexpected_response"}
        except requests.RequestException as e:
            if attempt == _HF_RETRIES:
                return {"available": False, "reason": f"network_error: {e}"}
            time.sleep(min(2 * attempt, 6))
    return {"available": False, "reason": "unknown"}

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True, silent=True) or {}
    notes = (data.get("notes") or "").strip()
    if not notes:
        return jsonify({"available": False, "reason": "notes required"}), 400
    # keep payload small
    notes = notes[:500]
    result = _hf_sentiment(notes)
    # Always return a consistent shape
    if result.get("available"):
        return jsonify({
            "available": True,
            "label": result["label"],
            "score": round(result["score"], 4),
            "model": HUGGINGFACE_MODEL
        }), 200
    return jsonify({"available": False, "reason": result.get("reason", "unknown")}), 200
if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)