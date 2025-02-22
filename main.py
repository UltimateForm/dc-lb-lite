import discord
import discord.ext.commands as commands
import os
from dotenv import load_dotenv
import math
import asyncio
from table2ascii import table2ascii as t2a
from models.players import LeaderBoard, Player, GameMatch
from parsers.main import compute_next_gate_text, compute_gate_text, make_ordinal
from aiofiles import open as aopen, os as aos

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
    _message: discord.Message | None = None
    _file_path = "./persist/leaderboard_msg_id"

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self._last_member = None

    def cog_unload(self):
        return super().cog_unload()

    async def write_msg_id(self):
        if self._message is None:
            return
        async with aopen(self._file_path, "w") as file:
            await file.write(str(self._message.id))

    async def delete_previous_message(self) -> str | None:
        if self.channel is None:
            return
        try:
            file_exists = await aos.path.exists(self._file_path)
            if not file_exists:
                return
            msg_id: str | None = ""
            async with aopen(self._file_path, "r") as file:
                msg_id = await file.read()
            if not msg_id.isdecimal():
                return
            parsed_msg_id = int(msg_id)
            msg = await self.channel.fetch_message(parsed_msg_id)
            await msg.delete()
        except Exception as e:
            print(e)

    @commands.Cog.listener()
    async def on_ready(self):
        print("Leaderboard is ready!")
        channel = await self.bot.fetch_channel(CHANNEL_ID)
        if isinstance(channel, discord.abc.Messageable):
            self.channel = channel
            await self.delete_previous_message()
            asyncio.create_task(self.send_board())

    def get_row(self, player_data: Player, config: LeaderBoard):
        kills: int = player_data.total_kills
        deaths: int = player_data.total_deaths
        score = player_data.total_score
        (_, rank_txt) = compute_gate_text(
            score, dict([(str(k), v) for (k, v) in config.rank_config.items()])
        )
        return [
            player_data.name,
            rank_txt or "None",
            score,
            kills,
            deaths,
        ]

    async def send_board(self, existing_leaderboard: LeaderBoard | None = None):
        if not self.channel:
            return
        leaderboard_data = existing_leaderboard
        if not leaderboard_data:
            leaderboard_data = await LeaderBoard.aload()
        board_data = sorted(
            [
                self.get_row(value, leaderboard_data)
                for value in leaderboard_data.players
            ],
            key=lambda x: x[2],
            reverse=True,
        )
        text = (
            "```"
            + t2a(
                header=["#", "Name", "Rank", "Score", "K", "D"],
                body=[
                    [index + 1, dt[0], dt[1], human_format(dt[2]), *dt[3:]]
                    for (index, dt) in enumerate(
                        board_data[: leaderboard_data.max_items]
                    )
                ],
            )
            + "```"
        )
        if self._message:
            self._message = await self._message.edit(content=text)
        else:
            self._message = await self.channel.send(text)
            asyncio.create_task(self.write_msg_id())


# region admin commands
admin_cmds = bot.create_group("mng", "Admin commands")
discordLeaderboard = Leaderboard(bot)


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


@bot.slash_command(description="show user match history")
@discord.default_permissions(send_messages=True)
@discord.guild_only()
async def mymh(ctx: discord.ApplicationContext, playfab_or_user_name: str):
    try:
        config = await LeaderBoard.aload()
        player = config.get_player(playfab_or_user_name)
        if player is None:
            await ctx.respond(f"Couldn't find player by id/name {playfab_or_user_name}")
            return
        if len(player.matches) < 1:
            await ctx.respond(f"No matches found with {playfab_or_user_name}")
            return
        embed = discord.Embed(title="Your Match History", color=15844367)
        for index, match in enumerate(player.matches):
            embed.add_field(
                name=f"{make_ordinal(index + 1)} match",
                value=f"Score: {match.score} | Kills: {match.kills} | Deaths: {match.deaths} | Structure Damage: {match.structure_damage}%",
                inline=False,
            )
        await ctx.respond(embed=embed)

    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


@bot.slash_command(description="show user score stats")
@discord.default_permissions(send_messages=True)
@discord.guild_only()
async def myscore(ctx: discord.ApplicationContext, playfab_or_user_name: str):
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
            title="Your Score",
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
        await ctx.respond(embed=embed)
    except Exception as e:
        print(e)
        await ctx.respond("ERROR")


bot.add_cog(discordLeaderboard)
bot.run(os.environ["D_TOKEN"])
