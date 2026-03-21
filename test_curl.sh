#!/bin/bash
PORT=${PORT:-5000}
URL=${1:-"http://127.0.0.1:5000/qualify-lead"}

echo "Smoke: Emergency lockout"
curl -s -X POST "$URL" \
 -H "Content-Type: application/json" \
 -H "X-API-Key: 7f9a3c2e9d8b4f6a1c3e5d7b9a0f2c4d" \
 -d @sample_payload.json

echo
echo "Warm scenario"
cat > /tmp/warm.json <<'JSON'
{
  "message": "Rekey request in Burwood, flexible later today.",
  "contact": {"name":"Tom","phone":"+61433009521"},
  "source": "curl"
}
JSON

curl -s -X POST "$URL" \
 -H "Content-Type: application/json" \
 -H "X-API-Key: 7f9a3c2e9d8b4f6a1c3e5d7b9a0f2c4d" \
 -d @/tmp/warm.json

echo
echo "Bad payload (expect 400)"
curl -i -s -X POST "$URL" \
 -H "Content-Type: application/json" \
 -H "X-API-Key: 7f9a3c2e9d8b4f6a1c3e5d7b9a0f2c4d" \
 -d '{}'