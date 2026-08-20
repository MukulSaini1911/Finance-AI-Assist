# =============================================================================
# streamlit_app/app.py
#
# OFA AI Assist — Streamlit Chat UI
#
# Features:
#   • Streaming responses via SSE (server-sent events)
#   • Chat history preserved in st.session_state
#   • Source document citations displayed as expandable cards
#   • Clear conversation button
#   • Professional enterprise styling
#   • Dark/light mode compatible (uses Streamlit's built-in theming)
# =============================================================================

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BACKEND_URL = os.getenv("STREAMLIT_BACKEND_URL", "http://localhost:8000")
PAGE_TITLE = os.getenv("STREAMLIT_PAGE_TITLE", "OFA AI Assist — Finance Assistant")
STREAM_ENDPOINT = f"{BACKEND_URL}/api/v1/chat/stream"
CHAT_ENDPOINT = f"{BACKEND_URL}/api/v1/chat"

from pathlib import Path as _Path

# ---------------------------------------------------------------------------
# Page configuration (must be the first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — subtle enterprise polish without overriding Streamlit's theme
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Chat message bubbles */
    .user-bubble {
        background: linear-gradient(135deg, #0078d4 0%, #005a9e 100%);
        color: white;
        border-radius: 18px 18px 4px 18px;
        padding: 12px 16px;
        margin: 4px 0;
        max-width: 80%;
        margin-left: auto;
        word-wrap: break-word;
    }
    .assistant-bubble {
        background: var(--secondary-background-color, #f0f2f6);
        border-radius: 18px 18px 18px 4px;
        padding: 12px 16px;
        margin: 4px 0;
        max-width: 80%;
        word-wrap: break-word;
    }
    /* Source citation cards */
    .source-card {
        border-left: 4px solid #0078d4;
        padding: 8px 12px;
        margin: 4px 0;
        border-radius: 0 8px 8px 0;
        font-size: 0.85em;
    }
    /* Typing indicator */
    .typing-indicator span {
        animation: blink 1.4s infinite both;
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        background: #0078d4;
        margin: 0 2px;
    }
    .typing-indicator span:nth-child(2) { animation-delay: 0.2s; }
    .typing-indicator span:nth-child(3) { animation-delay: 0.4s; }
    @keyframes blink {
        0%, 80%, 100% { transform: scale(0.8); opacity: 0.5; }
        40% { transform: scale(1.0); opacity: 1; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _init_session_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid4())
    if "messages" not in st.session_state:
        # Each message: {"role": "user"|"assistant", "content": str, "sources": list}
        st.session_state.messages = []
    if "is_loading" not in st.session_state:
        st.session_state.is_loading = False


_init_session_state()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    _logo = _Path(__file__).parent.parent / "Images" / "Sopra-Steria-logo.png"
    st.image(str(_logo), width=100)
    st.title("OFA AI Assist")
    st.caption("Enterprise Finance Knowledge Assistant")
    st.divider()

    st.markdown("**About**")
    st.markdown(
        "I answer finance process questions based **only** on official Knowledge Base documents. "
        "I will not guess or fabricate information."
    )
    st.divider()

    st.markdown("**Suggested Questions**")
    suggested_questions = [
        "What is the AP invoice matching process?",
        "How do we process AR receipt application?",
        "What is the FA capitalization threshold?",
        "Which GL period close steps are mandatory?",
        "How are OTL hours posted to projects?",
    ]
    for q in suggested_questions:
        if st.button(q, key=f"sq_{q[:20]}", use_container_width=True):
            st.session_state._prefill_question = q

    st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.session_id = str(uuid4())
            st.rerun()
    with col2:
        st.caption(f"Session: `{st.session_state.session_id[:8]}…`")


# ---------------------------------------------------------------------------
# Main area — header
# ---------------------------------------------------------------------------

st.title("🧑‍💻 OFA AI Assist — Finance Assistant")
st.caption(
    "Ask me anything about AP, AR, FA, GL, OTL, PA, and related finance procedures."
)
st.divider()


# ---------------------------------------------------------------------------
# Helper — render source citation cards
# ---------------------------------------------------------------------------

def _render_sources(sources: list[dict[str, Any]]) -> None:
    """Render source documents as expandable citation cards."""
    if not sources:
        return

    with st.expander(f"📄 Sources ({len(sources)} document{'s' if len(sources) > 1 else ''})"):
        for i, src in enumerate(sources, start=1):
            score = src.get("relevance_score")
            score_badge = f"  `{score:.0%}`" if score else ""
            filename = src.get("filename", "Unknown")
            page = src.get("page_number")
            section = src.get("section")

            header = f"**[{i}] {filename}**"
            if page:
                header += f" — Page {page}"
            if section:
                header += f" — *{section}*"
            header += score_badge

            st.markdown(f'<div class="source-card">{header}</div>', unsafe_allow_html=True)
            excerpt = src.get("excerpt", "")
            if excerpt:
                st.markdown(f"> {excerpt[:300]}{'…' if len(excerpt) > 300 else ''}")

            url = src.get("url")
            if url:
                st.markdown(f"[Open in SharePoint ↗]({url})")
            st.divider()


# ---------------------------------------------------------------------------
# Render conversation history
# ---------------------------------------------------------------------------

chat_container = st.container()

with chat_container:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🧑‍💼" if msg["role"] == "user" else "🤖"):
            st.markdown(msg["content"])
            if msg.get("sources"):
                _render_sources(msg["sources"])


# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------

# Handle pre-filled question from sidebar buttons
prefill = st.session_state.pop("_prefill_question", None)
user_input: str | None = st.chat_input(
    "Ask a finance Knowledge Base question…",
    disabled=st.session_state.is_loading,
)

if prefill and not user_input:
    user_input = prefill

if user_input:
    # Append user message to history immediately (optimistic UI)
    st.session_state.messages.append({"role": "user", "content": user_input, "sources": []})
    st.session_state.is_loading = True

    with chat_container:
        with st.chat_message("user", avatar="🧑‍💼"):
            st.markdown(user_input)

        # Typing indicator
        with st.chat_message("assistant", avatar="🤖"):
            typing_placeholder = st.empty()
            typing_placeholder.markdown(
                '<div class="typing-indicator"><span></span><span></span><span></span></div>',
                unsafe_allow_html=True,
            )

            # Build conversation history payload (exclude last user message — already in body)
            history_payload = [
                {"role": m["role"], "content": m["content"]}
                for m in st.session_state.messages[:-1]  # All but the latest
            ]

            request_body = {
                "session_id": st.session_state.session_id,
                "message": user_input,
                "conversation_history": history_payload,
                "stream": True,
            }

            # ---------------------------------------------------------------
            # Stream response via SSE
            # ---------------------------------------------------------------
            answer_text = ""
            sources: list[dict[str, Any]] = []
            error_occurred = False
            stream_error_message: str | None = None

            try:
                with httpx.stream(
                    "POST",
                    STREAM_ENDPOINT,
                    json=request_body,
                    timeout=120.0,
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    response.raise_for_status()

                    answer_placeholder = st.empty()

                    for line in response.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload_str = line[len("data: "):]
                        if not payload_str.strip():
                            continue

                        try:
                            event = json.loads(payload_str)
                        except json.JSONDecodeError:
                            continue

                        if "token" in event:
                            typing_placeholder.empty()  # Remove typing indicator
                            answer_text += event["token"]
                            answer_placeholder.markdown(answer_text + "▌")  # Cursor

                        elif event.get("done"):
                            sources = event.get("sources", [])
                            answer_placeholder.markdown(answer_text)  # Remove cursor

                        elif "error" in event:
                            error_occurred = True
                            typing_placeholder.empty()
                            stream_error_message = event["error"]

            except httpx.ConnectError:
                error_occurred = True
                typing_placeholder.empty()
                stream_error_message = (
                    "Cannot connect to the backend. "
                    "Please ensure the FastAPI server is running."
                )
            except Exception as exc:
                error_occurred = True
                typing_placeholder.empty()
                stream_error_message = f"Unexpected streaming error: {exc}"

            # Fallback: if streaming fails or yields no visible text,
            # perform a regular non-stream call so users always get a response.
            if error_occurred or not answer_text.strip():
                try:
                    fallback_body = dict(request_body)
                    fallback_body["stream"] = False
                    fallback_resp = httpx.post(
                        CHAT_ENDPOINT,
                        json=fallback_body,
                        timeout=120.0,
                    )
                    fallback_resp.raise_for_status()
                    payload = fallback_resp.json()
                    answer_text = payload.get("answer", "").strip()
                    sources = payload.get("sources", [])
                    if answer_text:
                        typing_placeholder.empty()
                        answer_placeholder.markdown(answer_text)
                        error_occurred = False
                        stream_error_message = None
                except Exception as fallback_exc:
                    error_occurred = True
                    stream_error_message = (
                        stream_error_message
                        or f"Fallback request failed: {fallback_exc}"
                    )

            if error_occurred and stream_error_message:
                st.error(f"❌ {stream_error_message}")

            if not error_occurred and sources:
                _render_sources(sources)

    # Append assistant response to history
    if not error_occurred and answer_text:
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer_text,
                "sources": sources,
            }
        )
    elif error_occurred:
        failure_text = stream_error_message or (
            "Unable to fetch a response from the backend service."
        )
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": f"❌ {failure_text}",
                "sources": [],
            }
        )
    else:
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": "I could not generate a response. Please try again.",
                "sources": [],
            }
        )

    st.session_state.is_loading = False
    st.rerun()
