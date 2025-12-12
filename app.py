from flask import Flask, request, jsonify, render_template, Response
from pymongo import MongoClient
from datetime import datetime
import os
import io
import csv

app = Flask(__name__, template_folder="templates")

# --- CONFIGURE MONGODB ---
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "petition_db")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db["students"]

# --- ADMIN KEY (for CSV export) ---
# Set this in Render environment variables: ADMIN_KEY = "a-strong-secret"
ADMIN_KEY = os.environ.get("ADMIN_KEY", None)


# --- ROUTES serving pages ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/petition")
def petition_page():
    return render_template("petition.html")

@app.route("/demand")
def demand_page():
    return render_template("demand.html")

# --- API endpoints ---
@app.route("/api/submit", methods=["POST"])
def api_submit():
    """
    Expected JSON:
    {
      "afn": "AFN123",
      "year": "2nd",
      "branch": "CSE",
      "comment": "Explanation / demand text",
      "form_type": "petition" or "demand"
    }
    """
    data = request.get_json(force=True)
    required = ["afn", "year", "branch", "comment", "form_type"]
    for r in required:
        if r not in data or not str(data[r]).strip():
            return jsonify({"success": False, "error": f"Missing or empty field: {r}"}), 400

    entry = {
        "afn": str(data["afn"]).strip(),
        "year": str(data["year"]).strip(),
        "branch": str(data["branch"]).strip(),
        "comment": str(data["comment"]).strip(),
        "form_type": str(data["form_type"]).strip(),
        "created_at": datetime.utcnow()
    }

    result = collection.insert_one(entry)
    entry["_id"] = str(result.inserted_id)
    return jsonify({"success": True, "entry": entry}), 201

@app.route("/api/records", methods=["GET"])
def api_records():
    docs = []
    for d in collection.find().sort("created_at", -1):
        docs.append({
            "_id": str(d.get("_id")),
            "afn": d.get("afn"),
            "year": d.get("year"),
            "branch": d.get("branch"),
            "comment": d.get("comment"),
            "form_type": d.get("form_type"),
            "created_at": d.get("created_at").isoformat() if d.get("created_at") else None
        })
    return jsonify({"success": True, "records": docs})

@app.route("/api/counts", methods=["GET"])
def api_counts():
    total = collection.count_documents({})
    petitions = collection.count_documents({"form_type": "petition"})
    demands = collection.count_documents({"form_type": "demand"})
    return jsonify({"success": True, "total": total, "petitions": petitions, "demands": demands})


# --- ADMIN CSV EXPORT ENDPOINT ---
def _check_admin_key():
    """
    Checks authorization using:
    1. X-ADMIN-KEY header (preferred)
    2. admin_key query parameter (fallback; less secure, avoid in production)
    Returns True if authorized, False otherwise.
    """
    if ADMIN_KEY is None:
        return False  # admin key not configured

    # Preferred: header
    header_key = request.headers.get("X-ADMIN-KEY")
    if header_key and header_key == ADMIN_KEY:
        return True

    # Fallback: query param (not recommended in production)
    param_key = request.args.get("admin_key")
    if param_key and param_key == ADMIN_KEY:
        return True

    return False


@app.route("/admin/export.csv", methods=["GET"])
def admin_export_csv():
    """
    Returns all records as a CSV file. Protected by ADMIN_KEY.
    Usage (preferred via header):
      curl -H "X-ADMIN-KEY: <your-admin-key>" https://your-render-service/admin/export.csv -o petition_records.csv

    OR (testing only; not recommended because query strings may be logged):
      https://your-render-service/admin/export.csv?admin_key=<your-admin-key>
    """
    if not _check_admin_key():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    # Query records
    cursor = collection.find().sort("created_at", -1)

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([
        "id", "afn", "year", "branch", "form_type", "comment", "created_at_utc_iso"
    ])

    for doc in cursor:
        _id = str(doc.get("_id"))
        afn = doc.get("afn", "")
        year = doc.get("year", "")
        branch = doc.get("branch", "")
        form_type = doc.get("form_type", "")
        comment = doc.get("comment", "").replace("\r", " ").replace("\n", " \\n ")
        created_at = doc.get("created_at").isoformat() if doc.get("created_at") else ""
        writer.writerow([_id, afn, year, branch, form_type, comment, created_at])

    csv_data = output.getvalue()
    output.close()

    # Return as a downloadable response
    resp = Response(csv_data, mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=petition_records.csv"
    return resp


if __name__ == "__main__":
    # For production use gunicorn; this is for local dev
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))