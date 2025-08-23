import asyncio
import discord.ext.commands as commands
import discord
from aiofiles import open as aopen, os as aos
from common import logger
from models.players import RbbLeaderBoardCfg, RbbPlayer
from table2ascii import table2ascii as t2a
from parsers.main import (
    split_chunks,
    human_format,
)


# yes its a mess i dont have time or interest to fix this


class RbbLeaderboard(commands.Cog):
    bot: discord.Bot
    channels: list[discord.TextChannel] = []
    _messages: dict[int, list[discord.Message]]
    _file_path = "./persist/rbb_leaderboard_msg_id"

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._messages = dict()

    def cog_unload(self):
        return super().cog_unload()

    async def write_msg_ids(self, channel_id: int):
        messages = self._messages.get(channel_id, [])
        if messages is None:
            return
        async with aopen(f"{self._file_path}_{channel_id}", "w") as file:
            await file.write("\n".join([str(msg.id) for msg in messages]))

    async def delete_msg(self, msg_id: str, channel: discord.TextChannel):
        try:
            if not msg_id.isdecimal() or not channel:
                return
            parsed_msg_id = int(msg_id)
            msg = await channel.fetch_message(parsed_msg_id)
            await msg.delete()
        except Exception as e:
            logger.error(f"Failed to delete previous msg {msg_id}. {e}")

    async def delete_previous_messages(
        self, channel: discord.TextChannel
    ) -> str | None:
        file_path = f"{self._file_path}_{channel.id}"
        try:
            file_exists = await aos.path.exists(file_path)
            logger.info(f"DELETING PREVIOUS RBB MESSAGES {file_path}")
            if not file_exists:
                return
            msg_ids: list[str] = []
            async with aopen(file_path, "r") as file:
                msg_ids = await file.readlines()
            tasks = [self.delete_msg(id.strip(), channel) for id in msg_ids]
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(e)

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("Leaderboard is ready!")
        asyncio.create_task(self.init_board())

    async def init_board(self):
        board_data = await RbbLeaderBoardCfg.aload()
        if not board_data:
            logger.error("Failed to load RBB leaderboard configuration.")
            return
        for channel_id in board_data.channels:
            channel = await self.bot.fetch_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                self.channels.append(channel)
                await self.delete_previous_messages(channel)

        if not self.channels:
            logger.error("No valid channels found  for RBB leaderboard.")
            return
        asyncio.create_task(self.publish_leaderboards(board_data))

    def get_row(self, player_data: RbbPlayer):
        kills: int = player_data.kills
        score = player_data.total_score
        return [
            player_data.name,
            score,
            player_data.wins,
            kills,
            player_data.deaths,
        ]

    def get_table(
        self,
        players: list[RbbPlayer],
        start: int = 0,
        limit: int = 10,
        sort: bool = True,
    ):
        top_players = (
            sorted(players, key=lambda x: x.total_score, reverse=True)
            if sort
            else list(players)
        )
        board_data = [self.get_row(value) for value in top_players[:limit]]
        all_table = t2a(
            header=["#", "Name", "Score", "W", "K", "D"],
            body=[
                [
                    start + index + 1,  # index
                    dt[0],  # name
                    human_format(int(dt[1]), 10000),  # score
                    human_format(int(dt[2]), 10000) if dt[2] > 0 else "",  # wins
                    human_format(int(dt[3]), 10000),  # kills
                    human_format(int(dt[4]), 10000),  # deaths
                ]
                for (index, dt) in enumerate(board_data)
            ],
        )
        return all_table

    async def send_to_channel(
        self, channel: discord.TextChannel, chunks: list[str]
    ) -> None:
        messages = self._messages.get(channel.id, [])
        msgs_to_drop: list[discord.Message] = list(messages)
        rewrite = False
        for index, table_chunk in enumerate(chunks):
            msg: discord.Message | None = None
            if index < len(messages):
                msg = messages[index]
                msgs_to_drop.remove(msg)
                await msg.edit(content=table_chunk)
            else:
                msg = await channel.send(table_chunk)
                rewrite = True
                messages.append(msg)
        if len(msgs_to_drop):
            for msg in msgs_to_drop:
                logger.info(f"Dropping msg {msg.id}")
                messages.remove(msg)
                asyncio.create_task(msg.delete())
            rewrite = True
        self._messages[channel.id] = messages
        if rewrite:
            asyncio.create_task(self.write_msg_ids(channel.id))

    async def publish_leaderboards(
        self, leaderboard: RbbLeaderBoardCfg, force_rewrite=False
    ):
        if force_rewrite:
            self._messages = dict()
        chunks = await self.generate_leaderboard_chunks(leaderboard)
        for channel in self.channels:
            await self.send_to_channel(channel, chunks)

    async def generate_leaderboard_chunks(self, leaderboard: RbbLeaderBoardCfg):
        all_table = self.get_table(
            leaderboard.players,
            0,
            leaderboard.max_items,
        )

        chunk_size = 2000 - len("```\n\n```")
        chunks = list(
            map(lambda x: "```\n" + x + "```", split_chunks(all_table, chunk_size))
        )

        return chunks
