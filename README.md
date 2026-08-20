# OFA AI Assist — Finance Knowledge Assistant

A production-grade, RAG-powered finance assistant that answers user questions **strictly** from official Knowledge Base documents for finance modules (AP, AR, FA, GL, OTL, PA) stored in SharePoint — powered by Azure OpenAI GPT-4o, Azure AI Search, and LangChain.

---

## Architecture Overview

```
SharePoint (Knowledge Base Docs)
       │
       ▼
┌─────────────────────┐
│  Ingestion Pipeline │  ← run_ingestion.py / POST /api/v1/ingestion/run
│  • Download files   │
│  • Extract text     │
│  • Clean & chunk    │
│  • Embed (ADA-3)    │
│  • Upsert to Search │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Azure AI Search    │  ← Vector + BM25 Hybrid + Semantic re-ranking
│  (ofa-finance-kb idx)│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐     ┌─────────────────────┐
│  FastAPI Backend    │◄────│  Streamlit Frontend  │
│  /api/v1/chat       │     │  Chat UI + Sources   │
│  /api/v1/chat/stream│     └─────────────────────┘
│  /api/v1/ingestion  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Azure OpenAI       │
│  GPT-4o (chat)      │
└─────────────────────┘
```

---

## Project Structure

```
OFA-AI-Assist/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── chat.py          # Chat endpoints (streaming + non-streaming)
│   │       ├── health.py        # Liveness probe
│   │       └── ingestion.py     # Ingestion trigger endpoints
│   ├── config/
│   │   └── settings.py          # Pydantic-settings config (all env vars)
│   ├── models/
│   │   └── chat.py              # Shared Pydantic data models
│   ├── prompts/
│   │   └── finance_prompts.py   # All LLM prompts (hallucination prevention)
│   ├── rag/
│   │   ├── document_processor.py # Extract → clean → chunk → hash
│   │   └── sharepoint_loader.py  # SharePoint authentication & download
│   ├── services/
│   │   ├── embedding_service.py  # Azure OpenAI embeddings
│   │   ├── ingestion_service.py  # Full ingestion orchestration
│   │   ├── llm_service.py        # GPT-4o wrapper
│   │   ├── rag_service.py        # RAG pipeline (query → answer)
│   │   └── search_service.py     # Azure AI Search (index + hybrid search)
│   └── utils/
│       └── logger.py             # Structured JSON logging (structlog)
│   └── main.py                   # FastAPI app entry point
│
├── ingestion/
│   └── run_ingestion.py          # CLI script for scheduled indexing
│
├── streamlit_app/
│   └── app.py                    # Streamlit chat UI
│
├── tests/
│   ├── test_document_processor.py
│   ├── test_rag_service.py
│   └── test_api_health.py
│
├── logs/                         # Structured JSON log files
├── data/                         # (gitignored) local doc cache
├── .env.example                  # Environment variable template
├── requirements.txt
├── pyproject.toml                # Pytest configuration
├── Dockerfile                    # FastAPI backend container
├── Dockerfile.streamlit          # Streamlit UI container
└── docker-compose.yml            # Full-stack local development
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | |
| Azure OpenAI resource | GPT-4o + text-embedding-3-large deployments |
| Azure AI Search (Standard tier) | Needed for vector search |
| Azure AD App Registration | `Sites.Read.All` permission for SharePoint |
| SharePoint Online | HR documents in a dedicated folder |
| Docker Desktop | For containerised deployment |

---

## Quick Start

### 1. Clone & configure

```bash
git clone <repo-url>
cd OFA-AI-Assist
cp .env.example .env
# Edit .env with your Azure credentials
```

### 2. Create virtual environment & install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
```

### 3. Run the ingestion pipeline

```bash
python ingestion/run_ingestion.py --mode full
```

### 4. Start the FastAPI backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Start the Streamlit UI

```bash
streamlit run streamlit_app/app.py
```

Open http://localhost:8501 in your browser.

---

## Docker Deployment

```bash
# Build & start both containers
docker-compose up --build

# Run ingestion inside the API container
docker exec ofa-ai-assist-api python ingestion/run_ingestion.py --mode full
```

---

## Azure Configuration

### Azure OpenAI

1. Create an Azure OpenAI resource in the Azure Portal.
2. Deploy **GPT-4o** → note the deployment name → set `AZURE_OPENAI_CHAT_DEPLOYMENT`.
3. Deploy **text-embedding-3-large** → set `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`.
4. Copy the endpoint and API key → set `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`.

### Azure AI Search

1. Create an Azure AI Search resource (Standard S1 or higher for vector search).
2. The application **auto-creates the index** on first startup — no manual setup needed.
3. Copy endpoint + Admin key → set `AZURE_SEARCH_ENDPOINT` and `AZURE_SEARCH_API_KEY`.

### SharePoint App Registration

1. In Azure AD → App registrations → New registration.
2. Add application permission: **SharePoint → Sites.Read.All**.
3. Grant admin consent.
4. Create a client secret.
5. Set `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID`, `SHAREPOINT_CLIENT_SECRET`.

---

## API Reference

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Liveness probe |
| POST | `/api/v1/chat` | Non-streaming finance Knowledge Base question |
| POST | `/api/v1/chat/stream` | SSE streaming finance Knowledge Base question |
| POST | `/api/v1/ingestion/run` | Full SharePoint re-index |
| POST | `/api/v1/ingestion/incremental` | Incremental indexing |

Swagger UI available at http://localhost:8000/docs (development only).

---

## Running Tests

```bash
pytest
# With coverage:
pytest --cov=app --cov-report=html
```

---

## Hallucination Prevention

The assistant is engineered to **never fabricate** information:

1. **Strict system prompt** — explicitly prohibits using pre-training knowledge.
2. **Context-only answers** — the LLM receives only the retrieved chunks as its knowledge source.
3. **Out-of-scope sentinel** — if context is insufficient, the model must return a fixed string; sources are cleared.
4. **Temperature = 0** — deterministic outputs reduce creative hallucination.
5. **Score threshold** — chunks below the relevance threshold are discarded before being sent to the LLM.

---

## License

Internal use only. Not for public distribution.
