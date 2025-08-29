from dataclasses import dataclass, field
from math import e
import time
from typing import Any
from venv import logger

from models.IOBoundDataclass import IOBoundDataclass
from parsers.main import is_playfab_id_format


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
class DynamicBounty:
    points: int
    claimable: int
    expire_at: int | None
    source: str

    @property
    def exhausted(self):
        return (
            self.claimable <= 0
            or self.points <= 0
            or (self.expire_at and self.expire_at < time.time())
        )

    @property
    def timed(self):
        return bool(self.expire_at)


@dataclass
class RbbBounty:
    static_points: int
    static_claimable: int
    dynamic_bounties: list[DynamicBounty] = field(default_factory=list)

    def add_static_bounty(self, points: int, claimable: int):
        self.static_points += points
        self.static_claimable += claimable

    def add_timed_bounty(
        self, points: int, claimable: int, expire_at: int, source: str
    ):
        self.dynamic_bounties.append(
            DynamicBounty(points, claimable, expire_at, source)
        )

    def add_dynamic_bounty(self, points: int, claimable: int, source: str):
        self.dynamic_bounties.append(DynamicBounty(points, claimable, None, source))

    def tick_bounties(self):
        self.dynamic_bounties = [b for b in self.dynamic_bounties if not b.exhausted]

    def as_dict(self) -> dict[str, Any]:
        self_dict = self.__dict__.copy()
        self_dict["timed_bounties"] = list(b.__dict__ for b in self.dynamic_bounties)
        return self_dict

    @property
    def points(self):
        return (
            self.static_points + sum(b.points for b in self.dynamic_bounties)
            if self.dynamic_bounties
            else self.static_points
        )

    @property
    def exhausted(self):
        return self.claimable <= 0 or self.points <= 0

    @property
    def claimable(self):
        return (
            self.static_claimable + sum(b.claimable for b in self.dynamic_bounties)
            if self.dynamic_bounties
            else self.static_claimable
        )

    def claim(self) -> int:
        points = self.points
        if self.dynamic_bounties and len(self.dynamic_bounties) > 0:
            last_bounty = self.dynamic_bounties[-1]
            last_bounty.claimable -= 1
            if last_bounty.claimable == 0:
                self.dynamic_bounties.pop()
        else:
            self.static_claimable -= 1
        return points


FULL_DAY = 60 * 60 * 24


KS_BOUNTIES = {
    5: 15,
    6: 20,
    7: 25,
    8: 30,
    9: 35,
    10: 50,
}

MAX_KS_BOUNTY_KILLS = max(KS_BOUNTIES.keys())


