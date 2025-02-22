from dataclasses import dataclass, field
import statistics
from typing import Any

from models.IOBoundDataclass import IOBoundDataclass
from parsers.main import is_playfab_id_format


@dataclass
class GameMatch:
    kills: int = 0
    deaths: int = 0
    structure_damage: int = 0
    score: int = 0


@dataclass
class Player:
    name: str
    playfab_id: str
    matches: list[GameMatch] = field(default_factory=list)

    @property
    def total_kills(self) -> int:
        return sum([match.kills for match in self.matches])

    @property
    def total_deaths(self) -> int:
        return sum([match.deaths for match in self.matches])

    @property
    def total_score(self) -> int:
        return sum([match.score for match in self.matches])

    @property
    def avg_structure_damage(self) -> float:
        return statistics.mean([match.structure_damage for match in self.matches])

    def as_dict(self) -> dict[str, Any]:
        self_dict = self.__dict__.copy()
        self_dict["matches"] = list(match.__dict__ for match in self.matches)
        return self_dict


@dataclass
class LeaderBoard(IOBoundDataclass):
    players: list[Player] = field(default_factory=list)
    max_items: int = 30
    rank_config: dict[str, str] = field(default_factory=dict)

    @classmethod
    def get_path(cls) -> str:
        return "./persist/leaderboard.json"

    def as_dict(self) -> dict[str, Any]:
        self_dict = super().as_dict()
        self_dict["players"] = list(player.as_dict() for player in self.players)
        return self_dict

    def get_player(self, playfab_or_user_name: str) -> Player | None:
        player: Player | None = None
        if is_playfab_id_format(playfab_or_user_name):
            player = next(
                (
                    p
                    for p in self.players
                    if p.playfab_id == playfab_or_user_name.strip()
                ),
                None,
            )
        if player is None:
            arg_trimmed_normal = playfab_or_user_name.strip().lower()
            player = next(
                (
                    p
                    for p in self.players
                    if p.name.strip().lower() == arg_trimmed_normal
                ),
                None,
            )
        return player
