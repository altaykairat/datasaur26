# 🔥 F.I.R.E. — Freedom Intelligent Routing Engine

Customer Support Dispatch System with AI-powered ticket enrichment and rule-based routing.

## What It Does

FIRE takes customer support tickets (live or batch CSV) and automatically:
1. **Validates** the CSV schema and detects duplicate tickets
2. **Analyzes** each ticket using AI (DeepSeek API or local Phi-4 via Ollama) — extracts type, priority, language, sentiment, address, and summary
3. **Routes** it to the best office via geo-location (Haversine distance, deterministic 50/50 fallback)
4. **Filters** managers by hard skill rules (VIP, role, language) with a 5-level graduated cascade
5. **Assigns** via round-robin between the top-2 lowest-load managers

---

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Streamlit   │────▶│  TicketRouter    │────▶│  PostgreSQL DB   │
│  UI (app.py) │     │  (engine/)       │     │  (database/)     │
└─────────────┘     └──────┬───────────┘     └──────────────────┘
                           │
                    ┌──────▼───────────┐
                    │ IntelligenceEngine│
                    │ DeepSeek / Phi-4  │
                    └──────────────────┘
```

### Project Structure

```
├── app.py                      # Streamlit entry point, login/register, CSS theme
├── config.py                   # Reads DATABASE_URL, DEEPSEEK_API_KEY, OLLAMA_BASE_URL from .env
├── database/
│   ├── models.py               # ORM: User, Manager, Ticket, Office, RoundRobinState
│   ├── connection.py           # Engine + session factory
│   └── seed.py                 # Loads managers.csv + business_units.csv into DB
├── engine/
│   ├── intelligence.py         # AI wrapper: DeepSeek/Phi-4, retries, summary, fallback flags
│   ├── router.py               # TicketRouter orchestrator: enrich → geo → skill → balance
│   └── routing/                # OOP subpackage (refactored from monolithic router.py)
│       ├── geo.py              # GeoRouter: Haversine matching, fallback rules
│       ├── skills.py           # SkillMatcher: VIP/role/language hard filters, graduated cascade
│       ├── load_balancer.py    # LoadBalancer: top-2 + round-robin assignment
│       └── batch.py            # BatchProcessor: CSV ingestion, OCR, idempotency
├── views/
│   ├── customer.py             # Create ticket (with optional city/street + screenshot), view my tickets
│   ├── manager.py              # View assigned tickets with AI summary, client info; close tickets
│   └── admin/                  # OOP subpackage (refactored from monolithic admin.py)
│       ├── __init__.py         # Tab orchestrator
│       ├── batch.py            # CSV upload + batch routing UI
│       ├── stats.py            # Live database statistics + charts
│       └── settings.py         # Database management tools
├── utils/
│   ├── geo.py                  # Haversine distance, 30+ Kazakhstan city coordinates, fuzzy matching
│   ├── auth.py                 # bcrypt password hashing
│   ├── ocr.py                  # Tesseract/EasyOCR text extraction from images
│   └── logging.py              # Safe structured logger
├── input/
│   ├── managers.csv            # 51 managers with roles, skills, offices, current load
│   ├── business_units.csv      # 16 office locations across Kazakhstan
│   └── tickets.csv             # Sample tickets for testing batch routing
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Routing Pipeline

### Step 1 — Ticket Intake & Validation
- **CSV batch:** validates required columns (`GUID клиента`, `Описание`, `Сегмент клиента`, `Населённый пункт`, `Область`). Duplicate GUIDs → skipped with `duplicate_ticket_id` flag. Bad rows → `DeadLetter` status, batch continues.
- **Customer portal:** single ticket with optional city, street, and screenshot attachment.

### Step 2 — AI Enrichment (`engine/intelligence.py`)
The AI analyzes ticket text and returns:
- **type**: 7 enums (Жалоба / Смена данных / Консультация / Претензия / Неработоспособность приложения / Мошеннические действия / Спам)
- **priority**: 1–10 (clamped)
- **language**: RU / KZ / ENG (default RU)
- **sentiment**: Позитивный / Нейтральный / Негативный
- **normalized_address**: extracted city/street or "Unknown"
- **summary**: 1–2 sentences + recommended next actions

Retries 2× on failure, then falls back to safe defaults with `ai_fallback=true`.

### Step 3 — Geo-Routing (`engine/routing/geo.py`)
- Exact match → fuzzy match → region fallback → AI-extracted address
- If office city: direct route. If not: Haversine nearest office.
- Distance > 500km or unknown → deterministic 50/50 via `hash(ticket_id) % 2` to Астана/Алматы
- Flags: `address_unknown`, `foreign_country`, `geocode_failed`

