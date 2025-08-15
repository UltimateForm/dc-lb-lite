import discord
import asyncio
from table2ascii import table2ascii as t2a
from parsers.main import (
    compute_gate_text,
    split_chunks,
    human_format,
)
from common import logger
import discord.ext.commands as commands
from aiofiles import open as aopen, os as aos
from models.players import KovLeaderBoard, Player


class KovLeaderboard(commands.Cog):
    bot: discord.Bot
    channel: discord.abc.Messageable | None = None
    _messages: list[discord.Message]
    _file_path = "./persist/leaderboard_msg_id"
    _channel_id: int

    def __init__(self, bot: discord.Bot, channel_id: int):
        self.bot = bot
        self._messages = []
        self._channel_id = channel_id

    def cog_unload(self):
        return super().cog_unload()

    async def write_msg_ids(self):
        if self._messages is None:
            return
        async with aopen(self._file_path, "w") as file:
            await file.write("\n".join([str(msg.id) for msg in self._messages]))

    async def delete_msg(self, msg_id: str):
        try:
            if not msg_id.isdecimal() or not self.channel:
                return
            parsed_msg_id = int(msg_id)
            msg = await self.channel.fetch_message(parsed_msg_id)
            await msg.delete()
        except Exception as e:
            logger.error(f"Failed to delete previous msg {msg_id}. {e}")

    async def delete_previous_messages(self) -> str | None:
        if self.channel is None:
            return
        try:
            file_exists = await aos.path.exists(self._file_path)
            if not file_exists:
                return
            msg_ids: list[str] = []
            async with aopen(self._file_path, "r") as file:
                msg_ids = await file.readlines()

            tasks = [self.delete_msg(id.strip()) for id in msg_ids]
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(e)

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("Leaderboard is ready!")
        channel = await self.bot.fetch_channel(self._channel_id)
        if isinstance(channel, discord.abc.Messageable):
            self.channel = channel
            await self.delete_previous_messages()
            asyncio.create_task(self.send_board())

    def get_row(self, player_data: Player, ranks: dict[str, str]):
        kills: int = player_data.total_kills
        deaths: int = player_data.total_deaths
        score = player_data.total_score
        (_, rank_txt) = compute_gate_text(
            score, dict([(str(k), v) for (k, v) in ranks.items()])
        )
        return [
            player_data.name,
            rank_txt or "None",
            score,
            kills,
            deaths,
        ]

    def get_table(
        self,
        players: list[Player],
        ranks: dict[str, str],
        start: int = 0,
        limit: int = 10,
        sort: bool = True,
    ):
        top_players = (
            sorted(players, key=lambda x: x.total_score, reverse=True)
            if sort
            else list(players)
        )
        board_data = [self.get_row(value, ranks) for value in top_players[:limit]]
        all_table = t2a(
            header=["#", "Name", "Rank", "Score", "K", "D"],
            body=[
                [
                    start + index + 1,
                    dt[0],
                    dt[1],
                    human_format(int(dt[2]), 10000),
                    human_format(int(dt[3]), 10000),
                    human_format(int(dt[4]), 10000),
                ]
                for (index, dt) in enumerate(board_data)
            ],
        )
        return all_table

    async def send_board(
        self, existing_leaderboard: KovLeaderBoard | None = None, force_rewrite=False
    ):
        if not self.channel:
            return
        if force_rewrite:
            self._messages = []
        leaderboard_data = existing_leaderboard
        if not leaderboard_data:
            leaderboard_data = await KovLeaderBoard.aload()
        all_table = self.get_table(
            leaderboard_data.players,
            leaderboard_data.aliased_ranks(),
            0,
            leaderboard_data.max_items,
        )

        chunk_size = 2000 - len("```\n\n```")
        chunks = map(lambda x: "```\n" + x + "```", split_chunks(all_table, chunk_size))

        msgs_to_drop: list[discord.Message] = list(self._messages)
        rewrite = False
        for index, table_chunk in enumerate(chunks):
            msg: discord.Message | None = None
            if index < len(self._messages):
                msg = self._messages[index]
                msgs_to_drop.remove(msg)
                await msg.edit(content=table_chunk)
            else:
                msg = await self.channel.send(table_chunk)
                rewrite = True
                self._messages.append(msg)
        if len(msgs_to_drop):
            for msg in msgs_to_drop:
                logger.info(f"Dropping msg {msg.id}")
                self._messages.remove(msg)
                asyncio.create_task(msg.delete())
            rewrite = True
        if rewrite:
            asyncio.create_task(self.write_msg_ids())
