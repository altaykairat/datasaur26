# 🔥 F.I.R.E. — Freedom Intelligent Routing Engine

Customer Support Dispatch System with AI-powered ticket enrichment and rule-based routing.

## What It Does

FIRE takes customer support tickets (live or batch CSV) and automatically:
1. **Validates** the CSV schema and detects duplicate tickets
2. **Analyzes** each ticket using AI (DeepSeek API or local Phi-4 via Ollama) — extracts type, priority, language, sentiment, address, and summary
3. **Routes** it to the best office via geo-location (Haversine distance, deterministic 50/50 fallback)
4. **Filters** managers by hard skill rules (VIP, role, language) with a 5-level graduated cascade
5. **Assigns** via round-robin between the top-2 lowest-load managers

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

## Project Structure

```
├── app.py                  # Streamlit entry point, login/register, CSS theme
├── config.py               # Reads DATABASE_URL, DEEPSEEK_API_KEY, OLLAMA_BASE_URL from .env
├── database/
│   ├── models.py           # ORM: User, Manager, Ticket, Office, RoundRobinState
│   ├── connection.py       # Engine + session factory
│   └── seed.py             # Loads managers.csv + business_units.csv into DB
├── engine/
│   ├── intelligence.py     # AI wrapper: DeepSeek/Phi-4, bounded retries, summary, fallback flags
│   └── router.py           # TicketRouter: validate → enrich → geo-route → skill-filter → RR balance
├── views/
│   ├── admin.py            # CSV upload, batch routing, stats, management tools
│   ├── customer.py         # Create ticket, view my tickets
│   └── manager.py          # View tickets with AI summary, routing trace, flags; close tickets
├── utils/
│   ├── geo.py              # Haversine distance, 30+ Kazakhstan city coordinates, fuzzy matching
│   └── auth.py             # bcrypt password hashing
├── input/
│   ├── managers.csv        # 51 managers with roles, skills, offices, current load
│   ├── business_units.csv  # 16 office locations across Kazakhstan
│   └── tickets.csv         # Sample tickets for testing batch routing
├── requirements.txt
├── .env.example
└── .gitignore
```

## Routing Pipeline (engine/router.py)

### Step 1 — CSV Validation
- Checks required columns exist (`GUID клиента`, `Описание`, `Сегмент клиента`, `Населённый пункт`, `Область`)
- Duplicate GUIDs → skipped, flagged `duplicate_ticket_id`
- Bad rows → `DeadLetter` status, batch continues

### Step 2 — AI Enrichment (`engine/intelligence.py`)
The AI analyzes ticket text and returns:
- **type**: 7 enums (Жалоба / Смена данных / Консультация / Претензия / Неработоспособность приложения / Мошеннические действия / Спам)
- **priority**: 1–10 (clamped)
- **language**: RU / KZ / ENG (default RU)
- **sentiment**: Позитивный / Нейтральный / Негативный
- **normalized_address**: extracted city/street or "Unknown"
- **summary**: 1–2 sentences + recommended next actions

Retries 2× on failure, then falls back to safe defaults with `ai_fallback=true`.

### Step 3 — Geo-Routing (`utils/geo.py`)
- Exact match → fuzzy match → region fallback → AI-extracted address
- If office city: direct route. If not: Haversine nearest office.
- Distance > 500km or unknown → deterministic 50/50 via `hash(ticket_id) % 2` to Астана/Алматы
- Flags: `address_unknown`, `foreign_country`, `geocode_failed`

### Step 4 — Skill Filter (graduated cascade)
1. **Strict**: VIP skill + role + language in target office
2. **Relax language**: drop KZ/ENG, keep VIP + role
3. **Expand offices**: Астана + Алматы, keep VIP + role
4. **VIP escalation**: never relax VIP → unassigned
5. **Global fallback**: lowest-load Глав спец, then any active manager

### Step 5 — Round Robin Load Balance
- Sort by `(current_load, manager_id)`, pick **top 2**
- Alternate via persisted RR pointer in `rr_state` table
- `workload_at_assignment` recorded

### Routing Trace & Flags
Every ticket stores a full `routing_trace` JSON (geo decision, skill filter, load balance details) and `flags` dict for audit/explainability.

## Database Schema (database/models.py)

| Table | Key Columns |
|-------|-------------|
| `users` | id, username, password_hash, role (customer/manager/admin), manager_id (FK) |
| `managers` | id, name, role, skills (JSON), office_location, current_load, **is_active** |
| `tickets` | id, client_guid, **correlation_id**, description, status (10 states), assigned_manager_id, ai_analysis_json, segment, **office_rule**, **routing_trace** (JSON), **flags** (JSON), **workload_at_assignment**, created_at |
| `offices` | id, city, address, lat, lon |
| `rr_state` | id, office_city, candidate_key, pointer |

### Ticket States
```
New → Ingested → Queued → Enriching → Enriched → Routing → Assigned → Closed
                                                         ↘ RoutingFailed
                                  ↘ EnrichFailed
                                                                    ↘ DeadLetter
```

## Setup

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. PostgreSQL (Ubuntu/Debian instructions)
sudo apt update
sudo apt install -y postgresql-16 postgresql-contrib postgresql-client-16
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

Python 3.11+, Streamlit, SQLAlchemy, PostgreSQL, OpenAI SDK (for DeepSeek + Ollama), Pandas, bcrypt
