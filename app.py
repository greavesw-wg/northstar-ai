import psycopg2
import os
import csv
import re
import html
import jwt
import bcrypt

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify, redirect, url_for, Response
from flask_cors import CORS

app = Flask(__name__)
SECRET_KEY = os.getenv("SECRET_KEY", "fallback_secret")

CORS(app)

from datetime import datetime, timedelta
from uuid import uuid4
from typing import Any

from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from threading import Thread

from ai_engine.maintenance_triage_engine import (
     triage_message,
    generate_work_order,
    log_triage_event
)
from pathlib import Path

TICKET_STATUS = [
    "REQUEST_SUBMITTED",
    "CATEGORIZED",
    "ASSIGNED",
    "WORK_ORDER_CREATED",
    "VENDOR_NOTIFIED",
    "SCHEDULED",
    "IN_PROGRESS",
    "COMPLETED",
    "CLOSED",
    "FAILED"
]

DISPATCH_DIRECTORY = {
    "In-House": {
        "name": "Dwayne",
        "phone": "+16099649408",
        "role": "Technician",
        "close_code": "DWAYNE1001",
    },
    "Outsource": {
        "name": "Barbara",
        "phone": "+16098515173",
        "role": "Vendor",
        "close_code": "BARBARA2001",
    },
}

def get_dispatch_target(assigned_type):
    return DISPATCH_DIRECTORY.get(assigned_type, DISPATCH_DIRECTORY["In-House"])

def build_dispatch_message(
            ticket_id,
            tenant_name,
            property_name,
            building,
            unit,
            issue,
            tenant_phone,
            technician_close_code=None
    ):
    return (
        f"North Star AI Dispatch:\n"
        f"Work Order: {ticket_id}\n"
        f"Tenant: {tenant_name}\n"
        f"Phone: {tenant_phone}\n"
        f"Property: {property_name}\n"
        f"Building: {building}\n"
        f"Unit: {unit}\n"
        f"Issue: {issue}\n\n"
        f"Reply with your code and Work Order number when accepting or completing this job. Example: {technician_close_code} {ticket_id}"
    )


def build_tenant_assignment_message(assigned_type, assigned_name):
    if assigned_type == "In-House":
        return (
            f"North Star AI: Your maintenance request has been assigned to "
            f"{assigned_name}. You will receive updates as the request progresses."
        )

    return (
        f"North Star AI: Your maintenance request has been assigned to "
        f"{assigned_name} from our vendor network. You will receive updates as the request progresses."
    )

def generate_tenant_close_code():
    import random
    return str(random.randint(100000, 999999))

def send_sms(to_phone, message_body):
    try:
        sms_phone = format_phone(to_phone)

        message = twilio_client.messages.create(
            body=message_body,
            messaging_service_sid=os.getenv("TWILIO_MESSAGING_SERVICE_SID"),
            to=sms_phone
        )

        return {
            "sent": True,
            "status": str(message.status),
            "sid": message.sid,
            "error": None
        }

    except Exception as e:
        print(f"[TWILIO SEND_SMS ERROR] {e}", flush=True)
        return {
            "sent": False,
            "status": "failed",
            "sid": None,
            "error": str(e)
        }

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

def check_auth(username, password):
    return username == os.getenv("DASHBOARD_USER") and password == os.getenv("DASHBOARD_PASS")


def authenticate():
    return Response(
        'Login required', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )


def requires_auth(f):
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)

    decorated.__name__ = f.__name__
    return decorated


def clean_phone(phone):
    return re.sub(r"\D", "", str(phone).strip())


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def log_work_order_activity(request_id, event_type, message, actor="system"):
    conn = get_db_connection()
    cur = conn.cursor()

    # Ensure table exists (safe for now)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS work_order_activity_log (
            id SERIAL PRIMARY KEY,
            request_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            actor TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Insert activity log
    cur.execute("""
        INSERT INTO work_order_activity_log
            (request_id, event_type, message, actor)
        VALUES (%s, %s, %s, %s);
    """, (request_id, event_type, message, actor))

    conn.commit()
    cur.close()
    conn.close()

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_requests (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            issue TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

def generate_ticket_number(ticket_id, submitted_at):
    if isinstance(submitted_at, str):
        try:
            dt = datetime.fromisoformat(submitted_at.replace("Z", ""))
        except Exception:
            dt = datetime.utcnow()
    else:
        dt = submitted_at

    return f"NS-{dt.strftime('%Y%m%d')}-{int(ticket_id):06d}"

def format_status_badge(status_label, current_step=None):
    raw_status = str(status_label or "").strip().upper()

    if raw_status in ("WORK_ORDER_CREATED", "NEW"):
        cls = "status-created"
        display_status = raw_status

    elif raw_status in ("ASSIGNED_DWAYNE", "ASSIGNED_BARBARA"):
        cls = "status-assigned"
        display_status = raw_status

    elif raw_status == "COMPLETION_PENDING_CONFIRMATION":
        cls = "status-pending-confirmation"
        display_status = raw_status

    elif raw_status == "WORK_ORDER_CLOSED":
        cls = "status-closed"
        display_status = raw_status

    else:
        cls = "status-unknown"
        display_status = raw_status or str(current_step or "UNKNOWN").strip().upper()

    return f"<span class='status-badge {cls}'>{display_status}</span>"

twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)
CORS(app)

# --------------------------------------------------
# North Star Client / Property Profiles (MVP)
# Replace with PostgreSQL / Supabase later
# --------------------------------------------------

client_properties: list[dict[str, Any]] = []
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_client_properties():
    global client_properties
    client_properties = []

    if not os.path.exists(CLIENT_PROPERTIES_FILE):
        return

    with open(CLIENT_PROPERTIES_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["unit_count"] = int(row["unit_count"]) if row.get("unit_count") not in (None, "", "None") else None
            row["building_count"] = int(row["building_count"]) if row.get("building_count") not in (None, "",
                                                                                                    "None") else None
            row["service_enabled"] = str(row.get("service_enabled", "")).lower() == "true"
            client_properties.append(row)


def save_client_properties():
    os.makedirs(os.path.dirname(CLIENT_PROPERTIES_FILE), exist_ok=True)

    fieldnames = [
        "id",
        "client_name",
        "property_name",
        "property_type",
        "unit_count",
        "building_count",
        "current_pms",
        "property_notes",
        "sign_up_date",
        "service_begin_date",
        "service_end_date",
        "payment_due_date",
        "service_enabled",
        "onboarding_status",
        "created_at",
        "updated_at",
    ]

    with open(CLIENT_PROPERTIES_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(client_properties)


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def validate_client_property_payload(data: dict[str, Any]):
    required_fields = ["client_name", "property_name"]

    for field in required_fields:
        if not data.get(field):
            return False, f"{field} is required"

    return True, ""


LEADS_FILE = os.path.join(BASE_DIR, "NorthStar_Contact_Test/leads.csv")
LOG_FILE = os.path.join(BASE_DIR, "NorthStar_Contact_Test/Logs", "work_orders.csv")
FAIL_LOG = os.path.join(BASE_DIR, "NorthStar_Contact_Test/Logs", "failed_messages.log")
CLIENT_PROPERTIES_FILE = os.path.join(BASE_DIR, "NorthStar_Contact_Test/data", "client_properties.csv")
ACTIVITY_LOG = os.path.join(BASE_DIR, "NorthStar_Contact_Test/Logs", "activity_log.csv")
load_client_properties()


def log_message(from_number, message):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            from_number,
            message
        ])


