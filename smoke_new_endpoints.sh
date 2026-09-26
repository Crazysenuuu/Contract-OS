#!/bin/bash
# Smoke test for new spec-gap endpoints (§3.17-3.21 build-out)
# Run while backend is up on :8000 with dev DB at head c3d4e5f6a7b9
set -u
BASE="http://localhost:8000/api/v1"
PY="$(dirname "$0")/backend/venv/bin/python"

pass=0; fail=0
check() { # check <name> <actual> <expected>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "PASS $1 ($2)"; else fail=$((fail+1)); echo "FAIL $1 (expected $3, got $2)"; fi
}

# ---- Login ----
TOKEN=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"test@example.com","password":"password123"}' | "$PY" -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || { echo "FATAL: login failed"; exit 1; }
AUTH="Authorization: Bearer $TOKEN"
echo "logged in OK"

# ---- GET collections (read path on new tables) ----
check "GET /automation/rules"       "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/automation/rules)" 200
check "GET /forecast/runs"          "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/forecast/runs)" 200
check "GET /scenarios"              "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/scenarios)" 200
check "GET /action-items"           "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/action-items)" 200
check "GET /external-policy"        "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/external-policy)" 200
check "GET /parties/contacts"       "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/parties/contacts)" 200
check "GET /parties/search"         "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" "$BASE/parties/search?q=a")" 200
check "GET /ingestion/review-queue" "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/ingestion/review-queue)" 200
check "GET /ingestion/summary"      "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/ingestion/summary)" 200

# ---- POST automation rule (write path on new table) ----
CODE=$(curl -s -o /tmp/rule.json -w '%{http_code}' -X POST -H "$AUTH" -H 'Content-Type: application/json' $BASE/automation/rules \
  -d '{"name":"smoke-rule","trigger_event":"agreement.executed","conditions":{"all":[]},"action_key":"CREATE_TASK","action_config":{"title":"smoke test task"}}')
check "POST /automation/rules" "$CODE" 200
RULE_ID=$("$PY" -c "import json;d=json.load(open('/tmp/rule.json'));print(d.get('id',''))" 2>/dev/null)
if [ -n "$RULE_ID" ]; then
  check "PATCH /automation/rules/{id}" "$(curl -s -o /dev/null -w '%{http_code}' -X PATCH -H "$AUTH" -H 'Content-Type: application/json' $BASE/automation/rules/$RULE_ID -d '{"name":"smoke-rule","trigger_event":"agreement.executed","conditions":{"all":[]},"action_key":"CREATE_TASK","action_config":{"title":"smoke test task v2"}}')" 200
  check "DELETE /automation/rules/{id}" "$(curl -s -o /dev/null -w '%{http_code}' -X DELETE -H "$AUTH" $BASE/automation/rules/$RULE_ID)" 200
else
  fail=$((fail+1)); echo "FAIL rule lifecycle (no id in response)"; cat /tmp/rule.json; echo
fi

# ---- POST automation dry-run (pure logic, no writes) ----
check "POST /automation/rules/dry-run" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -H 'Content-Type: application/json' $BASE/automation/rules/dry-run \
  -d '{"event_type":"agreement.executed","conditions":{"all":[]},"action_key":"CREATE_TASK","action_config":{"title":"x"},"context":{}}')" 200

# ---- POST forecast run (write path, schema: metric_key/horizon_days/model_type) ----
check "POST /forecast/runs" "$(curl -s -o /tmp/run.json -w '%{http_code}' -X POST -H "$AUTH" -H 'Content-Type: application/json' $BASE/forecast/runs \
  -d '{"metric_key":"lifecycle.renewals_due_30d","horizon_days":30,"model_type":"trend"}')" 200
RUN_ID=$("$PY" -c "import json;d=json.load(open('/tmp/run.json'));print(d.get('id',''))" 2>/dev/null)
if [ -n "$RUN_ID" ]; then
  check "GET /forecast/runs/{id}" "$(curl -s -o /dev/null -w '%{http_code}' -H "$AUTH" $BASE/forecast/runs/$RUN_ID)" 200
else
  fail=$((fail+1)); echo "FAIL GET /forecast/runs/{id} (no id in POST response)"; cat /tmp/run.json; echo
fi

# ---- POST scenario (write path) ----
check "POST /scenarios" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -H 'Content-Type: application/json' $BASE/scenarios \
  -d '{"name":"smoke-scenario","variables":{"volume":{"metric_key":"lifecycle.renewals_due_30d","transformation":"scale","params":{"factor":1.1}}}}')" 200

# ---- POST party duplicate check (query params per endpoint signature) ----
check "POST /parties/duplicates/check" "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" "$BASE/parties/duplicates/check?legal_name=Smoke%20Co&email=smoke@example.com")" 200

# ---- Authz: no token -> 401 ----
check "GET /action-items no-auth" "$(curl -s -o /dev/null -w '%{http_code}' $BASE/action-items)" 401

echo "----"
echo "RESULT: $pass passed, $fail failed"
exit $fail
