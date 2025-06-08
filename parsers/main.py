import re
import numpy as np
from models.match_parse import MatchInput
from parsers.grok import parse_match_input, parse_match_row
from compute.score import set_points


def is_playfab_id_format(arg: str):
    return re.search(r"^([\S]{14,16})+$", arg) is not None


def compute_gate(value: int, gates: list[int]) -> int | None:
    # todo: ditch numpy alltogether
    # we could sort it by highest and then do next([x for x in keys if x <= minutes_played])
    np_gates = np.array(gates)
    lesser_gates = np_gates[np_gates <= value]
    if len(lesser_gates) == 0:
        return None
    current_gate = lesser_gates.max()
    return current_gate


def compute_next_gate(value: int, gates: list[int]) -> int | None:
    # todo: ditch numpy alltogether
    # we could sort it by highest and then do next([x for x in keys if x <= minutes_played])
    np_gates = np.array(gates)
    lesser_gates = np_gates[np_gates > value]
    if len(lesser_gates) == 0:
        return None
    next_gate = lesser_gates.min()
    return next_gate


def compute_gate_text(
    value: int, gates: dict[str, str]
) -> tuple[int | None, str | None]:
    gates_keys = list(gates.keys())
    gates_thresholds = list([int(key) for key in gates_keys if key.isnumeric()])
    current_gate = compute_gate(value, gates_thresholds)
    gate_txt = gates.get(str(current_gate), None)
    return (current_gate, gate_txt)


def compute_next_gate_text(
    value: int, gates: dict[str, str]
) -> tuple[int | None, str | None]:
    gates_keys = list(gates.keys())
    gates_thresholds = list([int(key) for key in gates_keys if key.isnumeric()])
    next_gate = compute_next_gate(value, gates_thresholds)
    gate_txt = gates.get(str(next_gate), None)
    return (next_gate, gate_txt)


# source https://stackoverflow.com/questions/9647202/ordinal-numbers-replacement
def make_ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = ["th", "st", "nd", "rd", "th"][min(n % 10, 4)]
    return str(n) + suffix


# source https://stackoverflow.com/questions/1094841/get-a-human-readable-version-of-a-file-size
def sizeof_fmt(num, suffix="B"):
    for unit in ("", "K", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


def split_chunks(sample: str, chunk_size: int) -> list[str]:
    lines = sample.splitlines()
    batches: list[str] = []
    while lines:
        curr = lines.pop(0) + "\n"
        if not batches:
            batches.append(curr)
        else:
            new_len = len(batches[-1] + curr)
            if new_len < chunk_size:
                batches[-1] += curr
            else:
                batches.append(curr)
    return batches


def parse_match(match_txt: str) -> MatchInput | None:
    head_and_rest = match_txt.split("\n", 1)
    head = head_and_rest[0]
    match_data = parse_match_input(head)
    if match_data is None:
        return None
    match_board = head_and_rest[1]
    matched_teams = re.match(
        r"^(TEAM [12](?:(?:\n|\r).*)*)(TEAM [12](?:(?:\n|\r).*)*)$",
        match_board,
        re.MULTILINE | re.IGNORECASE,
    )
    if matched_teams is None:
        return None
    team_blocks = matched_teams.groups()[:2]
    for block in team_blocks:
        split_with_head = block.split("\n", 1)
        [head, rows] = split_with_head
        head = head.strip()
        players = [parse_match_row(line) for line in split_with_head[1].splitlines()]
        defined_players = list([player for player in players if player is not None])
        if head == "TEAM 1":
            match_data.team_1 = defined_players
            set_points(defined_players, match_data.winning_team == 1)
        else:
            match_data.team_2 = defined_players
            set_points(defined_players, match_data.winning_team == 2)

    return match_data


def parse_matches(matche_str: str) -> list[MatchInput]:
    matches = re.split("^(?:\r?\n)", matche_str, flags=re.MULTILINE)
    matches_parsed = [parse_match(match) for match in matches]
    return list(match for match in matches_parsed if match is not None)
