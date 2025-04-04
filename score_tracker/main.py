from datetime import datetime, timezone
from discord.ext import commands
from discord.abc import Messageable
from discord import Bot
from rcon.rcon_listener import RconListener

# from reactivex import Observable
import asyncio
from parsers.rcon import parse_chat_event, parse_killfeed_event, parse_kov_add
from models.rcon import ChatEvent, KillfeedEvent, Player
from common import logger
import re


class ScoreTracker(commands.Cog):
    _channel_id: int
    _bot: Bot
    _rcon_listener: RconListener
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
        asyncio.create_task(
            self._channel.send(
                f"```{ev.user_name} ({ev.killer_id}) has killed {ev.killed_user_name} ({ev.killed_id})```"
            )
        )
        hunter_id = ev.killed_id
        victim_id = ev.killer_id
        current_ids = self._tracking.keys()
        if hunter_id not in current_ids or victim_id not in current_ids:
            return
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

    async def handle_rcon_event(self, ev: str):
        # asyncio.create_task(self._channel.send(f"```{ev}```"))
        if ev.lower().startswith("chat:"):
            event = parse_chat_event(ev)
            if event:
                await self.handle_chat_event(event)
            else:
                logger.error(f"Failed to parse chat event {ev}")
        elif ev.lower().startswith("killfeed:"):
            event = parse_killfeed_event(ev)
            if event:
                await self.handle_killfeed_event(event)
            else:
                logger.error(f"Failed to parse killfeed event {ev}")
        else:
            logger.error(f"Unknown rcon event type: {ev}")

    async def on_ready_async(self):
        channel = self._bot.get_channel(self._channel_id)
        if isinstance(channel, Messageable):
            self._channel = channel
            await self._channel.send("Connected, readying RCON events...")
        self._rcon_listener = RconListener(["chat", "killfeed"])

        def _handle(x: str):
            asyncio.create_task(self.handle_rcon_event(x))

        self._rcon_listener.subscribe(_handle)
        asyncio.create_task(self._rcon_listener.start())
