Pre-demo smoke test — run from the Mac terminal with the dps-poc server booted locally; every step must return the expected signal before demoing to Avalara.

```bash
# 0. Boot the server in one terminal
cd ~/STATYS/BigProject/dps-poc && python3 -m venv .venv 2>/dev/null; source .venv/bin/activate && pip install -q -r requirements.txt && python run.py &
SERVER_PID=$! && sleep 6 && echo "server pid=$SERVER_PID"

# 1. /v1/lists — both adapters must report status=live_csl and status=live_un with healthy entry_counts
curl -sS http://localhost:8000/v1/lists | python3 -m json.tool
# EXPECT: data_source="multi_source", total_entries>2500,
#         adapters[0].short_code="US_CSL" status="live_csl" entry_count~1800,
#         adapters[1].short_code="UN"     status="live_un"  entry_count~1000.
# FAIL modes:
#   UN entry_count=0 → XML XPath mismatch, see app/services/un_client.py _parse_xml
#   US_CSL status="sample" → Trade.gov fetch failed, check network/DNS
#   status="failed" on either → check server log for the underlying exception

# 2. Clean party — must PASS with zero matches
curl -sS -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name":"Widgets Global Inc.","party_type":"buyer"}' | python3 -m json.tool
# EXPECT: check_status="passed", requires_manual_review=false, matches=[]

# 3. Exact sanctioned entity — must FAIL with score 1.0
curl -sS -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name":"ACME Trading Company","country":"IR","party_type":"supplier"}' | python3 -m json.tool
# EXPECT: check_status="failed", matches[0].match_score=1.0, matches[0].source contains "SDN"

# 4. Near-miss typo — must land in MANUAL_REVIEW (score ~0.86)
curl -sS -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name":"Acm Trading","country":"IR"}' | python3 -m json.tool
# EXPECT: check_status="manual_review", 0.82 <= matches[0].match_score < 0.95

# 5. Natural-person screen against UN INDIVIDUAL records — must return a UN hit
# (pick a real UN-listed individual; Usama Bin Laden is on the 1267 list)
curl -sS -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name":"Usama bin Laden","party_type":"individual_buyer"}' | python3 -m json.tool
# EXPECT: check_status="failed" or "manual_review", matches[] includes at least one
#         entry where source contains "UN Security Council" — proves the UN adapter
#         is live and individual-name matching works across sources.

# 6. Batch screen — five parties, mixed B2B/B2C/C2C, aggregate flags set correctly
curl -sS -X POST http://localhost:8000/v1/check-batch \
  -H "Content-Type: application/json" \
  -d '{"parties":[
        {"name":"Widgets Global Inc.","party_type":"buyer"},
        {"name":"ACME Trading Company","party_type":"supplier"},
        {"name":"Usama bin Laden","party_type":"individual_buyer"},
        {"name":"Gazprombank","party_type":"ior"},
        {"name":"Shenzhen Widgets Factory","party_type":"manufacturer"}
      ]}' | python3 -m json.tool | head -80
# EXPECT: count=5, any_failed=true, any_manual_review=true,
#         results[1].check_status="failed", results[2] hits UN source.

# 7. Stop the server
kill $SERVER_PID 2>/dev/null; echo "done"
```
