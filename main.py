import discord
import discord.ext.commands as commands
import os
from dotenv import load_dotenv
import math
import asyncio
from table2ascii import table2ascii as t2a
from models.players import LeaderBoard, Player, GameMatch
from parsers.main import (
    compute_next_gate_text,
    compute_gate_text,
    make_ordinal,
    sizeof_fmt,
)
from aiofiles import open as aopen, os as aos
from discord.ext.pages import Paginator, Page

load_dotenv()

channel_id_raw = os.environ.get("LEADERBOARD_CHANNEL", "")
print(f"LOADING LEADERBOARD_CHANNEL {channel_id_raw}")
config_bot_channel_id_raw = os.environ.get("CONFIG_BOT_CHANNEL", "")
print(f"LOADING CONFIG BOT CHANNEL {config_bot_channel_id_raw}")
CHANNEL_ID = int(channel_id_raw) if channel_id_raw.isnumeric() else 0
CONFIG_BOT_CHANNEL_ID = (
    int(config_bot_channel_id_raw) if config_bot_channel_id_raw.isnumeric() else 0
)
bot = discord.Bot()


def human_format(number: int, min: int = 1000) -> str:
    if number < min:
        return str(number)
    units = ["", "K", "M", "G", "T", "P"]
    k = 1000.0
    magnitude = int(math.floor(math.log(number, k)))
    value = str(round(number / k**magnitude, 1)).removesuffix(r".0")
    return value + units[magnitude]


def rank_2_emoji(n: int):
    rank_emoji_map = {0: ":first_place:", 1: ":second_place:", 2: ":third_place:"}
    rank_out = rank_emoji_map.get(n, make_ordinal(n + 1))
    return rank_out


class Leaderboard(commands.Cog):
    bot: discord.Bot
    channel: discord.abc.Messageable | None = None
    _messages: list[discord.Message]
    _file_path = "./persist/leaderboard_msg_id"

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._last_member = None
        self._messages = []

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
            print(f"Failed to delete previous msg {msg_id}. {e}")

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
            print(e)

    @commands.Cog.listener()
    async def on_ready(self):
        print("Leaderboard is ready!")
        channel = await self.bot.fetch_channel(CHANNEL_ID)
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
                [start + index + 1, dt[0], dt[1], human_format(dt[2]), *dt[3:]]
                for (index, dt) in enumerate(board_data)
            ],
        )
        return all_table

    async def send_board(
        self, existing_leaderboard: LeaderBoard | None = None, force_rewrite=False
    ):
        if not self.channel:
            return
        if force_rewrite:
            self._messages = []
        leaderboard_data = existing_leaderboard
        if not leaderboard_data:
            leaderboard_data = await LeaderBoard.aload()
        all_table = self.get_table(
            leaderboard_data.players,
            leaderboard_data.rank_config,
            0,
            leaderboard_data.max_items,
        )

        texts: list[str] = []
        chunk_size = 2000 - len("```\n\n```")
        all_lines = all_table.splitlines()
        curr_index = 0
        line_count = len(all_lines)
        while curr_index < line_count:
            curr_chunk: str = "```\n"
            next_expected_size = len(curr_chunk) + len(all_lines[curr_index]) + 4
            while next_expected_size < chunk_size:
                curr_line = all_lines[curr_index]
                curr_chunk += curr_line + "\n"
                curr_index += 1
                if curr_index < line_count:
                    next_expected_size = (
                        len(curr_chunk) + len(all_lines[curr_index]) + 4
                    )
                else:
                    break
            curr_chunk += "```"
            texts.append(curr_chunk)
        msgs_to_drop: list[discord.Message] = list(self._messages)
        rewrite = False
        for index, table_chunk in enumerate(texts):
            msg: discord.Message | None = None
            if index < len(msgs_to_drop):
                msg = msgs_to_drop.pop(index)
                await msg.edit(content=table_chunk)
            else:
                msg = await self.channel.send(table_chunk)
                rewrite = True
                self._messages.append(msg)
        if len(msgs_to_drop):
            for msg in msgs_to_drop:
                self._messages.remove(msg)
                asyncio.create_task(msg.delete())
            rewrite = True
        if rewrite:
            asyncio.create_task(self.write_msg_ids())


# region admin commands
admin_cmds = bot.create_group("mng", "Admin commands")
discordLeaderboard = Leaderboard(bot)


@admin_cmds.command()
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def reload(ctx: discord.ApplicationContext, force_rewrite: bool = False):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        config = await LeaderBoard.aload()
        await discordLeaderboard.send_board(config, force_rewrite)
        await ctx.respond("Done")
    except Exception as e:
        print(e)
        await ctx.command.dispatch_error(ctx, e)


@admin_cmds.command()
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def set_rank(ctx: discord.ApplicationContext, score_gate: int, rank_name: str):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        config = await LeaderBoard.aload()
        config.rank_config[str(score_gate)] = rank_name
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        print(e)
        await ctx.command.dispatch_error(ctx, e)


@admin_cmds.command()
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def max_leaderboard(ctx: discord.ApplicationContext, max: int):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        config = await LeaderBoard.aload()
        config.max_items = max
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        print(e)
        await ctx.command.dispatch_error(ctx, e)


@admin_cmds.command()
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def add_player(ctx: discord.ApplicationContext, playfab_id: str, user_name: str):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        config = await LeaderBoard.aload()
        config.players.append(Player(user_name.strip(), playfab_id.strip()))
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


