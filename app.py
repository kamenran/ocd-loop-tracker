from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
import uuid
from datetime import datetime

app = Flask(__name__)
CORS(app)  # 🔓 Allows frontend (dashboard.html) to access this backend

# --- Database connection setup ---
def fGetConnection():
    return psycopg2.connect(
        dbname="ocd_tracker",
        user="postgres",
        password="Kamboarder1001",
        host="localhost",
        port="5432"
    )

# --- Route to create a new user ---
@app.route("/users", methods=["POST"])
def fPostUser():
    data = request.json
    sEmail = data.get("email")
    sPasswordHash = data.get("passwordHash")

    if not sEmail or not sPasswordHash:
        return jsonify({"error": "Missing fields"}), 400

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

# --- Route to create a new OCD event ---
@app.route('/events', methods=['POST'])
def create_event():
    data = request.get_json()

    user_id = data.get('user_id')
    timestamp = data.get('timestamp')
    trigger = data.get('trigger')
    compulsion = data.get('compulsion')
    emotion = data.get('emotion')
    notes = data.get('notes', '')

    if not user_id or not timestamp or not trigger or not compulsion or not emotion:
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

        return jsonify({'id': str(new_id)}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# --- Route to get analytics data ---
@app.route('/analytics', methods=['GET'])
def fGetAnalytics():
    try:
        conn = fGetConnection()
        cur = conn.cursor()

        # Get top trigger frequencies
        cur.execute("""
            SELECT trigger, COUNT(*) as count
            FROM events
            GROUP BY trigger
            ORDER BY count DESC;
        """)
        trigger_rows = cur.fetchall()
        top_triggers = {row[0]: row[1] for row in trigger_rows}

        # Get daily event counts
        cur.execute("""
            SELECT DATE(timestamp) as date, COUNT(*) as count
            FROM events
            GROUP BY DATE(timestamp)
            ORDER BY date ASC;
        """)
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

# --- Start the Flask app ---
if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
