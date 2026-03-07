# a2a_peer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import AgentCard, MessageSendParams, SendMessageRequest


@dataclass
class A2APeer:
    """
    Small helper that:
    1) fetches the peer Agent Card (once)
    2) sends A2A message/send calls
    """
    base_url: str                      # e.g. "http://127.0.0.1:2025"
    auth_headers: Optional[dict] = None  # if you later add auth

    _httpx: Optional[httpx.AsyncClient] = None
    _card: Optional[AgentCard] = None
    _client: Optional[A2AClient] = None

    async def _ensure_ready(self) -> None:
        if self._httpx is None:
            self._httpx = httpx.AsyncClient(timeout=30.0)

        if self._card is None:
            resolver = A2ACardResolver(httpx_client=self._httpx, base_url=self.base_url)
            self._card = await resolver.get_agent_card()

        if self._client is None:
            self._client = A2AClient(httpx_client=self._httpx, agent_card=self._card)

    async def send_text(
        self,
        text: str,
        *,
        context_id: Optional[str] = None,
        task_id: Optional[str] = None,
        role: str = "user",
    ) -> tuple[str, Optional[str], Optional[str]]:
        """
        Returns: (peer_text, returned_context_id, returned_task_id)
        """
        await self._ensure_ready()

        payload = {
            "message": {
                "role": role,
                "parts": [{"kind": "text", "text": text}],
                "message_id": uuid4().hex,
            }
        }
        # Multi-turn continuity (optional but recommended)
        if context_id:
            payload["message"]["context_id"] = context_id
        if task_id:
            payload["message"]["task_id"] = task_id

        req = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(**payload),
        )
        resp = await self._client.send_message(req)  # type: ignore[union-attr]

        result = resp.root.result  # Task or Message depending on server

        # Extract text from artifacts/history/status (Agent Server most often uses history)
        peer_text = ""

        # 1) artifacts
        artifacts = getattr(result, "artifacts", None) or []
        for art in artifacts:
            for part in (art.parts or []):
                if getattr(part, "root", None) and getattr(part.root, "text", None):
                    peer_text = part.root.text
                    break
            if peer_text:
                break

        # 2) history (common in Agent Server)
        if not peer_text:
            history = getattr(result, "history", None) or []
            for msg in reversed(history):
                if msg.role in ("assistant", "agent"):
                    for part in (msg.parts or []):
                        if getattr(part, "root", None) and getattr(part.root, "text", None):
                            peer_text = part.root.text
                            break
                    if peer_text:
                        break

        # 3) status message fallback
        if not peer_text:
            status = getattr(result, "status", None)
            if status and status.message and status.message.parts:
                for part in status.message.parts:
                    if getattr(part, "root", None) and getattr(part.root, "text", None):
                        peer_text = part.root.text
                        break

        returned_context_id = getattr(result, "context_id", None)
        returned_task_id = getattr(result, "id", None)

        return peer_text or "(no text found)", returned_context_id, returned_task_id

    async def aclose(self) -> None:
        if self._httpx is not None:
            await self._httpx.aclose()
            self._httpx = None