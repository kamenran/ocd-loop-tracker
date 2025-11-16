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

# Hugging Face config (API key set in Render env)
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")

# Default to this model if env var not set
HUGGINGFACE_MODEL = os.getenv(
    "HUGGINGFACE_MODEL",
    "michellejieli/emotion_text_classifier"
)

# New Router base (api-inference is deprecated)
HUGGINGFACE_BASE = os.getenv(
    "HUGGINGFACE_BASE",
    "https://router.huggingface.co/hf-inference"
)


def fGetConnection():
    conn_str = os.getenv("DATABASE_URL")
    if not conn_str:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(conn_str)

# ---------------- Users ----------------

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
        cur.execute("SELECT id, passwordhash FROM users WHERE email = %s", (sEmail,))
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

# ---------------- Events ----------------

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
        cur.close()
        conn.close()
        return jsonify({'id': str(new_id)}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------------- Analytics ----------------

@app.route('/analytics', methods=['GET'])
def fGetAnalytics():
    sUserId = request.args.get('user_id')
    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400

    try:
        conn = fGetConnection()
        cur = conn.cursor()

        # Top triggers
        cur.execute("""
            SELECT "trigger", COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY "trigger"
            ORDER BY count DESC;
        """, (sUserId,))
        trigger_rows = cur.fetchall()
        top_triggers = {row[0]: row[1] for row in trigger_rows}

        # Daily counts
        cur.execute("""
            SELECT DATE(timestamp) AS date, COUNT(*) AS count
            FROM events
            WHERE user_id = %s
            GROUP BY DATE(timestamp)
            ORDER BY date ASC;
        """, (sUserId,))
        date_rows = cur.fetchall()
        daily_counts = [
            {"date": row[0].isoformat(), "count": row[1]}
            for row in date_rows
        ]

        # AI emotion distribution
        cur.execute("""
            SELECT ai_emotion, COUNT(*) AS c
            FROM events
            WHERE user_id = %s AND ai_emotion IS NOT NULL
            GROUP BY ai_emotion
            ORDER BY c DESC;
        """, (sUserId,))
        ai_rows = cur.fetchall()
        ai_emotions = {row[0]: row[1] for row in ai_rows}

        cur.close()
        conn.close()
        return jsonify({
            "topTriggers": top_triggers,
            "dailyCounts": daily_counts,
            "aiEmotions": ai_emotions,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------------- Exports ----------------

@app.route('/export/csv', methods=['GET'])
def fExportCSV():
    sUserId = request.args.get('user_id')
    if not sUserId:
        return jsonify({'error': 'user_id is required'}), 400
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, user_id, "trigger", compulsion, emotion, notes, timestamp, ai_emotion
            FROM events
            WHERE user_id = %s
            ORDER BY timestamp ASC;
        """, (sUserId,))
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()

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
            SELECT id, user_id, "trigger", compulsion, emotion, notes, timestamp, ai_emotion
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

    try:
        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=LETTER, title="OCD Events Export")

        data = [colnames]
        for r in rows:
            r = list(r)
            # timestamp -> string
            try:
                r[6] = r[6].strftime("%Y-%m-%d %H:%M")
            except Exception:
                r[6] = str(r[6]) if r[6] is not None else ""
            # trim long notes
            if r[5] and len(str(r[5])) > 200:
                r[5] = str(r[5])[:200] + "..."
            data.append(r)

        table = Table(data, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND',      (0, 0), (-1, 0), colors.HexColor('#1ABC9C')),
            ('TEXTCOLOR',       (0, 0), (-1, 0), colors.white),
            ('FONTNAME',        (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',        (0, 0), (-1, 0), 10),
            ('GRID',            (0, 0), (-1, -1), 0.25, colors.grey),
            ('ROWBACKGROUNDS',  (0, 1), (-1, -1),
                                 [colors.whitesmoke, colors.HexColor('#F7F9FB')]),
            ('FONTSIZE',        (0, 1), (-1, -1), 9),
            ('VALIGN',          (0, 0), (-1, -1), 'TOP'),
        ]))

        styles = getSampleStyleSheet()
        title = Paragraph(
            f"<b>OCD Tracker — Events Export</b><br/>User: {sUserId}",
            styles['Title']
        )

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

# ---------------- Health ----------------

@app.route("/healthz", methods=["GET"])
def fHealth():
    return jsonify({"status": "ok"}), 200


@app.route("/readyz", methods=["GET"])
def fReady():
    try:
        conn = fGetConnection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        return jsonify({"status": "starting", "reason": str(e)[:160]}), 503

# ---------------- Analyze (Emotion AI) ----------------

@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Emotion detection for a note. Optionally persists to the event row.
    Request JSON: { notes: str, event_id?: str }
    """
    data = request.get_json(force=True, silent=True) or {}
    notes = (data.get("notes") or "").strip()
    event_id = data.get("event_id")

    if not notes:
        return jsonify({"error": "notes required"}), 400

    notes = notes[:500]

    url = f"{HUGGINGFACE_BASE}/models/{HUGGINGFACE_MODEL}"
    headers = {"Content-Type": "application/json"}
    if HUGGINGFACE_API_KEY:
        headers["Authorization"] = f"Bearer {HUGGINGFACE_API_KEY}"

    payload = {"inputs": notes}

    max_attempts = 6
    attempt = 0
    out_json = None
    last_err = None

    while attempt < max_attempts:
        attempt += 1
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
        except Exception as e:
            last_err = f"network_error: {e}"
            time.sleep(min(1.5 * attempt, 6))
            continue

        # Retry on these server-side statuses
        if r.status_code in (429, 500, 502, 503, 504):
            last_err = f"hf_status_{r.status_code}: {r.text[:200]}"
            time.sleep(min(1.5 * attempt, 6))
            continue

        # If it's a client-side / config error, return it directly
        if r.status_code >= 400:
            try:
                err_json = r.json()
            except Exception:
                err_json = None

            if isinstance(err_json, dict) and "error" in err_json:
                reason = err_json["error"]
            else:
                reason = r.text[:200]

            return jsonify({
                "available": False,
                "reason": f"hf_error_{r.status_code}: {reason}"
            }), 502

        try:
            out_json = r.json()
        except Exception as e:
            last_err = f"bad_json: {e}; body={r.text[:200]}"
            time.sleep(min(1.5 * attempt, 6))
            continue

        break

    if out_json is None:
        return jsonify({
            "available": False,
            "reason": last_err or "hf_unavailable"
        }), 502

    # HF can return [...] or [[...]]
    if isinstance(out_json, list):
        if out_json and isinstance(out_json[0], list):
            seq = out_json[0]
        else:
            seq = out_json
    else:
        return jsonify({
            "available": False,
            "reason": f"unexpected_response_type: {type(out_json).__name__}"
        }), 502

    scores = []
    for item in seq:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        score = item.get("score", 0.0)
        if not label:
            continue
        try:
            score = float(score)
        except Exception:
            score = 0.0
        scores.append({"label": label, "score": score})

    if not scores:
        return jsonify({
            "available": False,
            "reason": "no_scores"
        }), 502

    scores_sorted = sorted(scores, key=lambda x: x["score"], reverse=True)
    top = scores_sorted[0]

    resp = {
        "label": top["label"],
        "score": round(top["score"], 4),
        "scores": scores_sorted,
        "model": HUGGINGFACE_MODEL,
        "attempts": attempt
    }

    # Save emotion back to event if we have an event_id
    if event_id:
        try:
            conn = fGetConnection()
            cur = conn.cursor()
            cur.execute(
                'UPDATE events SET ai_emotion = %s, ai_emotion_scores = %s WHERE id = %s',
                (top["label"], json.dumps(scores_sorted), event_id)
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
