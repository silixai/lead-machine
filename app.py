# app.py — Locksmith Lead Analyzer (Flask)
from twilio.twiml.voice_response import VoiceResponse
from twilio.request_validator import RequestValidator
import os, json, re
import smtplib, ssl
from email.message import EmailMessage
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI
from twilio.rest import Client as TwilioClient
from datetime import datetime
import threading
import certifi
import requests
from flask_cors import CORS

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# ------------ Config & Clients ------------
load_dotenv()
LEAD_API_KEY = os.getenv("LEAD_API_KEY")
app = Flask(__name__)
CORS(app, resources={r"/qualify-lead": {"origins": [
    "https://silixai.com",
    "http://127.0.0.1:5500",
    "http://localhost:5500"
]}})

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
PORT = int(os.getenv("PORT", "5000"))

AUTO_REPLY_SMS_ENABLED = os.getenv("AUTO_REPLY_SMS_ENABLED", "true").lower() == "true"
AUTO_REPLY_EMAIL_ENABLED = os.getenv("AUTO_REPLY_EMAIL_ENABLED", "true").lower() == "true"
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Your Locksmith")
CALLBACK_NUMBER = os.getenv("CALLBACK_NUMBER", "")

FOLLOWUP_REMINDER_ENABLED = os.getenv("FOLLOWUP_REMINDER_ENABLED", "true").lower() == "true"
FOLLOWUP_REMINDER_MINUTES = int(os.getenv("FOLLOWUP_REMINDER_MINUTES", "10"))

MISSED_CALL_CAPTURE_ENABLED = os.getenv("MISSED_CALL_CAPTURE_ENABLED", "true").lower() == "true"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
MISSED_CALL_SMS_ENABLED = os.getenv("MISSED_CALL_SMS_ENABLED", "true").lower() == "true"

def is_authorized_request(req):
    api_key = req.headers.get("X-API-Key")
    return api_key and api_key == LEAD_API_KEY

def is_valid_twilio_request(req):
    if not TWILIO_AUTH_TOKEN:
        return False

    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    signature = req.headers.get("X-Twilio-Signature", "")

    url = PUBLIC_BASE_URL + req.path
    params = req.form.to_dict()

    return validator.validate(url, params, signature)

# SMS flags
ENABLE_SMS = os.getenv("ENABLE_SMS", "false").lower() == "true"
AFTER_HOURS_SMS_ONLY = os.getenv("AFTER_HOURS_SMS_ONLY", "true").lower() == "true"

# Timezone & business hours (for after-hours logic)
TIMEZONE = os.getenv("TIMEZONE", "Australia/Sydney")
BUSINESS_START_HOUR = int(os.getenv("BUSINESS_START_HOUR", "8"))
BUSINESS_END_HOUR   = int(os.getenv("BUSINESS_END_HOUR", "18"))

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM        = os.getenv("TWILIO_FROM", "")
ONCALL_MOBILE      = os.getenv("ONCALL_MOBILE", "")

# --- Email / SMTP (optional; only used if configured) ---
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))  # 465 for SSL, 587 for STARTTLS
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "")
EMAIL_TO = os.getenv("EMAIL_TO", "")

# Optional tuning keywords to detect emergencies from analysis text
EMERGENCY_JOB_KEYS = [s.strip().lower() for s in os.getenv(
    "EMERGENCY_JOB_KEYS",
    "lockout, child inside, keys locked in car, roller door jam, urgent"
).split(",")]

_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
_twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if (ENABLE_SMS and TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN) else None

def now_tz():
    if ZoneInfo:
        return datetime.now(ZoneInfo(TIMEZONE))
    return datetime.now()

def is_after_hours(dt=None):
    dt = dt or now_tz()
    return not (BUSINESS_START_HOUR <= dt.hour < BUSINESS_END_HOUR)

