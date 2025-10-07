from discord import ApplicationContext
from discord import Bot, Cog
from rcon.rcon_listener import RconListener
import asyncio
from discord.abc import Messageable
from reactivex import Observable, empty, operators
from parsers.grok import parse_chat_event, parse_killfeed_event, parse_login_event
from datetime import datetime


class GameEventsTracker(Cog):
    _listener: RconListener
    _channel_id: int
    chat_events: Observable = empty()
    killfeed_events: Observable = empty()
    login_events: Observable = empty()
    _background_task: asyncio.Task | None = None

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

    @Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self.on_ready_async())

    async def on_ready_async(self):
        channel = self._bot.get_channel(self._channel_id)
        if isinstance(channel, Messageable):
            await channel.send("Connected, readying RCON events...")

        self._background_task = asyncio.create_task(self._listener.start())

    # @commands.command(
    #     name="killfeed",
    #     description="Simulate killfeed event",
    #     default_permission=Permissions(administrator=True),
    # )
    async def killfeed_event(
        self, ctx: ApplicationContext, killer_id: str, victim_id: str
    ):
        now_str = datetime.now().strftime("%Y.%m.%d-%H.%M.%S")
        event_str = f"Killfeed: {now_str}: {killer_id} () killed {victim_id} ()"
        self._listener.on_next(event_str)
        await ctx.respond(f"`{event_str}` sent to killfeed listener.")

    # @commands.command(
    #     name="chat",
    #     description="Simulate chat event",
    #     default_permission=Permissions(administrator=True),
    # )
    async def chat_event(
        self, ctx: ApplicationContext, player_id: str, *, message: str
    ):
        event_str = f"Chat: {player_id}, {ctx.author.display_name}, (ALL) {message}"
        self._listener.on_next(event_str)
        await ctx.respond(f"`{event_str}` sent to chat listener.")
