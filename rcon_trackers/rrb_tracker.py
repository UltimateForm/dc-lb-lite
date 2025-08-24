import asyncio
from discord.ext import commands
from discord import Bot
import aiohttp
from reactivex import Subject
from common import logger
from leaderboard.rbb import RbbLeaderboard
from models.players import RbbBounty, RbbLeaderBoardCfg, RbbPlayer
from models.rcon import ChatEvent, KillfeedEvent, LoginEvent
from discord.abc import Messageable
from parsers.main import make_ordinal, split_chunks
from rcon.rcon import RconClient
from rcon.rcon_pool import RconConnectionPool
from rcon_trackers.game_events import GameEventsTracker
from table2ascii import table2ascii as t2a
from datetime import datetime, timezone
import re
import inflect

TP_PATTERN = r"^(cc|c|L|R|up|d|dd|f|ff|fff|ffa|rr|ll)$"
BANNED_PLAYER_COMMANDS = (
    r"^\s*(\.randy|\.carrot|\.torch|\.bard|\.tpr|ft10|\.candy|\.flowerhat|"
    + r"\.pkhat|\.bathat|\.barrelhat|\.jughat|\.keghat|\.skhat|\.wkhat|\.lionhat|\.horns|"
    + r"\.angelhat|\.domehat|\.goathat|\.bucket|\.doorshield|\.spoon|\.turdmaul|\.dlute|"
    + r"\.flower|\.lance|\.cross|\.bottle|\.rtp|\.givewp|\.vgivewp|\.vresize|\.pumpkin)"
)