# ------------ Helpers ------------
def parse_lead_quality(text: str) -> str:
    t = text.lower()
    if "hot" in t: return "Hot"
    if "warm" in t: return "Warm"
    if "cold" in t: return "Cold"
    return "Hot"  # optimistic default so urgent leads aren't missed

def extract_json(s: str):
    """
    Try to parse strict JSON from model output. If it fails, attempt to locate
    the first {...} block. Raise ValueError if none can be parsed.
    """
    s = s.strip()
    # direct attempt
    try:
        return json.loads(s)
    except Exception:
        pass
    # find first JSON object by braces
    m = re.search(r'\{.*\}', s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ValueError("Model did not return valid JSON")

def decide_should_sms(result: dict, lead_quality: str) -> bool:
    """
    SMS rules for locksmith:
    - If priority == 'Emergency' -> SMS immediately (always).
    - Else if after-hours and Hot -> SMS.
    - Else no SMS (unless AFTER_HOURS_SMS_ONLY is False and you want Hot during business hours too).
    """
    priority = (result.get("priority") or "").strip().lower()
    text_analysis = json.dumps(result).lower()  # cheap bag for keyword scan
    has_emergency_kw = any(k in text_analysis for k in EMERGENCY_JOB_KEYS)
    emergency = (priority == "emergency") or has_emergency_kw

    if emergency:
        return True
    if lead_quality == "Hot":
        if AFTER_HOURS_SMS_ONLY:
            return is_after_hours()
        else:
            return True
    return False

def send_sms(to: str, body: str):
    print(f"SEND_SMS check | ENABLE_SMS={ENABLE_SMS} | has_twilio={bool(_twilio)} | TWILIO_FROM={bool(TWILIO_FROM)} | to={to}")

    if not (_twilio and TWILIO_FROM and to):
        print("SEND_SMS skip: missing _twilio or TWILIO_FROM or destination")
        return

    try:
        msg = _twilio.messages.create(
            to=to,
            from_=TWILIO_FROM,
            body=body[:1000]
        )
        print(f"SEND_SMS success: sid={msg.sid}")
    except Exception as e:
        print(f"SEND_SMS error: {e}")

def make_prompt_locksmith(message: str) -> str:
    return f"""
You are a dispatcher assistant for an Australian locksmith service. 
Summarise and label the lead. Return STRICT JSON ONLY (no prose, no markdown) with this schema:

{{
  "lead_quality": "Hot | Warm | Cold",
  "priority": "Emergency | Same-day | Non-urgent",
  "job_type": "Residential lockout | Commercial lockout | Car lockout | Rekey | Lock replacement | Broken key extraction | Smart lock issue | Roller door jam",
  "location": {{"address": "optional string", "suburb": "optional string"}},
  "vehicle": {{"make": "optional string", "model": "optional string"}},
  "time_target": "Now | 60–120 minutes | Today | Later",
  "access_context": {{"child_inside": false, "pet_inside": false}},
  "notes": "one-sentence summary"
}}

Rules:
- If the message implies lockout, treat as "Emergency" unless timing is clearly flexible.
- If child or pet is inside, "Emergency".
- If it's a car lockout, fill vehicle make/model if mentioned.
- If no exact address, use suburb if present.
- Be conservative: if uncertain between Hot/Warm/Cold, prefer Warm.

Customer message:
\"\"\"{message}\"\"\"
"""

def analyze_with_openai(message: str) -> dict:
    if not _client:
        raise RuntimeError("OPENAI_API_KEY not configured")
    resp = _client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "You output ONLY valid JSON, nothing else."},
            {"role": "user", "content": make_prompt_locksmith(message)},
        ],
        temperature=0.2,
    )
    content = resp.choices[0].message.content.strip()
    data = extract_json(content)

    # safety defaults
    data["lead_quality"] = parse_lead_quality(json.dumps(data))
    if not data.get("priority"):
        data["priority"] = "Same-day"
    if not data.get("job_type"):
        data["job_type"] = "Locksmith job"
    if not data.get("time_target"):
        data["time_target"] = "Today"
    if "access_context" not in data:
        data["access_context"] = {"child_inside": False, "pet_inside": False}
    if "location" not in data:
        data["location"] = {"address": "", "suburb": ""}
    if "vehicle" not in data:
        data["vehicle"] = {"make": "", "model": ""}
    if not data.get("notes"):
        data["notes"] = "Locksmith request."

    return data

