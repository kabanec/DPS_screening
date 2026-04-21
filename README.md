# DPS POC — Denied Party Screening API

A standalone proof-of-concept Denied Party Screening (DPS) service built in
FastAPI. It screens party names — **companies, vessels, and natural
persons** — across **multiple government sanctions lists** through a single
normalized interface and returns a three-tier decision:
**passed / manual_review / failed**.

Prepared as a demo for Avalara.

---

## 1. What this service does

Every cross-border shipment has to be screened against denied-party lists
before it ships. Getting this wrong is not a paperwork problem — it is a
federal violation. OFAC, BIS, DDTC, UN sanctions and their EU/UK/Canadian
equivalents all levy penalties in the hundreds of thousands of dollars
per occurrence, and the obligation applies to every party on the invoice:
seller, buyer, manufacturer, Importer of Record, ship-to, and — for B2C
or C2C flows — the named natural persons behind them.

This API answers one question per party:

> *Is this name (company or person, optionally + country) on any denied-party
> list, and at what confidence?*

It accepts a single party or a batch of up to 100, runs fuzzy name matching
over every entry loaded from every enabled source, and returns:

- A classification (`passed` / `manual_review` / `failed`)
- Every match at or above a configurable minimum score
- The source list, list type, country, and sanctions programs for each match
- An authoritative URL back to the source listing

The match score threshold is tunable via environment variables — a
production deployment can be tightened to reduce manual review load or
loosened to catch more edge cases. The matcher is **type-agnostic** — the
same algorithm scores company names and personal names identically.

---

## 2. Data sources

The POC is built on a **multi-source architecture**. Each denied-party list
is exposed through an adapter that normalizes its native feed into a
common 7-key entry shape; the matcher and service layer are source-agnostic.

### 2.1 Sources wired today

