from discord.ext import commands
from discord import Bot
from rcon.rcon_listener import RconListener
import asyncio
from discord.abc import Messageable
from reactivex import Observable, empty, operators
from parsers.grok import parse_chat_event, parse_killfeed_event, parse_login_event


class GameEventsTracker(commands.Cog):
    _listener: RconListener
    _channel_id: int
    chat_events: Observable = empty()
    killfeed_events: Observable = empty()
    login_events: Observable = empty()

    def __init__(self, bot: Bot, channel_id: int):
        self._bot = bot
        self._channel_id = channel_id
        self._listener = RconListener(["chat", "killfeed", "login"])

        self.chat_events = self._listener.pipe(
            operators.filter(lambda x: x.startswith("Chat")),
            operators.map(parse_chat_event),
            operators.filter(lambda x: x is not None),
        )
        self.killfeed_events = self._listener.pipe(
            operators.filter(lambda x: x.startswith("Killfeed")),
            operators.map(parse_killfeed_event),
            operators.filter(lambda x: x is not None),
        )
        self.login_events = self._listener.pipe(
            operators.filter(lambda x: x.startswith("Login")),
            operators.map(parse_login_event),
            operators.filter(lambda x: x is not None),
        )

    @commands.Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self.on_ready_async())

    async def on_ready_async(self):
        channel = self._bot.get_channel(self._channel_id)
        if isinstance(channel, Messageable):
            await channel.send("Connected, readying RCON events...")

        asyncio.create_task(self._listener.start())