@admin_cmds.command()
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def add_match(
    ctx: discord.ApplicationContext,
    playfab_or_user_name: str,
    structure_damage_percent: int = 0,
    score: int = 0,
    kills: int = 0,
    deaths: int = 0,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(f"Couldn't find player by id/name {playfab_or_user_name}")
            return
        match_data = GameMatch(kills, deaths, structure_damage_percent, score)
        player.matches.append(match_data)
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


@admin_cmds.command()
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def metadata(
    ctx: discord.ApplicationContext,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        config = await LeaderBoard.aload()
        file_size = await LeaderBoard.afile_size()
        embed = discord.Embed(title="Metadata", color=15844367)
        embed.description = f"Data file size: {sizeof_fmt(file_size)}"
        max_matches = max([len(p.matches) for p in config.players])
        embed.add_field(
            name=f"{len(config.players)} players in the system",
            value=f"Max {max_matches} matches played",
        )
        await ctx.respond(embed=embed)
    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


# endregion


@bot.slash_command(description="show all available ranks")
@discord.default_permissions(send_messages=True)
@discord.guild_only()
async def ranks(ctx: discord.ApplicationContext):
    try:
        config = await LeaderBoard.aload()
        all_ranks_txt = "\n".join(
            f"{txt} - {pts} points"
            for (pts, txt) in sorted(
                config.rank_config.items(), key=lambda x: int(x[0]), reverse=True
            )
        )
        ranks_txt = "```\n" + all_ranks_txt + "\n```"
        await ctx.respond(ranks_txt)
    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


@bot.slash_command(description="show player leaderboard placement")
@discord.default_permissions(send_messages=True)
@discord.guild_only()
async def place(ctx: discord.ApplicationContext, playfab_or_user_name: str):
    try:
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(f"Couldn't find player by id/name {playfab_or_user_name}")
            return
        if len(player.matches) < 1:
            await ctx.respond(f"No matches found with {playfab_or_user_name}")
            return
        sorted_players = sorted(
            config.players, key=lambda p: p.total_score, reverse=True
        )
        p_index = sorted_players.index(player)
        place_start = max(0, p_index - 4)
        place_end = min(len(sorted_players), p_index + 5)
        snippet = sorted_players[place_start:place_end]
        table = discordLeaderboard.get_table(
            snippet, config.rank_config, place_start, sort=False
        )
        await ctx.respond("```\n" + table + "\n```")
    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


@bot.slash_command(description="show player match history")
@discord.default_permissions(send_messages=True)
@discord.guild_only()
async def mh(ctx: discord.ApplicationContext, playfab_or_user_name: str):
    try:
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(f"Couldn't find player by id/name {playfab_or_user_name}")
            return
        if len(player.matches) < 1:
            await ctx.respond(f"No matches found with {playfab_or_user_name}")
            return
        matches = player.matches
        chunk_size = 10
        description = f"{player.name} ({player.playfab_id})"
        embeds: list[discord.Embed] = []
        for chunk_index, chunk in enumerate(
            list(
                [
                    matches[i: i + chunk_size]
                    for i in range(0, len(matches), chunk_size)
                ]
            )
        ):
            embed = discord.Embed(
                title="Match History",
                color=15844367,
                description=description,
            )
            base_line = chunk_index * chunk_size
            for index, match in enumerate(chunk):
                embed.add_field(
                    name=f"{make_ordinal(base_line +index + 1)} match",
                    value=f"Score: {match.score} | Kills: {match.kills} | Deaths: {match.deaths} | Structure Damage: {match.structure_damage}%",
                    inline=False,
                )
            embed.set_footer(text="Use /score to check aggregated stats")
            embeds.append(embed)
        if len(embeds) == 1:
            await ctx.respond(embed=embeds[0])
        elif len(embeds) > 1:
            paginator = Paginator(
                pages=[Page(embeds=[em]) for em in embeds], author_check=True
            )
            await paginator.respond(ctx.interaction)
        else:
            raise Exception(f"{ctx.command}: Unexpected embed length {len(embeds)}")
        # await ctx.respond(embed=embed)

    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


@bot.slash_command(description="show player score stats")
@discord.default_permissions(send_messages=True)
@discord.guild_only()
async def score(ctx: discord.ApplicationContext, playfab_or_user_name: str):
    try:
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(f"Couldn't find player by id/name {playfab_or_user_name}")
            return
        if len(player.matches) < 1:
            await ctx.respond(f"No matches found with {playfab_or_user_name}")
            return
        players_above = len(
            [p for p in config.players if p.total_score > player.total_score]
        )
        rank_txt = rank_2_emoji(players_above)
        embed = discord.Embed(
            title="Score",
            description=f"**{rank_txt}** {player.name} ({player.playfab_id})"
            + "\n"
            + f"```{human_format(player.total_score, 100000)} Points```",
            color=15844367,
        )

        rank_gates = dict([(str(k), v) for (k, v) in config.rank_config.items()])
        (_, rank_txt) = compute_gate_text(
            player.total_score,
            rank_gates,
        )
        (next_rank_pts, next_rank) = compute_next_gate_text(
            player.total_score, rank_gates
        )
        embed.add_field(name="Rank", value=rank_txt or "None")
        if next_rank and next_rank_pts:
            embed.add_field(
                name=chr(173),
                value=f"{next_rank_pts - player.total_score} to {next_rank}",
            )
        embed.add_field(name=chr(173), value=chr(173))
        embed.add_field(
            name=f"{len(player.matches)} matches played",
            value=f"{player.total_kills} Kills | {player.total_deaths} Deaths | {player.avg_structure_damage}% Avg Structure Dmg",
        )
        embed.set_footer(text="Use /mh to check match history")
        await ctx.respond(embed=embed)
    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


bot.add_cog(discordLeaderboard)
bot.run(os.environ["D_TOKEN"])