def send_email(subject: str, html_body: str, to_addr: str = None, reply_to: str = None):
    """
    Send an HTML email via Gmail SMTP.
    - Uses certifi CA bundle to avoid TLS verify errors on macOS.
    - Port 465 => SSL; Port 587 => STARTTLS.
    - If EMAIL_FROM is empty, falls back to SMTP_USERNAME.
    """
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USERNAME", "")
    pwd  = os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("EMAIL_FROM", "") or user
    to_addr = to_addr or os.getenv("EMAIL_TO", "")

    if not (host and port and user and pwd and from_addr and to_addr):
        print("EMAIL skip: missing SMTP/EMAIL envs")
        return

    msg = EmailMessage()
    msg["Subject"] = subject or "Lead"
    msg["From"] = from_addr
    msg["To"] = to_addr
    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content("This email contains HTML. Please enable HTML view.")
    msg.add_alternative(html_body or "", subtype="html")

    context = ssl.create_default_context(cafile=certifi.where())

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as s:
                s.login(user, pwd)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                s.starttls(context=context)
                s.login(user, pwd)
                s.send_message(msg)
        print(f"EMAIL sent to {to_addr}")
    except Exception as e:
        print("EMAIL error:", repr(e))

def send_slack_card(result: dict, name: str, mobile: str, email_addr: str, source: str, message_txt: str):
    url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not url:
        print("SLACK skip: SLACK_WEBHOOK_URL not set")
        return

    pr = (result.get("priority","") or "").upper()
    lq = (result.get("lead_quality","") or "").upper()
    jt = result.get("job_type","") or "Lead"
    loc = result.get("location", {}) or {}
    addr = (loc.get("address") or "").strip()
    suburb = (loc.get("suburb") or "").strip()
    tt = result.get("time_target","") or "-"

    fields = [
        {"title":"Priority", "value": pr or "-", "short": True},
        {"title":"Quality", "value": lq or "-", "short": True},
        {"title":"When", "value": tt or "-", "short": True},
        {"title":"Location", "value": (addr or suburb or "-"), "short": True},
        {"title":"Contact", "value": f"{name or '-'} | {mobile or '-'} | {email_addr or '-'}", "short": False},
        {"title":"Source", "value": source or "-", "short": True},
    ]
    payload = {
        "attachments": [{
            "fallback": f"[{pr} • {lq}] {jt}",
            "color": "#439FE0",
            "title": jt,
            "text": message_txt or "",
            "fields": fields
        }]
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        print("SLACK status:", r.status_code)
    except Exception as e:
        print("SLACK error:", e)

def send_customer_sms(to_number, is_emergency=False):
    print(f"CUSTOMER_SMS check | AUTO_REPLY_SMS_ENABLED={AUTO_REPLY_SMS_ENABLED} | has_twilio={bool(_twilio)} | TWILIO_FROM={bool(TWILIO_FROM)} | to={to_number}")

    if not AUTO_REPLY_SMS_ENABLED or not to_number or not _twilio or not TWILIO_FROM:
        print("CUSTOMER_SMS skip: disabled or missing destination/_twilio/TWILIO_FROM")
        return False

    business = BUSINESS_NAME.strip() or "Your Locksmith"
    callback = CALLBACK_NUMBER.strip()

    if is_emergency:
        body = f"Thanks for contacting {business}. We received your urgent request and will contact you shortly."
    else:
        body = f"Thanks for contacting {business}. We received your request and will contact you shortly."

    if callback:
        body += f" If urgent, call {callback}."

    try:
        msg = _twilio.messages.create(
            from_=TWILIO_FROM,
            to=to_number,
            body=body
        )
        print(f"CUSTOMER_SMS success: sid={msg.sid}")
        return True
    except Exception as e:
        print(f"CUSTOMER_SMS error: {e}")
        return False        

def send_customer_email(to_email):
    if not to_email or not SMTP_USERNAME or not SMTP_PASSWORD:
        return False

    subject = f"Thanks for contacting {BUSINESS_NAME}"

    body = (
        f"Hi,\n\n"
        f"We received your request and will contact you shortly.\n\n"
        f"This is a demo of how your business can automatically capture and respond to leads in real-time.\n\n"
        f"If you'd like to see how this system can be set up for your business, call {CALLBACK_NUMBER}.\n\n"
        f"— {BUSINESS_NAME}"
    )

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = to_email
        msg.set_content(body)

        context = ssl.create_default_context(cafile=certifi.where())

        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)

        return True

    except Exception as e:
        print("CUSTOMER EMAIL error:", e)
        return False

