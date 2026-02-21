# Phase 1 — Pipeline Compliance Changes

## 1. Database Schema (`database/models.py`)

**Ticket states expanded: 3 → 10**
- New → Ingested → Queued → Enriching → Enriched → Routing → Assigned → Closed
- Failure states: EnrichFailed, RoutingFailed, DeadLetter

**New table: `rr_state`** — Round-robin pointer per office + manager pair. Ensures assignment alternates deterministically between top-2 managers.

**Manager: added `is_active`** — Boolean flag to exclude inactive/on-leave managers from routing.

**Ticket: 5 new columns**
- `correlation_id` — unique trace ID per ticket
- `office_rule` — how office was selected (exact_match, nearest_office, split_50_50, etc.)
- `routing_trace` (JSON) — full audit: geo decision, skill filter rules, candidate loads, RR pointer
- `flags` (JSON) — all boolean flags: `address_unknown`, `foreign_country`, `geocode_failed`, `ai_fallback`, `ai_schema_error`, `ai_type_low_confidence`, `no_candidate_after_hardskills`, `duplicate_ticket_id`, `invalid_load`, `needs_clarification`
- `workload_at_assignment` — manager's load at the moment ticket was assigned

---

## 2. AI Enrichment (`engine/intelligence.py`)

- **Summary field added** — LLM now returns 1–2 sentence summary + recommended next actions
- **Bounded retries** — 2 retries with backoff before fallback defaults
- **Consistent `ai_fallback=True`** on every fallback path (was missing before)
- **New flags on parse**:
  - `ai_schema_error` — LLM returned invalid JSON
  - `ai_type_low_confidence` — ticket type not in allowed enum, defaulted to "Консультация"
  - `needs_clarification` — empty ticket text, priority lowered to 3
  - `language_confidence=low` — unknown language, defaulted to RU
- **30s timeout** on LLM API calls
- **Sentiment values** now in Russian (Позитивный/Нейтральный/Негативный) per pipeline.md spec

---

## 3. Geo-Routing (`utils/geo.py`)

- `find_nearest_office` **skips offices with null lat/lon** — if all missing → triggers 50/50 fallback
- No crash on missing coordinates

---

## 4. Router — Complete Rewrite (`engine/router.py`)

### Geo flags
Returns explicit booleans: `address_unknown`, `foreign_country`, `geocode_failed`.
- Unresolvable address → `address_unknown=True`
- Distance > 500km → `foreign_country=True`
- No geocode possible → `geocode_failed=True`

### Graduated skill filter (5 levels)
1. **Strict**: all hard rules (VIP + role + language) in target office
2. **Relax language**: drop KZ/ENG requirement, keep VIP + role
3. **Expand offices**: search Astana + Almaty, keep VIP + role
4. **VIP escalation**: VIP with zero candidates → return empty (unassigned), **never assign VIP to non-VIP manager**
5. **Global fallback**: lowest-load "Главный специалист" managers, then any active manager

`no_candidate_after_hardskills=True` flagged whenever any fallback triggers.

### Round Robin load balancing
- Sort candidates by `(current_load, manager_id)` for determinism
- Pick **top 2** with lowest load
- Alternate between them via persisted RR pointer in `rr_state` DB table
- Pointer incremented atomically per assignment

### Load validation
- Negative/null `current_load` → coerced to 9999, flagged `invalid_load=True`

### CSV schema validation
- Checks required columns (`GUID клиента`, `Сегмент клиента`, `Населённый пункт`, `Область`, `Описание`) before processing
- Clear error message listing missing columns

### Per-row error handling
- Each row wrapped in try/except
- Bad rows → status `DeadLetter`, error logged, batch continues

### Duplicate detection
- Existing GUID check → skip, flag `duplicate_ticket_id=True`

### Full routing trace
Stored as JSON per ticket:
```json
{
  "geo_decision": {"resolved_city": "...", "rule": "...", "distance_km": ...},
  "skill_filter": {"office_city": "...", "applied_rules": [...], "fallback_used": "..."},
  "load_balance": {"candidate_ids": [...], "candidate_loads": [...], "rr_pointer": ..., "chosen_manager_id": ...}
}
```

### Correlation ID
UUID-based (`COR-xxxxxxxxxxxx`) generated per ticket for traceability.

---

## 5. Seed Script (`database/seed.py`)

- All seeded managers get `is_active=True`
- `RoundRobinState` imported so table is auto-created

---

## 6. Admin Dashboard (`views/admin.py`)

- Results table: added **`ai_summary`** and **`office_rule`** columns
- Statistics: 5th metric card — **Failed / Dead Letter** ticket count

---

## 7. Manager View (`views/manager.py`)

Each ticket card now shows:
- **AI Summary** — LLM-generated summary with recommended actions
- **"Why Assigned"** — geo rule + skill filter fallback explanation
- **Active flags** — displayed as code block (e.g., `address_unknown, ai_fallback`)
