from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class TelegramInboundMessage:
    update_id: int
    chat_id: int
    user_id: int | None
    text: str


class TelegramBotClient:
    def __init__(self, *, token: str, timeout_seconds: int = 20) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}/",
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def poll_messages(
        self,
        *,
        offset: int | None,
        timeout_seconds: int,
    ) -> list[TelegramInboundMessage]:
        params: dict[str, int] = {"timeout": timeout_seconds}
        if offset is not None:
            params["offset"] = offset
        response = await self._client.get("getUpdates", params=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(
                f"telegram_get_updates_failed:{payload.get('description', 'unknown')}"
            )

        messages: list[TelegramInboundMessage] = []
        for item in payload.get("result", []):
            message = item.get("message")
            if not isinstance(message, dict):
                continue
            text = message.get("text")
            chat = message.get("chat")
            sender = message.get("from")
            if not isinstance(text, str) or not isinstance(chat, dict):
                continue
            chat_id = chat.get("id")
            if not isinstance(chat_id, int):
                continue
            user_id = (
                sender.get("id")
                if isinstance(sender, dict) and isinstance(sender.get("id"), int)
                else None
            )
            update_id = item.get("update_id")
            if not isinstance(update_id, int):
                continue
            messages.append(
                TelegramInboundMessage(
                    update_id=update_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    text=text,
                )
            )
        return messages

    async def send_message(self, *, chat_id: int, text: str) -> None:
        response = await self._client.post(
            "sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(
                f"telegram_send_message_failed:{payload.get('description', 'unknown')}"
            )

    async def close(self) -> None:
        await self._client.aclose()
