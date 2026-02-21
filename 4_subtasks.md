# Work Split for 4-Person Team (Equal Division)

## Option A (recommended): 4 parallel workstreams aligned to the pipeline

### 1) Data + DB + Routing/Assignment (Engineer A)
**Scope**
- DB schema + migrations: `tickets_raw`, `ticket_ai`, `offices`, `managers`, `assignments`, `rr_state`, `events_log`
- CSV ingestion validation rules + idempotency
- Office selection logic (nearest office + 50/50 Astana/Almaty exceptions)
- Hard-skill filtering + load balancing + Round Robin pointer (transaction-safe)

**Deliverables**
- DB DDL + seed scripts from `business_units.csv`, `managers.csv`
- Routing module + unit tests for edge cases
- “Why assigned” `rule_trace` format

---

### 2) LLM/NLP Enrichment + Postprocessing (Engineer B)
**Scope**
- Prompting + structured output (JSON schema) for: type, sentiment, priority, language, summary
- Confidence/validation: enum mapping, clamping, fallbacks
- Retry/timeout strategy, circuit breaker behavior
- Postprocessing merge logic (fan-in)

**Deliverables**
- LLM call wrapper + prompt templates
- Output validator + fallback policy
- Test set using `tickets.csv` (golden samples)

---

### 3) Geocoding + Geo Quality + Distance Engine (Engineer C)
**Scope**
- Geocoder integration (or offline lookup strategy if required)
- Address normalization, caching
- Confidence scoring: full vs partial vs not found
- Distance computation vs offices + performance (batching)

**Deliverables**
- Geocoding module + cache
- Distance computation utility + tests
- Clear flags for “unknown address” / “foreign country”

---

### 4) Backend API + UI + Observability (Engineer D)
**Scope**
- API endpoints: upload CSV, ticket list/detail, processing status, managers/offices views
- Simple UI: upload + progress + table + ticket detail + reason trace
- Logging + metrics dashboard counters (by state, latency, fallback rates)
- Error surfacing in UI (dead-letter / manual review)

**Deliverables**
- Working UI demo + API spec
- Monitoring panel (even minimal)
- Integration wiring to workers/queue

---

## Integration plan (keeps it equal and reduces merge risk)

### Shared contract (define once, early)
- Ticket state machine constants
- Standard JSON outputs:
  - `ticket_ai` schema (LLM + geo)
  - `rule_trace` structure for routing decisions
- Error codes + flags set

### Merge points (dependencies)
- Engineer A provides DB + routing functions + seed data
- Engineer B produces `ticket_ai` NLP fields (validated JSON)
- Engineer C produces geo fields (`lat/lon`, confidence, country flag)
- Engineer D consumes all for UI/API + uses event logs and states

---

## Option B: Equal-by-feature vertical slices (alternative)
Each person builds a vertical slice end-to-end:

1) CSV ingestion + raw storage + UI upload  
2) LLM enrichment + viewing AI results in UI  
3) Geocoding + nearest office + viewing on UI  
4) Manager assignment + RR + viewing + audit logs  

**Note:** This is harder to integrate, but each slice is independently demo-able.

---

## Concrete work split checklist (quick assignment)

- **A:** DB + routing + RR + seed scripts + tests  
- **B:** LLM prompt + schema validation + defaults + tests  
- **C:** Geocoding + caching + distance + tests  
- **D:** API + UI + logs/metrics + integration demo  
