import asyncio
from discord import ApplicationContext, Bot, Cog
import aiohttp
from reactivex import Subject
from common import logger
from leaderboard.rbb import RbbLeaderboard
from models.players import RbbBounty, RbbLeaderBoardCfg, RbbPlayer
from models.rcon import ChatEvent, KillfeedEvent, LoginEvent
from discord.abc import Messageable
from parsers.main import (
    make_ordinal,
    split_chunks,
    get_playfab_ids_from_player_list,
)
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


class RbbTracker(Cog):
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
    _current_champ_username: str | None
    _player_name_map: dict[str, str]
    _current_cfg: RbbLeaderBoardCfg = RbbLeaderBoardCfg()
    _handlers: dict[str, asyncio.Task] = {}
    inflect_engine: inflect.engine
    rcon_pool: RconConnectionPool
    background_tasks: set[asyncio.Task]
    _debug_mode: bool = False
    pending_revenge: dict[str, str] = {}

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
        logger.info(f"Using server id {server_id}")
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

    def get_name(self, player_id: str) -> str:
        return self._player_name_map.get(player_id, player_id)

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
            if client:
                logger.info(f"Expiring client {client.id} since it errored out")
                client.used = 120
        finally:
            if not existing_client and client:
                await self.rcon_pool.release_client(client)

    async def get_rbb_brawl_content(self) -> list[str] | None:
        url = f"https://panel.academy-gaming.org/api/client/servers/{self._server_id}/files/contents?file={self._file_path}"
        headers = {"Authorization": f"Bearer {self._api_token}"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(30)
                ) as response:
                    if response.status != 200:
                        logger.error(f"PTERO BAD HTTP CODE: {response.status}")
                        return None
                    content = await response.text()
                    player_ids = content.strip().splitlines()
                    return player_ids
        except aiohttp.ClientError as e:
            logger.error(f"An error occurred: {e}")
            return None
        except asyncio.TimeoutError as e:
            logger.error(f"Request to PTERO timed out {e}")
            return None

    async def cull_offline_players(self, existing_client: RconClient | None = None):
        client: RconClient | None = existing_client
        try:
            client = await self.rcon_pool.get_client() if client is None else client
            player_list = await client.execute("playerlist")
            logger.info(f"Playerlist: {player_list}")
            online_ids = get_playfab_ids_from_player_list(player_list)
            logger.info(f"Online IDs: {online_ids}")
            logger.info(f"Tracking IDs: {self._tracking.keys()}")
            players_to_cull = [
                id for id in self._tracking.keys() if id not in online_ids
            ]
            logger.info(f"Players to cull: {players_to_cull}")
            # for id in players_to_cull:
            #     await self.elliminate_player(id, "offline")
        except Exception as e:
            logger.error(f"Failed to run playerlist cmd in cull_offline_players(): {e}")
            if client and not existing_client:
                logger.info(f"Expiring client {client.id} since it errored out")
                client.used = 120
            else:
                raise e
        finally:
            if client and not existing_client:
                await self.rcon_pool.release_client(client)

    async def handle_b_command(self, message: ChatEvent):
        client: RconClient | None = None
        try:
            client = await self.rcon_pool.get_client()
            player_list = await client.execute("playerlist")
            online_ids = get_playfab_ids_from_player_list(player_list)
            await self.broadcast_bounties(online_ids, existing_client=client)
        except Exception as e:
            logger.error(f"Failed to run b command: {e}")
            if client:
                logger.info(f"Expiring client {client.id} since it errored out")
                client.used = 120
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

    async def explain_bounty(self, id: str, user_name: str):
        player_bounties = self._current_cfg.bounties.get(id, None)
        if not player_bounties:
            return
        static_pts = player_bounties.static_points
        static_claimed = player_bounties.static_claimable
        components = (
            [f"manually set: {static_pts} x {static_claimed}"] if static_pts else []
        )
        other_sources = (
            set(b.source for b in player_bounties.dynamic_bounties)
            if player_bounties.dynamic_bounties
            else set()
        )
        for source in other_sources:
            source_bounties = [
                b for b in player_bounties.dynamic_bounties if b.source == source
            ]
            source_pts = sum(b.points for b in source_bounties)
            source_claimed = sum(b.claimable for b in source_bounties)
            components.append(f"{source}: {source_pts} x {source_claimed}")
        all_comps = " | ".join(components)
        await self.say_rcon(
            f"{user_name} has a total bounty of {player_bounties.points} points (claimable 7 times)\n{all_comps}"
        )

    async def start_match(self, player_ids: list[str], debug: bool = False):
        if debug:
            await self._current_cfg.asave()
            self._debug_mode = True
            await self.say_rcon("DEBUG BRAWL STARTING")
        self.pending_revenge = {}
        self._match_running = True
        self._tracking = {id: RbbPlayer(id, self.get_name(id)) for id in player_ids}
        logger.debug(f"Players to track: {self._tracking.keys()}")
        self._placed = {}
        joined_playfab_ids = " | ".join(player_ids)

        async def this_brawl_bounties(ids: list[str]):
            if not debug:
                await asyncio.sleep(7)
            await self.broadcast_bounties(ids, "THIS BRAWL'S BOUNTIES:\n")

        self.backtask(asyncio.create_task(this_brawl_bounties(player_ids)))

        debug_sig = "[DEBUG] " if self._debug_mode else ""

        def get_row(p: RbbPlayer):
            bounty = self._current_cfg.bounties.get(p.playfab_id, None)
            bounty_str = f"{bounty.points}pts x {bounty.claimable}" if bounty else ""
            return [p.playfab_id, p.name or "", bounty_str]

        table = t2a(
            header=["PlayfabId", "Name", "Bounty"],
            body=[get_row(p) for p in self._tracking.values()],
        )
        chunk_size = 2000 - len("```\n\n```")
        chunks = list(
            map(lambda x: "```\n" + x + "```", split_chunks(table, chunk_size))
        )
        await self._channel.send(
            f"{debug_sig}PlayfabIds in this brawl:\n```\n{joined_playfab_ids}\n```"
        )
        for chunk in chunks:
            await self._channel.send(chunk)
        await self.cull_offline_players()

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
                await self.elliminate_player(player.playfab_id, "rbbend")
        except Exception as e:
            logger.error(f"Failed to run rbbend cmd: {e}")
            if client:
                logger.info(f"Expiring client {client.id} since it errored out")
                client.used = 120
        finally:
            if client:
                await self.rcon_pool.release_client(client)

    def send_rvg_help(self):
        self.backtask(
            asyncio.create_task(
                self.say_rcon(
                    "rvg usage: .rvg {x1/x2/x3/x4/x5}\n-x1 is default, rvg bounty of 15, x2 is 30, x3 is 45, and so on\nrvg bounty above 15 is 200% deducted from own score"
                )
            )
        )

    async def handle_chat_event(self, message: ChatEvent):
        normal_msg = message.message.strip().lower()
        logger.info(
            f"CHAT: {message.user_name} ({message.player_id}): {message.message}"
        )
        if message.player_id not in self._player_name_map:
            self._player_name_map[message.player_id] = message.user_name
            logger.debug(
                f"Adding {message.user_name} ({message.player_id}) to player name map"
            )

        if (
            not self._match_running
            and message.message.startswith(".rvg")
            and message.player_id in self.pending_revenge
        ):
            message_comps = message.message.split(" ")
            points = 15
            player = self._current_cfg.get_player(message.player_id)
            if not player:
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon(
                            f"{message.user_name}, to place revenge bounty you must have participated in at least one finished RBB match"
                        )
                    )
                )
                return
            if len(message_comps) > 1:
                split_msg_last = message_comps[-1]

                if not split_msg_last.startswith("x"):
                    self.send_rvg_help()
                    return
                extra_points_unit = split_msg_last[1:]
                if not extra_points_unit.isnumeric():
                    self.send_rvg_help()
                    return
                extra_points_multiplier = int(extra_points_unit)
                if extra_points_multiplier > 5:
                    self.send_rvg_help()
                    return

                points = points * extra_points_multiplier

            target_id = self.pending_revenge.pop(message.player_id)

            logger.info(f"Handling revenge for {message.player_id} against {target_id}")
            caller_name = message.user_name or self.get_name(target_id)
            target_name = self.get_name(target_id)
            set_points, claims = self._current_cfg.add_revenge_bounty(
                message.player_id, target_id, points
            )
            bounty_msg = (
                f"{caller_name} HAS PLACED {set_points}PTS REVENGE BOUNTY ON {target_name}!"
            )
            if points > 15:
                deducted = points * 2
                player.score -= deducted
                bounty_msg += f" DEDUCTED {deducted}PTS FROM OWN SCORE"
                logger.info(
                    f"Deducted {deducted} from {message.player_id}-{caller_name} score to place bounty on {target_id}-{target_name}"
                )
            if set_points > 0 and claims > 0:
                self.backtask(asyncio.create_task(self.say_rcon(bounty_msg)))
            else:
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon(
                            f"{caller_name}, you cannot add a revenge bounty on {target_name} at this time"
                        )
                    )
                )
        if normal_msg == ".myb":
            player_bounties = self._current_cfg.bounties.get(message.player_id, None)
            if player_bounties:
                self.backtask(
                    asyncio.create_task(
                        self.explain_bounty(message.player_id, message.user_name)
                    )
                )
            else:
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon(
                            f"You have no bounties placed on you {message.user_name}... yet..."
                        )
                    )
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
                    or self._player_name_map.get(player.playfab_id, "")
                    or player.playfab_id
                )
                for player in self._tracking.values()
            ]
            joined_players = " | ".join(ingame_players)
            self.backtask(
                asyncio.create_task(
                    self.say_rcon(
                        f"{len(ingame_players)} PLAYERS LEFT IN THE BRAWL: {joined_players}"
                    )
                )
            )

        if self._match_running and message.player_id in self._tracking.keys():
            current_player = self._tracking[message.player_id]
            if current_player.name != message.user_name:
                current_player.name = message.user_name
            if re.match(BANNED_PLAYER_COMMANDS, message.message):
                await self.elliminate_player(message.player_id, "banned command")

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
                        if client:
                            logger.info(
                                f"Expiring client {client.id} since it errored out"
                            )
                            client.used = 120
                    finally:
                        if client:
                            await self.rcon_pool.release_client(client)

                self.backtask(asyncio.create_task(punish_func(current_player.name)))
            if re.match(TP_PATTERN, message.message):
                await self.elliminate_player(message.player_id, "tp command")

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
                        if client:
                            logger.info(
                                f"Expiring client {client.id} since it errored out"
                            )
                            client.used = 120
                    finally:
                        if client:
                            await self.rcon_pool.release_client(client)

                self.backtask(asyncio.create_task(punish_tp_func(message)))

        if normal_msg.startswith(".dbg") and message.player_id in [
            self._admin_id,
            "D1247A0B618D12E",
        ]:
            logger.info(
                f"Debug mode toggled (from {message.user_name}({message.player_id}))"
            )
            ids = list(id.upper() for id in normal_msg.split(" ")[1:])
            ids.append(message.player_id)
            if not ids or len(ids) < 2:
                logger.error(
                    f"Invalid .dbg command from {message.user_name} ({message.player_id}): {normal_msg}"
                )
                return
            await self.start_match(ids, True)

        if message.player_id != self._admin_id:
            return

        if normal_msg.startswith(".bounty"):
            if self._debug_mode:
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon("can't add bounties in debug mode")
                    )
                )
                return
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
            existing_bounty = self._current_cfg.bounties.get(bounty_target, None)
            if existing_bounty:
                existing_bounty.add_static_bounty(int(amount), int(times))
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon(
                            f"{amount} Pts x {times} bounty added for {self.get_name(bounty_target)}. Total: {existing_bounty.points} Pts x {existing_bounty.claimable}"
                        )
                    )
                )
            else:
                self._current_cfg.bounties[bounty_target] = RbbBounty(
                    int(amount), int(times)
                )
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon(
                            f"{amount} Pts x {times} bounty set for {self.get_name(bounty_target)}"
                        )
                    )
                )
            await self._current_cfg.asave()

        if normal_msg.startswith(".rmbounty"):
            if self._debug_mode:
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon("can't remove bounties in debug mode")
                    )
                )
                return
            comps = normal_msg.split(" ")
            if len(comps) < 2:
                logger.error(
                    f"Invalid removebounty command from {message.user_name} ({message.player_id}): {normal_msg}"
                )
                return
            bounty_target = comps[1].upper()
            if bounty_target in self._current_cfg.bounties:
                bounty = self._current_cfg.bounties.get(bounty_target, None)
                if not bounty:
                    return
                bounty.static_claimable = 0
                bounty.static_points = 0
                if bounty.exhausted:
                    self._current_cfg.bounties.pop(bounty_target)
                self.backtask(
                    asyncio.create_task(
                        self.say_rcon(
                            f"Manual bounties removed for {self.get_name(bounty_target)}"
                        )
                    )
                )
                await self._current_cfg.asave()

        if normal_msg == ".rbb lock":
            self.backtask(
                asyncio.create_task(
                    self._channel.send(
                        f"RBB lock command received, retrieving {self._file_path} ..."
                    )
                )
            )
            logger.info(
                f"rbb lock message received (from {message.user_name}({message.player_id})) and validated!"
            )
            player_ids = await self.get_rbb_brawl_content()
            self._current_cfg.tick_bounties()
            self._current_cfg.auto_top_10_bounties()
            if player_ids:
                await self.start_match(player_ids)
            else:
                self.backtask(
                    asyncio.create_task(
                        self._channel.send(
                            "No player ids from server, can't start match"
                        )
                    )
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
            await self.elliminate_player(event.player_id, "logged out during match")

    async def handle_rbb_match_over(self):
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
            header=["#", "Name", "Points", "K", "CB"],
            body=[
                [
                    player.place,
                    player.name,
                    get_points(player) + player.total_bounty_score,
                    player.kills,
                    player.total_bounty_score,
                ]
                for player in placed_players
            ],
        )
        current_time = round(datetime.now(timezone.utc).timestamp())
        time_sig = f"<t:{current_time}>"
        debug_sig = "[DEBUG] " if self._debug_mode else ""

        heading = f"## {debug_sig}Match over{time_sig}"
        chunk_size = 2000 - len("```\n\n```")
        chunks = list(
            map(lambda x: "```\n" + x + "```", split_chunks(table, chunk_size))
        )
        await self._channel.send(heading)

        for chunk in chunks:
            await self._channel.send(chunk)

        bounty_report = dict()
        bounty_report_heading = ["Id", "Name", "KS", "MostKills", "Place"]

        bounty_subheading = f"### {debug_sig}Post-match bounty attribution"

        def update_bounty_report_field(id: str, field: str, value: str):
            if id not in bounty_report:
                bounty_report[id] = {
                    "id": id,
                    "name": self.get_name(id),
                    "ks": "-",
                    "most_kills": "-",
                    "place": "-",
                }
            bounty_report[id][field] = value

        for index, player in enumerate(placed_players):
            player.score = get_points(player)
            found_player = self._current_cfg.get_player(player.playfab_id)
            if found_player is None:
                self._current_cfg.players.append(player)
            else:
                found_player.kills += player.kills
                found_player.score += player.score
                found_player.deaths += player.deaths
                found_player.wins += player.wins
                new_name = self.get_name(found_player.playfab_id)
                if new_name:
                    found_player.name = new_name
                if self._current_cfg.is_elligible_for_ks_bounty(player.kills):
                    added_ks_b = self._current_cfg.add_ks_bounty(
                        found_player, player.kills
                    )
                    added_ks_b_str = (
                        f"{added_ks_b[0]} pts x {added_ks_b[1]}" if added_ks_b else None
                    )
                    if added_ks_b_str:
                        update_bounty_report_field(
                            found_player.playfab_id, "ks", added_ks_b_str
                        )
                if index == 0:
                    added_ms_b = self._current_cfg.add_most_kills_bounty(
                        found_player, index + 1
                    )
                    added_ms_b_str = (
                        f"{added_ms_b[0]} pts x {added_ms_b[1]}" if added_ms_b else None
                    )
                    if added_ms_b_str:
                        update_bounty_report_field(
                            found_player.playfab_id, "most_kills", added_ms_b_str
                        )
                if player.place <= 4:

                    added_place_b = self._current_cfg.add_placement_bounty(
                        found_player, player.place
                    )
                    added_place_b_str = (
                        f"{added_place_b[0]} pts x {added_place_b[1]}"
                        if added_place_b
                        else None
                    )
                    if added_place_b_str:
                        update_bounty_report_field(
                            found_player.playfab_id, "place", added_place_b_str
                        )
                for bounty_id, bounty_points in player.claimed_bounties.items():
                    found_player.claim_bounty(bounty_id, bounty_points)
                    logger.info(
                        f"MATCH OVER SUM: {found_player.name} receive bounty {bounty_id} of {bounty_points} points"
                    )

        try:
            bounty_report_table = t2a(
                header=bounty_report_heading,
                body=[
                    [
                        b["id"],
                        b["name"],
                        b["ks"],
                        b["most_kills"],
                        b["place"],
                    ]
                    for b in bounty_report.values()
                ],
            )
            bounty_report_chunks = list(
                map(
                    lambda x: "```\n" + x + "```",
                    split_chunks(bounty_report_table, chunk_size),
                )
            )
            await self._channel.send(bounty_subheading)
            if len(bounty_report) > 0:
                for chunk in bounty_report_chunks:
                    await self._channel.send(chunk)
        except Exception as e:
            logger.error(f"Failed to create/send bounty report: {e}")

        if self._debug_mode:
            self._debug_mode = False
            self._current_cfg = await RbbLeaderBoardCfg.aload()
            await self.say_rcon("DEBUG BRAWL ENDED")
            return
        await self._current_cfg.asave()
        leaderboard = self._bot.get_cog(RbbLeaderboard.__name__)
        if isinstance(leaderboard, RbbLeaderboard):
            self.backtask(
                asyncio.create_task(
                    leaderboard.publish_leaderboards(self._current_cfg, False)
                )
            )

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

    async def elliminate_player(self, player_id: str, reason: str | None = None):
        if not self._match_running:
            return
        if player_id not in self._tracking.keys():
            return
        current_player = self._tracking.pop(player_id)
        current_player.place = len(self._tracking) + 1
        self._placed[current_player.playfab_id] = current_player
        debug_sig = "[DEBUG] " if self._debug_mode else ""
        reason_msg = f" (Reason: {reason})" if reason else ""
        await self._channel.send(
            f"```{debug_sig}{current_player.name} ({current_player.playfab_id}) has been eliminated{reason_msg}```"
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
            if current_wins >= 1
            else f"{player.name} WON THEIR FIRST BAR BRAWL!!\nIT WON'T BE THE LAST!!"
        )
        self.backtask(asyncio.create_task(self.say_rcon(msg)))
        debug_sig = "[DEBUG] " if self._debug_mode else ""
        await self._channel.send(
            f"```{debug_sig}{player.name} ({player.playfab_id}) is the last one standing!```"
        )

    async def punish_ffaer(self, name: str, id: str, victim: str):
        client: RconClient | None = None
        try:
            client = await self.rcon_pool.get_client()
            await client.execute(
                f"say MODS, BAN THIS GUY. BLOW UP HIS FUCKING HOUSE\n{name} JUST KILLED {victim} FROM OUTSIDE THE RING."
            )
            if not self._debug_mode:
                await client.execute(f"kick {id} Don't interfere with Bar Brawls")
        except Exception as e:
            logger.error(f"Failed to run punish_ffaer: {e}")
            if client:
                logger.info(f"Expiring client {client.id} since it errored out")
                client.used = 120
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
                f"{ev.user_name} BEAT THE PREVIOUS CHAMPION!! {ev.killed_user_name}!!! WILL THEY BECOME THE NEXT?!"
            )

        if victim_id not in current_ids:
            return
        debug_sig = "[DEBUG] " if self._debug_mode else ""
        current_hunter = self._tracking.get(hunter_id, None)
        current_victim = self._tracking.pop(victim_id)

        current_victim.name = (
            ev.killed_user_name if ev.killed_user_name else current_victim.name
        )
        claimed_points = 0
        killer_name = ev.user_name
        if current_hunter:
            current_hunter.name = ev.user_name if ev.user_name else current_hunter.name
            killer_name = current_hunter.name  # code smell, but ok
            current_hunter.kills += 1
            self.pending_revenge[victim_id] = hunter_id
            if current_hunter.kills >= 3 and current_hunter.kills in KS_STREAK_MSGS:
                self.backtask(
                    asyncio.create_task(
                        self.handle_killstreak(
                            current_hunter.name, current_hunter.kills
                        )
                    )
                )
            if current_victim.playfab_id in self._current_cfg.bounties:
                (points, avenged) = self._current_cfg.claim_bounty(
                    current_hunter, current_victim.playfab_id
                )
                claimed_points = points
                msg = (
                    f"{current_hunter.name} HAS GOT HIS REVENGE BY FISTING {current_victim.name}! CLAIMED A BOUNTY OF {claimed_points} POINTS (50% BONUS!)"
                    if avenged
                    else f"{current_hunter.name} CLAIMED A BOUNTY OF {claimed_points} POINTS FOR FISTING {current_victim.name}"
                )
                self.backtask(asyncio.create_task(self.say_rcon(msg)))
        dc_bt_msg = f"(claimed {claimed_points} pts bounty)" if claimed_points else ""
        dc_msg = f"```{debug_sig}{killer_name} ({ev.killer_id}) has eliminated {ev.killed_user_name or current_victim.name} ({ev.killed_id}) {dc_bt_msg}```"
        self.backtask(asyncio.create_task(self._channel.send(dc_msg)))
        if self._debug_mode:
            remaining_ids_str = " | ".join(
                list(id for id in current_ids if id != victim_id)
            )
            self.backtask(
                asyncio.create_task(
                    self._channel.send(
                        f"```{debug_sig}Players remaining:  {remaining_ids_str}```"
                    )
                )
            )
        current_victim.deaths += 1
        if len(self._placed) == 0:
            self.backtask(
                asyncio.create_task(
                    self.say_rcon(f"{ev.user_name} FISTED THE FIRST PLAYER")
                )
            )
        if not current_victim.name:
            current_victim.name = ev.killed_user_name
        current_tracking_length = len(self._tracking)
        current_victim.place = current_tracking_length + 1
        self._placed[current_victim.playfab_id] = current_victim
        await self.check_win()

    @Cog.listener()
    async def on_ready(self):
        self.backtask(asyncio.create_task(self.on_ready_async()))

    # @commands.command(
    #     name="dbg",
    #     description="Start a debug RBB match with specified IDs",
    #     default_permission=Permissions(administrator=True),
    # )
    async def debug_match(self, ctx: ApplicationContext, piped_ids: str):
        await ctx.defer()
        piped_ids.replace(" ", "")
        ids = [id.strip() for id in piped_ids.upper().split("|") if id]
        await self.start_match(ids, True)
        await ctx.respond("Debug match started.")

    # @commands.command(
    #     name="rbbend",
    #     description="End the current RBB match",
    #     default_permission=Permissions(administrator=True),
    # )
    async def rbbend_cmd(self, ctx: ApplicationContext):
        if not self._match_running:
            return
        await ctx.defer()
        fake_event = ChatEvent(
            player_id=self._admin_id,
            user_name="RBB Admin",
            message="rbbend",
            event_type="Chat",
            channel="Team",
        )
        await self.handle_chat_event(fake_event)
        await ctx.respond("Done")

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
        self._current_cfg.tick_bounties()
        self._current_cfg.auto_top_10_bounties()
        self._player_name_map = {
            player.playfab_id: player.name for player in self._current_cfg.players
        }
        self._current_champ_username = (
            self._player_name_map.get(self._current_cfg.last_winner, None)
            if self._current_cfg.last_winner
            else None
        )
        await self.set_admins()