def schedule_followup_reminder(name, mobile, result):
    if not FOLLOWUP_REMINDER_ENABLED:
        return

    def _reminder_job():
        try:
            priority = (result.get("priority") or "").upper()
            job_type = result.get("job_type") or "Lead"
            loc = result.get("location", {}) or {}
            suburb = (loc.get("suburb") or "").strip()
            addr = (loc.get("address") or "").strip()
            where = addr or suburb or "No location"

            reminder_body = (
                f"Reminder: unanswered lead — {job_type} | {where} | "
                f"{name or '-'} | {mobile or '-'} | {priority or 'NO PRIORITY'}"
            )

            if ENABLE_SMS and ONCALL_MOBILE:
                send_sms(ONCALL_MOBILE, reminder_body)

            send_slack_card(
                {
                    "priority": "Reminder",
                    "lead_quality": result.get("lead_quality", "Hot"),
                    "job_type": f"Follow-up: {job_type}",
                    "location": result.get("location", {}) or {},
                    "time_target": f"{FOLLOWUP_REMINDER_MINUTES} min follow-up",
                },
                name,
                mobile,
                "",
                "Lead Follow-up Reminder",
                reminder_body
            )

        except Exception as e:
            print(f"FOLLOW-UP reminder error: {e}")

    timer = threading.Timer(FOLLOWUP_REMINDER_MINUTES * 60, _reminder_job)
    timer.daemon = True
    timer.start()

