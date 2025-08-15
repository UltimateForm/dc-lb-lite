from datetime import datetime, timezone
from discord.ext import commands
from discord.abc import Messageable
from discord import Bot

import asyncio
from parsers.grok import parse_kov_add
from models.rcon import ChatEvent, KillfeedEvent, Player
from common import logger
from rcon_trackers.game_events import GameEventsTracker
import re


class KovTracker(commands.Cog):
    _channel_id: int
    _bot: Bot
    _channel: Messageable
    _admin_id: str
    _tracking: dict[str, Player]
    _match_running: bool

    def __init__(self, bot: Bot, channel_id: int, admin_id: str):
        self._channel_id = channel_id
        self._bot = bot
        self._admin_id = admin_id
        self._tracking = dict()
        self._match_running = False

    @commands.Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self.on_ready_async())

    async def handle_killfeed_event(self, ev: KillfeedEvent):
        if not self._match_running:
            return
        hunter_id = ev.killer_id
        victim_id = ev.killed_id
        current_ids = self._tracking.keys()
        if hunter_id not in current_ids or victim_id not in current_ids:
            return
        asyncio.create_task(
            self._channel.send(
                f"```{ev.user_name} ({ev.killer_id}) has killed {ev.killed_user_name} ({ev.killed_id})```"
            )
        )
        current_hunter = self._tracking[hunter_id]
        current_victim = self._tracking[victim_id]
        if not current_hunter.user_name:
            current_hunter.user_name = ev.user_name
        current_hunter.kills += 1
        if not current_victim.user_name:
            current_victim.user_name = ev.killed_user_name
        current_victim.deaths += 1

    async def handle_chat_event(self, ev: ChatEvent):
        chatter_id = ev.player_id
        if chatter_id != self._admin_id:
            return
        msg = ev.message.strip()
        if re.match(r"^.kov\s+t[0-9]\s+add.*", msg):
            parsed = parse_kov_add(msg)
            if not parsed or not parsed.player_id:
                logger.error(f"Failed to parse .kov add: {msg}")
            else:
                asyncio.create_task(
                    self._channel.send(f"Set to tracking `{parsed.player_id}` K/D")
                )
                self._tracking[parsed.player_id] = parsed
        elif re.match(r"^.kov\s+start\s+.*", msg):
            self._match_running = True
        elif msg == "botend":
            track_finish = [
                f"{value.user_name} ({key}) -> K {value.kills} | D {value.deaths}"
                for key, value in self._tracking.items()
            ]
            track_finish_str = "\n".join(track_finish)
            current_time = round(datetime.now(timezone.utc).timestamp())
            time_sig = f"<t:{current_time}>"
            await self._channel.send(
                f"### KOV MATCH RESULTS {time_sig}\n```\n" + track_finish_str + "\n```"
            )
            self._tracking = {}
            self._match_running = False

    async def on_ready_async(self):
        channel = self._bot.get_channel(self._channel_id)
        if not isinstance(channel, Messageable):
            raise ValueError(
                f"Expected channel with ID {self._channel_id} to be a Messageable, but got {type(channel)}"
            )
        await channel.send("Will use this channel for KOV tracking events")
        self._channel = channel
        game_events_tracker = self._bot.get_cog(GameEventsTracker.__name__)
        if not isinstance(game_events_tracker, GameEventsTracker):
            raise ValueError(
                f"Attempted to obtain game events tracker from cogs but received unexpected type {type(game_events_tracker)}"
            )

        def handle_killfeed_event(x: KillfeedEvent):
            asyncio.create_task(self.handle_killfeed_event(x))

        def handle_chat_event(x: ChatEvent):
            asyncio.create_task(self.handle_chat_event(x))

        game_events_tracker.chat_events.subscribe(handle_chat_event)
        game_events_tracker.killfeed_events.subscribe(handle_killfeed_event)
