"""
Shared LLM utilities for the lab-agent-system.

This module provides thin wrappers around LLM APIs (Gemini, Claude) that can be
used by any agent in the system.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> str:
    text = text.strip()
    # remove markdown fences if any
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", text)
        text = text.rstrip("```").strip()
    m = _JSON_RE.search(text)
    return m.group(0) if m else text


def safe_json_loads(text: str) -> Any:
    raw = _extract_json(text)
    try:
        return json.loads(raw)
    except Exception:
        # common minor fixes
        raw2 = raw.replace("\t", " ").strip()
        raw2 = re.sub(r",\s*}", "}", raw2)
        raw2 = re.sub(r",\s*]", "]", raw2)
        return json.loads(raw2)


@dataclass
class LLMResult:
    text: str
    raw: Any = None


class GeminiLLM:
    """Thin wrapper around google-genai, with a fallback to google-generativeai."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.getenv("QUIZ_GEMINI_MODEL", "gemini-3-flash-preview")
        self.api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
        self._client = None
        self._backend = None

        # Try new SDK first
        try:
            from google import genai  # type: ignore

            self._backend = "google-genai"
            self._client = genai.Client(api_key=self.api_key) if self.api_key else genai.Client()
        except Exception:
            # fallback legacy
            try:
                import google.generativeai as genai_legacy  # type: ignore

                self._backend = "google-generativeai"
                genai_legacy.configure(api_key=self.api_key)
                self._client = genai_legacy.GenerativeModel(self.model)
            except Exception as e:
                raise RuntimeError(
                    "Gemini SDK is not available. Install `google-genai` (recommended) or `google-generativeai`."
                ) from e

    async def generate(self, prompt: str, *, temperature: float = 0.0, max_output_tokens: int = 2048) -> LLMResult:
        """Generate text from prompt without blocking Discord event loop."""
        return await asyncio.to_thread(self._generate_sync, prompt, temperature=temperature, max_output_tokens=max_output_tokens)

    def _generate_sync(self, prompt: str, *, temperature: float = 0.0, max_output_tokens: int = 2048) -> LLMResult:
        if self._backend == "google-genai":
            from google.genai.types import GenerateContentConfig, ThinkingConfig

            # Disable thinking to prevent thinking tokens from consuming output token budget
            # See: https://github.com/googleapis/python-genai/issues/782
            resp = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    thinking_config=ThinkingConfig(thinking_budget=0),
                ),
            )
            # Debug: log finish reason if available
            finish_reason = None
            if hasattr(resp, "candidates") and resp.candidates:
                finish_reason = getattr(resp.candidates[0], "finish_reason", None)
            text = getattr(resp, "text", "") or ""
            return LLMResult(text=text, raw={"response": resp, "finish_reason": str(finish_reason)})
        else:
            # legacy google-generativeai
            resp = self._client.generate_content(
                prompt,
                generation_config={"temperature": temperature, "max_output_tokens": max_output_tokens},
            )
            # resp.text exists
            return LLMResult(text=getattr(resp, "text", "") or "", raw=resp)


class ClaudeLLM:
    """Thin wrapper around Anthropic Messages API (httpx only)."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.getenv("QUIZ_CLAUDE_MODEL", "claude-sonnet-4-5")
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")

    async def generate(self, prompt: str, *, temperature: float = 0.0, max_output_tokens: int = 2048) -> LLMResult:
        return await asyncio.to_thread(self._generate_sync, prompt, temperature=temperature, max_output_tokens=max_output_tokens)

    def _generate_sync(self, prompt: str, *, temperature: float = 0.0, max_output_tokens: int = 2048) -> LLMResult:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")

        payload = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        with httpx.Client(timeout=60) as client:
            resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = ""
        content = data.get("content") or []
        if content and isinstance(content, list):
            text = content[0].get("text", "") if isinstance(content[0], dict) else ""
        return LLMResult(text=text, raw=data)
