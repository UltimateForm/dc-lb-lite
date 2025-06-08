from dataclasses import dataclass, field


@dataclass
class MatchInputPlayer:
    user_name: str
    playfab_id: str
    structure_damage: int
    kills: int
    deaths: int
    score: int = field(init=False, default=0)
    debug_kd_score: int = field(init=False, default=0)

    @property
    def kdr(self):
        if self.deaths == 0:
            return self.kills
        else:
            return self.kills / self.deaths


@dataclass
class MatchInput:
    winning_team: int
    match_num: int
    team_1: list[MatchInputPlayer] = field(default_factory=list)
    team_2: list[MatchInputPlayer] = field(default_factory=list)
