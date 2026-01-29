from __future__ import annotations

import argparse
import os
from typing import Iterable, List

import httpx


def _parse_csv(value: str) -> List[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenRouter model probe")
    parser.add_argument("--models", type=str, default="", help="Comma-separated model ids to probe")
    parser.add_argument("--list", action="store_true", help="List models from /models")
    parser.add_argument("--filter", type=str, default="", help="Substring filter for --list")
    parser.add_argument("--limit", type=int, default=20, help="Max models to show for --list")
    parser.add_argument("--prompt", type=str, default="ping", help="Prompt used for probing")
    parser.add_argument("--max-tokens", type=int, default=32, help="Max tokens for probing")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature for probing")
    return parser


def _headers(api_key: str) -> dict[str, str]:
    headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
    app_url = os.getenv("OPENROUTER_APP_URL", "").strip()
    app_name = os.getenv("OPENROUTER_APP_NAME", "lab-agent-system").strip()
    if app_url:
        headers["http-referer"] = app_url
    if app_name:
        headers["x-title"] = app_name
    return headers


def _list_models(base_url: str, api_key: str, filt: str, limit: int) -> int:
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{base_url}/models", headers=_headers(api_key))
        resp.raise_for_status()
        payload = resp.json()

    models = payload.get("data") or []
    if filt:
        models = [m for m in models if filt.lower() in str(m.get("id", "")).lower()]

    for idx, item in enumerate(models[: max(limit, 0)], start=1):
        model_id = item.get("id", "")
        print(f"{idx}. {model_id}")
    return 0


def _probe_models(
    base_url: str,
    api_key: str,
    models: Iterable[str],
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> int:
    with httpx.Client(timeout=30) as client:
        for model in models:
            payload = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = client.post(f"{base_url}/chat/completions", headers=_headers(api_key), json=payload)
            status = resp.status_code
            message = ""
            if status != 200:
                try:
                    err = resp.json().get("error", {})
                    message = err.get("message", "") if isinstance(err, dict) else ""
                except Exception:
                    message = resp.text.strip()[:200]
            else:
                message = "ok"
            print(f"{model}\t{status}\t{message}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("OPENROUTER_API_KEY is not set.")
        return 1

    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    models = _parse_csv(args.models)
    if args.list:
        return _list_models(base_url, api_key, args.filter, args.limit)
    if not models:
        print("--models is required unless --list is set.")
        return 1

    return _probe_models(
        base_url=base_url,
        api_key=api_key,
        models=models,
        prompt=args.prompt,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )


if __name__ == "__main__":
    raise SystemExit(main())
