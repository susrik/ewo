"""Discord listener: client construction (transport glue itself is no-cover)."""

from __future__ import annotations

from ewo.config import Config
from ewo.listeners.base import CommandRouter
from ewo.listeners.discord import build_client


def test_build_client(config: Config) -> None:
    import discord

    router = CommandRouter.__new__(CommandRouter)  # no HTTP client needed here
    client = build_client(config, router)
    assert isinstance(client, discord.Client)
    assert client.intents.message_content is True
