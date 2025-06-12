from models.match_parse import MatchInputPlayer
from statistics import stdev


STRUCT_SCORE_WEIGHT = 650
WIN_KD_POOL = 750
LOSS_KD_POOL = 650


def set_points(team: list[MatchInputPlayer], win: bool):
    victory_distro = 600 if win else -300
    kdrs = [player.kdr for player in team]
    total_kd_ratio = sum(kdrs)
    if total_kd_ratio == 0:
        total_kd_ratio = 0.1

    kd_distro = [kdr / total_kd_ratio for kdr in kdrs]
    deviation = stdev(kd_distro)
    total_win_kd_pool = WIN_KD_POOL * (1 - deviation)
    player_length = len(team)
    for index, player in enumerate(team):
        player.score += round(victory_distro / player_length)
        player_struct_damge_percent = player.structure_damage / 100
        struct_dmg_score = round(STRUCT_SCORE_WEIGHT * player_struct_damge_percent)
        kdr_percent = kd_distro[index]
        if win:
            player.score += struct_dmg_score
            kd_score_win = round(total_win_kd_pool * kdr_percent)
            player.debug_kd_score = kd_score_win
            player.score += kd_score_win
        else:
            struct_score_loss = -round(
                STRUCT_SCORE_WEIGHT
                * ((1 - player_struct_damge_percent) / (player_length - 1))
            )
            player.score += struct_score_loss
            kdr_loss_percent = (1 - kdr_percent) / (player_length - 1)
            kd_score_loss = -round(LOSS_KD_POOL * kdr_loss_percent)
            player.debug_kd_score = kd_score_loss
            player.score += kd_score_loss
