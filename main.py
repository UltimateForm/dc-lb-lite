import aiofiles
import discord
import discord.ext.commands as commands
import os
from dotenv import load_dotenv
import math
import asyncio
from table2ascii import table2ascii as t2a
from models.match_parse import MatchInputPlayer
from models.players import LeaderBoard, Player, GameMatch
from parsers.main import (
    compute_next_gate_text,
    compute_gate_text,
    make_ordinal,
    sizeof_fmt,
    split_chunks,
    parse_matches,
)
from aiofiles import open as aopen, os as aos
from discord.ext.pages import Paginator, Page
import io
import json
from datetime import datetime
from common import logger

logger.use_date_time_logger()

load_dotenv()

channel_id_raw = os.environ.get("LEADERBOARD_CHANNEL", "")
logger.info(f"LOADING LEADERBOARD_CHANNEL {channel_id_raw}")
config_bot_channel_id_raw = os.environ.get("CONFIG_BOT_CHANNEL", "")
logger.info(f"LOADING CONFIG BOT CHANNEL {config_bot_channel_id_raw}")
admin_id = os.environ.get("ADMIN_FFID", None)
logger.info(f"LOADING ADMINID {admin_id}")
if not admin_id:
    raise Exception("ADMIN ID NOT LOADED")

CHANNEL_ID = int(channel_id_raw) if channel_id_raw.isnumeric() else 0
CONFIG_BOT_CHANNEL_ID = (
    int(config_bot_channel_id_raw) if config_bot_channel_id_raw.isnumeric() else 0
)
bot = discord.Bot()


def custom_format(number: float, precision: int):
    if number == 0:
        return "0"
    elif number < 1:
        return f"{number:.{precision}f}".rstrip("0").rstrip(".")
    else:
        integer_part = int(number)
        decimal_part = number - integer_part
        if decimal_part == 0:
            return str(integer_part)
        else:
            return f"{integer_part}.{str(decimal_part)[2:precision+2]}"


def human_format(number: int, min: int = 1000) -> str:
    if number < min:
        return str(number)
    units = ["", "K", "M", "G", "T", "P"]
    k = 1000.0
    magnitude = int(math.floor(math.log(number, k)))
    formatted_number = custom_format(number / k**magnitude, 1)
    return "{}{}".format(formatted_number, units[magnitude])


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
                [
                    start + index + 1,
                    dt[0],
                    dt[1],
                    human_format(dt[2], 10000),
                    human_format(dt[3], 10000),
                    human_format(dt[4], 10000),
                ]
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


# region admin commands
admin_cmds = bot.create_group("mng", "Admin commands")
discordLeaderboard = Leaderboard(bot)


@admin_cmds.command(
    description="reload board, force_rewrite=true will send new messages instead trying to edit current ones"
)
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def reload(ctx: discord.ApplicationContext, force_rewrite: bool = False):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        await discordLeaderboard.send_board(config, force_rewrite)
        await ctx.respond("Done")
    except Exception as e:
        logger.error(e)
        await ctx.command.dispatch_error(ctx, e)


