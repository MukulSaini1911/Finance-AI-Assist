# OFA AI Assist - Complete Project Report

## 1. Project Overview

OFA AI Assist is an enterprise finance knowledge assistant built using a Retrieval-Augmented Generation (RAG) architecture.
It answers user questions strictly from organization-approved Knowledge Base documents for finance modules such as:

- AP (Accounts Payable)
- AR (Accounts Receivable)
- FA (Fixed Assets)
- GL (General Ledger)
- OTL (Oracle Time and Labor)
- PA (Project Accounting)

The solution combines a FastAPI backend, a Streamlit frontend, Azure OpenAI for LLM and embeddings, Azure AI Search for hybrid retrieval, and SharePoint as the source knowledge repository.

---

## 2. Business Objective

The primary objective is to provide a reliable internal assistant that:

- Responds with policy/process-grounded answers only.
- Reduces manual effort in resolving finance process questions.
- Uses source citations for traceability and auditability.
- Prevents hallucinations by enforcing strict prompt and retrieval controls.

---

## 3. End-to-End Architecture

### High-level flow

1. Knowledge documents are stored in SharePoint (or local Knowledge Base for local ingestion mode).
2. Ingestion pipeline extracts, cleans, chunks, and embeds document text.
3. Embedded chunks are indexed in Azure AI Search.
4. User asks a question through Streamlit UI.
5. Backend converts question (and history) into retrieval query.
6. Hybrid retrieval returns top relevant chunks.
7. Azure OpenAI generates grounded answer using only retrieved context.
8. UI shows answer plus source citations.

### Runtime components

- Backend API: FastAPI
- Frontend UI: Streamlit
- Retrieval index: Azure AI Search
- Generative model and embeddings: Azure OpenAI
- Document source: SharePoint and local Knowledge Base folder
- Logging: Structlog JSON-style logs

---

## 4. Project Structure and Responsibilities

### Core backend

- app/main.py
  - FastAPI app factory
  - Lifespan startup checks
  - CORS
  - middleware and exception handling
  - router mounting

- app/config/settings.py
  - Centralized environment-driven configuration via pydantic-settings

- app/models/chat.py
  - Pydantic models for requests, responses, source documents, ingestion models

### API layer

- app/api/v1/chat.py
  - Non-stream endpoint: POST /api/v1/chat
  - Stream endpoint: POST /api/v1/chat/stream (SSE)

- app/api/v1/ingestion.py
  - POST /api/v1/ingestion/run
  - POST /api/v1/ingestion/incremental
  - Ingestion API key guard

- app/api/v1/health.py
  - GET /api/v1/health liveness endpoint

### RAG and processing layer

- app/services/rag_service.py
  - Full query pipeline orchestration
  - follow-up condensation
  - embedding + hybrid search
  - prompt invocation and answer generation
  - SSE token streaming

- app/services/search_service.py
  - Azure AI Search index creation, upsert, hybrid retrieval, dedupe checks

- app/services/embedding_service.py
  - Embedding generation via Azure OpenAI embeddings
  - optional local fallback embeddings for testing mode

- app/services/llm_service.py
  - AzureChatOpenAI wrapper for streaming and non-streaming model instances

- app/services/ingestion_service.py
  - SharePoint ingestion orchestration (full and incremental)

### Document and connector utilities

- app/rag/document_processor.py
  - Extraction (PDF, DOCX, TXT, HTML)
  - cleaning and chunking
  - hash generation and metadata

- app/rag/sharepoint_loader.py
  - SharePoint authentication and document listing/downloading

### Prompt design

- app/prompts/finance_prompts.py
  - System prompt and out-of-scope sentinel
  - follow-up question condensation prompt

### Frontend

- streamlit_app/app.py
  - Chat UI, SSE rendering, citations, fallback behavior

### Ingestion CLIs

- ingestion/run_ingestion.py
  - SharePoint ingestion run helper

- ingestion/ingest_local.py
  - Local folder ingestion (recursive), optional fallback embeddings for testing

---

## 5. Complete Tech Stack

## 5.1 Programming Language and Runtime

- Python 3.11+ target (Docker image is Python 3.11-slim)
- Async programming with asyncio for non-blocking service calls

## 5.2 Backend Framework

- FastAPI
  - REST APIs
  - dependency injection
  - automatic OpenAPI docs
  - response model validation

## 5.3 Frontend Framework

