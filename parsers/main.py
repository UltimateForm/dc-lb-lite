import re
import numpy as np


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
