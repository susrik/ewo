"""Discord listener: transport adapter over the shared CommandRouter."""

from __future__ import annotations

from typing import Any

from ewo.config import Config
from ewo.listeners.base import CommandRouter


def build_client(config: Config, router: CommandRouter) -> Any:
    """Build a discord.py client wired to the command router."""
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_message(message: discord.Message) -> None:  # pragma: no cover - thin glue
        if message.author.bot:
            return
        if config.discord.channel_id and message.channel.id != config.discord.channel_id:
            return
        reply = router.handle(message.content)
        if reply:
            await message.channel.send(reply)

    return client


async def run_discord_listener(config: Config) -> None:  # pragma: no cover - network
    router = CommandRouter(config.api_base_url)
    client = build_client(config, router)
    await client.start(config.discord.token)
