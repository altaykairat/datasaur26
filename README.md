# 🔥 F.I.R.E. — Freedom Intelligent Routing Engine

Customer Support Dispatch System with AI-powered ticket enrichment, robust geo-routing, and intelligent rule-based load balancing.

## What It Does

FIRE takes customer support tickets (live or batch CSV) and automatically:
1. **Validates & Deduplicates:** Checks CSV schema, validates data, and utilizes client GUIDs for idempotency and duplicate ticket prevention.
2. **Analyzes (AI Enrichment):** Extracts data from text and attachments (using EasyOCR) and enriches it via DeepSeek API or local Phi-4 (via Ollama) to identify: type, priority, language, sentiment, address, and summary. It also determines an AI **Confidence Score**.
3. **Geo-Routes:** Maps the extracted client address to the exact, physically closest office branch utilizing OpenStreetMap (Nominatim) and Haversine distance, with fallback policies for unknown or foreign regions.
4. **Filters:** Selects the best available branch managers using a strict cascade of hard rules (VIP segments, manager roles, language compatibility).
5. **Assigns:** Balances heavy workloads evenly via a persisted Round-Robin state among the top-2 lowest-load eligible managers.
6. **Triages:** Overrides routing based on AI Confidence scores, escalating potential fraud and safely pushing unclear cases to a `NEEDS_CLARIFICATION` manual queue.

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

## Routing Pipeline (`engine/router.py`)

### Step 1 — Ingestion & Idempotency
- Validates the presence of required schema columns (`GUID клиента`, `Описание`, `Сегмент клиента`, `Населённый пункт`, `Область`).
- Performs an OCR pass on available attachments using `easyocr`.
- Duplicate `GUID`s are automatically skipped and flagged `duplicate_ticket_id`.

### Step 2 — AI Enrichment (`engine/intelligence.py`)
Tickets are analyzed, and structural inferences naturally resolve resolving:
- **type**: Жалоба / Смена данных / Консультация / Претензия / Неработоспособность приложения / Мошеннические действия / Спам
- **confidence**: 0.0 to 1.0 logic (drives fallback queues)
- **normalized_address**: City, Street (no hallucinations) 
- Retries 2× on failure with safe defaults.

### Step 3 — Confidence & Override Triaging
Based on the defined operational requirements, cases are triaged to govern safety:
- **High Confidence (>= 0.85):** Streamlined normal assignment.
- **Medium Confidence (0.60 - 0.84):** Assigned normally but flagged for review (`needs_review=True`).
- **Low Confidence (< 0.60):** Pauses routing pipeline immediately and shifts ticket to `NEEDS_CLARIFICATION` state (unless Spam).
- **Fraud Overrides:** `Мошеннические действия` tickets with `< 0.80` are forcefully flagged for review instead of suspension to maintain prompt SLA handling.

### Step 4 — Robust Geo-Routing (`utils/geo.py`)
- Leverages locally mapped dictionaries and external Nominatim lookups to find absolute geographic coordinate values.
- Determines the exact office branch inside a matching city using street-level Haversine distance.
- Dispatches unidentifiable/isolated queries evenly via a deterministic `hash(ticket_id) % 2` 50/50 fallback to Astana/Almaty.

### Step 5 — Skill Cascade
Filters the locally identified branch's managers down:
1. **Strict:** VIP classification + Required Role + Required Language in target branch.
2. **Relax language:** Temporarily drops KZ/ENG constraint.
3. **Expand City:** Steps outside the nearest branch to check the parent city branches.
4. **Global Expand:** Dispatches to Almaty / Astana / System-wide.
5. **VIP Exception:** Strict escalation bounds never drop VIP assignments. 

### Step 6 — Round-Robin Load Balance
- Sorts the qualified candidate pool by `(current_load, manager_id)`, and extracts the top 2.
- Ping-pongs assignments precisely via a database-persisted circular round-robin pointer.

## Setup

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. PostgreSQL Database
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'admin';"
sudo -u postgres createdb fire_db

# 4. Configure environment
cp .env.example .env
# Edit .env:
#   DATABASE_URL=postgresql://postgres:admin@localhost:5432/fire_db
#   DEEPSEEK_API_KEY=your-api-key

# 5. DB Migration & Seed
python -m database.seed

# 6. Boot Streamlit Subsystem
streamlit run app.py
```

## AI Operation Modes

| Mode | Notes |
|------|-------|
| **DeepSeek** | Cloud API, robust and heavily compliant prompt interpretation. Needs API key. |
| **Phi-4 Local** | Completely offline network execution. Needs `ollama serve` and min `8GB RAM`. |
