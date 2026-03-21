# Lead_Machine_Locksmith

Locksmith-tuned version of your lead analyzer. Runs the same way as your plumber build (Flask + ngrok + Apps Script or site form), but returns **structured JSON** suitable for SMS-first emergencies.

## Quick start (local dev)
1. Unzip this folder and move it to `~/Downloads/Lead_Machine_Locksmith` on your Mac.
2. In Terminal:
   ```bash
   cd ~/Downloads/Lead_Machine_Locksmith
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env    # fill in keys
   python app.py
   ```
3. New Terminal:
   ```bash
   ngrok http 5000
   ```
   Copy the https URL and use it in your form/Apps Script as `ANALYZER_URL` ending with `/qualify-lead`.

## API
**POST** `/qualify-lead`

### Example request
```json
{
  "message": "Residential lockout at 12 Smith St, Parramatta. Child inside. Need help now.",
  "contact": {
    "name": "Jane Doe",
    "phone": "+61 4xx xxx xxx"
  },
  "meta": {
    "suburb": "Parramatta",
    "address": "12 Smith St",
    "time_target": "Now"
  },
  "source": "website"
}
```

### Example response
```json
{
  "status": "success",
  "lead_quality": "Hot",
  "priority": "Emergency",
  "job_type": "Residential lockout",
  "location": {"address": "12 Smith St", "suburb": "Parramatta"},
  "vehicle": {"make": "", "model": ""},
  "time_target": "Now",
  "access_context": {"child_inside": true, "pet_inside": false},
  "notes": "Single-sentence summary."
}
```

## Slack Actions (Apps Script)
Use `apps_script_locksmith.gs` included here. It posts a mobile-friendly Slack message with **tap-to-call** and **Open Maps**.

## Website Form (optional demo)
Open `website_form_example.html` and point `API_URL` to your ngrok URL for quick testing without Google Forms.

## SMS rules
- If `priority == Emergency` → SMS immediately (24/7).
- Else if after-hours **and** `lead_quality == Hot` → SMS.
- Otherwise, no SMS. Flip `.env` flags to change behaviour.