| Adapter | Short code | Feed | Format | API key? | Entries |
|---|---|---|---|---|---|
| **US Consolidated Screening List** (Trade.gov) | `US_CSL` | [`data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json`](https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.json) | JSON | No | ~1,800 |
| **UN Security Council Consolidated List** | `UN` | [`scsanctions.un.org/resources/xml/en/consolidated.xml`](https://scsanctions.un.org/resources/xml/en/consolidated.xml) | XML | No | ~1,000 |

The US CSL is itself an aggregate of 12 agency lists:

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

The UN list covers the 1267 (Al-Qaida/Daesh), 1988 (Taliban), and 1540
(WMD proliferation) regimes and contains **both Individuals and Entities**
with their aliases, references, and native country attribution.

### 2.2 Sources architected for (adapter drop-in)

The `SourceAdapter` protocol + `SourceRegistry` make adding a new list a
three-step drop-in (write the adapter, instantiate in `main.py`, toggle in
config). No changes to the matcher or screening service are required.

| Jurisdiction | List | Feed | Effort |
|---|---|---|---|
| 🇬🇧 UK HM Treasury | OFSI Consolidated List | CSV / XML (public) | ~half day |
| 🇪🇺 European Union | CFSP Consolidated List | XML (EU FSD, free with registration) | ~1 day |
| 🇨🇦 Canada | OSFI Consolidated + SEMA | XML (public) | ~half day |
| 🇦🇺 Australia | DFAT Consolidated List | XLSX (public) | ~half day |
| 🇨🇭 Switzerland | SECO SESAM | XML (public) | ~half day |
| 🇯🇵 Japan | METI End-User List | HTML/PDF (scrape) | ~1 day |
| 🌐 Aggregated | OpenSanctions.org (optional commercial) | JSON API | — (pay per seat) |

Each adapter is independent — a failure on one source does not abort the
others. Per-source health is surfaced on `/health` and `/v1/lists`.

### 2.3 Normalization contract

Every adapter MUST emit entries conforming to this 7-key canonical shape:

```python
{
    "id":                     str,                  # stable per-source id
    "name":                   str,                  # primary searchable name
    "alt_names":              List[str],            # aliases
    "source":                 str,                  # human-readable list name
    "type":                   "Individual" | "Entity" | "Vessel" | "Aircraft" | "Other",
    "list_type":              str | None,           # short code from the source
    "country":                str | None,           # ISO-2 or country name
    "programs":               List[str] | str,      # sanctions programs
    "source_information_url": str | None            # link back to authoritative listing
}
```

The matcher never looks at the `source` field — it just sees a flat list
of normalized entries. This is why adding a new source is additive work:
the scoring and classification logic does not change.

### 2.4 Offline fallback

A bundled sample dataset (`app/data/sample_csl.json`, 12 representative
entries) ships with the project and is used automatically if the live
fetch fails — so the API always stays responsive, even fully offline.
Set `USE_SAMPLE_ONLY=true` to force offline mode.

---

## 3. What we screen against what

| Screened entity | Sanctioned as | Matched via |
|---|---|---|
| Company on commercial invoice (B2B seller / buyer / manufacturer / IOR) | `Entity` records (US CSL, UN ENTITY) | Fuzzy token-set on normalized legal name, legal suffixes stripped |
| Natural person (B2C or C2C seller / buyer, e.g. marketplace listing) | `Individual` records (US CSL SDN individuals, UN INDIVIDUAL) | Fuzzy token-set on normalized full name (first + second + third + fourth names joined) |
| Vessel / aircraft (bill-of-lading conveyance) | `Vessel` / `Aircraft` records (OFAC SDN) | Fuzzy token-set on vessel name |

Because every entry is normalized into the same shape regardless of
whether it is a company or a person, callers do not need to pre-classify
the input. A single `POST /v1/check-party` with `{"name": "…"}` is
scored against individuals **and** entities **and** vessels across every
loaded source in one pass.

---

## 4. Architecture

```
┌─────────────────────────┐  ┌─────────────────────────┐
│  Trade.gov CSL JSON     │  │  UN SC Consolidated XML │   ← sources wired today
└────────────┬────────────┘  └────────────┬────────────┘
             │                            │
             │    (async, concurrent      │
             │     startup load)          │
             ▼                            ▼
┌──────────────────────────────────────────────────────────────┐
│                       SourceRegistry                         │
│   (duck-typed adapter contract — UK/EU/CA/AU/CH/JP drop in   │
│    here without matcher or service changes)                  │
└──────────────────────────┬───────────────────────────────────┘
                           │ .get_entries() — flat, normalized
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI application                       │
│                                                              │
│   ┌───────────────┐   ┌───────────────┐  ┌────────────────┐  │
│   │ SourceRegistry│──▶│   Matcher     │─▶│   DPSService   │  │
│   │  (N adapters) │   │  (rapidfuzz   │  │  (classify:    │  │
│   │               │   │   token-set   │  │   passed /     │  │
│   │               │   │   ratio,      │  │   manual_rev / │  │
│   │               │   │   type-       │  │   failed)      │  │
│   │               │   │   agnostic)   │  │                │  │
│   └───────────────┘   └───────────────┘  └──────┬─────────┘  │
│                                                 │            │
│   ┌────────────────────────────────────┐        │            │
│   │  Routers:                          │◀───────┘            │
│   │   POST /v1/check-party             │                     │
│   │   POST /v1/check-batch             │                     │
│   │   GET  /v1/lists                   │                     │
│   │   GET  /health                     │                     │
│   │   GET  /docs   /redoc              │                     │
│   └────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

Request flow for a single party check:

1. Client POSTs a name (+ optional country / address / party_type).
2. `DPSService.check_party()` pulls `registry.get_entries()` — a flat
   list of normalized entries from every loaded adapter.
3. `find_matches()` normalizes the query name (Unicode-fold, lowercase,
   drop punctuation, strip legal suffixes like *LLC, Inc., Ltd., GmbH*)
   and scores it against every canonical name and alias using
   `rapidfuzz.fuzz.token_set_ratio`.
4. Matches at or above `MATCH_MIN_SCORE` (default 0.82) are returned,
   sorted by score.
5. The service classifies the top score into `passed` / `manual_review` /
   `failed` and returns a structured response including which adapters
   contributed (`data_source` = `multi_source` when 2+ loaded).

No data is written anywhere — the service is stateless by design, so
horizontal scaling is trivial.

---

## 5. Quickstart

```bash
cd dps-poc
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

The server starts on `http://localhost:8000`.

Open **`http://localhost:8000/docs`** for interactive Swagger UI — every
endpoint is live-testable from the browser. `http://localhost:8000/redoc`
serves the same OpenAPI 3.1 spec in ReDoc format, and
`http://localhost:8000/openapi.json` gives you the raw machine-readable
spec for client generation.

---

## 6. API reference

### 6.1 `POST /v1/check-party`

Screen a single party — company **or** natural person.

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
optional. `party_type` is echoed back in the response so callers can
correlate results to invoice roles (`supplier` / `buyer` / `manufacturer` /
`ior` / `ship_to`, or `individual_seller` / `individual_buyer` for B2C/C2C).

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
  "data_source": "multi_source"
}
```

`data_source` reflects the rollup: `multi_source` when 2+ adapters loaded
data, otherwise the single adapter's native source tag (`live_csl`,
`live_un`, `sample`, …).

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

### 6.2 `POST /v1/check-batch`

Screen up to 100 parties in a single request — useful for screening every
party on a customs invoice (company seller, company buyer, named
individuals for B2C/C2C, manufacturer, IOR, ship-to) in one round trip.

**Request**

```json
{
  "parties": [
    {"name": "Widgets Global Inc.",      "party_type": "buyer"},
    {"name": "ACME Trading Company",     "party_type": "supplier"},
    {"name": "John Q. Smith",            "party_type": "individual_buyer"},
    {"name": "Gazprombank",              "party_type": "ior"},
    {"name": "Shenzhen Widgets Factory", "party_type": "manufacturer"}
  ]
}
```

**Response — 200 OK**

```json
{
  "results": [ ... per-party CheckPartyResponse ... ],
  "count": 5,
  "any_failed": true,
  "any_manual_review": true
}
```

The aggregate flags `any_failed` / `any_manual_review` give calling
systems a single boolean to gate the shipment on, without having to
iterate results.

### 6.3 `GET /v1/lists`

Returns metadata about every loaded denied-party list — per-adapter load
status plus per-source-list row counts. Useful for admin dashboards and
for sanity-checking coverage and freshness.

```json
{
  "data_source": "multi_source",
  "loaded_at": "2026-04-19T14:30:02.118411+00:00",
  "total_entries": 2847,
  "adapters": [
    {
      "short_code": "US_CSL",
      "name": "US Consolidated Screening List (Trade.gov)",
      "status": "live_csl",
      "entry_count": 1847,
      "loaded_at": "2026-04-19T14:30:02.118411+00:00"
    },
    {
      "short_code": "UN",
      "name": "UN Security Council Consolidated List",
      "status": "live_un",
      "entry_count": 1000,
      "loaded_at": "2026-04-19T14:30:03.552001+00:00"
    }
  ],
  "lists": [
    {"source": "AECA Debarred List - State Department", "entry_count": 134},
    {"source": "Denied Persons List (DPL) - Bureau of Industry and Security", "entry_count": 58},
    {"source": "Entity List (EL) - Bureau of Industry and Security", "entry_count": 512},
    {"source": "Specially Designated Nationals (SDN) - Treasury Department", "entry_count": 1069},
    {"source": "UN Security Council Consolidated List", "entry_count": 1000}
  ]
}
```

### 6.4 `GET /health`

Liveness + readiness probe.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "data_source": "multi_source",
  "total_entries": 2847
}
```