# ------------ Routes ------------
@app.route("/qualify-lead", methods=["POST"])
def qualify_lead():

    if not is_authorized_request(request):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        payload = request.get_json(silent=True) or {}

        # pull fields used in email body
        contact = payload.get("contact") or {}
        meta = payload.get("meta") or {}

        name = (payload.get("name") or contact.get("name") or "").strip()
        mobile = (
            payload.get("mobile")
            or payload.get("phone")
            or contact.get("mobile")
            or contact.get("phone")
            or ""
        ).strip()
        email_addr = (payload.get("email") or contact.get("email") or "").strip()
        source = (payload.get("source") or "Locksmith Website").strip()

        message = (payload.get("message") or "").strip()
        if not message:
            parts = [
                meta.get("job_type") or "",
                meta.get("address") or meta.get("suburb") or "",
                f"Time: {meta.get('time_target')}" if meta.get("time_target") else "",
                "Child inside" if meta.get("child_inside") else "",
                "Pet inside" if meta.get("pet_inside") else "",
                meta.get("notes") or "",
            ]
            message = ". ".join([p for p in parts if p]).strip()

        if not message:
            return jsonify({"status": "error", "message": "No usable message provided"}), 400

        # analyze
        result = analyze_with_openai(message)
        lead_quality = result.get("lead_quality", "Hot")
        is_emergency = str(result.get("priority", "")).lower() == "emergency"
        send_customer_sms(mobile, is_emergency=is_emergency)

        if AUTO_REPLY_EMAIL_ENABLED and email_addr:
            send_customer_email(email_addr)

        schedule_followup_reminder(name, mobile, result)

        # --- ALWAYS send email (independent of SMS flags) ---
        try:
            priority = (result.get("priority") or "").upper()
            job_type = result.get("job_type", "")
            loc = result.get("location", {}) or {}
            addr = (loc.get("address") or "").strip()
            suburb = (loc.get("suburb") or "").strip()
            tt = result.get("time_target", "")
            maps_link = f"https://maps.google.com/?q={(addr+' '+suburb).strip().replace(' ', '+')}" if (addr or suburb) else ""
            subject = f"[{priority} • {lead_quality.upper()}] {job_type}".strip() or "Lead"
            html = f"""
              <h3>{job_type or 'Lead'}</h3>
              <p><b>Priority:</b> {priority or '-'} &nbsp; <b>Quality:</b> {lead_quality or '-'}</p>
              <p><b>When:</b> {tt or '-'}</p>
              <p><b>Address:</b> {addr or '-'} {suburb or ''} {(" | <a href='"+maps_link+"'>Open Maps</a>") if maps_link else ""}</p>
              <p><b>Contact:</b> {name or '-'} &nbsp; {mobile or '-'} &nbsp; {email_addr or '-'}</p>
              <p><b>Source:</b> {source}</p>
              <p><b>Message:</b><br>{message}</p>
            """
            send_email(subject, html, reply_to=(email_addr or None))
            send_slack_card(result, name, mobile, email_addr, source, message)

        except Exception as _e:
            print("EMAIL compose/send block error:", _e)

        # --- SMS (optional) ---
        if ENABLE_SMS and decide_should_sms(result, lead_quality):
            suburb_sms = result.get("location", {}).get("suburb") or ""
            addr_sms = result.get("location", {}).get("address") or suburb_sms
            maps_link_sms = f"https://maps.google.com/?q={addr_sms.replace(' ', '+')}" if addr_sms else ""
            sms_body = (
                f"[{result.get('priority','').upper()} • {lead_quality.upper()}] {result.get('job_type','')}"
                f" — {addr_sms or 'No address'} ({result.get('time_target','')})\n"
                f"Call {mobile or '-'}{' • Map: ' + maps_link_sms if maps_link_sms else ''}"
            )
            send_sms(ONCALL_MOBILE, sms_body)

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/voice", methods=["POST"])
def voice_webhook():
    if not MISSED_CALL_CAPTURE_ENABLED:
        return ("", 204)

    if not is_valid_twilio_request(request):
        return ("Unauthorized", 403)

    resp = VoiceResponse()

    resp.dial(
        ONCALL_MOBILE,
        timeout=20,
        action=f"{PUBLIC_BASE_URL}/voice-status",
        method="POST"
    )

    return str(resp)

@app.route("/voice-status", methods=["POST"])
def voice_status():
    if not MISSED_CALL_CAPTURE_ENABLED:
        return ("", 204)

    if not is_valid_twilio_request(request):
        return ("Unauthorized", 403)

    call_status = request.form.get("DialCallStatus", "")
    caller = request.form.get("From", "")

    if call_status in ["no-answer", "busy", "failed"]:
        message = "Sorry we missed your call. Reply here or call again and we'll assist shortly."

        if MISSED_CALL_SMS_ENABLED and ENABLE_SMS and _twilio and TWILIO_FROM and caller:
            try:
                _twilio.messages.create(
                    from_=TWILIO_FROM,
                    to=caller,
                    body=message
                )
            except Exception as e:
                print(f"MISSED CALL SMS error: {e}")

        send_slack_card(
            {
                "priority": "Missed Call",
                "lead_quality": "Hot",
                "job_type": "Missed Call",
                "location": {},
                "time_target": "Immediate follow-up",
            },
            "",
            caller,
            "",
            "Missed Call Alert",
            f"Customer called but no answer: {caller}"
        )

    return ("", 204)

  
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
