import re
import numpy as np


def is_playfab_id_format(arg: str):
    return re.search(r"^([\S]{15,16})+$", arg) is not None


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
def make_ordinal(n):
    n = int(n)
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = ["th", "st", "nd", "rd", "th"][min(n % 10, 4)]
    return str(n) + suffix
