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
        return round(
            statistics.mean([match.structure_damage for match in self.matches]), 2
        )

    def as_dict(self) -> dict[str, Any]:
        self_dict = self.__dict__.copy()
        self_dict["matches"] = list(match.__dict__ for match in self.matches)
        return self_dict


@dataclass
class KovLeaderBoard(IOBoundDataclass):
    players: list[Player] = field(default_factory=list)
    max_items: int = 30
    rank_config: dict[str, str] = field(default_factory=dict)
    rank_short: dict[str, str] | None = field(default_factory=dict)

    @classmethod
    def get_path(cls) -> str:
        return "./persist/leaderboard.json"

    def as_dict(self) -> dict[str, Any]:
        self_dict = super().as_dict()
        self_dict["players"] = list(player.as_dict() for player in self.players)
        return self_dict

    def aliased_ranks(self):
        aliases = self.rank_short or dict()
        return {k: aliases.get(k, v) for k, v in self.rank_config.items()}

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


@dataclass
class RbbPlayer:
    playfab_id: str
    name: str
    kills: int = 0
    place: int = 0
    score: int = 0
    wins: int = 0
    deaths: int = 0
    claimed_bounties: dict[str, int] = field(default_factory=dict)

    @property
    def total_bounty_score(self) -> int:
        if not self.claimed_bounties or len(self.claimed_bounties) == 0:
            return 0
        return sum(self.claimed_bounties.values())

    @property
    def total_score(self) -> int:
        return self.score + self.total_bounty_score

    def claim_bounty(self, bounty_id: str, points: int):
        if self.claimed_bounties is None:
            self.claimed_bounties = dict()
        if bounty_id in self.claimed_bounties:
            self.claimed_bounties[bounty_id] += points
        else:
            self.claimed_bounties[bounty_id] = points

@dataclass
class RbbBounty:
    points: int
    claimable: int


@dataclass
class RbbLeaderBoardCfg(IOBoundDataclass):
    channels: list[int] = field(default_factory=list)
    players: list[RbbPlayer] = field(default_factory=list)
    max_items: int = 30
    bounties: dict[str, RbbBounty] = field(default_factory=dict)
    _last_winner: str | None = None

    @classmethod
    def get_path(cls) -> str:
        return "./persist/rbb.leaderboard.json"

    def get_player(self, playfab_or_user_name: str) -> RbbPlayer | None:
        player: RbbPlayer | None = None
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

    def as_dict(self):
        self_dict = self.__dict__.copy()
        self_dict["players"] = list(player.__dict__.copy() for player in self.players)
        self_dict["bounties"] = {
            k: v.__dict__.copy() for k, v in self.bounties.items()
        }
        return self_dict
