"""Generators for the six marathon methodologies supported by the TriPlanner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterable, List

import pandas as pd


DAY_NAMES = [
    "Segunda",
    "Terça",
    "Quarta",
    "Quinta",
    "Sexta",
    "Sábado",
    "Domingo",
]


@dataclass
class MarathonPlanConfig:
    race_date: date
    current_long_run_km: float
    weekly_days: int
    base_weekly_km: float
    target_marathon_pace: float
    runner_level: str


@dataclass
class _Session:
    session_type: str
    intensity_label: str
    distance_km: float
    description: str


def _pace_table(cfg: MarathonPlanConfig) -> dict[str, float]:
    mp = cfg.target_marathon_pace
    return {
        "MP": mp,
        "Easy": mp + 0.6,
        "Long": mp + 0.4,
        "Tempo": max(0, mp - 0.15),
        "Interval": max(0, mp - 0.55),
        "Repetition": max(0, mp - 0.75),
    }


def _start_date_for_cycle(race_date: date, weeks: int) -> date:
    return race_date - timedelta(days=weeks * 7 - 1)


def _apply_weekly_limit(sessions: List[_Session], weekly_days: int) -> List[_Session]:
    run_indices = [i for i, s in enumerate(sessions) if s.distance_km > 0]
    while len(run_indices) > weekly_days:
        removable = [
            i
            for i in run_indices
            if sessions[i].session_type
            not in {"Long Run", "Tempo", "Interval", "Marathon Pace", "Strength"}
        ]
        target_idx = removable[0] if removable else run_indices[-1]
        sessions[target_idx] = _Session(
            session_type="Rest",
            intensity_label="Rest",
            distance_km=0.0,
            description="Descanso ou cross-training leve.",
        )
        run_indices = [i for i, s in enumerate(sessions) if s.distance_km > 0]
    return sessions


def _build_week_dataframe(
    method: str,
    week_idx: int,
    start_date: date,
    sessions: List[_Session],
) -> pd.DataFrame:
    data_rows = []
    week_start = start_date + timedelta(days=7 * week_idx)
    for day_offset, sess in enumerate(sessions):
        current_date = week_start + timedelta(days=day_offset)
        data_rows.append(
            {
                "week": week_idx + 1,
                "date": current_date,
                "day_name": DAY_NAMES[day_offset],
                "session_type": sess.session_type,
                "distance_km": round(sess.distance_km, 1),
                "intensity_label": sess.intensity_label,
                "description": sess.description,
                "method": method,
            }
        )
    return pd.DataFrame(data_rows)


def _week_volume_progression(
    total_weeks: int, start_km: float, peak_multiplier: float, taper_weeks: int
) -> list[float]:
    peak = start_km * peak_multiplier
    ramp_weeks = max(1, total_weeks - taper_weeks)
    ramp_increment = (peak - start_km) / max(1, ramp_weeks - 1)
    volumes: list[float] = []
    for idx in range(total_weeks):
        if idx < ramp_weeks:
            target = start_km + ramp_increment * idx
        else:
            remaining = total_weeks - idx
            taper_span = max(1, taper_weeks)
            factor = 0.6 + 0.2 * (remaining / taper_span)
            target = peak * min(1.0, factor)
        if (idx + 1) % 4 == 0:
            target *= 0.75
        volumes.append(round(max(10.0, target), 1))
    return volumes


def _allocate_easy_runs(week_volume: float, fixed_sessions: Iterable[_Session]) -> list[float]:
    allocated = sum(sess.distance_km for sess in fixed_sessions)
    remaining = max(0.0, week_volume - allocated)
    easy_slots = sum(
        1
        for sess in fixed_sessions
        if sess.distance_km == 0 and sess.session_type not in {"Rest"}
    )
    if easy_slots == 0:
        return []
    per_day = remaining / easy_slots
    return [per_day] * easy_slots


def gerar_plano_hansons(cfg: MarathonPlanConfig) -> pd.DataFrame:
    weeks = 18
    paces = _pace_table(cfg)
    start_km = max(cfg.base_weekly_km, cfg.current_long_run_km * 2.2, 40)
    volumes = _week_volume_progression(weeks, start_km, 1.25, taper_weeks=2)

    long_run_table = {
        1: (13, 16),
        2: (16, 16),
        3: (18, 18),
        4: (14, 16),
        5: (19, 21),
        6: (21, 22),
        7: (22, 22),
        8: (22, 23),
        9: (24, 24),
        10: (19, 21),
        11: (24, 25),
        12: (25, 26),
        13: (24, 26),
        14: (24, 24),
        15: (26, 26),
        16: (19, 21),
        17: (16, 16),
        18: (10, 13),
    }

    start_date = _start_date_for_cycle(cfg.race_date, weeks)
    frames: list[pd.DataFrame] = []
    for idx in range(weeks):
        week_no = idx + 1
        phase = (
            "Speed"
            if week_no <= 6
            else "Strength"
            if week_no <= 12
            else "Tempo"
            if week_no <= 16
            else "Taper"
        )

        long_km = sum(long_run_table.get(week_no, (0, 0))) / 2
        long_km = min(26.0, long_km)

        quality_descr = ""
        quality_pace = ""
        quality_distance = 10.0
        if phase == "Speed":
            quality_descr = "Intervalos curtos (400–1000 m) em ritmo de 5K." \
                " Trabalhe economia e cadência."
            quality_pace = f"~{paces['Interval']:.2f} min/km"
        elif phase == "Strength":
            quality_descr = "Séries longas em MP -10s/km com recuperações curtas." \
                " Ex.: 3x3 km."
            quality_pace = f"MP -10s (≈{paces['Tempo']:.2f} min/km)"
            quality_distance = 12.0
        elif phase == "Tempo":
            quality_descr = "Bloco contínuo em ritmo de maratona (8–16 km)."
            quality_pace = f"MP (≈{paces['MP']:.2f} min/km)"
            quality_distance = 14.0
        else:
            quality_descr = "Toque leve em MP, reduzindo volume para a prova."
            quality_pace = f"MP (≈{paces['MP']:.2f} min/km)"
            quality_distance = 8.0

        week_volume = volumes[idx]
        easy_template = [
            _Session(
                "Easy",
                f"Z2 (~{paces['Easy']:.2f} min/km)",
                0.0,
                "Rodagem leve para acumular volume.",
            ),
            _Session("Quality", quality_pace, quality_distance, quality_descr),
            _Session(
                "Easy",
                f"Z2 (~{paces['Easy']:.2f} min/km)",
                0.0,
                "Rodagem curta para recuperar.",
            ),
            _Session(
                "Tempo",
                f"MP (≈{paces['MP']:.2f} min/km)",
                quality_distance * 0.8,
                "Ritmo de maratona controlado.",
            ),
            _Session(
                "Easy",
                f"Z2 (~{paces['Easy']:.2f} min/km)",
                0.0,
                "Easy run.",
            ),
            _Session(
                "Easy",
                f"Z2 (~{paces['Easy']:.2f} min/km)",
                0.0,
                "Rodagem média confortável.",
            ),
            _Session(
                "Long Run",
                f"Z2/MP (≈{paces['Long']:.2f} min/km)",
                long_km,
                "Longão com últimos km em MP se bem.",
            ),
        ]

        easy_dists = _allocate_easy_runs(week_volume, easy_template)
        easy_counter = 0
        sessions: List[_Session] = []
        for sess in easy_template:
            if sess.distance_km == 0:
                dist = easy_dists[easy_counter] if easy_counter < len(easy_dists) else 0.0
                easy_counter += 1
                sessions.append(
                    _Session(sess.session_type, sess.intensity_label, dist, sess.description)
                )
            else:
                sessions.append(sess)

        sessions = _apply_weekly_limit(sessions, cfg.weekly_days)
        frames.append(_build_week_dataframe("Hansons", idx, start_date, sessions))

    return pd.concat(frames, ignore_index=True)


def gerar_plano_daniels(cfg: MarathonPlanConfig) -> pd.DataFrame:
    weeks = 18
    paces = _pace_table(cfg)
    start_km = max(cfg.base_weekly_km, cfg.current_long_run_km * 2.0, 45)
    volumes = _week_volume_progression(weeks, start_km, 1.3, taper_weeks=2)
    start_date = _start_date_for_cycle(cfg.race_date, weeks)

    frames: list[pd.DataFrame] = []
    for idx in range(weeks):
        week_no = idx + 1
        phase = (
            "Base" if week_no <= 5 else "Fase II" if week_no <= 10 else "Fase III" if week_no <= 15 else "Taper"
        )

        long_peak = 30 if cfg.runner_level != "iniciante" else 28
        long_km = min(long_peak, 18 + 0.8 * idx)
        if week_no % 4 == 0:
            long_km *= 0.75
        if phase == "Taper":
            long_km = max(16.0, long_km * 0.6)

        quality_sessions: list[_Session] = []
        if phase == "Base":
            quality_sessions.append(
                _Session("Strides", "E + strides", 8.0, "Rodagem E com 6-8 strides de 20s.")
            )
        elif phase == "Fase II":
            quality_sessions.append(
                _Session(
                    "Tempo",
                    f"T (≈{paces['Tempo']:.2f} min/km)",
                    10.0,
                    "20-30 min em limiar (T).",
                ),
            )
            if cfg.weekly_days >= 5:
                quality_sessions.append(
                    _Session(
                        "Interval",
                        f"I (≈{paces['Interval']:.2f} min/km)",
                        8.0,
                        "Intervalos VO2 (I) curtos, 5K pace.",
                    ),
                )
        elif phase == "Fase III":
            quality_sessions.append(
                _Session(
                    "Marathon Pace",
                    f"M (≈{paces['MP']:.2f} min/km)",
                    14.0,
                    "Bloco em ritmo de maratona (2x8 km M).",
                ),
            )
            quality_sessions.append(
                _Session(
                    "Tempo",
                    f"T (≈{paces['Tempo']:.2f} min/km)",
                    10.0,
                    "Manutenção do limiar (20 min T).",
                ),
            )
        else:
            quality_sessions.append(
                _Session(
                    "Marathon Pace",
                    f"M (≈{paces['MP']:.2f} min/km)",
                    8.0,
                    "Ritmo de prova curto, manter leve.",
                ),
            )

        base_template: List[_Session] = [
            _Session("Easy", f"E (~{paces['Easy']:.2f} min/km)", 0.0, "Rodagem E para acumular volume."),
            quality_sessions[0],
            _Session("Easy", f"E (~{paces['Easy']:.2f} min/km)", 0.0, "Rodagem leve."),
        ]

        if len(quality_sessions) > 1:
            base_template.append(quality_sessions[1])
        base_template.append(_Session("Easy", f"E (~{paces['Easy']:.2f} min/km)", 0.0, "Rodagem leve ou descanso."))
        base_template.append(_Session("Easy", f"E (~{paces['Easy']:.2f} min/km)", 0.0, "Rodagem leve."))
        base_template.append(
            _Session(
                "Long Run",
                f"E/M (≈{paces['Long']:.2f} min/km)",
                long_km,
                "Longão progressivo, último terço em M.",
            )
        )

        week_volume = volumes[idx]
        easy_dists = _allocate_easy_runs(week_volume, base_template)
        easy_counter = 0
        sessions: List[_Session] = []
        for sess in base_template:
            if sess.distance_km == 0:
                dist = easy_dists[easy_counter] if easy_counter < len(easy_dists) else 0.0
                easy_counter += 1
                sessions.append(_Session(sess.session_type, sess.intensity_label, dist, sess.description))
            else:
                sessions.append(sess)

        sessions = _apply_weekly_limit(sessions, cfg.weekly_days)
        frames.append(_build_week_dataframe("Daniels", idx, start_date, sessions))

    return pd.concat(frames, ignore_index=True)


def gerar_plano_pfitzinger(cfg: MarathonPlanConfig) -> pd.DataFrame:
    weeks = 18
    start_km = max(cfg.base_weekly_km, cfg.current_long_run_km * 2.4, 55 if cfg.runner_level != "iniciante" else 45)
    volumes = _week_volume_progression(weeks, start_km, 1.25, taper_weeks=2)
    start_date = _start_date_for_cycle(cfg.race_date, weeks)
    frames: list[pd.DataFrame] = []

    for idx in range(weeks):
        week_no = idx + 1
        long_km = min(34.0, 18 + idx * 0.9)
        if week_no % 4 == 0:
            long_km *= 0.78
        if week_no in {14, 15}:
            long_km = min(35.0, long_km + 2)
        if week_no >= 17:
            long_km *= 0.65

        medium_long = min(22.0, 12 + idx * 0.5)
        if week_no % 4 == 0:
            medium_long *= 0.75
        if week_no >= 17:
            medium_long *= 0.6

        t_distance = 10.0 if week_no < 12 else 14.0
        quality = _Session("Tempo", "T", t_distance, "Bloco contínuo em limiar (20-40 min).")
        mp_addition = _Session("Marathon Pace", "M", 12.0 if week_no > 8 else 8.0, "Parte do treino em ritmo de maratona.")

        template: List[_Session] = [
            _Session("Easy", "E", 0.0, "Rodagem leve."),
            quality,
            _Session("Easy", "E", 0.0, "Recuperação pós-qualidade."),
            _Session("Medium Long", "E", medium_long, "Medium long de base aeróbica."),
            mp_addition,
            _Session("Easy", "E", 0.0, "Rodagem leve opcional."),
            _Session("Long Run", "E/M", long_km, "Longão; alguns km em M se bem."),
        ]

        week_volume = volumes[idx]
        easy_dists = _allocate_easy_runs(week_volume, template)
        easy_counter = 0
        sessions: List[_Session] = []
        for sess in template:
            if sess.distance_km == 0:
                dist = easy_dists[easy_counter] if easy_counter < len(easy_dists) else 0.0
                easy_counter += 1
                sessions.append(_Session(sess.session_type, sess.intensity_label, dist, sess.description))
            else:
                sessions.append(sess)

        sessions = _apply_weekly_limit(sessions, cfg.weekly_days)
        frames.append(_build_week_dataframe("Pfitzinger", idx, start_date, sessions))

    return pd.concat(frames, ignore_index=True)


def gerar_plano_canova(cfg: MarathonPlanConfig) -> pd.DataFrame:
    weeks = 18
    start_km = max(cfg.base_weekly_km, 65 if cfg.runner_level != "iniciante" else 55)
    volumes = _week_volume_progression(weeks, start_km, 1.35, taper_weeks=2)
    start_date = _start_date_for_cycle(cfg.race_date, weeks)
    frames: list[pd.DataFrame] = []

    for idx in range(weeks):
        week_no = idx + 1
        if week_no <= 6:
            phase = "General"
        elif week_no <= 12:
            phase = "Special"
        else:
            phase = "Specific"

        long_base = 24 if phase == "General" else 30 if phase == "Special" else 34
        long_km = min(long_base + idx, 38)
        if week_no % 4 == 0:
            long_km *= 0.8
        if week_no >= 17:
            long_km *= 0.6

        special_1 = _Session(
            "Special Marathon", "MP ±10s", 14.0 if phase == "General" else 18.0,
            "Blocos longos alternando entre MP e ligeiramente mais rápido."
        )
        special_2 = _Session(
            "Tempo", "MP", 12.0 if phase != "Specific" else 18.0,
            "Treino específico com foco em economia no MP."
        )

        template: List[_Session] = [
            _Session("Easy", "Z2", 0.0, "Rodagem aeróbica."),
            special_1,
            _Session("Easy", "Z2", 0.0, "Rodagem leve para absorção."),
            special_2,
            _Session("Easy", "Z2", 0.0, "Rodagem regenerativa."),
            _Session("Medium Long", "Z2/Z3", min(long_km * 0.6, 24), "Endurance específica."),
            _Session("Long Run", "Z2/MP", long_km, "Longão com blocos específicos em MP."),
        ]

        week_volume = volumes[idx]
        easy_dists = _allocate_easy_runs(week_volume, template)
        easy_counter = 0
        sessions: List[_Session] = []
        for sess in template:
            if sess.distance_km == 0:
                dist = easy_dists[easy_counter] if easy_counter < len(easy_dists) else 0.0
                easy_counter += 1
                sessions.append(_Session(sess.session_type, sess.intensity_label, dist, sess.description))
            else:
                sessions.append(sess)

        sessions = _apply_weekly_limit(sessions, cfg.weekly_days)
        frames.append(_build_week_dataframe("Canova", idx, start_date, sessions))

    return pd.concat(frames, ignore_index=True)


def gerar_plano_lydiard(cfg: MarathonPlanConfig) -> pd.DataFrame:
    weeks = 18
    start_km = max(cfg.base_weekly_km, 50)
    volumes = _week_volume_progression(weeks, start_km, 1.25, taper_weeks=2)
    start_date = _start_date_for_cycle(cfg.race_date, weeks)
    frames: list[pd.DataFrame] = []

    for idx in range(weeks):
        week_no = idx + 1
        if week_no <= 8:
            phase = "Base"
        elif week_no <= 12:
            phase = "Hill"
        elif week_no <= 16:
            phase = "Anaerobic"
        else:
            phase = "Coordination"

        long_km = min(32.0, 18 + idx)
        if week_no % 4 == 0:
            long_km *= 0.8
        if phase == "Coordination":
            long_km *= 0.65

        hill_descr = "Circuito de colinas com saltos e corridas fortes."
        speed_descr = "Intervalos rápidos (R/I) mantendo técnica." if phase == "Anaerobic" else ""

        quality = _Session(
            "Hills" if phase == "Hill" else "Intervals" if phase == "Anaerobic" else "Steady",
            "Z3" if phase == "Base" else "Colinas" if phase == "Hill" else "R/I",
            10.0 if phase != "Coordination" else 6.0,
            hill_descr if phase == "Hill" else speed_descr or "Ritmo constante controlado.",
        )

        template: List[_Session] = [
            _Session("Easy", "Z2", 0.0, "Rodagem aeróbica."),
            quality,
            _Session("Easy", "Z2", 0.0, "Rodagem leve."),
            _Session("Medium Long", "Z2", min(22.0, 12 + idx * 0.6), "Endurance contínua."),
            _Session("Easy", "Z2", 0.0, "Rodagem leve ou strides."),
            _Session("Easy", "Z2", 0.0, "Rodagem leve."),
            _Session("Long Run", "Z2", long_km, "Longão semanal em Z2."),
        ]

        week_volume = volumes[idx]
        easy_dists = _allocate_easy_runs(week_volume, template)
        easy_counter = 0
        sessions: List[_Session] = []
        for sess in template:
            if sess.distance_km == 0:
                dist = easy_dists[easy_counter] if easy_counter < len(easy_dists) else 0.0
                easy_counter += 1
                sessions.append(_Session(sess.session_type, sess.intensity_label, dist, sess.description))
            else:
                sessions.append(sess)

        sessions = _apply_weekly_limit(sessions, cfg.weekly_days)
        frames.append(_build_week_dataframe("Lydiard", idx, start_date, sessions))

    return pd.concat(frames, ignore_index=True)


def gerar_plano_higdon(cfg: MarathonPlanConfig) -> pd.DataFrame:
    weeks = 18
    start_km = max(cfg.base_weekly_km, 35)
    volumes = _week_volume_progression(weeks, start_km, 1.2, taper_weeks=2)
    start_date = _start_date_for_cycle(cfg.race_date, weeks)
    frames: list[pd.DataFrame] = []

    for idx in range(weeks):
        week_no = idx + 1
        long_km = min(34.0, 12 + idx * 1.2)
        if week_no % 3 == 0:
            long_km *= 0.8
        if week_no >= 17:
            long_km *= 0.6

        tempo_distance = 8.0 if week_no < 10 else 10.0
        quality = _Session("Tempo", "T leve", tempo_distance, "Tempo run confortável (15-30 min).")

        template: List[_Session] = [
            _Session("Easy", "Z2", 0.0, "Rodagem leve."),
            quality,
            _Session("Rest", "Rest", 0.0, "Descanso ou cross-training."),
            _Session("Easy", "Z2", 0.0, "Rodagem leve."),
            _Session("Easy", "Z2", 0.0, "Rodagem leve."),
            _Session("Rest", "Rest", 0.0, "Descanso."),
            _Session("Long Run", "Z2", long_km, "Longão progressivo."),
        ]

        week_volume = volumes[idx]
        easy_dists = _allocate_easy_runs(week_volume, template)
        easy_counter = 0
        sessions: List[_Session] = []
        for sess in template:
            if sess.distance_km == 0 and sess.session_type == "Easy":
                dist = easy_dists[easy_counter] if easy_counter < len(easy_dists) else 0.0
                easy_counter += 1
                sessions.append(_Session(sess.session_type, sess.intensity_label, dist, sess.description))
            else:
                sessions.append(sess)

        sessions = _apply_weekly_limit(sessions, cfg.weekly_days)
        frames.append(_build_week_dataframe("Higdon", idx, start_date, sessions))

    return pd.concat(frames, ignore_index=True)


def gerar_plano_maratona(method_key: str, cfg: MarathonPlanConfig) -> pd.DataFrame:
    routers: dict[str, Callable[[MarathonPlanConfig], pd.DataFrame]] = {
        "Hansons": gerar_plano_hansons,
        "Daniels": gerar_plano_daniels,
        "Pfitzinger": gerar_plano_pfitzinger,
        "Canova": gerar_plano_canova,
        "Lydiard": gerar_plano_lydiard,
        "Higdon": gerar_plano_higdon,
    }
    if method_key not in routers:
        raise ValueError(f"Método de maratona não suportado: {method_key}")
    return routers[method_key](cfg)

