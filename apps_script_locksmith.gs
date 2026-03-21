/**
 * Locksmith Google Apps Script — posts form submissions to your analyzer, then to Slack, and emails the customer.
 * - Set ANALYZER_URL to your ngrok URL + "/qualify-lead"
 * - Set SLACK_WEBHOOK_URL
 */
const ANALYZER_URL = "https://YOUR-NGROK.ngrok-free.app/qualify-lead";
const SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/REPLACE/ME";
const SENDER = "no-reply@yourdomain.com";

function onFormSubmit(e) {
  const r = e.values;              // adjust indices to your form's columns
  // Example column mapping (change to your sheet):
  // Timestamp, Name, Phone, Address, Suburb, Job Type, Time Target, Child Inside, Pet Inside, Vehicle Make, Vehicle Model, Notes, Photo URL
  const name   = r[1] || "";
  const phone  = r[2] || "";
  const addr   = r[3] || "";
  const suburb = r[4] || "";
  const job    = r[5] || "";
  const timeT  = r[6] || "";
  const child  = (r[7] || "").toString().toLowerCase().startsWith("y");
  const pet    = (r[8] || "").toString().toLowerCase().startsWith("y");
  const make   = r[9] || "";
  const model  = r[10] || "";
  const notes  = r[11] || "";
  const photo  = r[12] || "";

  const composed = [
    job || "Locksmith job",
    addr || suburb || "",
    timeT ? `Time: ${timeT}` : "",
    child ? "Child inside" : "",
    pet ? "Pet inside" : "",
    notes || ""
  ].filter(Boolean).join(". ");

  const payload = {
    message: composed,
    contact: { name, phone },
    meta: {
      address: addr, suburb,
      job_type: job, time_target: timeT,
      child_inside: child, pet_inside: pet,
      vehicle: { make, model },
      photo_url: photo
    },
    source: "google_form"
  };

  const analysis = postJson(ANALYZER_URL, payload);

  // Slack alert — tap-to-call + maps
  const telLink  = `<tel:${phone}|Call ${phone}>`;
  const mapQ     = encodeURIComponent(addr || suburb || "");
  const mapsLink = mapQ ? `<https://maps.google.com/?q=${mapQ}|Open Maps>` : "";
  const title    = `*${analysis.priority.toUpperCase()} · ${analysis.lead_quality.toUpperCase()} · ${analysis.job_type}*`;
  const line1    = `${addr || suburb || "No address"} — *${analysis.time_target}*`;
  const flags    = (analysis.access_context?.child_inside ? "Child inside " : "")
                 + (analysis.access_context?.pet_inside ? "Pet inside" : "");

  const blocks = [
    { type: "section", text: { type: "mrkdwn", text: `${title}\n${line1}\n${telLink} ${mapsLink}` } },
    { type: "context", elements: [{ type: "mrkdwn", text: `${flags || ""}`.trim() || analysis.notes }] }
  ];

  UrlFetchApp.fetch(SLACK_WEBHOOK_URL, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ text: "New locksmith lead", mrkdwn: true, blocks }),
    muteHttpExceptions: true
  });

  // Auto-reply to customer (optional)
  if (phone || name) {
    const email = Session.getActiveUser().getEmail(); // fallback; replace with a form field if you collect email
  }
  // If you collect customer email, send a receipt:
  // MailApp.sendEmail(customerEmail, "We've received your locksmith request", "Thanks, we're on it. We'll call you shortly.");
}

function postJson(url, data) {
  const res = UrlFetchApp.fetch(url, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(data),
    muteHttpExceptions: true
  });
  const code = res.getResponseCode();
  const text = res.getContentText();
  if (code >= 200 && code < 300) {
    return JSON.parse(text);
  }
  throw new Error(`Analyzer error ${code}: ${text}`);
}
