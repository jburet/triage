"""Real Slack client. Write-only, one of the two systems Triage may write to."""

from __future__ import annotations

from typing import Any


class SlackWebClient:
    """Thin wrapper over ``slack_sdk``'s async web client.

    Long drafts are attached as a snippet rather than pasted inline: the
    review-exhausted path posts a whole ticket draft, which is unreadable as a
    chat message.
    """

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("a Slack bot token is required when dry-run is off")
        self._token = token
        self._client: Any = None

    def _web(self) -> Any:
        if self._client is None:
            from slack_sdk.web.async_client import AsyncWebClient

            self._client = AsyncWebClient(token=self._token)
        return self._client

    async def post(self, *, channel: str, text: str, attachment: str | None = None) -> str:
        response = await self._web().chat_postMessage(channel=channel, text=text)
        timestamp = str(response["ts"])
        if attachment:
            await self._web().files_upload_v2(
                channel=channel,
                thread_ts=timestamp,
                content=attachment,
                filename="draft-ticket.md",
                title="Draft ticket",
            )
        return timestamp
