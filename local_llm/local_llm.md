# Local AI Architecture Report

## Project Goal

Deploy a fully local AI stack capable of:

1. Ticket text analysis (classification, extraction, summarization)
2. Image understanding for attached screenshots/documents
3. Admin chatbot for statistics → SQL → graph visualization (via Vanna AI)

Hardware Available:

* RTX 5000 (32 GB VRAM)
* 50+ GB system RAM
* On-prem stationary PC (local network)

---

# 1. Final Model Selection Strategy

## 1.1 Text LLM (Primary Language Model)

### Responsibilities

* Ticket classification into predefined categories
* Entity extraction (amounts, cities, tickers, IDs)
* Fraud signal detection
* Structured JSON output
* Admin chatbot intent detection
* NL → query plan → Vanna handoff
* Provide guidance instructions for image extraction when attachments exist

### Design Requirements

* Strong multilingual capability (RU/KZ/EN)
* Deterministic JSON output
* Low latency (< 500ms typical)
* Tool-calling capable

### Deployment

* Runs locally (GPU or hybrid GPU+CPU offload)
* Separate service: `text-llm-service`
* JSON schema enforced via validator layer

---

## 1.2 Vision-Language Model (VLM)

### Responsibilities

* Screenshot understanding
* Order error detection
* Receipt interpretation
* UI state reasoning
* Multimodal reasoning (image + short contextual hints)

### Design Requirements

* Structured JSON output only
* Called only for attachments
* 1–4 sec latency acceptable

### Deployment

* Dedicated GPU service: `vision-service`
* Receives:

  * Image file
  * Short extraction instructions from Text LLM
  * Minimal contextual hints (ticker, expected fields)
* Schema validation required

---

# 2. Core Processing Strategy (Text + Image Split)

When a ticket contains images:

## Step 1 — Always run Text LLM first

Input:

* Ticket body
* Subject
* Metadata

Output:

* Draft structured JSON
* Expected fields from images
* Image extraction instructions
* Risk score

## Step 2 — Run VLM only on attachments

For each image:

* Provide image
* Provide short structured instruction from Step 1

Output:

* Image-specific structured JSON

## Step 3 — Deterministic Merge Layer

* Merge text JSON + image JSON
* Normalize formats
* Validate fields
* Compute confidence score

This avoids running the VLM on text and prevents unnecessary GPU usage.

---

# 3. Supporting Deterministic Tools (Low Compute Cost)

These reduce VLM usage and improve reliability.

## 3.1 OCR (Secondary Tool)

* Crop-based OCR only
* Used for numeric verification
* Used when dense text extraction required

## 3.2 Validators

* Regex for phone/IIN/card masks
* Amount normalization
* Date normalization
* Enum mapping for ticket types

## 3.3 Image Template Matching

* Perceptual hash (pHash)
* Known error screen templates
* Skip VLM for recurring layouts

## 3.4 Embeddings + Vector Search

* Deduplicate spam
* Cluster similar tickets
* Find similar past incidents

## 3.5 Geocoding (Offline)

* Local city dictionary
* Fuzzy match correction
* City → office mapping

## 3.6 Document Parsers

* PDF extraction (PyMuPDF)
* DOCX/XLSX parsing
* QR decoding

---

# 4. System Architecture

```
                ┌──────────────┐
                │   Web UI     │
                └──────┬───────┘
                       │
             ┌─────────▼──────────┐
             │  Orchestrator API  │
             └──────┬─────────────┘
                    │
            ┌───────▼────────┐
            │  Text LLM      │
            └───────┬────────┘
                    │
        ┌───────────┴─────────────┐
        │                         │
 (no image)                 (image attached)
        │                         │
        │                  ┌──────▼─────────┐
        │                  │ Vision LLM     │
        │                  └──────┬─────────┘
        │                         │
        └───────────────┬─────────┘
                        ▼
                Validation Layer
                        ▼
                     Database
                        ▼
                    Vanna AI
                        ▼
                     Charts
```

---

# 5. Routing Logic

## 5.1 Ticket Processing

IF no image:

* Text LLM → JSON
* Validate → Store

IF image present:

* Text LLM first
* Template match check

  * If known layout → deterministic extraction
  * Else → Vision LLM
* Merge results
* Validate

IF validation fails:

* Retry repair prompt (component-specific)
* Optional DeepSeek fallback

---

## 5.2 Admin Chatbot Flow

User query → Text LLM

Text LLM returns:

* Query template ID
* Parameters

Backend executes safe parameterized SQL

Vanna AI builds chart

UI renders graph

VLM is never used in chatbot flow.

---

# 6. Performance Expectations

| Task                  | Model    | Latency     |
| --------------------- | -------- | ----------- |
| Ticket classification | Text LLM | ~200–500 ms |
| Admin chatbot intent  | Text LLM | ~200–400 ms |
| Screenshot analysis   | VLM      | ~2–4 s      |

Concurrent managers supported with proper worker queueing.

---

# 7. DeepSeek Usage Strategy

DeepSeek remains optional fallback for:

* Low-confidence fraud cases
* Legal claim tickets
* Complex multilingual edge cases
* Benchmark evaluation

Primary operation is fully local.

---

# 8. Reliability Safeguards

* JSON schema enforcement
* Field-level validators
* Confidence scoring
* Image hash deduplication
* Full audit logging
* Rate limiting
* Caching by text/image hash

Never allow free-form LLM outputs to drive business logic.

---

# 9. Final Architecture Decision

Run:

* 1 Local Text LLM (always first in pipeline)
* 1 Local Vision-Language Model (attachments only)
* Deterministic tool layer
* SQL + Vanna for visualization

This provides:

* Lower latency for text tasks
* Reduced GPU contention
* Cost control
* Full data privacy
* Production-grade reliability

---

End of Report.