@admin_cmds.command(description="set rank score gate")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def set_rank(
    ctx: discord.ApplicationContext,
    score_gate: int,
    rank_name: str,
    short_name: str | None = None,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        config.rank_config[str(score_gate)] = rank_name
        if short_name:
            if not config.rank_short:
                config.rank_short = dict()
            config.rank_short[str(score_gate)] = short_name
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        logger.error(e)
        await ctx.command.dispatch_error(ctx, e)


@admin_cmds.command(description="delete rank score gate")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def del_rank(ctx: discord.ApplicationContext, score_gate: int):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        config.rank_config.pop(str(score_gate))
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        logger.error(e)
        await ctx.command.dispatch_error(ctx, e)


@admin_cmds.command(description="set max leaderboard rows")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def max_leaderboard(ctx: discord.ApplicationContext, max: int):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        config.max_items = max
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        logger.error(e)
        await ctx.command.dispatch_error(ctx, e)


@admin_cmds.command(description="add player to the system")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def add_player(ctx: discord.ApplicationContext, playfab_id: str, user_name: str):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        config.players.append(Player(user_name.strip(), playfab_id.strip()))
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond("Done")
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="removes player from the system")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def rm_player(
    ctx: discord.ApplicationContext,
    playfab_or_user_name: str,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(
                f"Couldn't find player by id/name {playfab_or_user_name}."
            )
            return
        config.players.remove(player)
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond(
            f"Done. Removed player {player.name} ({player.playfab_id}) from the system."
        )
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="deletes player match")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def del_match(
    ctx: discord.ApplicationContext,
    playfab_or_user_name: str,
    match_number: int,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(
                f"Couldn't find player by id/name {playfab_or_user_name}. Run `/mng add_player` first to add player."
            )
            return
        if match_number > len(player.matches):
            await ctx.respond(
                f"Match number {match_number} is out of bounds. Player {player.name} has {len(player.matches)} matches.",
            )
            return
        player.matches.pop(match_number - 1)
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond(
            f"Done. Deleted {make_ordinal(match_number)} match for {player.name} ({player.playfab_id})."
        )
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="edit player match")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def edit_match(
    ctx: discord.ApplicationContext,
    playfab_or_user_name: str,
    match_number: int,
    structure_damage_percent: int | None = None,
    score: int | None = None,
    kills: int | None = None,
    deaths: int | None = None,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(
                f"Couldn't find player by id/name {playfab_or_user_name}. Run `/mng add_player` first to add player."
            )
            return
        if match_number > len(player.matches):
            await ctx.respond(
                f"Match number {match_number} is out of bounds. Player {player.name} has {len(player.matches)} matches.",
            )
            return
        match = player.matches[match_number - 1]
        if structure_damage_percent is not None:
            match.structure_damage = structure_damage_percent
        if score is not None:
            match.score = score
        if kills is not None:
            match.kills = kills
        if deaths is not None:
            match.deaths = deaths
        await config.asave()
        await discordLeaderboard.send_board(config)
        await ctx.respond(
            f"Done. Edited {make_ordinal(match_number)} match for {player.name} ({player.playfab_id})."
        )
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


def push_match(
    config: LeaderBoard,
    playfab_id: str | None = None,
    user_name: str | None = None,
    structure_damage_percent: int = 0,
    score: int = 0,
    kills: int = 0,
    deaths: int = 0,
) -> tuple[Player | None, bool]:
    player = config.get_player(playfab_id or user_name or "")
    new_player = False
    if player is None:
        if playfab_id and user_name:
            player = Player(user_name.strip(), playfab_id.strip())
            config.players.append(player)
            new_player = True
        else:
            return (None, False)
    proper_score = score if not new_player else score + 2000
    match_data = GameMatch(kills, deaths, structure_damage_percent, proper_score)
    player.matches.append(match_data)
    return (player, new_player)


@admin_cmds.command(description="add player match score")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def add_match(
    ctx: discord.ApplicationContext,
    playfab_id: str | None = None,
    user_name: str | None = None,
    structure_damage_percent: int = 0,
    score: int = 0,
    kills: int = 0,
    deaths: int = 0,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        (player, new_player) = push_match(
            config,
            playfab_id,
            user_name,
            structure_damage_percent,
            score,
            kills,
            deaths,
        )
        if not player:
            await ctx.respond(
                "ERROR: "
                + "Provide either playfab_id or user_name to identify player."
                + "Provide both if not sure whether player exists, in which case they will be created in the system."
            )
            return
        await config.asave()
        await discordLeaderboard.send_board(config)
        player_txt = f"{player.name} ({player.playfab_id})"
        if new_player:
            player_txt += " **[new player]**"
        await ctx.respond(
            f"Done. Added {make_ordinal(len(player.matches))} match for {player_txt}."
        )
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


BULK_STAGED: list[MatchInputPlayer] = []


@admin_cmds.command(description="add matches from a file")
@discord.default_permissions(send_messages=True)
@discord.guild_only()
@discord.option("file", type=discord.SlashCommandOptionType.attachment)
async def bulk_match(
    ctx: discord.ApplicationContext,
    file: discord.message.Attachment,
    page_size: int = 3,
):
    try:
        await ctx.defer()
        BULK_STAGED.clear()
        textBytes = await file.read()
        textStr = textBytes.decode()
        matches = parse_matches(textStr)
        embeds: list[discord.Embed] = []
        while len(matches) > 0:
            removed = [matches.pop(0) for m in range(min(len(matches), page_size))]
            page_embed = discord.Embed(
                title="Bulk Add Match",
                description="`/mng confirm` to confirm addition\n`/mng reject` to reject addition",
                color=15844367,
            )
            for match in removed:
                # Split match data into separate fields for each team to avoid 1024 char limit
                team1_txt = ""
                team2_txt = ""
                
                for player in match.team_1:
                    BULK_STAGED.append(player)
                    team1_txt += (
                        f"- {player.user_name} ({player.playfab_id}): "
                        f"Score {player.score}; "
                        f"K {player.kills} | D {player.deaths} | {player.structure_damage}% DMG\n"
                    )
                
                for player in match.team_2:
                    BULK_STAGED.append(player)
                    team2_txt += (
                        f"- {player.user_name} ({player.playfab_id}): "
                        f"Score {player.score}; "
                        f"K {player.kills} | D {player.deaths} | {player.structure_damage}% DMG\n"
                    )
                
                # Add separate fields for each team to stay under Discord's 1024 char limit
                page_embed.add_field(
                    name=f"Match {match.match_num} - Team 1 ({'WIN' if match.winning_team == 1 else 'LOSS'})",
                    value=team1_txt,
                    inline=False
                )
                page_embed.add_field(
                    name=f"Match {match.match_num} - Team 2 ({'WIN' if match.winning_team == 2 else 'LOSS'})",
                    value=team2_txt,
                    inline=False
                )
            embeds.append(page_embed)
        paginator = Paginator(
            pages=[Page(embeds=[em]) for em in embeds], author_check=True
        )
        await paginator.respond(ctx.interaction)
        # await ctx.respond(embed=embed)
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="confirm bulk add match")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def confirm(ctx: discord.ApplicationContext):
    try:
        if len(BULK_STAGED) == 0:
            await ctx.respond("Nothing is staged")
            return
        await ctx.defer()
        config = await LeaderBoard.aload()
        txt = ""
        for player in BULK_STAGED:
            (added, new_player) = push_match(
                config,
                player.playfab_id,
                player.user_name,
                player.structure_damage,
                player.score,
                player.kills,
                player.deaths,
            )
            txt += f"- {player.user_name} ({player.playfab_id}): Success: {bool(added)}; New: {new_player}\n"
        await config.asave()
        await discordLeaderboard.send_board(config)
        chunk_size = 2000 - len("```\n\n```")
        chunks = map(lambda x: "```\n" + x + "```", split_chunks(txt, chunk_size))
        for chunk in chunks:
            await ctx.send(chunk)
        await ctx.respond("Done")
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="confirm bulk add match")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def reject(ctx: discord.ApplicationContext):
    try:
        BULK_STAGED.clear()
        await ctx.respond("Done. Discarded bulk add match.")
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="show system metadata")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def metadata(
    ctx: discord.ApplicationContext,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
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
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="get full system json")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def get_json(
    ctx: discord.ApplicationContext,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        async with aiofiles.open(LeaderBoard.get_path(), "r") as config_file:
            file_content = await config_file.read()
            await ctx.respond(
                file=discord.File(
                    io.BytesIO(
                        bytes(
                            json.dumps(json.loads(file_content), indent=2),
                            encoding="utf8",
                        )
                    ),
                    "leaderboard.json",
                ),
            )
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR")


@admin_cmds.command(description="create backup of current leaderboard")
@discord.default_permissions(administrator=True)
@discord.guild_only()
async def backup(
    ctx: discord.ApplicationContext,
):
    try:
        if CONFIG_BOT_CHANNEL_ID and ctx.channel_id != CONFIG_BOT_CHANNEL_ID:
            await ctx.respond("Unauthorized")
            return
        await ctx.defer()
        
        # Create timestamp for backup filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"leaderboard_backup_{timestamp}.json"
        backup_path = f"./persist/{backup_filename}"
        
        # Copy current leaderboard to backup file
        async with aiofiles.open(LeaderBoard.get_path(), "r") as source_file:
            content = await source_file.read()
        
        async with aiofiles.open(backup_path, "w") as backup_file:
            await backup_file.write(content)
        
        # Send the backup file to Discord
        await ctx.respond(
            f"✅ Backup created successfully!\n📁 **{backup_filename}**\n\n💾 **Backup saved on server** in `./persist/` directory\n📎 **File attached below** for your convenience",
            file=discord.File(backup_path, backup_filename)
        )
        
    except Exception as e:
        logger.error(e)
        await ctx.respond("ERROR: Failed to create backup")


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
        logger.error(e)
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
            snippet, config.aliased_ranks(), place_start, sort=False
        )
        await ctx.respond("```\n" + table + "\n```")
    except Exception as e:
        logger.error(e)
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
        logger.error(e)
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
        logger.error(e)
        await ctx.respond("ERROR")


score_tracker = ScoreTracker(bot, CONFIG_BOT_CHANNEL_ID, admin_id)
bot.add_cog(score_tracker)
bot.add_cog(discordLeaderboard)
bot.run(os.environ["D_TOKEN"])
