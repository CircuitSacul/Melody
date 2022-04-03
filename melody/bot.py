from __future__ import annotations

from asyncio import Lock
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import crescent
import hikari
from songbird import Queue, ytdl
from songbird.hikari import Voicebox

from melody.exceptions import MelodyErr

from .config import CONFIG

INTENTS = hikari.Intents.GUILDS | hikari.Intents.GUILD_VOICE_STATES


@dataclass
class Player:
    voicebox: Voicebox
    queue: Queue


class Bot(crescent.Bot):
    def __init__(self) -> None:
        super().__init__(
            token=CONFIG.token, intents=INTENTS, default_guild=CONFIG.guild
        )

        self.players: dict[int, Player] = {}
        self.locks: dict[int, Lock] = {}
        self.plugins.load("melody.commands.music")

    @property
    def me(self) -> hikari.OwnUser:
        me = self.get_me()
        assert me is not None
        return me

    @asynccontextmanager
    async def lock(self, guild: int) -> AsyncIterator[None]:
        lock = self.locks.get(guild, Lock())
        try:
            await lock.acquire()
            yield
        finally:
            lock.release()
            # TODO: use a weakref for locks

    async def on_next(self, *args, **kwargs) -> None:
        print("Playing next")
        print(args, kwargs)

    async def on_fail(self, *args, **kwargs) -> None:
        print("Failed to play")
        print(args, kwargs)

    async def verify_vc(self, guild: int) -> None:
        async with self.lock(guild):
            voice = self.players.get(guild)
            if not voice:
                return
            if not voice.voicebox.is_alive:
                await self.leave_vc(guild)
                return
            if not self.voice.connections.get(hikari.Snowflake(guild)):
                await self.leave_vc(guild)
                return
            if not self.cache.get_voice_state(guild, self.me.id):
                await self.leave_vc(guild)
                return
            channel = self.cache.get_guild_channel(voice.voicebox.channel_id)
            if channel is None:
                await self.leave_vc(guild)
                return
            connected = self.cache.get_voice_states_view_for_channel(
                channel.guild_id, channel
            )
            if len(connected) == 1:  # Bot is the only one in the channel
                await self.leave_vc(guild)

    async def join_vc(self, guild: int, channel: int) -> bool:
        await self.verify_vc(guild)
        async with self.lock(guild):
            if guild in self.players:
                return False
            voice = await Voicebox.connect(
                self, hikari.Snowflake(guild), hikari.Snowflake(channel)
            )
            self.players[guild] = Player(
                voice, Queue(voice.driver, self.on_next, self.on_fail)
            )
        return True

    async def leave_vc(self, guild: int) -> bool:
        voice = self.players.pop(guild, None)
        if not voice:
            return False
        try:
            await voice.voicebox.leave()
        except Exception:
            pass
        try:
            vc = self.voice.connections.get(hikari.Snowflake(guild))
            if vc:
                await vc.disconnect()
        except Exception:
            pass

        return True

    async def play_url(self, guild: int, url: str) -> None:
        async with self.lock(guild):
            voice = self.players.get(guild)
            if not voice:
                raise MelodyErr("I am not in a voice channel!")
            if len(voice.queue) >= 10:
                raise MelodyErr("Too many songs in queue!")
            voice.queue.append(await ytdl(url))
