from datetime import datetime
from pygrok import Grok
import re
from models.match_parse import MatchInput, MatchInputPlayer
from models.rcon import (
    ChatEvent,
    KillfeedEvent,
    LoginEvent,
    Player,
    ServerInfo,
)

GROK_KILLFEED_EVENT = r"%{WORD:event_type}: %{NOTSPACE:date}: (?:%{NOTSPACE:killer_id})? \(%{GREEDYDATA:user_name}\) killed (?:%{NOTSPACE:killed_id})? \(%{GREEDYDATA:killed_user_name}\)"
GROK_LOGIN_EVENT = r"%{WORD:event_type}: %{NOTSPACE:date}: %{GREEDYDATA:user_name} \(%{WORD:player_id}\) logged %{WORD:instance}"
DATE_FORMAT = r"%Y.%m.%d-%H.%M.%S"
GROK_CHAT_EVENT = r"%{WORD:event_type}: %{NOTSPACE:player_id}, %{GREEDYDATA:user_name}, \(%{WORD:channel}\) %{GREEDYDATA:message}"
GROK_SERVER_INFO = r"HostName: %{GREEDYDATA:host}\nServerName: %{GREEDYDATA:server_name}\nVersion: %{GREEDYDATA:version}\nGameMode: %{GREEDYDATA:game_mode}\nMap: %{GREEDYDATA:map}"
GROK_PLAYERLIST_ROW = (
    r"%{NOTSPACE:player_id}, %{GREEDYDATA:user_name}, %{GREEDYDATA}, %{GREEDYDATA}"
)
GROK_MATCHSTATE = r"MatchState: %{GREEDYDATA:state}"
GROK_KOV_ADD = r".kov %{NOTSPACE:team} add %{NOTSPACE:id}"
GROK_MATCH_HEAD = r"MATCH %{NUMBER:match_num} - TEAM %{NUMBER:winning_team} WINS"
GROK_MATCH_ROW = r"%{GREEDYDATA:user_name}\s+\(%{NOTSPACE:playfab_id}\)\s+-\s+%{NUMBER:structure_damage}%\s+DMG\s+-\s+K\s+%{NUMBER:kills}\s+\|\s+D\s+%{NUMBER:deaths}"


def parse_event(event: str, grok_pattern: str) -> tuple[bool, dict[str, str] | None]:
    pattern = Grok(grok_pattern)
    match = pattern.match(event)
    if not match:
        return (False, match)
    else:
        return (True, match)


def parse_killfeed_event(event: str) -> KillfeedEvent | None:
    (success, parsed) = parse_event(event, GROK_KILLFEED_EVENT)
    if not success or not parsed:
        return None
    return KillfeedEvent(**parsed)


def parse_login_event(event: str) -> LoginEvent | None:
    (success, parsed) = parse_event(event, GROK_LOGIN_EVENT)
    if not success or not parsed:
        return None
    return LoginEvent(**parsed)


def parse_chat_event(event: str) -> ChatEvent | None:
    without_new_lines = r" \ ".join(event.splitlines())
    (success, parsed) = parse_event(without_new_lines, GROK_CHAT_EVENT)
    if not success or not parsed:
        return None
    return ChatEvent(**parsed)


def parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, DATE_FORMAT)


def parse_server_info(raw: str) -> ServerInfo | None:
    (success, parsed) = parse_event(raw, GROK_SERVER_INFO)
    if not success or not parsed:
        return None
    return ServerInfo(**parsed)


def parse_kov_add(raw: str) -> Player | None:
    (success, parsed) = parse_event(raw, GROK_KOV_ADD)
    if not success or not parsed:
        return None
    return Player(player_id=parsed.get("id", ""), user_name="", kills=0, deaths=0)


def parse_matchstate(raw: str) -> str | None:
    (success, parsed) = parse_event(raw, GROK_MATCHSTATE)
    if not success or not parsed:
        return None
    return parsed.get("state", None)


def is_playfab_id_format(arg: str):
    return re.search(r"^([\S]{14,16})+$", arg) is not None


def parse_match_input(raw: str) -> MatchInput | None:
    (success, parsed) = parse_event(raw, GROK_MATCH_HEAD)
    if not success or not parsed:
        return None
    match_num = parsed.get("match_num", "0")
    winning_team = parsed.get("winning_team", "0")
    return MatchInput(int(winning_team), int(match_num))


def parse_match_row(raw: str) -> MatchInputPlayer | None:
    (success, parsed) = parse_event(raw, GROK_MATCH_ROW)
    if not success or not parsed:
        return None

    structure_damage = int(parsed.get("structure_damage", "0"))
    kills = int(parsed.get("kills", "0"))
    deaths = int(parsed.get("deaths", "0"))

    match_input_player = MatchInputPlayer(
        parsed["user_name"], parsed["playfab_id"], structure_damage, kills, deaths
    )
    return match_input_player