- Streamlit
  - rapid UI development
  - chat-like interaction surface
  - integrated state management via session_state

## 5.4 AI and LLM Frameworks

- LangChain ecosystem
  - langchain
  - langchain-core
  - langchain-openai
  - langchain-community
  - langchain-text-splitters

## 5.5 Cloud and Search Stack

- Azure OpenAI (chat + embeddings)
- Azure AI Search (vector + lexical + semantic)
- Microsoft 365 SharePoint (knowledge source)

## 5.6 Containerization and Deployment

- Docker multi-stage backend image
- Separate Streamlit Docker image
- docker-compose for full local stack orchestration

## 5.7 Validation, Config, and Logging

- pydantic and pydantic-settings
- python-dotenv
- structlog

## 5.8 Testing Stack

- pytest
- pytest-asyncio
- pytest-cov
- respx

---

## 6. Azure Services Used (Detailed)

## 6.1 Azure OpenAI Service

Purpose:
- Generate final answers (chat model)
- Generate vector embeddings for chunks and user queries

Configuration fields:
- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_API_KEY
- AZURE_OPENAI_API_VERSION
- AZURE_OPENAI_CHAT_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_DIMENSIONS

Runtime integration:
- Chat model in app/services/llm_service.py using AzureChatOpenAI
- Embeddings in app/services/embedding_service.py using AzureOpenAIEmbeddings

## 6.2 Azure AI Search

Purpose:
- Store chunked finance knowledge documents
- Hybrid retrieval for best recall and precision
- Semantic reranking for improved final relevance

Configuration fields:
- AZURE_SEARCH_ENDPOINT
- AZURE_SEARCH_API_KEY
- AZURE_SEARCH_INDEX_NAME
- AZURE_SEARCH_SEMANTIC_CONFIG

Index schema highlights:
- Key field: document_id
- Main content field: content
- Vector field: embedding
- Metadata fields for filename, title, section, page, hash, chunk metadata

Search mode:
- BM25 text search + vector similarity + semantic ranking

## 6.3 Azure AD App Registration (for SharePoint App-only Access)

Purpose:
- Secure machine-to-machine access to SharePoint files

Typical permission:
- Sites.Read.All (application permission)

Configuration fields:
- SHAREPOINT_TENANT_ID
- SHAREPOINT_CLIENT_ID
- SHAREPOINT_CLIENT_SECRET

## 6.4 SharePoint Online

Purpose:
- Primary source of official finance Knowledge Base documents

Configuration fields:
- SHAREPOINT_SITE_URL
- SHAREPOINT_FOLDER_PATH

---

## 7. Python Packages and Why They Are Used

## 7.1 Web/API and HTTP

- fastapi: backend framework and route handling
- uvicorn[standard]: ASGI server for FastAPI
- httpx: HTTP client used by Streamlit and health checks
- python-multipart: form/data upload support in FastAPI

## 7.2 AI and Prompt Orchestration

- langchain: orchestration of chains/prompts
- langchain-core: core message and runnable abstractions
- langchain-openai: Azure OpenAI adapters for chat and embeddings
- langchain-community: broader integration support
- langchain-text-splitters: recursive text chunking
- openai: underlying OpenAI client dependency
- tiktoken: token accounting support

## 7.3 Azure SDKs

- azure-identity: Azure identity auth helpers
- azure-search-documents: Azure AI Search indexing and querying
- azure-storage-blob: optional blob support for future extensions

## 7.4 SharePoint Connector

- Office365-REST-Python-Client: SharePoint listing and download operations

## 7.5 Document Parsing

- pypdf: PDF text extraction
- python-docx: DOCX extraction
- beautifulsoup4: HTML text extraction
- lxml: parser backend for HTML/XML

## 7.6 Config, Logging, Reliability

- pydantic: strongly typed models and validation
- pydantic-settings: environment configuration model
- python-dotenv: local environment variable loading
- structlog: structured logging
- tenacity: retry utility (available for resilience workflows)

## 7.7 Testing

- pytest: test runner
- pytest-asyncio: async test support
- pytest-cov: coverage reporting
- respx: mock layer for httpx interactions

---

## 8. RAG Design and Hallucination Controls

The project uses multiple safeguards:

- Context-only answer policy in system prompt
- Out-of-scope sentinel response when context is insufficient
- Citation requirement in answer format
- Score threshold filtering in retrieval pipeline
- Optional greeting bypass for conversational UX

Prompt file:
- app/prompts/finance_prompts.py

