"""Deterministic checks for unsafe or instruction-override chat requests."""

from __future__ import annotations

import re


PROMPT_INJECTION_RESPONSE = (
    "I can't help with requests to override instructions, reveal private prompts, "
    "or access unauthorized information."
)

_INJECTION_PATTERNS = (
    re.compile(r"\b(ignore|disregard|forget|override|bypass)\b.{0,80}\b(previous|prior|system|developer|all|these)\b", re.I | re.S),
    re.compile(r"\b(reveal|show|print|output|share|leak|exfiltrate)\b.{0,80}\b(system prompt|hidden prompt|instructions|api key|secret|password|credential|token)\b", re.I | re.S),
    re.compile(r"\b(jailbreak|dan mode|developer message|system message|hidden instructions)\b", re.I),
    re.compile(r"\b(act|pretend|roleplay)\s+as\b.{0,60}\b(system|developer|admin|root)\b", re.I | re.S),
    re.compile(r"\b(disclose|provide|give|grant)\b.{0,60}\b(unauthorized|confidential|private|restricted)\b", re.I | re.S),
)


def is_prompt_injection(text: str) -> bool:
    """Return whether text attempts to change instructions or obtain secrets."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return any(pattern.search(normalized) for pattern in _INJECTION_PATTERNS)


def contains_prompt_injection(texts: list[str]) -> bool:
    """Check the current question and untrusted conversation turns together."""
    return any(is_prompt_injection(text) for text in texts)