def log_activity(event_type, client="", property_name="", action="", result=""):
    os.makedirs(os.path.dirname(ACTIVITY_LOG), exist_ok=True)
    file_exists = os.path.exists(ACTIVITY_LOG)

    with open(ACTIVITY_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow([
                "timestamp",
                "event_type",
                "client",
                "property",
                "action",
                "result",
            ])

        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            event_type,
            client,
            property_name,
            action,
            result,
        ])


def ensure_csv_exists():
    if not os.path.exists(LEADS_FILE):
        with open(LEADS_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            writer.writerow([
                "timestamp",
                "first_name",
                "last_name",
                "email",
                "phone",
                "company_property",
                "unit_count",
                "current_pms",
                "score",
                "category",
                "summary",
                "message"
            ])


def analyze_lead_with_openai(first_name, last_name, company_property, unit_count, current_pms, message):
    prompt = f"""
You are analyzing an inbound lead for North Star AI, an AI-assisted maintenance and operations platform for multifamily real estate.

Return ONLY valid JSON with these keys:
score
category
summary

Rules:
- score must be one of: LOW, MEDIUM, HIGH
- category should be a short business label
- summary should be 1 concise sentence

Lead details:
Name: {first_name} {last_name}
Company / Property: {company_property}
Units: {unit_count}
Current PMS: {current_pms}
Message: {message}
"""

    try:
        response = openai_client.responses.create(
            model="gpt-4.1-mini",
            input=prompt
        )

        text = response.output_text.strip()

        import json
        import re

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))

        score = parsed.get("score", "MEDIUM")
        category = parsed.get("category", "General Inquiry")
        summary = parsed.get("summary", "Lead submitted through the North Star contact form.")

        return {
            "score": score,
            "category": category,
            "summary": summary
        }

    except Exception as e:
        print("OPENAI ANALYSIS ERROR:")
        print(str(e))
        return {
            "score": "MEDIUM",
            "category": "General Inquiry",
            "summary": "Lead submitted through the North Star contact form."
        }