---

## 9. Data Ingestion Design

## 9.1 SharePoint ingestion

Flow:
- list files recursively
- download content
- extract and clean text
- chunk text
- create embeddings
- upsert to Azure AI Search

Modes:
- full: re-index all files
- incremental: skip unchanged chunks based on content hash

## 9.2 Local ingestion

Use case:
- useful for local testing or when SharePoint access is unavailable

Capabilities:
- recursive folder traversal for nested module folders
- supports docx, pdf, txt, html, htm
- optional local embedding fallback mode for test environments

---

## 10. API Surface

- GET /api/v1/health
  - Liveness probe

- POST /api/v1/chat
  - Non-stream answer

- POST /api/v1/chat/stream
  - SSE token stream with final done event and sources

- POST /api/v1/ingestion/run
  - Full ingestion trigger

- POST /api/v1/ingestion/incremental
  - Incremental ingestion trigger

---

## 11. Security and Secrets Handling

Current controls:

- Sensitive values loaded through environment variables
- Pydantic SecretStr used for keys and secrets
- Ingestion API guarded by X-Ingestion-Key check

Recommendations for production hardening:

- Move secrets to Azure Key Vault
- Replace static ingestion key with Azure AD auth / RBAC
- Restrict CORS origins (remove wildcard in production)
- Add API gateway and request rate limiting
- Add audit logs for privileged ingestion operations

---

## 12. Deployment and Operations

## 12.1 Local

- Backend: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
- Frontend: streamlit run streamlit_app/app.py

## 12.2 Docker

- Multi-stage backend image in Dockerfile
- Streamlit image in Dockerfile.streamlit
- Combined stack in docker-compose.yml

## 12.3 Startup checks

At backend startup:
- Azure Search connectivity is validated
- Search index is auto-created if missing
- LLM client is initialized

---

## 13. Testing and Quality Status

Test framework configuration:
- pyproject.toml defines pytest options and testpaths

Current suite includes:
- API endpoint tests
- model validation tests
- document processor tests
- RAG and ingestion behavior tests
- Knowledge Base structure checks

Recent status observed during project setup:
- Full suite executed successfully with passing tests

---

## 14. Known Operational Lessons from This Project

- Knowledge Base document extraction needed DOCX table-aware parsing.
- Local ingestion required recursive scanning due to nested module folders.
- Search index schema mismatch can occur when reusing old indexes.
- Streaming UI must have a non-stream fallback to avoid blank responses.

---

## 15. Environment Variables Reference

## Core AI
- AZURE_OPENAI_ENDPOINT
- AZURE_OPENAI_API_KEY
- AZURE_OPENAI_API_VERSION
- AZURE_OPENAI_CHAT_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_DEPLOYMENT
- AZURE_OPENAI_EMBEDDING_DIMENSIONS

## Search
- AZURE_SEARCH_ENDPOINT
- AZURE_SEARCH_API_KEY
- AZURE_SEARCH_INDEX_NAME
- AZURE_SEARCH_SEMANTIC_CONFIG

## SharePoint
- SHAREPOINT_TENANT_ID
- SHAREPOINT_CLIENT_ID
- SHAREPOINT_CLIENT_SECRET
- SHAREPOINT_SITE_URL
- SHAREPOINT_FOLDER_PATH

## App
- APP_ENV
- APP_LOG_LEVEL
- APP_HOST
- APP_PORT

## RAG tuning
- RAG_CHUNK_SIZE
- RAG_CHUNK_OVERLAP
- RAG_TOP_K
- RAG_SCORE_THRESHOLD
- USE_LOCAL_EMBEDDING_FALLBACK

## Streamlit
- STREAMLIT_BACKEND_URL
- STREAMLIT_PAGE_TITLE

---

## 16. Suggested Next Improvements

- Add module-aware metadata filters (AP/AR/FA/GL/OTL/PA) directly in retrieval.
- Add evaluation harness for groundedness and citation correctness.
- Add tracing for latency breakdown (embed, search, generation).
- Add background workers for scheduled ingestion and large batch handling.
- Add role-based access controls per module if needed.

---

## 17. Conclusion

OFA AI Assist is a complete enterprise RAG platform for finance knowledge assistance.
It combines modern Python AI tooling, Azure AI services, and a production-ready API/UI architecture.
The system is designed for grounded answers, transparent citations, and scalable ingestion from enterprise knowledge repositories.
