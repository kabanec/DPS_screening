# DPS POC — Denied Party Screening API

A standalone proof-of-concept Denied Party Screening (DPS) service built in
FastAPI. It screens party names (supplier, buyer, manufacturer, Importer of
Record, ship-to) against the US government's **Consolidated Screening List
(CSL)** and returns a three-tier decision: **passed / manual_review / failed**.

Prepared as a demo for Avalara.

---

## 1. What this service does

Every cross-border shipment has to be screened against denied-party lists
before it ships. Getting this wrong is not a paperwork problem — it's a
federal violation (OFAC, BIS, DDTC all levy penalties in the hundreds of
thousands of dollars per occurrence).

This API answers one question per party:

> *Is this name (optionally + country) on any US denied-party list, and at
> what confidence?*

It accepts a single party or a batch of up to 100, runs fuzzy name matching
over every entry in the CSL, and returns:

- A classification (`passed` / `manual_review` / `failed`)
- Every match at or above a configurable minimum score
- The source list, list type, country, and sanctions programs for each match
- An authoritative URL back to the source listing

The match score threshold is tunable via environment variables — a
production deployment can be tightened to reduce manual review load or
loosened to catch more edge cases.

---

## 2. Data sources

The service pulls the official US government Consolidated Screening List
from **Trade.gov**, which aggregates lists from three agencies:

| Agency | List | Short code |
|---|---|---|
| Treasury / OFAC | Specially Designated Nationals (SDN) | SDN |
| Treasury / OFAC | Sectoral Sanctions Identifications (SSI) | SSI |
| Treasury / OFAC | Foreign Sanctions Evaders | FSE |
| Treasury / OFAC | Non-SDN Palestinian Legislative Council | NS-PLC |
| Treasury / OFAC | Non-SDN Menu-Based Sanctions | NS-MBS |
| Commerce / BIS | Entity List | EL |
| Commerce / BIS | Denied Persons List | DPL |
| Commerce / BIS | Unverified List | UVL |
| Commerce / BIS | Military End User List | MEU |
| State / DDTC | AECA Debarred | DTC |
| State / ISN | Nonproliferation Sanctions | ISN |
| Commerce / ITA | Israeli Boycott Requester List | — |

**Feed URL:** `https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json`
(public, no API key required).

The feed is fetched once at application startup and kept fully in memory
(~200K JSON payload, low single-digit MB after parsing). A background
refresh job can be wired up via the CSL\_REFRESH\_SECONDS setting — it's
included in config but intentionally left inert in this POC to keep the
demo deterministic.

A bundled sample dataset (`app/data/sample_csl.json`, 12 representative
entries) ships with the project and is used automatically if the live
fetch fails — so the API always stays responsive, even fully offline.

---

## 3. Architecture

```
                ┌─────────────────────────────────────┐
                │         Trade.gov CSL feed          │
                │  (public JSON, ~1–2K party entries) │
                └─────────────────┬───────────────────┘
                                  │ fetched once at startup
                                  ▼
┌───────────────────────────────────────────────────────────────┐
│                        FastAPI application                    │
│                                                               │
│   ┌───────────────┐   ┌───────────────┐   ┌───────────────┐   │
│   │  CSLClient    │──▶│   Matcher     │──▶│  DPSService   │   │
│   │  (in-memory   │   │  (rapidfuzz   │   │  (classify:   │   │
│   │   index)      │   │   token-set   │   │   passed /    │   │
│   │               │   │   ratio)      │   │   manual_rev /│   │
│   │               │   │               │   │   failed)     │   │
│   └───────────────┘   └───────────────┘   └───────┬───────┘   │
│                                                   │           │
│   ┌────────────────────────────────────┐          │           │
│   │  Routers:                          │◀─────────┘           │
│   │   POST /v1/check-party             │                      │
│   │   POST /v1/check-batch             │                      │
│   │   GET  /v1/lists                   │                      │
│   │   GET  /health                     │                      │
│   │   GET  /docs   /redoc              │                      │
│   └────────────────────────────────────┘                      │
└───────────────────────────────────────────────────────────────┘
```

Request flow for a single party check:

1. Client POSTs a name (+ optional country / address / party_type).
2. `DPSService.check_party()` calls `find_matches()` in the matcher.
3. The matcher normalizes the query name (Unicode-fold, lowercase, drop
   punctuation, strip legal suffixes like *LLC, Inc., Ltd., GmbH*) and
   scores it against every canonical name and alias in the CSL using
   `rapidfuzz.fuzz.token_set_ratio`.
4. Matches at or above `MATCH_MIN_SCORE` (default 0.82) are returned,
   sorted by score.
5. The service classifies the top score into `passed` / `manual_review` /
   `failed` and returns a structured response.

No data is written anywhere — the service is stateless by design, so
scaling horizontally is trivial.

---

## 4. Quickstart

```bash
# 1. Clone or copy into your PyCharm workspace, then:
cd dps-poc
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Copy env template and tweak if you like
cp .env.example .env

# 3. Run
python run.py
```

The server starts on `http://localhost:8000`.