@app.route("/debug/db")
def debug_db():
    import os, psycopg2

    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    cur = conn.cursor()

    cur.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
    ORDER BY table_name;
    """)
    tables = cur.fetchall()

    output = f"TABLES: {tables}\n\n"

    try:
        cur.execute("SELECT * FROM maintenance_requests ORDER BY id DESC LIMIT 5;")
        rows = cur.fetchall()
        output += "ROWS:\n"
        for r in rows:
            output += str(r) + "\n"
    except Exception as e:
        output += f"ERROR: {e}"

    cur.close()
    conn.close()

    return f"<pre>{output}</pre>"

@app.route("/sms", methods=["POST"])
def sms_handler():
    from_number = request.form.get("From", "").strip()
    to_number = request.form.get("To", "").strip()
    message = request.form.get("Body", "").strip()

    print("===== INBOUND /SMS DEBUG =====", flush=True)
    print(f"FROM: [{from_number}]", flush=True)
    print(f"TO: [{to_number}]", flush=True)
    print(f"BODY: [{message}]", flush=True)

    # STEP 0: Check whether this inbound SMS is a technician/vendor command.
    dispatch_response = handle_dispatch_person_sms(from_number, message)
    if dispatch_response is not None:
            print("DISPATCH RESPONSE RETURNED", flush=True)
            return dispatch_response

    print("NO DISPATCH RESPONSE - CONTINUING TO TENANT CLOSE CHECK", flush=True)

    # STEP 0B: Check whether this inbound SMS is a tenant closeout confirmation.
    tenant_close_response = handle_tenant_close_sms(from_number, message)
    if tenant_close_response is not None:
            print("TENANT CLOSE RESPONSE RETURNED", flush=True)
            return tenant_close_response

    print("NO TENANT CLOSE RESPONSE - CONTINUING AS NEW SMS REQUEST", flush=True)

    conn = get_db_connection()
    cur = conn.cursor()

    # STEP 1: Resolve property from inbound Twilio phone number
    normalized_to_number = normalize_sms_phone(to_number)

    cur.execute("""
        SELECT
            p.id,
            p.property_name
        FROM property_phone_numbers pp
        JOIN properties p
            ON p.id = pp.property_id
        WHERE pp.phone_number = %s
            AND p.status = 'active'
        LIMIT 1
    """, (normalized_to_number,))

    property_row = cur.fetchone()

    if not property_row:
        cur.close()
        conn.close()
        return jsonify({
            "error": "Inbound SMS number is not mapped to an active property."
        }), 400

    property_id = property_row[0]
    property_name = property_row[1]

    print(f"Matched inbound number {normalized_to_number} to {property_name}", flush=True)

    # 🔥 STEP 2: Log request with property_id

    tenant_close_code = generate_tenant_close_code()
    assigned_type = "In-House"

    dispatch_target = get_dispatch_target(assigned_type)

    technician_close_code = dispatch_target["close_code"]
    assigned_name = dispatch_target["name"]
    dispatch_phone = dispatch_target["phone"]

    ticket_id = "Pending"

    dispatch_message = build_dispatch_message(
        ticket_id=ticket_id,
        tenant_name="SMS Resident",
        property_name=property_name,
        building="Unknown",
        unit="Unknown",
        issue=message,
        tenant_phone=from_number,
    )

    tenant_assignment_message = build_tenant_assignment_message(
        assigned_type=assigned_type,
        assigned_name=assigned_name,
    )

    cur.execute("""
        INSERT INTO maintenance_requests_v2 (
            resident_phone,
            issue_description,
            property_id,
            status,
            submitted_at,
            tenant_close_code,
            technician_close_code,
            technician_confirmed,
            tenant_confirmed
        )
        VALUES (%s, %s, %s, 'WORK_ORDER_CREATED', NOW(), %s, %s, FALSE, FALSE)
    """, (from_number, message, property_id, tenant_close_code, technician_close_code))

    conn.commit()
    cur.close()
    conn.close()

    # 🔥 STEP 3: Reply
    resp = MessagingResponse()
    resp.message(
        f"NorthStar AI: Request received for {property_name}. "
        f"A technician will review shortly."
    )

    return str(resp)

@app.route("/sms-fallback", methods=["POST"])
def sms_fallback():
    from_number = request.form.get("From", "").strip()
    message = request.form.get("Body", "").strip()

    with open(FAIL_LOG, "a", encoding="utf-8") as f:
        f.write(
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"{from_number} | {message}\n"
        )

    return "Logged", 200

def format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)

    if len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        pass
    else:
        raise ValueError(f"Invalid phone number: {phone}")

    return f"+{digits}"

def normalize_sms_phone(phone):
    digits = re.sub(r"\D", "", phone or "")

    if len(digits) == 10:
        return "+1" + digits

    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits

    if phone and phone.startswith("+"):
        return phone

    return phone


def find_dispatch_person_by_code(dispatch_code):
    incoming_code = (dispatch_code or "").strip().upper()

    for assigned_type, person in DISPATCH_DIRECTORY.items():
        close_code = person.get("close_code", "").strip().upper()

        if incoming_code == close_code:
            return {
                "assigned_type": assigned_type,
                "name": person.get("name"),
                "phone": normalize_sms_phone(person.get("phone")),
                "role": person.get("role"),
                "close_code": close_code,
            }

    return None

def parse_dispatch_command(message_body):
    """
    Expected format:
        DWAYNE1001 134
        BARBARA2001 139

    Returns:
        (dispatch_code, ticket_suffix)

    ticket_suffix is the last three digits of the work order / request id.
    """
    body = (message_body or "").strip().upper()
    parts = body.split()

    if len(parts) != 2:
        return None, None

    dispatch_code = parts[0].strip()
    ticket_suffix = re.sub(r"\D", "", parts[1])

    if not dispatch_code or not ticket_suffix:
        return None, None

    return dispatch_code, ticket_suffix

def handle_dispatch_person_sms(from_number, message_body):
    dispatch_code, ticket_suffix = parse_dispatch_command(message_body)

    if not dispatch_code or not ticket_suffix:
        return None

    dispatch_person = find_dispatch_person_by_code(dispatch_code)

    if not dispatch_person:
        return None

    from_number = normalize_sms_phone(from_number)
    dispatch_phone = normalize_sms_phone(dispatch_person["phone"])
    assigned_name = dispatch_person["name"]
    assigned_type = dispatch_person["assigned_type"]

    if from_number != dispatch_phone:
        resp = MessagingResponse()
        resp.message(
            "North Star AI: This dispatch code is not authorized from this phone number."
        )
        return str(resp)

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # Find the newest open work order assigned to this dispatch type.
        cur.execute("""
            SELECT
                id,
                resident_phone,
                status,
                tenant_close_code,
                technician_confirmed,
                tenant_confirmed
            FROM maintenance_requests_v2
            WHERE technician_close_code = %s
              AND RIGHT(id::text, 3) = %s
              AND status IN (
                    'WORK_ORDER_CREATED',
                    'ASSIGNED_DWAYNE',
                    'ASSIGNED_BARBARA',
                    'COMPLETION_PENDING_CONFIRMATION'
              )
            ORDER BY submitted_at DESC
            LIMIT 1
        """, (dispatch_person["close_code"], ticket_suffix))

        row = cur.fetchone()

        if not row:
            resp = MessagingResponse()
            resp.message(
                f"North Star AI: No active Work Order ending in #{ticket_suffix} was found for {assigned_name}."
            )
            return str(resp)

        request_id = row[0]
        tenant_phone = row[1]
        current_status = row[2]
        tenant_close_code = row[3]
        technician_confirmed = row[4]
        tenant_confirmed = row[5]

        # First technician/vendor signal: accept assignment.
        if current_status == "WORK_ORDER_CREATED":
            assigned_status = f"ASSIGNED_{assigned_name.upper()}"

            cur.execute("""
                UPDATE maintenance_requests_v2
                SET status = %s,
                    assigned_type = %s,
                    assigned_to = %s,
                    current_step = %s,
                    last_event = %s,
                    status_updated_at = NOW()
                WHERE id = %s
            """, (
                assigned_status,
                assigned_type,
                assigned_name,
                f"Assigned to {assigned_name}",
                "WORK_ORDER_ASSIGNED",
                request_id
            ))

            conn.commit()

            safe_log_work_order_activity(
                request_id,
                "WORK_ORDER_ASSIGNED",
                f"Work order assigned to {assigned_name}.",
                actor=assigned_name
            )

            send_sms(
                tenant_phone,
                f"North Star AI: {assigned_name} has been assigned to your maintenance request. "
                f"Updates will continue to be sent to this number."
            )

            resp = MessagingResponse()
            resp.message(
                f"North Star AI: Work Order #{request_id} assigned to {assigned_name}."
            )
            return str(resp)

        # Second technician/vendor signal: completed by technician/vendor.
        if current_status in (f"ASSIGNED_{assigned_name.upper()}", "COMPLETION_PENDING_CONFIRMATION"):
            technician_confirmed = True

            if technician_confirmed and tenant_confirmed:
                new_status = "WORK_ORDER_CLOSED"
                current_step = "Work Order Closed"
                last_event = "WORK_ORDER_CLOSED"
            else:
                new_status = "COMPLETION_PENDING_CONFIRMATION"
                current_step = "Pending Tenant Confirmation"
                last_event = "TECHNICIAN_COMPLETED"

            cur.execute("""
                UPDATE maintenance_requests_v2
                SET status = %s,
                    technician_confirmed = TRUE,
                    current_step = %s,
                    last_event = %s,
                    status_updated_at = NOW(),
                    closed_at = CASE WHEN %s = 'WORK_ORDER_CLOSED' THEN NOW() ELSE closed_at END
                WHERE id = %s
            """, (
                new_status,
                current_step,
                last_event,
                new_status,
                request_id
            ))

            conn.commit()

            safe_log_work_order_activity(
                request_id,
                last_event,
                f"{assigned_name} marked work order as completed.",
                actor=assigned_name
            )

            if new_status == "WORK_ORDER_CLOSED":
                send_sms(
                    tenant_phone,
                    f"North Star AI: Work Order #{request_id} has been closed. Thank you for confirming completion."
                )
            else:
                send_sms(
                    tenant_phone,
                    f"North Star AI: {assigned_name} has marked Work Order #{request_id} as completed. "
                    f"If the work was completed satisfactorily, reply CLOSE {tenant_close_code}."
                )

            resp = MessagingResponse()
            resp.message(
                f"North Star AI: Completion signal received for Work Order #{request_id}. "
                f"Status: {new_status}."
            )
            return str(resp)

        resp = MessagingResponse()
        resp.message(
            f"North Star AI: Work Order #{request_id} is currently {current_status}."
        )
        return str(resp)

    finally:
        cur.close()
        conn.close()


def handle_tenant_close_sms(from_number, message_body):
    from_number = normalize_sms_phone(from_number)
    body = (message_body or "").strip().upper()

    if not body.startswith("CLOSE "):
        return None

    tenant_code = body.replace("CLOSE", "", 1).strip()

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        digits_from_number = re.sub(r"\D", "", from_number)

        if len(digits_from_number) == 11 and digits_from_number.startswith("1"):
            digits_from_number = digits_from_number[1:]

        cur.execute("""
            SELECT
                id,
                status,
                technician_confirmed,
                tenant_confirmed
            FROM maintenance_requests_v2
            WHERE RIGHT(REGEXP_REPLACE(resident_phone, '\\D', '', 'g'), 10) = %s
              AND tenant_close_code = %s
              AND status IN (
                    'ASSIGNED_DWAYNE',
                    'ASSIGNED_BARBARA',
                    'COMPLETION_PENDING_CONFIRMATION'
              )
            ORDER BY submitted_at DESC
            LIMIT 1
        """, (digits_from_number, tenant_code))

        row = cur.fetchone()

        resp = MessagingResponse()

        if not row:
            resp.message(
                "North Star AI: No matching open work order was found for that closeout code."
            )
            return str(resp)

        request_id = row[0]
        current_status = row[1]
        technician_confirmed = row[2]
        tenant_confirmed = True

        if technician_confirmed and tenant_confirmed:
            new_status = "WORK_ORDER_CLOSED"
            current_step = "Work Order Closed"
            last_event = "WORK_ORDER_CLOSED"
        else:
            new_status = "COMPLETION_PENDING_CONFIRMATION"
            current_step = "Pending Technician Completion"
            last_event = "TENANT_CONFIRMED"

        cur.execute("""
            UPDATE maintenance_requests_v2
            SET status = %s,
                tenant_confirmed = TRUE,
                current_step = %s,
                last_event = %s,
                status_updated_at = NOW(),
                closed_at = CASE WHEN %s = 'WORK_ORDER_CLOSED' THEN NOW() ELSE closed_at END
            WHERE id = %s
        """, (
            new_status,
            current_step,
            last_event,
            new_status,
            request_id
        ))

        conn.commit()

        safe_log_work_order_activity(
            request_id,
            last_event,
            f"Tenant submitted closeout confirmation code.",
            actor="tenant"
        )

        if new_status == "WORK_ORDER_CLOSED":
            resp.message(
                f"North Star AI: Thank you. Work Order #{request_id} has been closed."
            )
        else:
            resp.message(
                f"North Star AI: Thank you. Your confirmation has been received. "
                f"The work order is pending technician completion confirmation."
            )

        return str(resp)

    finally:
        cur.close()
        conn.close()

def next_work_order_sequence() -> int:
    logs_dir = Path("NorthStar_Contact_Test/Logs")
    work_orders_file = logs_dir / "work_orders.csv"

    if not work_orders_file.exists():
        return 1

    with open(work_orders_file, "r", encoding="utf-8") as f:
        return max(sum(1 for _ in f), 1)

def safe_log_work_order_activity(request_id, event_type, message, actor="system"):
    try:
        log_work_order_activity(request_id, event_type, message, actor)
    except Exception as e:
        print(f"[ACTIVITY LOG ERROR] {event_type}: {e}")

def update_ticket_status(request_id, status, event_type, message, current_step=None):
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE maintenance_requests_v2
            SET status = %s,
                status_updated_at = NOW(),
                last_event = %s,
                current_step = %s
            WHERE id = %s
        """, (
            status,
            event_type,
            current_step,
            request_id
        ))

        conn.commit()
        cur.close()
        conn.close()

        safe_log_work_order_activity(
            request_id,
            event_type,
            message
        )

    except Exception as e:
        print(f"[TICKET STATUS ERROR] request_id={request_id} status={status} event={event_type}: {e}")