### Step 4 — Skill Filter (`engine/routing/skills.py`)
Graduated cascade with hard rules that are **never relaxed**:
1. **Strict**: VIP skill + role + language in target office branch
2. **Relax language**: drop KZ/ENG, keep VIP + role
3. **Expand to city**: all branches in same city, keep VIP + role
4. **Absolute city fallback**: any manager in the city (prefer VIP if applicable)
5. **Expand offices**: Астана + Алматы, keep VIP + role
6. **VIP escalation**: never relax VIP → unassigned (empty candidates)
7. **Global fallback**: lowest-load Главный специалист, then any active manager

Key hard rules:
- **VIP/Priority segment** → manager must have `VIP` skill (never relaxed)
- **Смена данных type** → manager must have `Главный специалист` role (never relaxed)
- **KZ/ENG language** → manager must have corresponding language skill (relaxed in fallback)

### Step 5 — Round Robin Load Balance (`engine/routing/load_balancer.py`)
- Sort by `(current_load, manager_id)`, pick **top 2**
- Alternate via persisted RR pointer in `rr_state` table
- `workload_at_assignment` recorded for audit

### Routing Trace & Flags
Every ticket stores a full `routing_trace` JSON (geo decision, skill filter, load balance details) and `flags` dict for audit/explainability.

---

## Ticket State Machine

```
NEW → INGESTED → QUEUED → ENRICHING → ENRICHED → ROUTING → ASSIGNED → CLOSED
                                                         ↘ RoutingFailed
                                  ↘ EnrichFailed
                                                                    ↘ DeadLetter
                                                                    ↘ Spam
```

---

## Database Schema (`database/models.py`)

| Table | Key Columns |
|-------|-------------|
| `users` | id, username, password_hash, role (customer/manager/admin), manager_id (FK) |
| `managers` | id, name, role, skills (JSON), office_location, office_id (FK), current_load, is_active |
| `tickets` | id, customer_id, client_guid, correlation_id, description, status, assigned_manager_id, ai_analysis_json, segment, client_city, client_address, office_rule, routed_branch_id, routing_trace (JSON), flags (JSON), workload_at_assignment, created_at |
| `offices` | id, city, name, address, lat, lon |
| `rr_state` | id, office_id, candidate_key, pointer |

---

## Edge Cases & Safety

### Data Quality
- Missing CSV columns → reject batch with clear error
- Duplicate `GUID клиента` → idempotent skip, flag `duplicate_ticket_id`
- Empty description + no attachment → `needs_clarification` flag, lower priority
- `NaN` descriptions → displayed as "No comments provided" in UI

### AI Robustness
- LLM timeout/unavailable → fallback defaults (RU, Neutral, priority 5, Consultation type), `ai_fallback=true`
- Invalid JSON from LLM → validate + retry once, then fallback, `ai_schema_error=true`
- Spam detection → early exit, ticket saved as `Spam` status, not assigned

### Geo-Routing Safety
- Unknown address / cannot geocode → deterministic 50/50 Астана/Алматы via `hash(ticket_id) % 2`
- Address outside Kazakhstan → same 50/50 fallback, `foreign_country=true`
- Tie in nearest office distance → deterministic tie-break by lowest `office_id`

### Assignment Fairness
- VIP with no VIP managers → escalation/unassigned (never assign to non-VIP)
- Invalid load values (missing/negative) → coerce to large value, `invalid_load=true`
- RR state missing (first run) → initialize pointer to 0
- Concurrency → DB transaction for atomic RR updates

### Observability
- `correlation_id` on every ticket for traceability
- Full `routing_trace` stored in DB for audit/explainability
- `flags` dict captures all edge-case triggers

---

## Setup

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. PostgreSQL
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'admin';"
sudo -u postgres createdb fire_db

# 4. Configure environment
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql://postgres:admin@localhost:5432/fire_db
#   DEEPSEEK_API_KEY=sk-your-key
#   OLLAMA_BASE_URL=http://localhost:11434

# 5. Seed DB
python -m database.seed

# 6. Run
streamlit run app.py
# Default admin login: admin / admin
```

## AI Modes

| Mode | Config | Notes |
|------|--------|-------|
| **DeepSeek** | Set `DEEPSEEK_API_KEY` in `.env` | Cloud API, fast, requires internet |
| **Phi-4 Local** | Run `ollama serve` + `ollama pull phi4` | Fully offline, requires ~8GB RAM |

Switch between modes in the Admin Dashboard → Batch Routing tab.

## Tech Stack

Python 3.11+, Streamlit, SQLAlchemy, PostgreSQL, OpenAI SDK (for DeepSeek + Ollama), Pandas, bcrypt, Tesseract/EasyOCR
