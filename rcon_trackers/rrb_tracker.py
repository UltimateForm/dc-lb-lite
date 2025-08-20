import asyncio
from discord.ext import commands
from discord import Bot
import aiohttp
from reactivex import Subject
from common import logger
from leaderboard.rbb import RbbLeaderboard
from models.players import RbbLeaderBoardCfg, RbbPlayer
from models.rcon import ChatEvent, KillfeedEvent, LoginEvent
from discord.abc import Messageable
from parsers.main import make_ordinal
from rcon.rcon import RconContext
from rcon_trackers.game_events import GameEventsTracker
from table2ascii import table2ascii as t2a
from datetime import datetime, timezone
import re

TP_PATTERN = r"^(cc|c|L|R|up|d|dd|f|ff|fff|ffa|rr|ll)$"
BANNED_PLAYER_COMMANDS = (
    r"^\s*(\.randy|\.carrot|\.torch|\.bard|\.tpr|ft10|\.candy|\.flowerhat|"
    + r"\.pkhat|\.bathat|\.barrelhat|\.jughat|\.keghat|\.skhat|\.wkhat|\.lionhat|\.horns|"
    + r"\.angelhat|\.domehat|\.goathat|\.bucket|\.doorshield|\.spoon|\.turdmaul|\.dlute|"
    + r"\.flower|\.lance|\.cross|\.bottle|\.rtp|\.givewp|\.vgivewp|\.vresize|\.pumpkin)"
)