def send_tenant_acknowledgment(request_id, phone, tenant_close_code):
    try:
        sms_phone = format_phone(phone)

        message = twilio_client.messages.create(
            body=(
                f"North Star AI: Your maintenance request has been received. "
                f"Work Order #{request_id} has been created. "
                f"Your closeout confirmation code is {tenant_close_code}. "
                f"When the work is completed satisfactorily, reply CLOSE {tenant_close_code}."
            ),
            messaging_service_sid=os.getenv("TWILIO_MESSAGING_SERVICE_SID"),
            to=sms_phone
        )

        safe_log_work_order_activity(
            request_id,
            "TENANT_NOTIFIED",
            f"Tenant acknowledgement SMS queued to {sms_phone}. SID={message.sid}"
        )

        return {
            "sent": True,
            "status": str(message.status),
            "sid": message.sid,
            "error": None
        }

    except Exception as e:
        safe_log_work_order_activity(
            request_id,
            "TENANT_NOTIFICATION_FAILED",
            f"Tenant acknowledgement SMS failed: {e}"
        )

        print(f"[TWILIO ACK ERROR] {e}")

        return {
            "sent": False,
            "status": "failed",
            "sid": None,
            "error": str(e)

        }

def run_post_submission_tasks(
    request_id,
    name,
    phone,
    building,
    unit,
    issue,
    assigned_type,
    property_name,
    tenant_close_code,
    routing_phone

    ):
    try:
        request_payload = {
            "request_id": request_id,
            "property_name": property_name,
            "building": building,
            "unit_number": unit,
            "resident_name": name,
            "resident_phone": phone,
            "message": issue,
            "assigned_type": assigned_type,
        }

        try:
            triage = triage_message(request_payload)

            work_order = generate_work_order(
                request_payload,
                triage,
                sequence_number=next_work_order_sequence(),
            )

            safe_log_work_order_activity(
                request_id,
                "WORK_ORDER_GENERATED",
                f"Work order generated for Building {building}, Unit {unit}."
            )

            print(f"ACK ROUTING PHONE: [{routing_phone}] for property [{property_name}]", flush=True)
            ack = send_tenant_acknowledgment(request_id, phone, tenant_close_code)
            print(f"ACK RESULT: {ack}", flush=True)

            dispatch_target = get_dispatch_target(assigned_type)

            technician_close_code = dispatch_target["close_code"]

            dispatch_message = build_dispatch_message(
                ticket_id=request_id,
                tenant_name=name,
                property_name=property_name,
                building=building,
                unit=unit,
                issue=issue,
                tenant_phone=phone,
                technician_close_code=technician_close_code,
            )

            send_sms(dispatch_target["phone"], dispatch_message)

        except Exception as e:
            safe_log_work_order_activity(
                request_id,
                "TRIAGE_FAILED",
                f"AI triage/work-order generation failed: {e}"
            )
            print(f"[TRIAGE ERROR] {e}")

        update_ticket_status(
            request_id,
            status="WORK_ORDER_CREATED",
            event_type="TENANT_NOTIFIED",
            message="Tenant acknowledgment sent.",
            current_step="Tenant Notified"
        )

        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            UPDATE maintenance_requests_v2
            SET acknowledgment_sent = %s,
                acknowledgment_status = %s,
                acknowledgment_sid = %s,
                acknowledgment_error = %s,
                acknowledgment_sent_at = CASE WHEN %s THEN NOW() ELSE acknowledgment_sent_at END
            WHERE id = %s
        """, (
            ack["sent"],
            ack["status"],
            ack["sid"],
            ack["error"],
            ack["sent"],
            request_id
        ))

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print(f"[POST SUBMISSION TASK ERROR] {e}")

@app.route("/maintenance-request", methods=["POST"])
def maintenance_request():
    data = request.get_json(silent=True) or request.form

    name = str(data.get("name", "")).strip()
    phone = clean_phone(data.get("phone", ""))
    community_access_code = str(data.get("community_access_code", "")).strip().upper()
    building = " ".join(str(data.get("building", "")).split()).strip()
    unit = " ".join(str(data.get("unit", "")).split()).strip()
    issue = str(data.get("issue", "")).strip()

    print("FORM VALUES:", {
        "name": name,
        "phone": phone,
        "community_access_code": community_access_code,
        "building": building,
        "unit": unit,
        "issue": issue
    }, flush=True)

    if not name or not phone or not community_access_code or not building or not unit or not issue:
            return jsonify({"error": "Name, phone, building, unit, and issue are required."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, property_name
            FROM properties
            WHERE UPPER(property_code) = %s
              AND status = 'active'
            LIMIT 1
        """, (community_access_code,))

        community = cur.fetchone()

        if community is None:
            return jsonify({
                "error": "Invalid community access code. Please check the code provided by your property management team."
            }), 400

        property_id = community[0]
        property_name = community[1]

        cur.execute("""
            SELECT phone_number
            FROM property_phone_numbers
            WHERE property_id = %s
            LIMIT 1
        """, (property_id,))

        phone_row = cur.fetchone()

        if phone_row:
            routing_phone = phone_row[0]
        else:
            routing_phone = None

        # Defer strict matching until client/community templates exist.
        property_id = None
        building_id = None
        unit_id = None
        resident_id = None

        issue_lower = issue.lower()

        outsourced_keywords = [
            "hvac",
            "heat",
            "heating",
            "no heat",
            "air conditioning",
            "ac",
            "a/c",
            "cooling",
            "refrigerator",
            "fridge",
            "appliance",
            "plumbing",
            "electrical",
            "breaker",
            "pest",
            "lock",
            "roof",
            "leaking roof",
            "roof leak"
        ]

        in_house_keywords = [
            "faucet",
            "sink",
            "toilet",
            "cabinet",
            "door",
            "light",
            "bulb",
            "hallway light",
            "window",
            "minor leak",
            "faucet leaking",
            "leaking faucet"
        ]

        if any(keyword in issue_lower for keyword in in_house_keywords):
            assigned_type = "In-House"
        elif any(keyword in issue_lower for keyword in outsourced_keywords):
            assigned_type = "Outsource"
        else:
            assigned_type = "In-House"

        print(f"Assigned Type: {assigned_type}")

        if assigned_type == "Outsource":
            print("Trigger Vendor Coordination Loop")
        else:
            print("Handled by In-House Maintenance")

        # ------------------------------------------------------------
        # NORTH STAR DATABASE-DRIVEN ROUTING
        # Web form route:
        # Community Access Code -> properties.property_code
        # ------------------------------------------------------------

        community_access_code = data.get("community_access_code", "").strip().upper()
        print(f"FORM ACCESS CODE RECEIVED: [{community_access_code}]", flush=True)
        cur.execute("""
            SELECT id, property_name
            FROM properties
            WHERE UPPER(property_code) = %s
              AND status = 'active'
            LIMIT 1
        """, (community_access_code,))

        property_result = cur.fetchone()

        if not property_result:
            cur.close()
            conn.close()
            return jsonify({
                "success": False,
                "message": "Invalid community access code. Please check the code provided by your property management team."
            }), 400

        property_id = property_result[0]
        property_name = property_result[1]

        print(f"Matched community access code {community_access_code} to {property_name}", flush=True)

        cur.execute("""
            SELECT create_maintenance_request_from_intake(
                %s::text,
                %s::text,
                %s::text,
                %s::text,
                %s::text,
                %s::text,
                %s::text
            )
        """, (
            property_id,
            building,
            unit,
            name,
            phone,
            issue,
            assigned_type
        ))

        result = cur.fetchone()

        if result is None:
            raise Exception("Function returned NULL")

        request_id = result[0]

        tenant_close_code = generate_tenant_close_code()
        dispatch_target = get_dispatch_target(assigned_type)

        assigned_name = dispatch_target["name"]
        technician_close_code = dispatch_target["close_code"]

        cur.execute("""
            UPDATE maintenance_requests_v2
            SET community_access_code = %s,
                assigned_type = %s,
                assigned_to = %s,
                tenant_close_code = %s,
                technician_close_code = %s,
                technician_confirmed = FALSE,
                tenant_confirmed = FALSE
            WHERE id = %s
        """, (
            community_access_code,
            assigned_type,
            assigned_name,
            tenant_close_code,
            technician_close_code,
            request_id
        ))

        conn.commit()

        update_ticket_status(
            request_id,
            "new",
            "REQUEST_SUBMITTED",
            f"{name} submitted request for Building {building}, Unit {unit}: {issue}",
            "Request Submitted"
        )

        update_ticket_status(
            request_id,
            "new",
            "WORK_ORDER_CREATED",
            f"Work order opened and assigned to {assigned_type}.",
            "Work Order Created"
        )

        Thread(
            target=run_post_submission_tasks,
            args=(request_id, name, phone, building, unit, issue, assigned_type, property_name, tenant_close_code, routing_phone),
            daemon=True
        ).start()

        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "message": "Maintenance request submitted.",
        }), 200


    except Exception as e:
        print("DATABASE ERROR:", repr(e))
        return jsonify({"error": f"Database insert failed: {str(e)}"}), 500


