from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_bcrypt import Bcrypt
import psycopg2
import uuid
from datetime import datetime
import csv
import io
from flask import Response
app = Flask(__name__)
bcrypt = Bcrypt(app)
CORS(app)  #Allows frontend (dashboard.html) to access this backend

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
if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
