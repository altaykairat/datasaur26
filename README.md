# 🔥 F.I.R.E. — Freedom Intelligent Routing Engine

Customer Support Dispatch System with AI-powered ticket enrichment and rule-based routing.

## What It Does

FIRE takes customer support tickets (live or batch CSV) and automatically:
1. **Analyzes** each ticket using AI (DeepSeek API or local Phi-4 via Ollama)
2. **Routes** it to the best manager using geo-location, skill matching, and load balancing

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Streamlit   │────▶│  TicketRouter    │────▶│  PostgreSQL DB  │
│  UI (app.py) │     │  (engine/)       │     │  (database/)    │
└─────────────┘     └──────┬───────────┘     └─────────────────┘
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
│   ├── models.py           # SQLAlchemy ORM: User, Manager, Ticket, Office
│   ├── connection.py       # Engine + session factory
│   └── seed.py             # Loads managers.csv + business_units.csv into DB
├── engine/
│   ├── intelligence.py     # AI wrapper: DeepSeek API or Phi-4 (Ollama), returns JSON analysis
│   └── router.py           # TicketRouter: geo-route → skill-filter → load-balance pipeline
├── views/
│   ├── admin.py            # CSV upload, batch routing, live stats/charts, AI mode toggle
│   ├── customer.py         # Create ticket, view my tickets
│   └── manager.py          # View assigned tickets, close tickets
├── utils/
│   ├── geo.py              # Haversine distance, 30+ Kazakhstan city coordinates, fuzzy matching
│   └── auth.py             # bcrypt password hashing
├── managers.csv            # 51 managers with roles, skills, offices, current load
├── business_units.csv      # 16 office locations across Kazakhstan
├── tickets.csv             # Sample tickets for testing batch routing
├── requirements.txt
├── .env.example            # Template for environment variables
└── .gitignore
```

## Routing Pipeline (engine/router.py)

Each ticket goes through 3 steps:

### Step 1 — AI Enrichment (`engine/intelligence.py`)
The AI analyzes the ticket description and returns:
- **type**: Жалоба / Смена данных / Консультация / Претензия / Неработоспособность приложения / Мошеннические действия / Спам
- **priority**: 1–10
- **language**: RU / KZ / ENG
- **sentiment**: Positive / Neutral / Negative
- **normalized_address**: extracted city/street or "Unknown"

### Step 2 — Geo-Routing (`utils/geo.py`)
- Match the client's city to the nearest office using Haversine distance
- If distance > 500km or city unknown → random fallback to Astana or Almaty

### Step 3 — Skill Filter + Load Balance
- **VIP segment** → manager must have "VIP" skill
- **Type = "Смена данных"** → manager must be "Главный специалист"
- **Language KZ/ENG** → manager must have that language skill
- From eligible managers, pick randomly from the 2 with lowest `current_load`
- If no eligible manager found → fallback pool from Astana/Almaty

## Database Schema (database/models.py)

| Table | Key Columns |
|-------|-------------|
| `users` | id, username, password_hash, role (customer/manager/admin), manager_id (FK) |
| `managers` | id, name, role, skills (JSON array), office_location, current_load |
| `tickets` | id, customer_id, description, status (New/Assigned/Closed), assigned_manager_id, ai_analysis_json, segment, created_at |
| `offices` | id, city, address, lat, lon |

## Setup

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. PostgreSQL (Ubuntu/Debian instructions)
# Install PostgreSQL 16 server and client if not installed
sudo apt update
sudo apt install -y postgresql-16 postgresql-contrib postgresql-client-16

# Set a password for the default 'postgres' user (needed for the app)
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'admin';"

# Create the database using the postgres system user
sudo -u postgres createdb fire_db

# 4. Configure environment
cp .env.example .env
# Edit .env and ensure DATABASE_URL matches the password set above:
#   DATABASE_URL=postgresql://postgres:admin@localhost:5432/fire_db
#   DEEPSEEK_API_KEY=sk-your-key
#   OLLAMA_BASE_URL=http://localhost:11434

# 5. DB Initialization
# NOTE: Ensure managers.csv and business_units.csv are placed in the root directory
# alongside app.py, not inside an /input/ folder.
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