KS_STREAK_MSGS = {
    3: "{0} IS ON A 3 PLAYER KILLSTREAK!!",
    5: "{0} IS UNSTOPPABLE!! 5 PLAYER KILL STREAK!!",
    7: "{0} HAS BLOODY KNUCKLES!! 7 PLAYER KILL STREAK!!",
    10: "{0} IS GODLIKE. 10 PLAYER KILL STREAK!!",
}


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
    # TODO: this is redundant (also in rbb leaderboard)
    _current_champ_username: str | None
    _player_name_map: dict[str, str]
    _current_cfg: RbbLeaderBoardCfg = RbbLeaderBoardCfg()
    _handlers: dict[str, asyncio.Task] = {}
    inflect_engine: inflect.engine
    rcon_pool: RconConnectionPool
    background_tasks: set[asyncio.Task]

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
        self._admins = set([self._admin_id, "D1247A0B618D12E"])
        self._moderators = []
        self._player_name_map = dict()
        self._current_champ_username = None
        self._current_cfg = RbbLeaderBoardCfg.load()
        self.inflect_engine = inflect.engine()
        self.rcon_pool = RconConnectionPool(3)
        self.background_tasks = set()

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

    async def say_rcon(self, message: str, existing_client: RconClient | None = None):
        client: RconClient | None = existing_client
        try:
            client = await self.rcon_pool.get_client() if client is None else client
            chunks = split_chunks(message, 300)
            for chunk in chunks:
                await client.execute(command=f"say {chunk}")
        except Exception as e:
            logger.error(f"Failed to run say cmd: {e}")
        finally:
            if not existing_client and client:
                await self.rcon_pool.release_client(client)

    async def get_rbb_brawl_content(self) -> list[str] | None:
        url = f"https://panel.academy-gaming.org/api/client/servers/{self._server_id}/files/contents?file={self._file_path}"
        headers = {"Authorization": f"Bearer {self._api_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"PTERO BAD HTTP CODE: {response.status}")
                        return None
                    content = await response.text()
                    player_ids = content.strip().splitlines()
                    return player_ids
        except aiohttp.ClientError as e:
            logger.error(f"An error occurred: {e}")
            return None

    async def handle_b_command(self, message: ChatEvent):
        client: RconClient | None = None
        try:
            client = await self.rcon_pool.get_client()
            player_list = await client.execute("playerlist")
            online_ids = re.findall(r"^([A-F0-9]*),", player_list, re.MULTILINE)
            await self.broadcast_bounties(online_ids, existing_client=client)
        except Exception as e:
            logger.error(f"Failed to run b command: {e}")
        finally:
            if client:
                await self.rcon_pool.release_client(client)

    def backtask(self, task: asyncio.Task):
        try:
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)
            logger.debug(f"Background task added; total: {len(self.background_tasks)}")
        except Exception as e:
            logger.error(f"Background task failed: {e}")


    async def broadcast_bounties(
        self,
        ids: list[str],
        heading: str = "RBB Bounties:\n",
        existing_client: RconClient | None = None,
    ):
        if len(ids or []) == 0:
            logger.error("Can't broadcast bounties because player list empty")
            return
        if not self._current_cfg:
            logger.error("Current RBB config is not loaded, cannot broadcast bounties.")
            return
        if not self._current_cfg.bounties:
            logger.error("No bounties set, cannot broadcast bounties.")
            return
        players = self._current_cfg.players if self._current_cfg else []
        if len(players) == 0:
            logger.error("Can't broadcast bounties because player list empty")
            return
        pairs = [
            (player.playfab_id, player.name)
            for player in players
            if player.playfab_id in ids
        ]
        msg = heading

        for playfab_id, name in pairs:
            bounty = self._current_cfg.bounties.get(playfab_id, None)
            if bounty:
                msg += f"{name} - {bounty.points} Pts x {bounty.claimable}\n"

        if (len(pairs)) == 0 or msg.strip() == heading.strip():
            msg = "No bounties available"
            await self.say_rcon(msg, existing_client)
            return
        await self.say_rcon(msg, existing_client)

    async def handle_rbbend_cmd(self, message: ChatEvent):
        client: RconClient | None = None
        try:
            client = await self.rcon_pool.get_client()
            if not self._match_running:
                await client.execute("say No match is currently running you dummass")
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
        except Exception as e:
            logger.error(f"Failed to run rbbend cmd: {e}")
        finally:
            if client:
                await self.rcon_pool.release_client(client)

    async def handle_chat_event(self, message: ChatEvent):
        normal_msg = message.message.strip().lower()

        if message.player_id not in self._player_name_map:
            self._player_name_map[message.player_id] = message.user_name
            logger.debug(
                f"Adding {message.user_name} ({message.player_id}) to player name map"
            )

        if normal_msg == ".myrbb" and self._current_cfg:
            logger.debug(
                f"Received .myrbb command from {message.user_name} ({message.player_id})"
            )
            player = self._current_cfg.get_player(message.player_id)
            if player is not None:
                sorted_players = sorted(
                    self._current_cfg.players, key=lambda p: p.total_score, reverse=True
                )
                placement = next(
                    (
                        i + 1
                        for i, p in enumerate(sorted_players)
                        if p.playfab_id == player.playfab_id
                    ),
                    None,
                )
                ordinal_placement = make_ordinal(placement or 0)
                msg = (
                    f"{message.user_name} ({player.total_score} points): Placed {ordinal_placement} | "
                    f"{player.kills} fistings | "
                    f"fisted {player.deaths} times | "
                    f"{player.wins or 0} wins"
                )
                if player.claimed_bounties and len(player.claimed_bounties):
                    msg += f" | {player.total_bounty_score} points from bounties"
                self.backtask(asyncio.create_task(self.say_rcon(msg)))

        if normal_msg == ".rbbchamp" and self._current_champ_username:
            await self.say_rcon(
                f"Current RBB champion is: {self._current_champ_username}"
            )
        if normal_msg == ".b" and (
            message.player_id in self._tracking or message.player_id in self._admins
        ):
            self.backtask(asyncio.create_task(self.handle_b_command(message)))

        if self._match_running:
            logger.debug(
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
            self.backtask(asyncio.create_task(
                self.say_rcon(f"Players left in current RBB match: {joined_players}")
            ))

        if self._match_running and message.player_id in self._tracking.keys():
            current_player = self._tracking[message.player_id]
            if current_player.name != message.user_name:
                current_player.name = message.user_name
            if re.match(BANNED_PLAYER_COMMANDS, message.message):
                await self.elliminate_player(message.player_id)

                async def punish_func(name: str):
                    client: RconClient | None = None
                    try:
                        client = await self.rcon_pool.get_client()
                        await client.execute(
                            f"say {name} WAS ELIMINATED\nDON'T USE COMMANDS IN BAR BRAWLS!"
                        )
                        await client.execute(f"killplayer {message.player_id}")
                    except Exception as e:
                        logger.error(f"Failed to punish ffaer: {e}")
                    finally:
                        if client:
                            await self.rcon_pool.release_client(client)

                self.backtask(asyncio.create_task(punish_func(current_player.name)))
            if re.match(TP_PATTERN, message.message):
                await self.elliminate_player(message.player_id)

                async def punish_tp_func(msg_ev: ChatEvent):
                    client: RconClient | None = None
                    try:
                        client = await self.rcon_pool.get_client()
                        await client.execute(
                            f"say {msg_ev.user_name} WAS ELIMINATED\nDON'T USE COMMANDS IN BAR BRAWLS!"
                        )
                        await client.execute(f"killplayer {msg_ev.player_id}")
                    except Exception as e:
                        logger.error(f"Failed to punish TPer: {e}")
                    finally:
                        if client:
                            await self.rcon_pool.release_client(client)

                self.backtask(asyncio.create_task(punish_tp_func(message)))

        if message.player_id != self._admin_id:
            return

        if normal_msg.startswith(".bounty"):
            comps = normal_msg.split(" ")
            if len(comps) < 3:
                logger.error(
                    f"Invalid bounty command from {message.user_name} ({message.player_id}): {normal_msg}"
                )
                return
            bounty_target = comps[1].upper()
            amount = comps[2]
            times = int(comps[3]) if len(comps) > 3 and comps[3].isnumeric() else 1
            if len(comps) > 3 and comps[3].isnumeric():
                times = int(comps[3])
            if not self._current_cfg:
                logger.error("Current RBB config is not loaded, cannot set bounty.")
                return
            self._current_cfg.bounties[bounty_target] = RbbBounty(
                int(amount), int(times)
            )
            self.backtask(asyncio.create_task(
                self.say_rcon(
                    f"Bounty set for {self._player_name_map.get(bounty_target)}"
                )
            ))
            await self._current_cfg.asave()

        if normal_msg.startswith(".rmbounty"):
            comps = normal_msg.split(" ")
            if len(comps) < 2:
                logger.error(
                    f"Invalid removebounty command from {message.user_name} ({message.player_id}): {normal_msg}"
                )
                return
            bounty_target = comps[1]
            if not self._current_cfg:
                logger.error("Current RBB config is not loaded, cannot remove bounty.")
                return
            if bounty_target in self._current_cfg.bounties:
                self._current_cfg.bounties.pop(bounty_target)
                self.backtask(asyncio.create_task(
                    self.say_rcon(
                        f"Bounty removed for {self._player_name_map.get(bounty_target)}"
                    )
                ))
                await self._current_cfg.asave()

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
                logger.debug(f"Players to track: {self._tracking.keys()}")
                self._placed = {}
                joined_playfab_ids = " | ".join(player_ids)
                async def this_brawl_bounties(ids: list[str]):
                    await asyncio.sleep(7)
                    await self.broadcast_bounties(ids, "THIS BRAWL'S BOUNTIES:\n")
                self.backtask(asyncio.create_task(
                    this_brawl_bounties(player_ids)
                ))
                await self._channel.send(
                    f"PlayfabIds received from server txt:\n```\n{joined_playfab_ids}\n```"
                )

        if normal_msg == "rbbend":
            logger.info(
                f"rbbend message received (from {message.user_name}({message.player_id})) and validated!"
            )
            await self.handle_rbbend_cmd(message)

            await self.check_win()

    async def handle_login_event(self, event: LoginEvent):
        if event.player_id in self._tracking:
            self._player_name_map[event.player_id] = event.user_name
            await self.elliminate_player(event.player_id)

    async def handle_rbb_match_over(self):
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
                new_name = self._player_name_map.get(found_player.playfab_id)
                if new_name:
                    found_player.name = new_name
                for bounty_id, bounty_points in player.claimed_bounties.items():
                    found_player.claim_bounty(bounty_id, bounty_points)

        await self._current_cfg.asave()
        leaderboard = self._bot.get_cog(RbbLeaderboard.__name__)
        if isinstance(leaderboard, RbbLeaderboard):
            self.backtask(asyncio.create_task(
                leaderboard.publish_leaderboards(self._current_cfg, False)
            ))

    async def check_win(self):
        current_tracking_length = len(self._tracking)
        if current_tracking_length == 1:
            last_player = self._tracking.popitem()[1]
            self._player_name_map[last_player.playfab_id] = last_player.name
            self._placed[last_player.playfab_id] = last_player
            last_player.place = current_tracking_length
            last_player.wins += 1
            self._current_champ_username = last_player.name
            if self._current_cfg.last_winner == last_player.playfab_id:
                self._current_cfg.win_streak += 1
            else:
                self._current_cfg.win_streak = 1
            self._current_cfg.last_winner = last_player.playfab_id
            logger.info("SET WINNER TO: " + self._current_champ_username)
            await self.congratulate_winner(last_player, self._current_cfg.win_streak)

            await self.handle_rbb_match_over()

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

    async def congratulate_winner(self, player: RbbPlayer, streak: int = 1):
        if not self._match_running:
            return
        leaderboard_player = (
            self._current_cfg.get_player(player.playfab_id)
            if self._current_cfg
            else None
        )
        current_wins = leaderboard_player.wins if leaderboard_player else 0
        win_ordinal = make_ordinal(current_wins + 1)

        streak_msgs = [
            "{0} IS THE HARDEST BASTARD!!\nTHEY WIN THE BAR BRAWL!!!\nTHEIR {1} WIN!\n",
            "{0} - TWO WINS IN A ROW!!\nANOTHER BAR BRAWLS DUB!!\nTHEIR {1} WIN!!",
            "{0} {2} TIMES IN A ROW!!\nCAN ANYBODY BEAT THEM??\nTHEIR {1} WIN!!",
        ]

        msg = (
            streak_msgs[min(streak - 1, len(streak_msgs) - 1)].format(
                player.name,
                win_ordinal,
                self.inflect_engine.number_to_words(streak),  # type: ignore
            )
            if current_wins > 1
            else f"{player.name} WON THEIR FIRST BAR BRAWL!!\nIT WON'T BE THE LAST!!"
        )
        self.backtask(asyncio.create_task(self.say_rcon(msg)))

        await self._channel.send(
            f"```{player.name} ({player.playfab_id}) is the last one standing!```"
        )

    async def punish_ffaer(self, name: str, id: str, victim: str):
        client: RconClient | None = None
        try:
            client = await self.rcon_pool.get_client()
            await client.execute(
                f"say MODS, BAN THIS GUY. BLOW UP HIS FUCKING HOUSE\n{name} JUST KILLED {victim} FROM OUTSIDE THE RING."
            )
            await client.execute(f"kick {id} Don't interfere with Bar Brawls")
        except Exception as e:
            logger.error(f"Failed to run rbbend cmd: {e}")
        finally:
            if client:
                await self.rcon_pool.release_client(client)

    async def handle_killstreak(self, name: str, kills: int):
        if kills < 3:
            return
        if kills not in KS_STREAK_MSGS:
            return
        msg = KS_STREAK_MSGS[kills].format(name)
        await self.say_rcon(msg)

    async def handle_killfeed_event(self, ev: KillfeedEvent):

        if ev.killer_id not in self._player_name_map:
            self._player_name_map[ev.killer_id] = ev.user_name
            logger.debug(
                f"KS: Adding {ev.user_name} ({ev.killer_id}) to player name map"
            )

        if ev.killed_id not in self._player_name_map:
            self._player_name_map[ev.killed_id] = ev.killed_user_name
            logger.debug(
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
            await self.punish_ffaer(ev.user_name, ev.killer_id, ev.killed_user_name)

        if (
            hunter_id in current_ids
            and victim_id in current_ids
            and victim_id == self._current_cfg.last_winner
        ):
            await self.say_rcon(
                f"say {ev.user_name} BEAT THE PREVIOUS CHAMPION!! {ev.killed_user_name}!!! WILL THEY BECOME THE NEXT?!"
            )

        if victim_id not in current_ids:
            return
        self.backtask(
            asyncio.create_task(
                self._channel.send(
                    f"```{ev.user_name} ({ev.killer_id}) has eliminated {ev.killed_user_name} ({ev.killed_id})```"
                )
            )
        )
        current_hunter = self._tracking.get(hunter_id, None)
        current_victim = self._tracking.pop(victim_id)
        if current_hunter:
            if not current_hunter.name:
                current_hunter.name = ev.user_name
            current_hunter.kills += 1
            if current_hunter.kills >= 3 and current_hunter.kills in KS_STREAK_MSGS:
                self.backtask(
                    asyncio.create_task(
                        self.handle_killstreak(
                            current_hunter.name, current_hunter.kills
                        )
                    )
                )
            if current_victim.playfab_id in self._current_cfg.bounties:
                bounty = self._current_cfg.bounties[current_victim.playfab_id]
                current_hunter.claimed_bounties[current_victim.playfab_id] = (
                    bounty.points
                )
                bounty.claimable -= 1
                if bounty.claimable <= 0:
                    logger.info(
                        f"Bounty on {current_victim.name} ({current_victim.playfab_id}) claimed out, removing bounty."
                    )
                    removed_bounty = self._current_cfg.bounties.pop(
                        current_victim.playfab_id, None
                    )
                    if not removed_bounty:
                        logger.error(
                            f"Tried removing bounty for {current_victim.name} ({current_victim.playfab_id}) but none was removed.. why?"
                        )
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon(
                            f"{current_hunter.name} HAS CLAIMED A BOUNTY OF {bounty.points} POINTS FOR FISTING {current_victim.name}"
                        )
                    )
                )

        current_victim.deaths += 1

        if len(self._placed) == 0:
            self.backtask(asyncio.create_task(
                self.say_rcon(f"{ev.user_name} FISTED THE FIRST PLAYER")
            ))
        if not current_victim.name:
            current_victim.name = ev.killed_user_name
        current_tracking_length = len(self._tracking)
        current_victim.place = current_tracking_length + 1
        self._placed[current_victim.playfab_id] = current_victim
        await self.check_win()

    @commands.Cog.listener()
    async def on_ready(self):
        self.backtask(asyncio.create_task(self.on_ready_async()))

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
            self.backtask(asyncio.create_task(self.handle_killfeed_event(x)))

        def handle_chat_event(x: ChatEvent):
            self.backtask(asyncio.create_task(self.handle_chat_event(x)))

        def handle_login_event(x: LoginEvent):
            if not self._match_running:
                return
            self.backtask(asyncio.create_task(self.handle_login_event(x)))

        game_events_tracker.chat_events.subscribe(handle_chat_event)
        game_events_tracker.killfeed_events.subscribe(handle_killfeed_event)
        game_events_tracker.login_events.subscribe(handle_login_event)
        self._current_cfg = await RbbLeaderBoardCfg.aload()
        self._player_name_map = {
            player.playfab_id: player.name for player in self._current_cfg.players
        }
        self._current_champ_username = (
            self._player_name_map.get(self._current_cfg.last_winner, None)
            if self._current_cfg.last_winner
            else None
        )
        await self.set_admins()