Returns `status: "degraded"` when the service is running but every
adapter returned zero entries (e.g. all live fetches failed and no
sample fallback loaded).

---

## 7. Matching algorithm

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

The same normalization runs on both companies and persons — no legal
suffix is ever present on a natural person's name, so the stoplist is a
no-op for person matching and the algorithm degrades gracefully.

**Step 2 — Score with `rapidfuzz.fuzz.token_set_ratio`.**

Token-set ratio handles word-order differences
(`"Trading Company, Acme"` vs `"ACME Trading Co."`, or
`"Smith, John Q."` vs `"John Q. Smith"`) and repeated-token noise. The
result is divided by 100 to get a 0.0–1.0 score.

**Step 3 — Score every alias, keep the best.**

Each entry has a canonical `name` plus `alt_names[]`. The matcher scores
the query against all of them and keeps the highest score per entry, so
matching on any known alias catches the party. UN records typically have
5–15 transliteration aliases per individual — this step is what makes
non-Latin-script name matching work.

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

## 8. Configuration

All configuration is via environment variables (or a `.env` file).
Defaults work out-of-the-box.

| Variable | Default | Purpose |
|---|---|---|
| `CSL_BULK_URL` | `https://data.trade.gov/.../consolidated.json` | US CSL source feed URL. |
| `CSL_REFRESH_SECONDS` | `86400` | Reserved for future background refresh. |
| `CSL_HTTP_TIMEOUT` | `60` | Timeout (seconds) for startup fetches. |
| `USE_SAMPLE_ONLY` | `false` | If `true`, skip every live fetch — run fully offline on the bundled sample. Useful for demos with no network. |
| `ENABLE_SOURCE_US_CSL` | `true` | Toggle the US CSL adapter. |
| `ENABLE_SOURCE_UN` | `true` | Toggle the UN Consolidated List adapter. |
| `MATCH_MIN_SCORE` | `0.82` | Floor — matches below this are discarded. |
| `MATCH_FAIL_SCORE` | `0.95` | At or above this, `check_status = failed`. |
| `HOST` | `0.0.0.0` | Bind address. |
| `PORT` | `8000` | Bind port. |

