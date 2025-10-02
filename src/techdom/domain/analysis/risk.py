"""Risk scoring helpers for TG2/TG3 funn."""
from __future__ import annotations

import math
from typing import Iterable

from techdom.infrastructure.configs import risk as risk_config


def calc_risk_score(
    tg2_items: Iterable[str],
    tg3_items: Iterable[str],
    has_tg_data: bool = False,
) -> int:
    """Returner risiko-score 0-100 basert på TG-observasjoner."""

    tg2_list = list(tg2_items)
    tg3_list = list(tg3_items)

    if not has_tg_data and not tg2_list and not tg3_list:
        return int(risk_config.DEFAULT_RISK_SCORE_NO_DATA)

    tg3_count = len(tg3_list)
    tg2_count = min(len(tg2_list), int(risk_config.CAP_TG2_ITEMS))

    penalty = (tg3_count * int(risk_config.PENALTY_TG3_PER_ITEM)) + (
        tg2_count * int(risk_config.PENALTY_TG2_PER_ITEM)
    )
    penalty = min(penalty, int(risk_config.MAX_RISK_PENALTY))

    risk_score = max(0, 100 - penalty)
    return int(risk_score)


def calc_total_score(
    econ_score_0_100: float, risk_score_0_100: float, has_tg_data: bool
) -> int:
    """Slå sammen økonomiscore og risiko til en totalscore."""

    econ = float(econ_score_0_100)
    risk = float(risk_score_0_100)

    weighted = ((1.0 - risk_config.WEIGHT_TOTAL_RISK) * econ) + (
        risk_config.WEIGHT_TOTAL_RISK * risk
    )
    total = math.ceil(weighted)

    if not has_tg_data:
        total = min(total, int(risk_config.MAX_TOTAL_IF_NO_TG))

    return int(max(0, min(100, total)))


__all__ = ["calc_risk_score", "calc_total_score"]