class RbbTracker(commands.Cog):
    _admin_id: str
    _api_token: str
    _server_id: str
    _file_path: str
    _channel_id: int
    _channel: Messageable
    _tracking: dict[str, RbbPlayer]
    _placed: dict[str, RbbPlayer]
    _bot: Bot
    _match_running: bool = False
    matches: Subject[list[RbbPlayer]]
    _moderators: list[str] = []
    _admins: set[str] = set()
    _current_champ: str | None
    _current_champ_username: str | None
    _player_name_map: dict[str, str]
    _current_cfg: RbbLeaderBoardCfg | None = None

    def __init__(
        self,
        bot: Bot,
        channel_id: int,
        admin_id: str,
        api_token: str,
        server_id: str,
        file_path: str,
    ):
        self._bot = bot
        self._admin_id = admin_id
        self._api_token = api_token
        self._server_id = server_id
        self._file_path = file_path
        self._channel_id = channel_id
        self._tracking = dict()
        self.matches = Subject()
        if not self._admin_id:
            raise Exception("ADMIN ID NOT LOADED")
        self._admins = set([self._admin_id])
        self._moderators = []
        self._player_name_map = dict()
        self._current_champ_username = None
        self._current_champ = None
        self._current_cfg = None

    async def set_moderators(self):
        url = f"https://panel.academy-gaming.org/api/client/servers/{self._server_id}/files/contents?file=/Mordhau/Saved/PlayerFiles/Moderator_List.txt"
        headers = {"Authorization": f"Bearer {self._api_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Error: {response.status}")
                        return None
                    content = await response.text()
                    mod_ids = content.strip().splitlines()
                    self._moderators = mod_ids
                    if self._channel:
                        mods_list = "\n- ".join(self._moderators)
                        await self._channel.send(f"Moderators set:\n {mods_list}")
        except aiohttp.ClientError as e:
            logger.error(f"An error occurred while setting mods: {e}")
            return None

    async def set_admins(self):
        url = f"https://panel.academy-gaming.org/api/client/servers/{self._server_id}/files/contents?file=/Mordhau/Saved/Config/LinuxServer/Game.ini"
        headers = {"Authorization": f"Bearer {self._api_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Error: {response.status}")
                        return None
                    content = await response.text()
                    pattern = r"^(Admins|superadmin)=(\w+)$"
                    line_matches = re.findall(pattern, content, re.MULTILINE)
                    ids = [m[1] for m in line_matches]
                    self._admins.update(ids)
                    if self._channel:
                        admin_list = " | ".join(self._admins)
                        await self._channel.send(
                            f"Admin list set:\n```\n{admin_list}\n```"
                        )
        except aiohttp.ClientError as e:
            logger.error(f"An error occurred while setting admins: {e}")
            return None

    async def get_rbb_brawl_content(self) -> list[str] | None:
        url = f"https://panel.academy-gaming.org/api/client/servers/{self._server_id}/files/contents?file={self._file_path}"
        headers = {"Authorization": f"Bearer {self._api_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Error: {response.status}")
                        return None
                    content = await response.text()
                    player_ids = content.strip().splitlines()
                    return player_ids
        except aiohttp.ClientError as e:
            logger.error(f"An error occurred: {e}")
            return None

    async def handle_chat_event(self, message: ChatEvent):
        normal_msg = message.message.strip().lower()

        if message.player_id not in self._player_name_map:
            self._player_name_map[message.player_id] = message.user_name
            logger.info(
                f"Adding {message.user_name} ({message.player_id}) to player name map"
            )


        if normal_msg == ".myrbb" and self._current_cfg:
            logger.info(
                f"Received .myrbb command from {message.user_name} ({message.player_id})"
            )
            player = self._current_cfg.get_player(message.player_id)
            if player is not None:
                sorted_players = sorted(self._current_cfg.players, key=lambda p: p.score, reverse=True)
                placement = next((i + 1 for i, p in enumerate(sorted_players) if p.playfab_id == player.playfab_id), None)
                ordinal_placement = make_ordinal(placement or 0)
                msg = (
                    f"{message.user_name} ({player.score} points): Placed {ordinal_placement} | "
                    f"{player.kills} fistings | "
                    f"fisted {player.deaths} times | "
                    f"{player.wins or 0} wins"
                )
                async with RconContext() as client:
                    await client.execute(
                        f"say {msg}"
                    )

        if normal_msg == ".rbbchamp" and self._current_champ_username:
            async with RconContext() as client:
                await client.execute(
                    f"say Current RBB champion is: {self._current_champ_username}"
                )
        if self._match_running:
            logger.info(
                f"Received chat event: {message.message} from {message.user_name}"
            )
        if self._match_running and normal_msg == ".rbb":
            ingame_players = [
                (
                    player.name
                    or self._player_name_map.get(player.playfab_id, None)
                    or player.playfab_id
                )
                for player in self._tracking.values()
            ]
            joined_players = " | ".join(ingame_players)
            async with RconContext() as client:
                await client.execute(
                    f"say Players left in current RBB match: {joined_players}"
                )

        if self._match_running and message.player_id in self._tracking.keys():
            current_player = self._tracking[message.player_id]
            if current_player.name != message.user_name:
                current_player.name = message.user_name
            if re.match(BANNED_PLAYER_COMMANDS, message.message):
                await self.elliminate_player(message.player_id)
                async with RconContext() as client:
                    await client.execute(
                        f"say {current_player.name} WAS ELIMINATED\nDON'T USE COMMANDS IN BAR BRAWLS!"
                    )
                    await client.execute(f"killplayer {message.player_id}")
            if re.match(TP_PATTERN, message.message):
                await self.elliminate_player(message.player_id)
                async with RconContext() as client:
                    await client.execute(
                        f"say {current_player.name} WAS ELIMINATED\nDON'T USE COMMANDS IN BAR BRAWLS!"
                    )
                    await client.execute(f"killplayer {message.player_id}")

        if message.player_id != self._admin_id:
            return

        if normal_msg == ".rbb lock":
            logger.info(
                f"rbb lock message received (from {message.user_name}({message.player_id})) and validated!"
            )
            player_ids = await self.get_rbb_brawl_content()
            if player_ids:
                self._match_running = True
                self._tracking = {
                    id: RbbPlayer(id, self._player_name_map.get(id, ""))
                    for id in player_ids
                }
                logger.info(f"Players to track: {self._tracking.keys()}")
                self._placed = {}
                joined_playfab_ids = ",".join(player_ids)
                await self._channel.send(
                    f"PlayfabIds received from server txt: {joined_playfab_ids}"
                )

        if normal_msg == "rbbend":
            logger.info(
                f"rbbend message received (from {message.user_name}({message.player_id})) and validated!"
            )

            async with RconContext() as client:
                if not self._match_running:
                    await client.execute(
                        "say No match is currently running you dummass"
                    )
                    return
                while len(self._tracking) > 1:
                    player = min(self._tracking.values(), key=lambda x: x.kills)
                    try:
                        await client.execute(f"killplayer {player.playfab_id}")
                    except Exception as e:
                        logger.error(
                            f"Error killing {player.name}({player.playfab_id}): {e}"
                        )
                    await self.elliminate_player(player.playfab_id)

            await self.check_win()

    async def handle_login_event(self, event: LoginEvent):
        if event.player_id in self._tracking:
            if event.player_id not in self._player_name_map:
                self._player_name_map[event.player_id] = event.user_name
                await self.elliminate_player(event.player_id)

    async def handle_rbb_match_over(self, winner: str):
        """
        Handles the end of an RBB match, calculates and displays player points.
        Scoring rules:
            - 1 kill = 1 point
            - 1 death = -3 points (and you're out)
            - Placement points:
                6th place = 4 points
                5th place = 5 points
                4th place = 6 points
                3rd place = 7 points
                2nd place = 8 points
                1st place = 10 points
        Updates internal state to mark the match as not running, computes points for each player
        based on kills, deaths, and placement, and generates a summary table.
        """

        self._match_running = False

        def get_points(player: RbbPlayer):
            # Exponential kill scoring: 1st kill = 1, 2nd = 2, ..., nth = n
            kd_points = sum(range(1, player.kills + 1))
            if player.place > 1 and kd_points > 5:
                kd_points += -3
            placement_points = max(0, 10 - (player.place - 1))
            return max(0, kd_points + placement_points)

        placed_players = sorted(
            self._placed.values(), key=lambda p: p.kills, reverse=True
        )
        table = t2a(
            header=["#", "Name", "Points", "K"],
            body=[
                [
                    player.place,
                    player.name or self._player_name_map.get(player.playfab_id, None),
                    get_points(player),
                    player.kills,
                ]
                for player in placed_players
            ],
        )
        current_time = round(datetime.now(timezone.utc).timestamp())
        time_sig = f"<t:{current_time}>"
        await self._channel.send(f"Match over{time_sig}\n```\n{table}\n```")
        self._current_cfg = await RbbLeaderBoardCfg.aload()
        self._current_cfg._last_winner = winner
        for player in placed_players:
            player.score = get_points(player)
            found_player = self._current_cfg.get_player(player.playfab_id)
            if found_player is None:
                self._current_cfg.players.append(player)
            else:
                found_player.kills += player.kills
                found_player.score += player.score
                found_player.deaths += player.deaths
                found_player.wins += player.wins

        await self._current_cfg.asave()
        leaderboard = self._bot.get_cog(RbbLeaderboard.__name__)
        if isinstance(leaderboard, RbbLeaderboard):
            asyncio.create_task(
                leaderboard.publish_leaderboards(self._current_cfg, False)
            )

    async def check_win(self):
        current_tracking_length = len(self._tracking)
        if current_tracking_length == 1:
            last_player = self._tracking.popitem()[1]
            self._placed[last_player.playfab_id] = last_player
            last_player.place = current_tracking_length
            last_player.wins += 1
            new_win = self._current_champ != last_player.playfab_id
            self._current_champ_username = last_player.name
            self._current_champ = last_player.playfab_id
            logger.info("SET WINNER TO: " + self._current_champ_username)
            await self.congratulate_winner(new_win, last_player)

            await self.handle_rbb_match_over(last_player.playfab_id)

    async def elliminate_player(self, player_id: str):
        if not self._match_running:
            return
        if player_id not in self._tracking.keys():
            return
        current_player = self._tracking.pop(player_id)
        current_player.place = len(self._tracking) + 1
        self._placed[current_player.playfab_id] = current_player
        await self._channel.send(
            f"```{current_player.name} ({current_player.playfab_id}) has been eliminated```"
        )
        await self.check_win()

    async def congratulate_winner(self, new_win: bool, player: RbbPlayer):
        if not self._match_running:
            return
        leaderboard_player = self._current_cfg.get_player(player.playfab_id) if self._current_cfg else None
        current_wins = leaderboard_player.wins if leaderboard_player else 0
        win_ordinal = make_ordinal(current_wins + 1)
    
        new_win_msg = f"{player.name or player.playfab_id} IS THE HARDEST BASTARD!!\nTHEY WIN THE BAR BRAWL!!!\nTHEIR {win_ordinal} WIN!\n"
        renew_msg = f"{player.name or player.playfab_id} REMAINS UNDEFEATED!!!! YET AGAIN HE WINS THE BAR BRAWL!!!\nTHEIR {win_ordinal} WIN!\n"
        msg = new_win_msg if new_win else renew_msg
        async with RconContext() as client:
            await client.execute(f"say {msg}")

        await self._channel.send(
            f"```{player.name} ({player.playfab_id}) is the last one standing!```"
        )

    async def punish_ffaer(self, name: str, id: str):
        async with RconContext() as client:
            await client.execute(
                f"say {name} IS THE HARDEST BASTARD\nHE WINS THE BAR BRAWL"
            )
            await client.execute(f"killplayer {id}")

    async def handle_killfeed_event(self, ev: KillfeedEvent):

        if ev.killer_id not in self._player_name_map:
            self._player_name_map[ev.killer_id] = ev.user_name
            logger.info(
                f"KS: Adding {ev.user_name} ({ev.killer_id}) to player name map"
            )

        if ev.killed_id not in self._player_name_map:
            self._player_name_map[ev.killed_id] = ev.killed_user_name
            logger.info(
                f"KS: Adding {ev.killed_user_name} ({ev.killed_id}) to player name map"
            )

        if not self._match_running:
            return
        logger.info(
            f"{ev.user_name} ({ev.killer_id}) has killed {ev.killed_user_name} ({ev.killed_id})"
        )
        hunter_id = ev.killer_id
        victim_id = ev.killed_id
        current_ids = self._tracking.keys()
        if (
            hunter_id not in current_ids
            and victim_id in current_ids
            and hunter_id not in self._admins
        ):
            async with RconContext() as client:
                await client.execute(
                    f"say {ev.killed_user_name} was killed by someone outside the ring... cringe\n{ev.user_name} will be kicked for it."
                )
                await client.execute(
                    f"kick {hunter_id} Don't interfere with Bar Brawls"
                )

        if hunter_id in current_ids and victim_id in current_ids and victim_id == self._current_champ:
            async with RconContext() as client:
                await client.execute(
                    f"say {ev.user_name} BEAT THE PREVIOUS CHAMPION!! {ev.killed_user_name}!!! WILL THEY BECOME THE NEXT?!"
                )

        if victim_id not in current_ids:
            return
        asyncio.create_task(
            self._channel.send(
                f"```{ev.user_name} ({ev.killer_id}) has eliminated {ev.killed_user_name} ({ev.killed_id})```"
            )
        )
        current_hunter = self._tracking.get(hunter_id, None)
        current_victim = self._tracking.pop(victim_id)
        if current_hunter:
            if not current_hunter.name:
                current_hunter.name = ev.user_name
            current_hunter.kills += 1
        current_victim.deaths += 1

        if len(self._placed) == 0:
            async with RconContext() as client:
                await client.execute(f"say {ev.user_name} FISTED THE FIRST PLAYER")
        if not current_victim.name:
            current_victim.name = ev.killed_user_name
        current_tracking_length = len(self._tracking)
        current_victim.place = current_tracking_length + 1
        self._placed[current_victim.playfab_id] = current_victim
        await self.check_win()

    @commands.Cog.listener()
    async def on_ready(self):
        asyncio.create_task(self.on_ready_async())

    async def on_ready_async(self):
        channel = self._bot.get_channel(self._channel_id)
        if not isinstance(channel, Messageable):
            raise ValueError(
                f"Expected channel with ID {self._channel_id} to be a Messageable, but got {type(channel)}"
            )
        await channel.send("Will use this channel for RBB tracking events")
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

        def handle_login_event(x: LoginEvent):
            if not self._match_running:
                return
            asyncio.create_task(self.handle_login_event(x))

        game_events_tracker.chat_events.subscribe(handle_chat_event)
        game_events_tracker.killfeed_events.subscribe(handle_killfeed_event)
        game_events_tracker.login_events.subscribe(handle_login_event)
        self._current_cfg = await RbbLeaderBoardCfg.aload()
        self._current_champ = self._current_cfg._last_winner
        await self.set_admins()