@app.route("/contact", methods=["POST"])
def contact():
    data = request.get_json(silent=True) or {}
    print("FORM DATA RECEIVED:", data, flush=True)
    first_name = str(data.get("first_name", "")).strip()
    last_name = str(data.get("last_name", "")).strip()
    email = str(data.get("email", "")).strip()
    phone = clean_phone(data.get("phone", ""))
    company_property = str(data.get("company_property", "")).strip()
    unit_count = str(data.get("unit_count", "")).strip()
    current_pms = str(data.get("current_pms", "")).strip()
    message = str(data.get("message", "")).strip()

    if not first_name or not last_name or not email:
        return jsonify({
            "error": "First name, last name, and email are required."
        }), 400
    log_activity(
        event_type="contact_received",
        client=company_property,
        property_name=company_property,
        action="contact_form",
        result="received"
    )
    ensure_csv_exists()

    lead_analysis = analyze_lead_with_openai(
        first_name=first_name,
        last_name=last_name,
        company_property=company_property,
        unit_count=unit_count,
        current_pms=current_pms,
        message=message
    )

    score = lead_analysis["score"]
    category = lead_analysis["category"]
    summary = lead_analysis["summary"]
    log_activity(
        event_type="lead_analyzed",
        client=company_property,
        property_name=company_property,
        action=f"score={score}; category={category}",
        result=category
    )
    log_activity(
        event_type="ai_response_generated",
        client=company_property,
        property_name=company_property,
        action="summary_created",
        result="category"
    )

    with open(LEADS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            first_name,
            last_name,
            email,
            phone,
            company_property,
            unit_count,
            current_pms,
            score,
            category,
            summary,
            message
        ])

    print(f"Lead Score: {score}")
    print(f"Lead Category: {category}")
    print(f"Lead Summary: {summary}")

    print("\n" + "=" * 70)
    print("NEW NORTH STAR INQUIRY")
    print(f"Name: {first_name} {last_name}")
    print(f"Email: {email}")
    print(f"Phone: {phone}")
    print(f"Company / Property: {company_property}")
    print(f"Units: {unit_count}")
    print(f"Current PMS: {current_pms}")
    print(f"Message: {message}")
    print("=" * 70 + "\n")

    sms_status = "not attempted"

    sms_body = f"""
    NEW NORTH STAR LEAD
    Score: {score}
    Category: {category}

    Name: {first_name} {last_name}
    Company: {company_property}
    Units: {unit_count}

    Summary:
    {summary}
    ---
    """

    try:
        print("=== SMS SIMULATION ===")
        print(f"To: {os.getenv('MY_PHONE_NUMBER')}")
        print(f"Message:\n{sms_body}")
        print("======================")

        sms_status = "simulated"

    except Exception as e:
        print("TWILIO ERROR:")
        print(str(e))
        sms_status = f"failed: {str(e)}"

    return jsonify({
        "success": True,
        "message": "Lead captured successfully.",
        "sms_status": sms_status
    })