Open **`http://localhost:8000/docs`** for interactive Swagger UI — every
endpoint is live-testable from the browser. `http://localhost:8000/redoc`
serves the same OpenAPI 3.1 spec in ReDoc format, and
`http://localhost:8000/openapi.json` gives you the raw machine-readable
spec for client generation.

---

## 5. API reference

### 5.1 `POST /v1/check-party`

Screen a single party.

**Request**

```json
{
  "name": "ACME Trading Company",
  "country": "IR",
  "address": "Tehran, Iran",
  "party_type": "supplier"
}
```

Only `name` is required. `country`, `address`, and `party_type` are
optional — `party_type` is echoed back in the response so callers can
correlate results to invoice roles (supplier / buyer / manufacturer /
IOR / ship-to).

**Response — 200 OK**

```json
{
  "party_name": "ACME Trading Company",
  "party_type": "supplier",
  "check_status": "failed",
  "requires_manual_review": true,
  "matches": [
    {
      "matched_name": "ACME Trading Company",
      "match_score": 1.0,
      "source": "Specially Designated Nationals (SDN) - Treasury Department",
      "list_type": "Entity",
      "country": "IR",
      "programs": "IRAN, SDGT",
      "source_info_url": "https://sanctionssearch.ofac.treas.gov/Details.aspx?id=00000"
    }
  ],
  "screened_at": "2026-04-19T14:32:11.402918+00:00",
  "data_source": "live_csl"
}
```

**`check_status` semantics**

| Status | Top score | Meaning |
|---|---|---|
| `passed` | below 0.82 | No meaningful match — safe to proceed. |
| `manual_review` | 0.82 ≤ score < 0.95 | Partial match — human review required before shipping. |
| `failed` | ≥ 0.95 | High-confidence match — treat as **DO NOT SHIP**. |

`requires_manual_review` is a convenience boolean: `true` for both
`manual_review` and `failed`.

**curl**

```bash
curl -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name": "ACME Trading Company", "country": "IR", "party_type": "supplier"}'
```

### 5.2 `POST /v1/check-batch`

Screen up to 100 parties in a single request — useful for screening every
party on a customs invoice in one round trip.

**Request**

```json
{
  "parties": [
    {"name": "Widgets Global Inc.",      "party_type": "buyer"},
    {"name": "ACME Trading Company",     "party_type": "supplier"},
    {"name": "Gazprombank",              "party_type": "ior"},
    {"name": "Shenzhen Widgets Factory", "party_type": "manufacturer"}
  ]
}
```

**Response — 200 OK**

```json
{
  "results": [
    { "party_name": "Widgets Global Inc.",      "check_status": "passed",         "requires_manual_review": false, "matches": [], ... },
    { "party_name": "ACME Trading Company",     "check_status": "failed",         "requires_manual_review": true,  "matches": [...], ... },
    { "party_name": "Gazprombank",              "check_status": "manual_review",  "requires_manual_review": true,  "matches": [...], ... },
    { "party_name": "Shenzhen Widgets Factory", "check_status": "passed",         "requires_manual_review": false, "matches": [], ... }
  ],
  "count": 4,
  "any_failed": true,
  "any_manual_review": true
}
```

The aggregate flags `any_failed` / `any_manual_review` give calling
systems a single boolean to gate the shipment on, without having to
iterate results.

### 5.3 `GET /v1/lists`

Returns metadata about the currently loaded dataset — useful for
sanity-checking coverage and showing loaded-at timestamps in admin UIs.

```json
{
  "data_source": "live_csl",
  "loaded_at": "2026-04-19T14:30:02.118411+00:00",
  "total_entries": 1847,
  "lists": [
    {"source": "AECA Debarred List - State Department", "entry_count": 134},
    {"source": "Denied Persons List (DPL) - Bureau of Industry and Security", "entry_count": 58},
    {"source": "Entity List (EL) - Bureau of Industry and Security", "entry_count": 512},
    {"source": "Sectoral Sanctions Identifications List (SSI) - Treasury Department", "entry_count": 74},
    {"source": "Specially Designated Nationals (SDN) - Treasury Department", "entry_count": 1069}
  ]
}
```

### 5.4 `GET /health`

Liveness + readiness probe.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "data_source": "live_csl",
  "total_entries": 1847
}
```

Returns `status: "degraded"` when the service is running but the dataset
is empty (e.g. initial fetch failed and the sample fallback also failed
to load).

---

## 6. Matching algorithm

The matcher is deliberately simple and explainable — every match gets a
single float in `[0.0, 1.0]`. Sanctions-screening buyers want to be able
to justify every decision on audit, so black-box embeddings were rejected
in favor of transparent string similarity.

**Step 1 — Normalize both sides.**

```
"ACME Trading Company, Inc."  ─┐
                                ├─▶  "acme"  (after normalize_name)
                                │
