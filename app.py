from flask import Flask, request, jsonify, render_template, Response
from pymongo import MongoClient, errors as pymongo_errors
from datetime import datetime
import os
import io
import csv
import logging

# Basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")

# --- CONFIGURE MONGODB (read from env) ---
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "petition_db")

# Connect with a short server selection timeout so failures are visible quickly
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    # attempt an info call to trigger immediate connection check
    client.admin.command("ping")
    db = client[DB_NAME]
    collection = db["students"]
    logger.info("Connected to MongoDB database '%s'.", DB_NAME)
except pymongo_errors.PyMongoError as e:
    # If connection fails, log and set collection to None so endpoints can return 503
    logger.exception("Failed to connect to MongoDB: %s", e)
    client = None
    db = None
    collection = None

# --- ADMIN KEY (for CSV export) ---
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


# --- Helper: check DB availability ---
def _db_available():
    if collection is None:
        return False
    return True


# --- API endpoints ---
@app.route("/api/submit", methods=["POST"])
def api_submit():
    if not _db_available():
        return jsonify({"success": False, "error": "Database unavailable"}), 503

    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

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

    try:
        result = collection.insert_one(entry)
        entry["_id"] = str(result.inserted_id)
        return jsonify({"success": True, "entry": entry}), 201
    except pymongo_errors.PyMongoError as e:
        logger.exception("DB insert failed: %s", e)
        return jsonify({"success": False, "error": "Database insert failed"}), 500


@app.route("/api/records", methods=["GET"])
def api_records():
    if not _db_available():
        return jsonify({"success": False, "error": "Database unavailable"}), 503

    try:
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
    except pymongo_errors.PyMongoError as e:
        logger.exception("DB read failed: %s", e)
        return jsonify({"success": False, "error": "Database read failed"}), 500


@app.route("/api/counts", methods=["GET"])
def api_counts():
    if not _db_available():
        return jsonify({"success": False, "error": "Database unavailable"}), 503

    try:
        total = collection.count_documents({})
        petitions = collection.count_documents({"form_type": "petition"})
        demands = collection.count_documents({"form_type": "demand"})
        return jsonify({"success": True, "total": total, "petitions": petitions, "demands": demands})
    except pymongo_errors.PyMongoError as e:
        logger.exception("DB count failed: %s", e)
        return jsonify({"success": False, "error": "Database count failed"}), 500


# --- ADMIN CSV EXPORT ENDPOINT ---
def _check_admin_key():
    if ADMIN_KEY is None:
        return False

    header_key = request.headers.get("X-ADMIN-KEY")
    if header_key and header_key == ADMIN_KEY:
        return True

    param_key = request.args.get("admin_key")
    if param_key and param_key == ADMIN_KEY:
        return True

    return False


@app.route("/admin/export.csv", methods=["GET"])
def admin_export_csv():
    if not _check_admin_key():
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if not _db_available():
        return jsonify({"success": False, "error": "Database unavailable"}), 503

    try:
        cursor = collection.find().sort("created_at", -1)
    except pymongo_errors.PyMongoError as e:
        logger.exception("DB read failed for CSV export: %s", e)
        return jsonify({"success": False, "error": "Database read failed"}), 500

    # Build CSV in-memory
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["id", "afn", "year", "branch", "form_type", "comment", "created_at_utc_iso"])

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

    resp = Response(csv_data, mimetype="text/csv")
    resp.headers["Content-Disposition"] = "attachment; filename=petition_records.csv"
    return resp


if __name__ == "__main__":
    # For Render use gunicorn start command; this section is for local development only.
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