@app.route("/api/client-properties", methods=["POST"])
def create_client_property():
    data = request.get_json(silent=True) or {}

    is_valid, error_message = validate_client_property_payload(data)
    if not is_valid:
        return jsonify({"success": False, "error": error_message}), 400

    record = {
        "id": str(uuid4()),
        "client_name": str(data.get("client_name", "")).strip(),
        "property_name": str(data.get("property_name", "")).strip(),
        "property_type": str(data.get("property_type", "")).strip(),
        "unit_count": int(data["unit_count"]) if data.get("unit_count") not in (None, "") else None,
        "building_count": int(data["building_count"]) if data.get("building_count") not in (None, "") else None,
        "current_pms": str(data.get("current_pms", "")).strip(),
        "property_notes": str(data.get("property_notes", "")).strip(),
        "sign_up_date": str(data.get("sign_up_date", "")).strip(),
        "service_begin_date": str(data.get("service_begin_date", "")).strip(),
        "service_end_date": str(data.get("service_end_date", "")).strip(),
        "payment_due_date": str(data.get("payment_due_date", "")).strip(),
        "service_enabled": bool(data.get("service_enabled", True)),
        "onboarding_status": str(data.get("onboarding_status", "in_progress")).strip() or "in_progress",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    client_properties.append(record)
    save_client_properties()
    log_activity(
        event_type="client_created",
        client=record.get("client_name", ""),
        property_name=record.get("property_name", ""),
        action="create",
        result="success"
    )
    return jsonify({
        "success": True,
        "message": "Client property created successfully.",
        "id": record["id"],
        "record": record
    }), 201


@app.route("/api/client-properties", methods=["GET"])
def list_client_properties():
    return jsonify({
        "count": len(client_properties),
        "clients": client_properties
    })
@app.route("/api/client-properties/<record_id>", methods=["PATCH"])
def update_client_property(record_id):
    data = request.get_json(silent=True) or {}

    record = next((r for r in client_properties if r["id"] == record_id), None)
    if not record:
        return jsonify({
            "success": False,
            "error": "Record not found."
        }), 404

    allowed_fields = {
        "client_name",
        "property_name",
        "property_type",
        "unit_count",
        "building_count",
        "current_pms",
        "property_notes",
        "sign_up_date",
        "service_begin_date",
        "service_end_date",
        "payment_due_date",
        "service_enabled",
        "onboarding_status"
    }

    for key, value in data.items():
        if key not in allowed_fields:
            continue

        if key in {"unit_count", "building_count"}:
            if value in (None, ""):
                record[key] = None
            else:
                try:
                    record[key] = int(value)
                except (TypeError, ValueError):
                    return jsonify({
                        "success": False,
                        "error": f"{key} must be an integer."
                    }), 400

        elif key == "service_enabled":
            if value not in (True, False):
                return jsonify({
                    "success": False,
                    "error": "service_enabled must be true or false."
                }), 400
            record[key] = value

        else:
            record[key] = str(value).strip()

    record["updated_at"] = now_iso()
    save_client_properties()

    return jsonify({
        "success": True,
        "message": "Client property updated successfully.",
        "record": record
    }), 200

@app.route("/dashboard")
@requires_auth
def dashboard():
    conn = get_db_connection()
    cur = conn.cursor()

    # Total requests
    cur.execute("SELECT COUNT(*) FROM maintenance_requests_v2")
    total_requests = cur.fetchone()[0]

    cur.execute("""
       SELECT
            mr.id,
            mr.resident_name,
            mr.resident_phone,
            COALESCE(p.property_name, 'Unassigned Community') AS property_name,
            mr.building_label,
            mr.unit_label,
            mr.issue_description,
            mr.status,
            mr.current_step,
            mr.assigned_type,
            mr.submitted_at
        FROM maintenance_requests_v2 mr
        LEFT JOIN properties p ON mr.property_id = p.id
        WHERE COALESCE(mr.dashboard_status, 'visible') = 'visible'
        ORDER BY mr.submitted_at DESC
        LIMIT 100               
    """)

    recent_requests = cur.fetchall()

    cur.close()
    conn.close()

    total_clients = total_requests
    enabled_clients = 0
    disabled_clients = 0
    total_units = 0

    activity_rows = ""

    for r in recent_requests:
        ticket_id = r[0]
        resident_name = r[1]
        resident_phone = (r[2] or "").strip()
        property_name = r[3]
        building = (r[4] or "").strip()
        unit = (r[5] or "").strip()
        issue = r[6]
        status = r[7]
        current_step = (r[8] or "").strip()
        print("DEBUG:", status, current_step)
        assigned_type = (r[9] or "").strip()
        submitted_at = r[10]

        ticket_number = generate_ticket_number(ticket_id, submitted_at)

        if building and unit:
            property_display = f"{property_name} • Building {building} • Unit {unit}"
        elif building:
            property_display = f"{property_name} • Building {building}"
        elif unit:
            property_display = f"{property_name} • Unit {unit}"
        else:
            property_display = (property_name or "Unassigned Community").strip()

        status_label = {
            "new": "New",
            "WORK_ORDER_CREATED": "Work Order Created",
            "TENANT_NOTIFIED": "Tenant Notified",
            "DISPATCHED": "Dispatched",
            "IN_PROGRESS": "In Progress",
            "COMPLETED": "Completed",
            "CLOSED": "Closed",
            "FAILED": "Failed",
        }.get(status, status or "Unknown")

        current_step_safe = html.escape(current_step or status_label, quote=True)

        id_safe = html.escape(str(ticket_id), quote=True)
        ticket_number_safe = html.escape(str(ticket_number), quote=True)
        clean_name = re.sub(r'[^a-zA-Z0-9]', '', resident_name)
        jitsi_room = f"NorthStar-{ticket_number}-{clean_name}"
        jitsi_room_safe = html.escape(str(jitsi_room), quote=True)
        video_cell = f'<a href="https://meet.jit.si/{jitsi_room_safe}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()" class="video-link">📹 Call</a>'
        # Ensure datetime object
        dt = datetime.fromisoformat(submitted_at) if isinstance(submitted_at, str) else submitted_at

        # Format: April 20, 2026, 6:15 PM
        formatted_time = dt.strftime("%B %d, %Y, %I:%M %p").replace(" 0", " ")

        submitted_at_safe = html.escape(formatted_time, quote=True)
        resident_name_safe = html.escape(str(resident_name), quote=True)
        resident_phone_safe = html.escape(str(resident_phone), quote=True) if resident_phone else ""
        phone_cell = f'<a href="tel:{resident_phone_safe}" onclick="event.stopPropagation()" class="phone-link">{resident_phone_safe}</a>' if resident_phone_safe else "—"
        property_display_safe = html.escape(str(property_display), quote=True)
        issue_safe = html.escape(str(issue), quote=True)
        status_label_safe = html.escape(str(status_label), quote=True)

        assigned_type_safe = html.escape(str(assigned_type), quote=True)

        activity_rows += f"""
        <tr 
            data-ticket-id="{id_safe}"
            data-ticket-number="{ticket_number_safe}"
            data-submitted-at="{submitted_at_safe}"
            data-event="Maintenance Request"
            data-resident-name="{resident_name_safe}"
            data-property-display="{property_display_safe}"
            data-issue="{issue_safe}"
            data-status="{status_label_safe}"
            onclick="openTicketModal(this)"
        >                       
            <td>{ticket_number_safe}</td>
            <td><span style="color:#94a3b8;">{submitted_at_safe}</span></td>
            <td>Maintenance Request</td>
            <td>{resident_name_safe}</td>
            <td>{phone_cell}</td>
            <td>{video_cell}</td>
            <td class="property-cell">{property_display_safe}</td>
            <td class="issue-cell">{issue_safe}</td>
            <td>{assigned_type_safe}</td>
            <td class="status-cell">{format_status_badge(status_label, current_step)}</td>
            <td>
                <button class="delete-btn" onclick="deleteTicket(event, '{id_safe}', '{ticket_number_safe}')">
                    Delete
                </button>
            </td>
        </tr>
        """

    if not activity_rows:
        activity_rows = """
            <tr>
                <td colspan="8">No recent activity yet.</td>
            </tr>
        """

    page_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>North Star Command</title>
        <style>
        html, body {{
            height: 100%;
            margin: 0;
        }}
        body {{
            font-family: Arial, sans-serif;
            background: #0b1220;
            color: #e5e7eb;
            overflow: hidden;
        }}
        .wrap {{
            height: 100vh;
            padding: 24px;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}
        .title {{
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 6px;
        }}
        .subtitle {{
            color: #94a3b8;
            margin-bottom: 18px;
            font-size: 14px;
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }}
        .card {{
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.25);
        }}
        .card-label {{
            font-size: 11px;
            color: #94a3b8;
            margin-bottom: 6px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        .card-value {{
            font-size: 18px;
            font-weight: 700;
        }}
        .panel {{
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 12px 16px;
            box-shadow: 0 4px 14px rgba(0,0,0,0.25);
        }}
        .panel.activity-panel {{
            flex: 1 1 auto;
            min-height: 0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        .table-container {{
            flex: 1 1 auto;
            min-height: 0;
            overflow-y: auto;
            overflow-x: auto;
            border: 1px solid #1f2937;
            border-radius: 8px;
        }}
        .ops-table {{
            min-width: 1200px;
            width: max-content;
            border-collapse: collapse;
        }}
        .ops-table th,
        .ops-table td {{
            padding: 8px 8px;
            border-bottom: 1px solid #1f2937;
            text-align: left;
            font-size: 12px;
            vertical-align: top;
            line-height: 1.2;
            white-space: nowrap;
        }}
        .ops-table th {{
            position: sticky;
            top: 0;
            background: #111827;
            z-index: 2;
            color: #93c5fd;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.05em;
        }}
        .ops-table td.issue-cell {{
            white-space: normal;
            overflow-wrap: anywhere;
            word-break: break-word;
            max-width: 420px;
        }}
        .ops-table td.property-cell {{
            white-space: normal;
            min-width: 140px;
            max-width: 260px;
        }}
        .ops-table td.status-cell {{
            min-width: 100px;
        }}
        .status-badge {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.2px;
            white-space: nowrap;
        }

        /* 🔴 RED: Work order created, not yet accepted */
        .status-created {
            background: #7f1d1d;
            color: #ffffff;
            border: 1px solid #ef4444;
        }

        /* 🟡 YELLOW: Technician/vendor assigned */
        .status-assigned {
            background: #854d0e;
            color: #ffffff;
            border: 1px solid #facc15;
        }

        /* 🟣 MAGENTA: AND gate has 1 and 0 */
        .status-pending-confirmation {
            background: #86198f;
            color: #ffffff;
            border: 1px solid #f0abfc;
        }

        /* 🟢 GREEN: AND gate has 1 and 1 */
        .status-closed {
            background: #166534;
            color: #ffffff;
            border: 1px solid #22c55e;
        }

        /* Fallback */
        .status-unknown {
            background: #374151;
            color: #ffffff;
            border: 1px solid #6b7280;
        }
        .enabled {{
            background: #052e16;
            color: #86efac;
            border: 1px solid #166534;
        }}
        .disabled {{
            background: #450a0a;
            color: #fca5a5;
            border: 1px solid #991b1b;
        }}
        .progress {{
            background: #3f2f0b;
            color: #fcd34d;
            border: 1px solid #a16207;
        }}
        button {{
            background: #1d4ed8;
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 12px;
            cursor: pointer;
            font-weight: 600;
        }}
        button.off {{
            background: #b91c1c;
        }}
        button:hover {{
            opacity: 0.92;
        }}
        .status-row {{
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
        }}
        .status-item strong {{
            display: block;
            margin-bottom: 6px;
        }}
        .phone-link {{
        color: #38bdf8;
        font-weight: 600;
        text-decoration: none;
        }}

        .phone-link:hover {{
        color: #0ea5e9;
        text-decoration: underline;
        }}
        .video-link {{
        color: #22c55e;
        font-weight: 600;
        text-decoration: none;
        }}

        .video-link:hover {{
        color: #16a34a;
        text-decoration: underline;
        }}
        </style>
    </head>
    <body>
        <div class="wrap">
            <div class="title">North Star Command</div>
            <div class="subtitle">Operational control center for client/property service management</div>

            <div class="panel">
                <h3 style="margin-top:0;">System Status</h3>
                <div class="status-row">
                    <div class="status-item">
                        <strong>AI Engine</strong>
                        <span class="badge enabled">Online</span>
                    </div>
                    <div class="status-item">
                        <strong>Lead Processor</strong>
                        <span class="badge enabled">Running</span>
                    </div>
                    <div class="status-item">
                        <strong>Activity Logger</strong>
                        <span class="badge enabled">Active</span>
                    </div>
                    <div class="status-item">
                        <strong>Data Store</strong>
                        <span class="badge enabled">Healthy</span>
                    </div>
                </div>
            </div>

            <div class="panel activity-panel">
                <h3 style="margin-top:0;">Recent Activity</h3>
                <div class="table-container">
                    <table class="ops-table">
                        <thead>
                            <tr>
                                <th>TICKET #</th>
                                <th>TIME</th>
                                <th>EVENT</th>
                                <th>CLIENT</th>
                                <th>PHONE</th>
                                <th>VIDEO</th>
                                <th>PROPERTY</th>
                                <th>ISSUE</th>
                                <th>ASSIGNED</th>
                                <th>STATUS</th>
                                <th>ACTION</th>                              
                            </tr>
                        </thead>
                        <tbody>
    """
    page_html += activity_rows
    page_html += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    <script>
            async function deleteTicket(event, ticketId, ticketNumber) {
    event.stopPropagation();

    const confirmed = confirm(`Remove ticket ${ticketNumber} from the dashboard?`);
    if (!confirmed) return;

    try {
        const response = await fetch(`/delete-ticket/${ticketId}`, {
            method: "POST",
            credentials: "same-origin"
        });

        if (!response.ok) {
            const text = await response.text();
            console.error("Delete failed:", response.status, text);
            alert(`Delete failed: ${response.status}`);
            return;
        }

        window.location.reload();
    } catch (error) {
        alert("Unable to remove ticket.");
        console.error(error);
    }
}
        </script>           
    </body>
    </html>
    """
    return page_html

@app.route("/api/client/dashboard", methods=["GET"])
def api_client_dashboard():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            mr.id,
            mr.resident_name,
            mr.resident_phone,
            COALESCE(p.property_name, 'Unassigned Community') AS property_name,
            mr.building_label,
            mr.unit_label,
            mr.issue_description,
            mr.status,
            mr.submitted_at,
            COALESCE(mr.assigned_type, 'In-House') AS assigned_type
        FROM maintenance_requests_v2 mr
        LEFT JOIN properties p
            ON mr.property_id = p.id
        WHERE COALESCE(mr.dashboard_status, 'visible') = 'visible'
        ORDER BY mr.submitted_at DESC
        LIMIT 100
    """)

    rows = cur.fetchall()

    tickets = []

    for row in rows:
        submitted_at = row[8]
        formatted_time = submitted_at.strftime("%B %d, %Y, %I:%M %p").replace(" 0", " ")

        tickets.append({
            "id": row[0],
            "ticket_number": f"NS-{submitted_at.strftime('%Y%m%d')}-{row[0]:06d}",
            "time": formatted_time,
            "event": "Maintenance Request",
            "client": row[1],
            "phone": row[2],
            "property": row[3],
            "building": row[4],
            "unit": row[5],
            "issue": row[6],
            "status": row[7],
            "assigned": row[9]
        })

    cur.close()
    conn.close()

    return jsonify(tickets)

@app.route("/api/client/work-orders", methods=["GET"])
def api_client_work_orders():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            mr.id,
            mr.resident_name,
            mr.resident_phone,
            COALESCE(p.property_name, 'Unassigned Community') AS property_name,
            mr.building_label,
            mr.unit_label,
            mr.issue_description,
            mr.status,
            mr.submitted_at,
            COALESCE(mr.assigned_type, 'In-House') AS assigned_type,
            COALESCE(latest_update.work_notes, '') AS work_notes
        FROM maintenance_requests_v2 mr
        LEFT JOIN properties p
            ON mr.property_id = p.id
        LEFT JOIN LATERAL (
            SELECT work_notes
            FROM work_order_updates wou
            WHERE wou.ticket_id = mr.id
            ORDER BY wou.id DESC
            LIMIT 1
        ) latest_update ON TRUE
        WHERE COALESCE(mr.dashboard_status, 'visible') = 'visible'
          AND COALESCE(mr.status, 'new') NOT IN ('Closed', 'closed')
        ORDER BY mr.submitted_at DESC
        LIMIT 100
    """)

    rows = cur.fetchall()
    work_orders = []

    for row in rows:
        submitted_at = row[8]

        work_orders.append({
            "id": row[0],
            "ticket_number": f"NS-{submitted_at.strftime('%Y%m%d')}-{row[0]:06d}",
            "resident_name": row[1],
            "resident_phone": row[2],
            "property": row[3],
            "building": row[4],
            "unit": row[5],
            "issue": row[6],
            "status": row[7],
            "submitted_at": submitted_at.strftime("%B %d, %Y, %I:%M %p").replace(" 0", " "),
            "assigned_type": row[9],
            "work_notes": row[10] or ""
        })

    cur.close()
    conn.close()

    return jsonify(work_orders)

@app.route("/api/client/work-orders/update", methods=["POST"])
def update_work_order():
    data = request.get_json()

    ticket_id = data.get("ticket_id")
    notes = data.get("notes", "").strip()

    if not ticket_id:
        return jsonify({"error": "ticket_id is required"}), 400

    if not notes:
        return jsonify({"error": "notes are required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # 1. Get the current lifecycle status first.
        cur.execute("""
            SELECT status
            FROM maintenance_requests_v2
            WHERE id = %s
        """, (ticket_id,))

        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Work order not found"}), 404

        current_status = row[0]

        # 2. Log the update without changing lifecycle status.
        cur.execute("""
            INSERT INTO work_order_updates (
                ticket_id,
                update_type,
                work_notes,
                status_before,
                status_after,
                updated_by
            )
            VALUES (
                %s,
                'resolution_note',
                %s,
                %s,
                %s,
                'system'
            )
        """, (
            ticket_id,
            notes,
            current_status,
            current_status
        ))

        # 3. Do NOT overwrite status.
        #    Save update metadata only.
        cur.execute("""
            UPDATE maintenance_requests_v2
            SET
                last_event = CASE
                    WHEN status = 'WORK_ORDER_CLOSED'
                        THEN 'WORK_ORDER_UPDATED_AFTER_CLOSE'
                    ELSE 'WORK_ORDER_UPDATED'
                END,
                status_updated_at = NOW()
            WHERE id = %s
        """, (ticket_id,))

        conn.commit()

        return jsonify({
            "success": True,
            "status_preserved": current_status
        })

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500

    finally:
        cur.close()
        conn.close()
@app.route("/delete-ticket/<int:ticket_id>", methods=["POST"])
@requires_auth
def delete_ticket(ticket_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE maintenance_requests_v2
        SET dashboard_status = 'hidden'
        WHERE id = %s
    """, (ticket_id,))

    conn.commit()
    cur.close()
    conn.close()

    return ("", 204)

@app.route("/toggle-service/<record_id>", methods=["POST"])
def toggle_service(record_id):
    for record in client_properties:
        if record.get("id") == record_id:
            current_state = bool(record.get("service_enabled", False))
            record["service_enabled"] = not current_state
            record["updated_at"] = now_iso()
            save_client_properties()
            log_activity(
                event_type="service_toggled",
                client=record.get("client_name", ""),
                property_name=record.get("property_name", ""),
                action="enabled" if record.get("service_enabled") else "disabled",
                result="success",
            )
            return redirect(url_for("dashboard"))

    return jsonify({
        "success": False,
        "error": "Record not found."
    }), 404

@app.route("/api/client/login", methods=["POST"])
def client_login():
    data = request.get_json()

    username = data.get("username")
    password = data.get("password")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, username, password_hash, client_name, community_access_code, role
        FROM client_users
        WHERE username = %s
        """,
        (username,)
    )

    user = cur.fetchone()

    if not user:
        return jsonify({"error": "Invalid username or password"}), 401

    user_id, db_username, password_hash, client_name, community_access_code, role = user

    if not bcrypt.checkpw(password.encode(), password_hash.encode()):
        return jsonify({"error": "Invalid username or password"}), 401

    token = jwt.encode(
        {
            "client_id": user_id,
            "community_access_code": community_access_code
        },
        SECRET_KEY,
        algorithm="HS256"
    )

    return jsonify({"token": token})
@app.route("/api/client/property", methods=["GET"])
def get_property():
    token = request.headers.get("Authorization")

    if not token:
        return jsonify({"error": "Missing token"}), 401

    try:
        token = token.split(" ")[1]
        decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return jsonify({"error": "Invalid token"}), 401

    # 🔑 THIS is coming from your login token
    access_code = decoded.get("community_access_code")

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            property_name,
            address_line1,
            city,
            state,
            postal_code,
            acreage,
            building_count,
            unit_count
        FROM properties
        WHERE property_code = %s
    """, (access_code,))   # <-- IMPORTANT: matches property_code

    row = cur.fetchone()

    cur.close()
    conn.close()

    if not row:
        return jsonify({"error": "No property found"}), 404

    (
        property_name,
        address,
        city,
        state,
        zip_code,
        acreage,
        building_count,
        unit_count
    ) = row

    return jsonify({
        "client_name": "Deer Creek Apartment Community",
        "property_name": property_name,
        "address": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "building_count": building_count,
        "unit_count": unit_count,
        "land_area": f"{acreage} acres of land",
        "layouts": "One- and two-bedroom open-concept floor plans",
        "stories": "Each building is 4 stories tall",
        "website": ""
    })
@app.route("/api/client/register", methods=["POST"])
def client_register():
    data = request.get_json()

    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    client_name = data.get("client_name", "").strip()
    community_access_code = data.get("community_access_code", "").strip().upper()

    if not username or not password or not client_name or not community_access_code:
        return jsonify({"error": "All fields are required"}), 400

    password_hash = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO client_users (
                username,
                password_hash,
                client_name,
                community_access_code,
                role
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            username,
            password_hash,
            client_name,
            community_access_code,
            "client"
        ))

        conn.commit()

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Username already exists"}), 409

    finally:
        cur.close()
        conn.close()

    return jsonify({"success": True, "message": "Client user created"}), 201

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)