---

## 9. Project layout

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
        ├── source_registry.py     # Multi-adapter registry + SourceAdapter protocol
        ├── csl_client.py          # US CSL adapter (Trade.gov JSON)
        ├── un_client.py           # UN SC Consolidated adapter (XML)
        ├── matcher.py             # normalize_name + score_pair + find_matches
        └── dps_service.py         # Orchestration + classification (source-agnostic)
```

---

## 10. Testing the demo quickly

With the server running, these curls show every path:

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

# (d) Natural person — scored against SDN individuals + UN INDIVIDUAL records
curl -s -X POST http://localhost:8000/v1/check-party \
  -H "Content-Type: application/json" \
  -d '{"name": "John Q. Smith", "party_type": "individual_buyer"}' | jq

# (e) Multi-source coverage check
curl -s http://localhost:8000/v1/lists | jq
```

And the batch endpoint — with both companies and persons in one call:

```bash
curl -s -X POST http://localhost:8000/v1/check-batch \
  -H "Content-Type: application/json" \
  -d '{
    "parties": [
      {"name": "Widgets Global Inc.",    "party_type": "buyer"},
      {"name": "Gazprombank",            "party_type": "ior"},
      {"name": "Jane R. Doe",            "party_type": "individual_buyer"},
      {"name": "ACME Trading Company",   "party_type": "supplier"}
    ]
  }' | jq
```

---

## 11. Path to production

This POC is intentionally minimal. To harden for production use:

| Concern | Current | Production |
|---|---|---|
| Auth | None (open). | API key header or OAuth2 client-credentials. |
| Rate limiting | None. | Per-key quota, e.g. via `slowapi` or an API gateway. |
| Data refresh | Once at startup. | Scheduled refresh (`CSL_REFRESH_SECONDS` is wired) with a disk cache and atomic swap so restarts don't require a live fetch. |
| Snapshot audit | None. | Persist every refresh as a SHA-256-keyed snapshot with a retention window for point-in-time screening audit. |
| Observability | stdlib logging. | Structured logs + Prometheus metrics + OpenTelemetry traces, per-adapter. |
| Persistence | Stateless. | Persist every screening request + result (including which adapters contributed) for audit compliance. |
| CORS | Wide open. | Lock to specific origins per deployment. |
| Deployment | `python run.py`. | Docker image + Render.com / ECS / Cloud Run. |
| Test suite | None in this POC. | `pytest` with fixtures for the sample dataset + per-adapter contract tests + OpenAPI schema tests. |
| Source coverage | US CSL + UN. | + UK OFSI, EU CFSP, Canada OSFI, Australia DFAT, Switzerland SECO, Japan METI (all adapter drop-in). |

The code layout is organised so each of these is a drop-in addition —
middleware for auth and rate limiting, a background task for refresh,
a persistence layer behind `DPSService`, and new adapters behind the
`SourceAdapter` protocol — without reshaping the existing matcher or
router code.

---

## 12. License

MIT. Data comes from US government public-domain sources (Trade.gov /
OFAC / BIS / DDTC) and the UN Security Council's public XML feed;
re-distribution carries no licensing constraints.
