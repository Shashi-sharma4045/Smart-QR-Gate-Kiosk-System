"""
app.py — EC2 Flask Backend
Endpoints:
  POST /api/request_outpass
  GET  /api/pass_status/<pass_id>
  POST /api/scan_qr
  GET  /api/recent_logs
"""

import os
import json
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from database import db, row_to_dict

# ── Bootstrap ──────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gate_kiosk")

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── SNS configuration ──────────────────────────────────────────────────────
AWS_REGION    = os.environ.get("AWS_REGION", "ap-south-1")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")


def publish_sns(subject: str, message: str) -> None:
    """
    Publish a message to the configured SNS topic.
    Silently skips if SNS_TOPIC_ARN is not set or the call fails,
    so the API still works during local / offline testing.
    """
    if not SNS_TOPIC_ARN:
        log.info("[SNS] SNS_TOPIC_ARN not set — skipping publish.")
        return
    try:
        client = boto3.client("sns", region_name=AWS_REGION)
        client.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=subject[:100],          # SNS subject max = 100 chars
            Message=message,
        )
        log.info("[SNS] Published: %s", subject)
    except (BotoCoreError, ClientError) as exc:
        log.warning("[SNS] Publish failed (non-fatal): %s", exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_display(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return iso_str


# ── Helpers ────────────────────────────────────────────────────────────────

def _generate_pass_id(enrollment: str) -> str:
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    return f"OUT-{enrollment}-{ts}"


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "smart-gate-kiosk-ec2"}), 200


# 1. Create outpass ──────────────────────────────────────────────────────────
@app.route("/api/request_outpass", methods=["POST"])
def request_outpass():
    data = request.get_json(force=True, silent=True) or {}

    required = ["student_name", "enrollment_no", "section", "category",
                "destination", "out_time", "in_time"]
    missing  = [f for f in required if not str(data.get(f, "")).strip()]
    if missing:
        return jsonify({
            "success": False,
            "error":   f"Missing required fields: {', '.join(missing)}",
        }), 400

    student_name  = data["student_name"].strip()
    enrollment_no = data["enrollment_no"].strip()
    section       = data["section"].strip()
    category      = data["category"].strip()
    destination   = data["destination"].strip()
    out_time      = data["out_time"].strip()
    in_time       = data["in_time"].strip()
    pass_id       = _generate_pass_id(enrollment_no)

    try:
        with db() as conn:
            conn.execute(
                """
                INSERT INTO outpasses
                    (pass_id, student_name, enrollment_no, section,
                     category, destination, out_time, in_time, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)
                """,
                (pass_id, student_name, enrollment_no, section,
                 category, destination, out_time, in_time, _now_iso()),
            )
    except Exception as exc:
        log.error("DB insert error: %s", exc)
        return jsonify({"success": False, "error": "Database error."}), 500

    # Publish SNS (non-blocking)
    publish_sns(
        subject=f"New Outpass: {student_name}",
        message=(
            f"New Outpass Generated for {student_name} (Enr: {enrollment_no}).\n"
            f"Pass ID : {pass_id}\n"
            f"Category: {category}\n"
            f"Destination: {destination}\n"
            f"Out Time: {_fmt_display(out_time)}\n"
            f"In Time : {_fmt_display(in_time)}\n"
            f"Status  : APPROVED"
        ),
    )

    return jsonify({
        "success": True,
        "pass_id": pass_id,
        "status":  "approved",
        "message": "Outpass approved successfully.",
    }), 201


# 2. Pass status ─────────────────────────────────────────────────────────────
@app.route("/api/pass_status/<pass_id>", methods=["GET"])
def pass_status(pass_id: str):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM outpasses WHERE pass_id = ?", (pass_id,)
        ).fetchone()

    if not row:
        return jsonify({"success": False, "error": "Pass not found."}), 404

    record = row_to_dict(row)
    return jsonify({"success": True, "pass": record}), 200


# 3. Scan QR (guard endpoint) ─────────────────────────────────────────────────
@app.route("/api/scan_qr", methods=["POST"])
def scan_qr():
    data    = request.get_json(force=True, silent=True) or {}
    pass_id = str(data.get("pass_id", "")).strip()

    if not pass_id:
        return jsonify({"success": False, "error": "pass_id is required."}), 400

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM outpasses WHERE pass_id = ?", (pass_id,)
        ).fetchone()

    if not row:
        return jsonify({
            "success":    False,
            "error":      "Invalid QR code — pass not found in database.",
            "event":      "INVALID",
            "banner_type": "error",
        }), 404

    record     = row_to_dict(row)
    cur_status = record["status"]
    name       = record["student_name"]
    now        = _now_iso()

    # State machine
    if cur_status == "approved":
        new_status   = "exited"
        event        = "EXITED"
        update_field = "exited_at"
        banner_type  = "exit"
        headline     = f"{name} has EXITED"
        sub          = f"Campus departure logged at {_fmt_display(now)}"
    elif cur_status == "exited":
        new_status   = "returned"
        event        = "RETURNED"
        update_field = "returned_at"
        banner_type  = "return"
        headline     = f"{name} has RETURNED"
        sub          = f"Hostel return logged at {_fmt_display(now)}"
    elif cur_status == "returned":
        return jsonify({
            "success":    False,
            "error":      f"Pass fully consumed. {name} has already returned.",
            "event":      "SPENT",
            "banner_type": "error",
            "pass":        record,
        }), 409
    else:
        return jsonify({
            "success":    False,
            "error":      f"Unexpected pass status: '{cur_status}'.",
            "banner_type": "error",
        }), 400

    # Persist transition
    try:
        with db() as conn:
            conn.execute(
                f"UPDATE outpasses SET status=?, {update_field}=? WHERE pass_id=?",
                (new_status, now, pass_id),
            )
    except Exception as exc:
        log.error("DB update error: %s", exc)
        return jsonify({"success": False, "error": "Database write failed."}), 500

    # Reload fresh record
    with db() as conn:
        fresh = row_to_dict(
            conn.execute("SELECT * FROM outpasses WHERE pass_id=?", (pass_id,)).fetchone()
        )

    # Publish SNS
    publish_sns(
        subject=f"Gate Alert: {name} {event}",
        message=(
            f"Gate Alert: {name} (Enr: {record['enrollment_no']}) has {event} at {_fmt_display(now)}.\n"
            f"Pass ID    : {pass_id}\n"
            f"Category   : {record['category']}\n"
            f"Destination: {record['destination']}\n"
            f"Out Time   : {_fmt_display(record['out_time'])}\n"
            f"In Time    : {_fmt_display(record['in_time'])}\n"
            f"New Status : {new_status.upper()}"
        ),
    )

    return jsonify({
        "success":    True,
        "event":      event,
        "banner_type": banner_type,
        "headline":   headline,
        "sub":        sub,
        "pass":       fresh,
    }), 200


# 4. Recent activity log ───────────────────────────────────────────────────────
@app.route("/api/recent_logs", methods=["GET"])
def recent_logs():
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM outpasses ORDER BY id DESC LIMIT 15"
        ).fetchall()
    return jsonify({
        "success": True,
        "logs":    [row_to_dict(r) for r in rows],
    }), 200


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
