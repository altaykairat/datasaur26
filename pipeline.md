# Ticket Processing Service — End-to-End Pipeline

## 1) Runtime pipeline (time series per ticket)

1. **Ticket intake**
   - Source: uploaded **CSV (Tickets)** (and reference CSVs: Managers, Business Units).
   - Validate schema, required fields, GUID format, address fields, segment, free-text.
   - Persist *raw* rows to DB as `INGESTED`.

2. **Normalization & enrichment job creation**
   - Create a processing record per ticket: `status=QUEUED`, `correlation_id`, timestamps.
   - Push `ticket_id` to a queue / background worker (so UI doesn’t block).

3. **Parallel AI enrichment (fan-out)**
   - **LLM/NLP module** extracts:
     - `type` ∈ {Жалоба, Смена данных, Консультация, Претензия, Неработоспособность приложения, Мошеннические действия, Спам}
     - `sentiment` ∈ {Позитивный, Нейтральный, Негативный}
     - `priority` score 1..10
     - `language` ∈ {KZ, ENG, RU} (default RU if unknown)
     - `summary`: 1–2 sentences + recommended next actions for manager
   - **Geocoding / geo-normalization** (in parallel):
     - Convert textual address → `lat/lon`
     - Store confidence/quality flags (e.g., “partial address”, “not found”).

4. **Post-processing (fan-in)**
   - Merge LLM + geo results into a single “AI analytics” entity.
   - Apply deterministic fixes:
     - language fallback to RU if empty
     - clamp priority to [1..10]
     - map/validate `type` to allowed enum; if unknown → safe default + flag `ai_type_low_confidence`
   - Mark `status=ENRICHED`.

5. **Office selection (geo filter)**
   - If coordinates valid:
     - compute distance to each Business Unit (office) and pick nearest office
   - **Exception rule**
     - if address unknown **OR** client is outside the country → split **50/50 between Astana and Almaty offices**
     - store which rule triggered

6. **Eligible manager filtering (hard skills cascade)**
   Within the selected office:
   - If segment is **VIP/Priority** → manager must have **VIP** skill
   - If `type == "Смена данных"` → manager must be **"Глав спец"**
   - If `language` is **KZ** or **ENG** → manager must have corresponding language skill
   - If no eligible managers: apply explicit fallback (see *Edge cases*) and flag `no_candidate_after_hardskills`

7. **Load balancing + assignment**
   - From eligible managers, choose **two** with **minimal current workload**
   - Distribute between those two by **Round Robin**
     - RR pointer is persisted (per office + candidate-pair)

8. **Output & UI availability**
   - Ticket becomes visible in UI as `ASSIGNED`, with:
     - original fields
     - AI analytics (type/sentiment/priority/language/summary + geo)
     - chosen office and distance (or exception rule)
     - assigned manager and workload at decision time
   - Target processing time per ticket: **≤ 10 seconds** (design implication: parallelism + caching + timeouts)

---

## 2) State machine (per ticket)

### Primary states
- **NEW (in CSV)**
- **INGESTED** (raw stored, schema validated)
- **QUEUED** (job created)
- **ENRICHING** (fan-out: LLM + geocoder)
- **ENRICHED** (fan-in: analytics stored)
- **ROUTING** (office + manager eligibility + RR)
- **ASSIGNED** (final manager stored, UI-ready)

### Failure/side states (with retries)
- **ENRICH_FAILED**
  - LLM/geocoder timeout/error → retry `N` times → then fallback values + flag(s)
- **ROUTING_FAILED**
  - no candidates / data inconsistency → fallback routing + flag(s)
- **DEAD_LETTER**
  - non-recoverable validation errors; requires manual fix

---

## 3) Static architecture (components)

### A) UI (web panel)
- Upload/preview CSVs
- Processing monitor: counts by state, SLA timers
- Ticket detail view: raw + AI analytics + routing explanation (“why assigned”)
- Manager/office views: workloads, skills, office address map layer
- **“🌟 AI Analytics Companion” (Implemented)**: Natural-language UI in Russian leveraging Vanna + DeepSeek Coder to securely generate real-time SQL queries, dataframes, and Plotly charts.

### B) API service (backend)
- **Ingestion API**: upload CSV, validate, store raw, enqueue jobs
- **Ticket API**: list/filter/sort tickets, show details
- **Routing API** (optional): reroute/recompute on demand (admin only)
- **Analytics API**: aggregates for dashboards

### C) Workers
- **Enrichment worker**
  - calls LLM for NLP attributes + summary
  - calls geocoder for lat/lon
  - handles timeouts, retries, circuit breaker
- **Routing worker**
  - nearest office computation + exception handling
  - hard-skill filters
  - load balancing + round robin assignment

### D) DB (PostgreSQL)
Minimum entities (normalized):
- `tickets_raw` — ingested fields
- `ticket_ai` — type, sentiment, priority, language, summary, geocode + confidence, timestamps
- `offices` — business units / office metadata
- `managers` — skills, role, office_id, current_load
- `assignments` — ticket_id, manager_id, office_id, rule_trace, rr_key, decided_at
- `rr_state` — per office + candidate-pair pointer
- `events_log` — state transitions, errors, retries

### E) Observability & controls
- Structured logs with `correlation_id` per ticket
- Metrics: processing latency, LLM latency, geocode hit rate, fallback rate, “no candidate” rate
- Audit: persist “why assigned” (rule trace) for explainability and dispute resolution

---

## 4) Edge-case handling (explicit)

1. **Unknown / incomplete address**
   - Skip distance calculation
   - Apply **50/50 Astana/Almaty** rule
   - Flag: `address_unknown=true`, `office_rule="split_50_50"`

2. **Foreign country**
   - Apply **50/50 Astana/Almaty** rule
   - Flag: `foreign_country=true`, `office_rule="split_50_50"`

3. **LLM failure / timeout**
   - Retry (bounded)
   - If still failing, set safe defaults:
     - `language=RU`
     - `type=Консультация` (or other agreed safe default)
     - `sentiment=Нейтральный`
     - `priority=5`
     - `summary="AI unavailable; review text manually."`
   - Continue routing with flags: `llm_failed=true`

4. **Geocoder failure**
   - Retry; then treat as unknown address → **50/50** rule
   - Flag: `geocode_failed=true`

5. **No eligible managers after hard-skill filtering**
   - Deterministic fallback order (example):
     1) keep office, relax language constraint *(if business allows)*, else
     2) keep constraints, expand to both main offices, else
     3) assign to least-loaded “Глав спец” pool for manual triage
   - Always persist: `no_candidate_after_hardskills=true` and show in UI

6. **Round Robin correctness under concurrency**
   - RR pointer stored transactionally with assignment to avoid double-assignments
   - Use DB locking / atomic update for `rr_state`
