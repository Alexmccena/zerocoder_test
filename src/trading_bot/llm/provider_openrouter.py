from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from trading_bot.llm.contracts import ProviderCompletion, ProviderUsage


class OpenRouterError(RuntimeError):
    """Raised when OpenRouter request fails."""


class OpenRouterParseError(OpenRouterError):
    """Raised when OpenRouter payload cannot be parsed into JSON."""


def _extract_message_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
        return "\n".join(chunks).strip()
    return ""


def _unwrap_json_block(raw_text: str) -> str:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


@dataclass(slots=True)
class OpenRouterProvider:
    api_key: str
    base_url: str
    http_referer: str | None = None
    app_name: str | None = None
    retries: int = 2

    def __post_init__(self) -> None:
        headers: dict[str, str] = {"Authorization": f"Bearer {self.api_key}"}
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.app_name:
            headers["X-Title"] = self.app_name
        self._client = httpx.AsyncClient(
            base_url=self.base_url.rstrip("/") + "/",
            headers=headers,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def complete_json(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
    ) -> ProviderCompletion:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        attempt = 0
        last_error: Exception | None = None
        while attempt <= self.retries:
            started_at = time.perf_counter()
            try:
                response = await self._client.post(
                    "chat/completions",
                    json=payload,
                    timeout=httpx.Timeout(timeout_seconds),
                )
                if response.status_code == 429 and attempt < self.retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    attempt += 1
                    continue
                response.raise_for_status()
                parsed = response.json()
                completion = self._build_completion(
                    parsed=parsed,
                    model_name=model_name,
                    latency_seconds=time.perf_counter() - started_at,
                )
                return completion
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                await asyncio.sleep(0.5 * (attempt + 1))
                attempt += 1
            except OpenRouterParseError:
                raise
            except Exception as exc:  # pragma: no cover - defensive guard
                raise OpenRouterError(f"openrouter_unexpected_error:{exc.__class__.__name__}") from exc
        raise OpenRouterError(f"openrouter_request_failed:{last_error!s}")

    def _build_completion(
        self,
        *,
        parsed: dict[str, Any],
        model_name: str,
        latency_seconds: float,
    ) -> ProviderCompletion:
        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenRouterParseError("openrouter_missing_choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        raw_text = _extract_message_text(content)
        if not raw_text:
            raise OpenRouterParseError("openrouter_empty_response")
        json_text = _unwrap_json_block(raw_text)
        try:
            output_json = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise OpenRouterParseError("openrouter_invalid_json") from exc
        if not isinstance(output_json, dict):
            raise OpenRouterParseError("openrouter_response_not_object")
        usage_payload = parsed.get("usage", {}) if isinstance(parsed.get("usage"), dict) else {}
        prompt_tokens = int(usage_payload.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage_payload.get("completion_tokens", 0) or 0)
        cost_value = usage_payload.get("cost", usage_payload.get("total_cost", 0.0))
        try:
            cost_usd = float(cost_value or 0.0)
        except (TypeError, ValueError):  # pragma: no cover - defensive guard
            cost_usd = 0.0
        return ProviderCompletion(
            provider="openrouter",
            model_name=model_name,
            output_json=output_json,
            raw_text=raw_text,
            usage=ProviderUsage(
                input_tokens=max(prompt_tokens, 0),
                output_tokens=max(completion_tokens, 0),
                cost_usd=max(cost_usd, 0.0),
            ),
            latency_seconds=max(latency_seconds, 0.0),
        )