"acme trading co.  (Tehran)"  ─┘
```

Normalization does, in order:

1. `unicodedata.normalize("NFKD")` + ASCII encode — strips accents.
2. Lowercase.
3. Strip punctuation (`[^\w\s]+` replaced with space).
4. Drop tokens in the legal-suffix stoplist: `llc, inc, ltd, co, corp,
   gmbh, ag, sa, bv, nv, plc, pty, ltda, oao, ooo, trading, company` and
   their punctuated variants.
5. Collapse whitespace.

**Step 2 — Score with `rapidfuzz.fuzz.token_set_ratio`.**

Token-set ratio handles word-order differences
(`"Trading Company, Acme"` vs `"ACME Trading Co."`) and repeated-token
noise. The result is divided by 100 to get a 0.0–1.0 score.

**Step 3 — Score every alias, keep the best.**

Each CSL entry has a canonical `name` plus `alt_names[]`. The matcher
scores the query against all of them and keeps the highest score per
entry, so matching on any known alias catches the party.

**Step 4 — Apply thresholds.**

- Drop everything below `MATCH_MIN_SCORE` (default 0.82).
- Everything else is returned, sorted descending by score.
- Top score determines the aggregate `check_status`.

The **country field is captured but not used as a filter.** Sanctions
lists frequently omit country or use ambiguous values, and filtering on
it would cause false negatives — which in this domain is strictly worse
than a false positive. The country is passed through to the response so
downstream systems can use it for their own audit logic.

---

## 7. Configuration

All configuration is via environment variables (or a `.env` file).
Defaults work out-of-the-box.

| Variable | Default | Purpose |
|---|---|---|
| `CSL_BULK_URL` | `https://data.trade.gov/.../consolidated.json` | Source feed URL. |
| `CSL_REFRESH_SECONDS` | `86400` | Reserved for future background refresh. |
| `CSL_HTTP_TIMEOUT` | `60` | Timeout (seconds) for the startup fetch. |
| `USE_SAMPLE_ONLY` | `false` | If `true`, skip the live fetch entirely — run fully offline on the bundled dataset. Useful for demos with no network. |
| `MATCH_MIN_SCORE` | `0.82` | Floor — matches below this are discarded. |
| `MATCH_FAIL_SCORE` | `0.95` | At or above this, `check_status = failed`. |
| `HOST` | `0.0.0.0` | Bind address. |
| `PORT` | `8000` | Bind port. |

---

## 8. Project layout

```
dps-poc/
├── README.md                      ◀── you are here
├── requirements.txt
├── run.py                         # uvicorn launcher
├── .env.example
├── .gitignore
└── app/
    ├── __init__.py                # __version__
    ├── main.py                    # FastAPI app + lifespan + router registration
    ├── config.py                  # Settings (pydantic-settings)
    ├── models.py                  # Pydantic request/response models
    ├── data/
    │   └── sample_csl.json        # 12-entry offline fallback dataset
    ├── routers/
    │   ├── screening.py           # /v1/check-party, /v1/check-batch
    │   └── meta.py                # /health, /v1/lists
    └── services/
        ├── csl_client.py          # Live fetch + sample fallback + in-memory index
        ├── matcher.py             # normalize_name + score_pair + find_matches
        └── dps_service.py         # Orchestration + classification
```

---

## 9. Testing the demo quickly

With the server running, these three curls show every path:

```bash
# (a) Clean party — should return passed, no matches
curl -s -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name": "Widgets Global Inc.", "party_type": "buyer"}' | jq

# (b) Exact sanctioned party — should return failed, score = 1.0
curl -s -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name": "ACME Trading Company", "country": "IR"}' | jq

# (c) Near-miss / partial typo — lands in manual_review (score ~0.86)
curl -s -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name": "Acm Trading", "country": "IR"}' | jq
```

And the batch endpoint:

```bash
curl -s -X POST http://localhost:8000/v1/check-batch \
  -H "Content-Type: application/json" \
  -d '{
    "parties": [
      {"name": "Widgets Global Inc.",    "party_type": "buyer"},
      {"name": "Gazprombank",            "party_type": "ior"},
      {"name": "ACME Trading Company",   "party_type": "supplier"}
    ]
  }' | jq
```

---

## 10. Path to production

This POC is intentionally minimal. To harden for production use:

| Concern | Current | Production |
|---|---|---|
| Auth | None (open). | API key header or OAuth2 client-credentials. |
| Rate limiting | None. | Per-key quota, e.g. via `slowapi` or an API gateway. |
| Data refresh | Once at startup. | Scheduled refresh (`CSL_REFRESH_SECONDS` is already wired) with a disk cache so restarts don't require a live fetch. |
| Observability | stdlib logging. | Structured logs + Prometheus metrics + OpenTelemetry traces. |
| Persistence | Stateless. | Persist every screening request + result for audit compliance. |
| CORS | Wide open. | Lock to specific origins per deployment. |
| Deployment | `python run.py`. | Docker image + Render.com / ECS / Cloud Run. |
| Test suite | None in this POC. | `pytest` with fixtures for the sample dataset + contract tests against the OpenAPI spec. |

The code layout is organised so each of these is a drop-in addition —
middleware for auth and rate limiting, a background task for refresh,
and a persistence layer behind `DPSService` — without reshaping the
existing service or router code.

---

## 11. License

MIT. Data comes from US government public-domain sources (Trade.gov /
OFAC / BIS / DDTC); re-distribution carries no licensing constraints.