@dataclass
class RbbLeaderBoardCfg(IOBoundDataclass):
    channels: list[int] = field(default_factory=list)
    players: list[RbbPlayer] = field(default_factory=list)
    max_items: int = 30
    bounties: dict[str, RbbBounty] = field(default_factory=dict)
    last_winner: str | None = None
    win_streak: int = 0
    time_top_tens_bounties: int = 0

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

    def is_elligible_for_ks_bounty(self, kill_streak: int):
        return kill_streak not in KS_BOUNTIES and kill_streak < MAX_KS_BOUNTY_KILLS

    def add_ks_bounty(self, player: RbbPlayer, kill_streak: int):
        if not self.is_elligible_for_ks_bounty(kill_streak):
            return
        bounty_source = "killstreak"
        points = KS_BOUNTIES.get(
            kill_streak, MAX_KS_BOUNTY_KILLS + (kill_streak - MAX_KS_BOUNTY_KILLS) * 10
        )
        logger.info(f"Adding KS bounty for {player.name}: {points} points, 1 claim.")
        bounty = self.bounties.get(player.playfab_id, None)
        if not bounty:
            logger.info(
                f"{player.name} has no bounty, adding new one, with killstreak bounty of {points} points, 1 claim."
            )
            new_bounty = RbbBounty(0, 0)
            new_bounty.add_dynamic_bounty(points, 1, bounty_source)
            self.bounties[player.playfab_id] = new_bounty
            return
        existing_ks_bounty = next(
            (b for b in bounty.dynamic_bounties if b.source == bounty_source), None
        )
        if existing_ks_bounty:
            if existing_ks_bounty.points < points:
                logger.info(
                    f"Updating existing KS bounty for {player.name} to {points} points."
                )
                existing_ks_bounty.points = points
                existing_ks_bounty.claimable = 1
            elif existing_ks_bounty.points == points:
                next_bounty = next(
                    (b for b in KS_BOUNTIES.values() if b > existing_ks_bounty.points),
                    MAX_KS_BOUNTY_KILLS + (kill_streak - MAX_KS_BOUNTY_KILLS) * 10,
                )
                logger.info(
                    f"{player.name} already has a KS bounty of {existing_ks_bounty.points} points, bumping to {next_bounty} points."
                )
                existing_ks_bounty.points = next_bounty
                existing_ks_bounty.claimable = 1
            else:
                logger.info(
                    f"{player.name} already has a KS bounty of {existing_ks_bounty.points} points, which is higher than {points}, not bumping."
                )
        else:
            logger.info(
                f"Adding new KS bounty for {player.name}: {points} points, 1 claim."
            )
            bounty.add_dynamic_bounty(points, 1, bounty_source)

    def as_dict(self):
        self_dict = self.__dict__.copy()
        self_dict["players"] = list(player.__dict__.copy() for player in self.players)
        self_dict["bounties"] = {k: v.as_dict() for k, v in self.bounties.items()}
        return self_dict

    def tick_bounties(self):
        for bounty in self.bounties.values():
            bounty.tick_bounties()
        self.bounties = {
            k: v for k, v in self.bounties.items() if not v.exhausted
        }

    def claim_bounty(self, player: RbbPlayer, bounty_id: str) -> int:
        bounty = self.bounties.get(bounty_id, None)
        if not bounty or bounty.exhausted:
            return 0
        points = bounty.claim()
        player.claim_bounty(bounty_id, points)
        if bounty.exhausted:
            self.bounties.pop(bounty_id)
        return points

    def auto_top_10_bounties(self):
        current_time = time.time()
        logger.info("Calculating top 10 bounties...")
        if current_time - self.time_top_tens_bounties <= FULL_DAY:
            logger.info("Top 10 bounties already calculated today.")
            return
        self.time_top_tens_bounties = round(current_time)
        # Define bounty rules for top 10
        bounty_rules = [
            (15, 5),  # 10th
            (15, 5),  # 9th
            (15, 5),  # 8th
            (20, 4),  # 7th
            (20, 4),  # 6th
            (20, 4),  # 5th
            (25, 4),  # 4th
            (30, 3),  # 3rd
            (35, 3),  # 2nd
            (45, 3),  # 1st
        ]
        # Sort players by total_score descending
        sorted_players = sorted(self.players, key=lambda p: p.total_score, reverse=True)
        player_names = " | ".join([p.name for p in sorted_players[:9]])
        logger.info(f"Setting auto bounties for: {player_names}")
        # Assign bounties to top 10
        for idx, (points, claimable) in enumerate(bounty_rules):
            place = 10 - idx
            if len(sorted_players) < place:
                continue
            player = sorted_players[place - 1]
            bounty_source = "top10"
            expire_at = int(current_time + FULL_DAY)
            # Get or create bounty for player
            bounty = self.bounties.get(player.playfab_id)
            if not bounty:
                bounty = RbbBounty(0, 0)
                self.bounties[player.playfab_id] = bounty
            if any(b for b in bounty.dynamic_bounties if b.source == bounty_source):
                logger.info(f"Top10 bounty already set for {player.name}")
                continue
            logger.info(
                f"Adding top10 bounty for {player.name}: {points} points, {claimable} claims."
            )
            bounty.add_timed_bounty(points, claimable, expire_at, bounty_source)
