"""Plan generators for 70.3 training methodologies.

This module provides three classic approaches for Half Ironman prep:
- Joe Friel inspired periodisation (Friel_703)
- BarryP running frequency adapted to triathlon (BarryP_Tri)
- Sweet Spot heavy cycling focus (SweetSpot_703)

Each generator returns a pandas DataFrame that can be rendered directly
inside the Streamlit UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import Callable, Iterable

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

SWIM_SPEED_KMH_BY_LEVEL = {"iniciante": 2.3, "intermediario": 2.6, "avancado": 3.0}
BIKE_SPEED_KMH_BY_LEVEL = {"iniciante": 25.0, "intermediario": 28.0, "avancado": 31.0}
RUN_SPEED_KMH_BY_LEVEL = {"iniciante": 9.0, "intermediario": 10.0, "avancado": 11.0}


@dataclass
class Plan70Config:
    race_date: date
    current_long_run_km: float
    current_long_ride_km: float
    current_weekly_swim_km: float
    current_weekly_bike_km: float
    current_weekly_run_km: float
    available_hours_per_week: float
    swim_sessions_per_week: int
    bike_sessions_per_week: int
    run_sessions_per_week: int
    athlete_level: str
    target_703_time_hours: float | None = None
    target_run_pace_703: float | None = None
    prefers_two_bricks: bool | None = False
    has_gym_access: bool | None = False


@dataclass
class _Session:
    day_offset: int
    sport: str
    session_label: str
    duration_min: float
    distance_km: float | None
    intensity_zone: str
    key_focus: str
    description: str
    method: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_hours_from_current(cfg: Plan70Config) -> float:
    swim_hours = cfg.current_weekly_swim_km / SWIM_SPEED_KMH_BY_LEVEL.get(cfg.athlete_level, 2.6)
    bike_hours = cfg.current_weekly_bike_km / BIKE_SPEED_KMH_BY_LEVEL.get(cfg.athlete_level, 28.0)
    run_hours = cfg.current_weekly_run_km / RUN_SPEED_KMH_BY_LEVEL.get(cfg.athlete_level, 10.0)
    return swim_hours + bike_hours + run_hours


def _weeks_to_race(race_date: date) -> int:
    today = date.today()
    days = max(7, (race_date - today).days)
    return math.ceil(days / 7)


def _start_date_for_cycle(race_date: date, total_weeks: int) -> date:
    return race_date - timedelta(days=total_weeks * 7 - 1)


def _progressive_hours(total_weeks: int, cfg: Plan70Config, peak_multiplier: float = 1.35) -> list[float]:
    base_hours = max(5.0, min(cfg.available_hours_per_week * 0.75, _estimate_hours_from_current(cfg) * 1.05))
    peak_hours = min(cfg.available_hours_per_week, base_hours * peak_multiplier)
    hours: list[float] = []
    for idx in range(total_weeks):
        if idx >= total_weeks - 2:
            target = peak_hours * 0.65
        elif idx >= total_weeks - 4:
            target = peak_hours * 0.82
        else:
            target = base_hours + (peak_hours - base_hours) * (idx / max(1, total_weeks - 4))
        if (idx + 1) % 4 == 0:
            target *= 0.82
        hours.append(round(target, 2))
    return hours


def _linear_progression(start: float, peak: float, weeks: int, taper_weeks: int = 2) -> list[float]:
    values: list[float] = []
    ramp_weeks = max(1, weeks - taper_weeks)
    inc = (peak - start) / max(1, ramp_weeks - 1)
    for idx in range(weeks):
        if idx < ramp_weeks:
            val = start + inc * idx
        else:
            factor = 0.75 if idx == weeks - 2 else 0.6
            val = peak * factor
        if (idx + 1) % 4 == 0:
            val *= 0.88
        values.append(round(val, 1))
    return values


def _cap_sessions(sess: list[_Session], cfg: Plan70Config) -> list[_Session]:
    # ensure session counts do not exceed weekly availability by sport
    sport_limits = {
        "swim": cfg.swim_sessions_per_week,
        "bike": cfg.bike_sessions_per_week,
        "run": cfg.run_sessions_per_week,
        "strength": 2,
    }
    counts: dict[str, int] = {}
    filtered: list[_Session] = []
    for s in sess:
        limit = sport_limits.get(s.sport, 7)
        current = counts.get(s.sport, 0)
        if current < limit:
            filtered.append(s)
            counts[s.sport] = current + 1
    return filtered


def _scale_durations_to_cap(sess: list[_Session], hours_cap: float) -> list[_Session]:
    total_hours = sum(s.duration_min for s in sess) / 60
    if total_hours <= hours_cap + 0.1:
        return sess
    factor = hours_cap / total_hours
    scaled: list[_Session] = []
    for s in sess:
        new_duration = round(s.duration_min * factor, 1)
        new_distance = s.distance_km
        if new_distance is not None:
            new_distance = round(new_distance * factor, 2)
        scaled.append(
            _Session(
                day_offset=s.day_offset,
                sport=s.sport,
                session_label=s.session_label,
                duration_min=new_duration,
                distance_km=new_distance,
                intensity_zone=s.intensity_zone,
                key_focus=s.key_focus,
                description=s.description + " (ajustado ao limite semanal)",
                method=s.method,
            )
        )
    return scaled


def _session_rows(sessions: Iterable[_Session], week_idx: int, start_date: date) -> list[dict]:
    rows: list[dict] = []
    for sess in sessions:
        current_date = start_date + timedelta(days=week_idx * 7 + sess.day_offset)
        rows.append(
            {
                "week": week_idx + 1,
                "date": current_date,
                "day_name": DAY_NAMES[sess.day_offset],
                "sport": sess.sport,
                "session_label": sess.session_label,
                "duration_min": round(sess.duration_min, 1),
                "distance_km": None if sess.distance_km is None else round(sess.distance_km, 2),
                "intensity_zone": sess.intensity_zone,
                "key_focus": sess.key_focus,
                "description": sess.description,
                "method": sess.method,
            }
        )
    return rows


def _sport_speed(cfg: Plan70Config, sport: str) -> float:
    level = cfg.athlete_level
    if sport == "swim":
        return SWIM_SPEED_KMH_BY_LEVEL.get(level, 2.6)
    if sport == "bike":
        return BIKE_SPEED_KMH_BY_LEVEL.get(level, 28.0)
    return RUN_SPEED_KMH_BY_LEVEL.get(level, 10.0)


def _distance_from_duration(duration_min: float, cfg: Plan70Config, sport: str) -> float:
    speed = _sport_speed(cfg, sport)
    return (duration_min / 60.0) * speed


# ---------------------------------------------------------------------------
# Method generators
# ---------------------------------------------------------------------------


def gerar_plano_703_friel(cfg: Plan70Config) -> pd.DataFrame:
    total_weeks = max(12, _weeks_to_race(cfg.race_date))
    start_date = _start_date_for_cycle(cfg.race_date, total_weeks)
    hours_progression = _progressive_hours(total_weeks, cfg, peak_multiplier=1.35)

    prep = max(2, min(3, total_weeks // 8))
    base = max(4, total_weeks // 3)
    build = max(4, total_weeks // 3)
    peak = 2
    taper = 2
    while prep + base + build + peak + taper > total_weeks:
        base = max(3, base - 1)
    phase_boundaries = [prep, prep + base, prep + base + build, prep + base + build + peak]

    long_run_prog = _linear_progression(max(12.0, cfg.current_long_run_km), 20.0, total_weeks)
    long_ride_prog = _linear_progression(max(70.0, cfg.current_long_ride_km), 150.0, total_weeks)

    frames: list[dict] = []
    for wk in range(total_weeks):
        week_hours = hours_progression[wk]
        if wk < phase_boundaries[0]:
            phase = "Prep"
        elif wk < phase_boundaries[1]:
            phase = "Base"
        elif wk < phase_boundaries[2]:
            phase = "Build"
        elif wk < phase_boundaries[3]:
            phase = "Peak"
        else:
            phase = "Taper"

        sessions: list[_Session] = []
        long_run_km = long_run_prog[wk]
        long_ride_km = long_ride_prog[wk]
        include_brick = phase in {"Build", "Peak"}

        # Monday – swim technique or rest
        swim_duration = 45 if phase != "Taper" else 30
        swim_distance = _distance_from_duration(swim_duration, cfg, "swim")
        sessions.append(
            _Session(
                day_offset=0,
                sport="swim",
                session_label="Técnica + drills",
                duration_min=swim_duration,
                distance_km=swim_distance,
                intensity_zone="easy",
                key_focus="technique",
                description="Trabalho de técnica com  drills e respirações controladas.",
                method="Friel_703",
            )
        )

        # Tuesday – run tempo or easy depending on phase
        run_duration = 50 if phase in {"Build", "Peak"} else 40
        run_zone = "Z3" if phase in {"Build", "Peak"} else "Z2"
        run_desc = "Tempo contínuo em limiar inferior." if run_zone == "Z3" else "Rodagem aeróbica confortável."
        sessions.append(
            _Session(
                day_offset=1,
                sport="run",
                session_label="Tempo Run" if run_zone == "Z3" else "Easy Run",
                duration_min=run_duration,
                distance_km=_distance_from_duration(run_duration, cfg, "run"),
                intensity_zone=run_zone,
                key_focus="tempo" if run_zone == "Z3" else "endurance",
                description=run_desc,
                method="Friel_703",
            )
        )

        # Wednesday – bike aerobic + optional brick strides
        bike_duration = 75 if phase in {"Build", "Peak"} else 65
        bike_zone = "Z2" if phase in {"Prep", "Base"} else "Z2/Z3"
        bike_desc = "Endurance progressivo com 2-3 blocos em Z3." if bike_zone == "Z2/Z3" else "Pedal contínuo leve a moderado."
        sessions.append(
            _Session(
                day_offset=2,
                sport="bike",
                session_label="Endurance Ride",
                duration_min=bike_duration,
                distance_km=_distance_from_duration(bike_duration, cfg, "bike"),
                intensity_zone=bike_zone,
                key_focus="endurance",
                description=bike_desc,
                method="Friel_703",
            )
        )

        # Thursday – swim aerobic
        swim_end_duration = 55 if phase not in {"Prep", "Taper"} else 45
        sessions.append(
            _Session(
                day_offset=3,
                sport="swim",
                session_label="Endurance Swim",
                duration_min=swim_end_duration,
                distance_km=_distance_from_duration(swim_end_duration, cfg, "swim"),
                intensity_zone="aerobic",
                key_focus="endurance",
                description="Séries aeróbias contínuas, foco em técnica sob leve fadiga.",
                method="Friel_703",
            )
        )

        # Friday – rest or optional mobility
        sessions.append(
            _Session(
                day_offset=4,
                sport="rest",
                session_label="Rest",
                duration_min=0,
                distance_km=0.0,
                intensity_zone="Rest",
                key_focus="recovery",
                description="Dia de descanso ativo: mobilidade ou 20min spinning leve se desejar.",
                method="Friel_703",
            )
        )

        # Saturday – long ride (+ brick later)
        long_ride_duration = (long_ride_km / _sport_speed(cfg, "bike")) * 60
        sessions.append(
            _Session(
                day_offset=5,
                sport="bike",
                session_label="Long Ride",
                duration_min=long_ride_duration,
                distance_km=long_ride_km,
                intensity_zone="Z2" if phase in {"Prep", "Base"} else "Z2/Z3",
                key_focus="endurance",
                description="Longão de bike com cadência estável; inclua 2x20-30min em ritmo de prova nas fases avançadas.",
                method="Friel_703",
            )
        )
        if include_brick and cfg.run_sessions_per_week >= 3:
            brick_run_duration = 30 if phase != "Peak" else 35
            sessions.append(
                _Session(
                    day_offset=5,
                    sport="run",
                    session_label="Brick Run",
                    duration_min=brick_run_duration,
                    distance_km=_distance_from_duration(brick_run_duration, cfg, "run"),
                    intensity_zone="Z2",
                    key_focus="brick",
                    description="Corrida curta logo após o pedal, ritmo controlado Z2 focando transição eficiente.",
                    method="Friel_703",
                )
            )

        # Sunday – long run
        long_run_duration = (long_run_km / _sport_speed(cfg, "run")) * 60
        run_zone = "Z2" if phase in {"Prep", "Base"} else "Z2/Z3"
        sessions.append(
            _Session(
                day_offset=6,
                sport="run",
                session_label="Long Run",
                duration_min=long_run_duration,
                distance_km=long_run_km,
                intensity_zone=run_zone,
                key_focus="endurance",
                description="Longão progressivo, últimos 20-30min em ritmo de prova na fase Build/Peak.",
                method="Friel_703",
            )
        )

        sessions = _cap_sessions(sessions, cfg)
        sessions = _scale_durations_to_cap(sessions, cfg.available_hours_per_week)
        frames.extend(_session_rows(sessions, wk, start_date))

    return pd.DataFrame(frames)


def gerar_plano_703_barryp(cfg: Plan70Config) -> pd.DataFrame:
    total_weeks = max(14, _weeks_to_race(cfg.race_date))
    start_date = _start_date_for_cycle(cfg.race_date, total_weeks)
    hours_progression = _progressive_hours(total_weeks, cfg, peak_multiplier=1.28)

    run_slots = cfg.run_sessions_per_week
    if run_slots >= 6:
        pattern = (3, 2, 1)
    elif run_slots == 5:
        pattern = (2, 2, 1)
    else:
        pattern = (2, 1, 1)

    long_run_prog = _linear_progression(max(14.0, cfg.current_long_run_km), 21.0, total_weeks)
    long_ride_prog = _linear_progression(max(75.0, cfg.current_long_ride_km), 145.0, total_weeks)

    frames: list[dict] = []
    for wk in range(total_weeks):
        week_hours = hours_progression[wk]
        phase = "Base" if wk < total_weeks * 0.4 else "Build" if wk < total_weeks * 0.75 else "Peak"
        sessions: list[_Session] = []

        # Calculate run distances based on BarryP ratios
        run_hours = week_hours * 0.28
        total_run_km = run_hours * RUN_SPEED_KMH_BY_LEVEL.get(cfg.athlete_level, 10.0)
        s_count, m_count, l_count = pattern
        unit = total_run_km / (s_count * 1 + m_count * 2 + l_count * 3)
        short_km = unit
        medium_km = unit * 2
        long_km = unit * 3
        long_km = max(long_km, long_run_prog[wk])

        # Monday – easy run short
        sessions.append(
            _Session(
                day_offset=0,
                sport="run",
                session_label="Run S",
                duration_min=(short_km / _sport_speed(cfg, "run")) * 60,
                distance_km=short_km,
                intensity_zone="Z1/Z2",
                key_focus="endurance",
                description="Corrida curta leve para acumular volume com baixo estresse.",
                method="BarryP_Tri",
            )
        )

        # Tuesday – bike tempo + optional medium run
        bike_duration = 70 if phase == "Base" else 80
        bike_zone = "Z2/Z3" if phase != "Base" else "Z2"
        sessions.append(
            _Session(
                day_offset=1,
                sport="bike",
                session_label="Bike Tempo",
                duration_min=bike_duration,
                distance_km=_distance_from_duration(bike_duration, cfg, "bike"),
                intensity_zone=bike_zone,
                key_focus="tempo" if bike_zone == "Z2/Z3" else "endurance",
                description="Blocos de 10-20min em Z3 com recuperações curtas.",
                method="BarryP_Tri",
            )
        )
        if pattern[1] >= 2:
            sessions.append(
                _Session(
                    day_offset=1,
                    sport="run",
                    session_label="Run M",
                    duration_min=(medium_km / _sport_speed(cfg, "run")) * 60,
                    distance_km=medium_km,
                    intensity_zone="Z2",
                    key_focus="endurance",
                    description="Rodagem média controlada, cadência estável.",
                    method="BarryP_Tri",
                )
            )

        # Wednesday – swim technique + medium run if slots allow
        sessions.append(
            _Session(
                day_offset=2,
                sport="swim",
                session_label="Técnica Swim",
                duration_min=50,
                distance_km=_distance_from_duration(50, cfg, "swim"),
                intensity_zone="technique",
                key_focus="technique",
                description="Drills, respiração bilateral e controle de deslize.",
                method="BarryP_Tri",
            )
        )
        if pattern[1] >= 1:
            sessions.append(
                _Session(
                    day_offset=2,
                    sport="run",
                    session_label="Run M/Tempo" if phase != "Base" else "Run M",
                    duration_min=(medium_km / _sport_speed(cfg, "run")) * 60,
                    distance_km=medium_km,
                    intensity_zone="Z3" if phase != "Base" else "Z2",
                    key_focus="tempo" if phase != "Base" else "endurance",
                    description="Inclua 10-20min em Z3 nas semanas de Build/Peak.",
                    method="BarryP_Tri",
                )
            )

        # Thursday – swim endurance
        swim_dur = 55 if phase != "Peak" else 45
        sessions.append(
            _Session(
                day_offset=3,
                sport="swim",
                session_label="Endurance Swim",
                duration_min=swim_dur,
                distance_km=_distance_from_duration(swim_dur, cfg, "swim"),
                intensity_zone="aerobic",
                key_focus="endurance",
                description="Séries contínuas aeróbias com respirações regulares.",
                method="BarryP_Tri",
            )
        )

        # Friday – rest or easy short run
        sessions.append(
            _Session(
                day_offset=4,
                sport="run",
                session_label="Run S",
                duration_min=(short_km / _sport_speed(cfg, "run")) * 60,
                distance_km=short_km,
                intensity_zone="Z1/Z2",
                key_focus="recovery",
                description="Rodagem curtinha, solta as pernas para o fim de semana.",
                method="BarryP_Tri",
            )
        )

        # Saturday – long ride with brick
        long_ride_km = long_ride_prog[wk]
        long_ride_duration = (long_ride_km / _sport_speed(cfg, "bike")) * 60
        sessions.append(
            _Session(
                day_offset=5,
                sport="bike",
                session_label="Long Ride",
                duration_min=long_ride_duration,
                distance_km=long_ride_km,
                intensity_zone="Z2",
                key_focus="endurance",
                description="Longão de bike, cadência suave; inclua 2x20min Z3 nas semanas-chave.",
                method="BarryP_Tri",
            )
        )
        if cfg.run_sessions_per_week >= 4:
            brick_dur = 25 if phase == "Base" else 35
            sessions.append(
                _Session(
                    day_offset=5,
                    sport="run",
                    session_label="Brick Run",
                    duration_min=brick_dur,
                    distance_km=_distance_from_duration(brick_dur, cfg, "run"),
                    intensity_zone="Z2",
                    key_focus="brick",
                    description="Transição bike→run em Z2, foque cadência alta e postura relaxada.",
                    method="BarryP_Tri",
                )
            )

        # Sunday – long run (BarryP backbone)
        long_run_km = long_km
        sessions.append(
            _Session(
                day_offset=6,
                sport="run",
                session_label="Run L",
                duration_min=(long_run_km / _sport_speed(cfg, "run")) * 60,
                distance_km=long_run_km,
                intensity_zone="Z2" if phase != "Peak" else "Z2/Z3",
                key_focus="endurance",
                description="Longão constante; últimas 20min em Z3 nas semanas de pico.",
                method="BarryP_Tri",
            )
        )

        sessions = _cap_sessions(sessions, cfg)
        sessions = _scale_durations_to_cap(sessions, cfg.available_hours_per_week)
        frames.extend(_session_rows(sessions, wk, start_date))

    return pd.DataFrame(frames)


def gerar_plano_703_sweetspot(cfg: Plan70Config) -> pd.DataFrame:
    total_weeks = max(12, _weeks_to_race(cfg.race_date))
    start_date = _start_date_for_cycle(cfg.race_date, total_weeks)
    hours_progression = _progressive_hours(total_weeks, cfg, peak_multiplier=1.4)

    long_run_prog = _linear_progression(max(13.0, cfg.current_long_run_km), 22.0, total_weeks)
    long_ride_prog = _linear_progression(max(80.0, cfg.current_long_ride_km), 160.0, total_weeks)

    frames: list[dict] = []
    for wk in range(total_weeks):
        week_hours = hours_progression[wk]
        phase = "Base" if wk < total_weeks * 0.35 else "Build" if wk < total_weeks * 0.7 else "Specific" if wk < total_weeks - 2 else "Taper"
        sessions: list[_Session] = []

        # Monday – swim technique or rest
        swim_dur = 45
        sessions.append(
            _Session(
                day_offset=0,
                sport="swim",
                session_label="Swim Técnica",
                duration_min=swim_dur,
                distance_km=_distance_from_duration(swim_dur, cfg, "swim"),
                intensity_zone="technique",
                key_focus="technique",
                description="Drills + respiração bilateral; mantenha ritmo confortável.",
                method="SweetSpot_703",
            )
        )

        # Tuesday – Sweet spot key session
        ss_blocks = "3x12-15min" if phase == "Base" else "3x15-20min"
        bike_dur = 80 if phase == "Base" else 95
        sessions.append(
            _Session(
                day_offset=1,
                sport="bike",
                session_label="Sweet Spot Bike",
                duration_min=bike_dur,
                distance_km=_distance_from_duration(bike_dur, cfg, "bike"),
                intensity_zone="Sweet Spot",
                key_focus="sweet_spot",
                description=f"{ss_blocks} em Z3/Z4 com recuperações curtas. Capriche na posição aerodinâmica.",
                method="SweetSpot_703",
            )
        )
        # Optional short run off the bike for specific feel
        if cfg.run_sessions_per_week >= 4 and phase != "Taper":
            sessions.append(
                _Session(
                    day_offset=1,
                    sport="run",
                    session_label="Run curto pós-bike",
                    duration_min=25,
                    distance_km=_distance_from_duration(25, cfg, "run"),
                    intensity_zone="Z2",
                    key_focus="brick",
                    description="Transição curta para fixar cadência de prova.",
                    method="SweetSpot_703",
                )
            )

        # Wednesday – swim endurance + tempo run
        swim_end_dur = 55
        sessions.append(
            _Session(
                day_offset=2,
                sport="swim",
                session_label="Swim Endurance",
                duration_min=swim_end_dur,
                distance_km=_distance_from_duration(swim_end_dur, cfg, "swim"),
                intensity_zone="aerobic",
                key_focus="endurance",
                description="Séries aeróbias longas; inclua 6-10x200m com 20s se sentir bem.",
                method="SweetSpot_703",
            )
        )
        run_tempo_dur = 45 if phase == "Base" else 55
        sessions.append(
            _Session(
                day_offset=2,
                sport="run",
                session_label="Tempo Run",
                duration_min=run_tempo_dur,
                distance_km=_distance_from_duration(run_tempo_dur, cfg, "run"),
                intensity_zone="Z3",
                key_focus="tempo",
                description="Bloco contínuo em Z3 (ritmo de meia) para suportar corrida do 70.3.",
                method="SweetSpot_703",
            )
        )

        # Thursday – Sweet spot/tempo long blocks
        tempo_dur = 75 if phase == "Base" else 90
        tempo_desc = "2x15-20min em Z3" if phase == "Base" else "2x25min em ritmo de prova" if phase == "Specific" else "3x15-20min em Z3/Z4"
        sessions.append(
            _Session(
                day_offset=3,
                sport="bike",
                session_label="Tempo / Race Specific",
                duration_min=tempo_dur,
                distance_km=_distance_from_duration(tempo_dur, cfg, "bike"),
                intensity_zone="Z3/Z4",
                key_focus="tempo",
                description=f"{tempo_desc}; mantenha alimentação e posição aero.",
                method="SweetSpot_703",
            )
        )

        # Friday – rest or easy swim
        sessions.append(
            _Session(
                day_offset=4,
                sport="rest",
                session_label="Rest",
                duration_min=0,
                distance_km=0.0,
                intensity_zone="Rest",
                key_focus="recovery",
                description="Descanso ativo, mobilidade, 15-20min de core se possível.",
                method="SweetSpot_703",
            )
        )

        # Saturday – long ride (race specific) with brick run
        long_ride_km = long_ride_prog[wk]
        long_ride_duration = (long_ride_km / _sport_speed(cfg, "bike")) * 60
        desc = "Longão de bike em Z2 com 2-3 blocos de 20-30min em race pace." if phase != "Taper" else "Longo moderado reduzido, inclua 2x10min em race pace."
        sessions.append(
            _Session(
                day_offset=5,
                sport="bike",
                session_label="Long Ride específico",
                duration_min=long_ride_duration,
                distance_km=long_ride_km,
                intensity_zone="Z2/Z3",
                key_focus="endurance",
                description=desc,
                method="SweetSpot_703",
            )
        )
        brick_run_duration = 30 if phase != "Specific" else 45
        sessions.append(
            _Session(
                day_offset=5,
                sport="run",
                session_label="Brick Run",
                duration_min=brick_run_duration,
                distance_km=_distance_from_duration(brick_run_duration, cfg, "run"),
                intensity_zone="Z2" if phase != "Specific" else "Z2/Z3",
                key_focus="brick",
                description="Corrida pós-bike focando ritmo de prova e nutrição." if phase != "Taper" else "Corrida curta para manter a transição afiada.",
                method="SweetSpot_703",
            )
        )

        # Sunday – long run
        long_run_km = long_run_prog[wk]
        long_run_duration = (long_run_km / _sport_speed(cfg, "run")) * 60
        run_desc = "Longão Z2; insira 2x15min em Z3 nas semanas-chave." if phase in {"Build", "Specific"} else "Rodagem longa estável em Z2."
        sessions.append(
            _Session(
                day_offset=6,
                sport="run",
                session_label="Long Run",
                duration_min=long_run_duration,
                distance_km=long_run_km,
                intensity_zone="Z2" if phase == "Taper" else "Z2/Z3",
                key_focus="endurance",
                description=run_desc,
                method="SweetSpot_703",
            )
        )

        sessions = _cap_sessions(sessions, cfg)
        sessions = _scale_durations_to_cap(sessions, cfg.available_hours_per_week)
        frames.extend(_session_rows(sessions, wk, start_date))

    return pd.DataFrame(frames)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def gerar_plano_703(method_key: str, cfg: Plan70Config) -> pd.DataFrame:
    if method_key == "Friel_703":
        return gerar_plano_703_friel(cfg)
    if method_key == "BarryP_Tri":
        return gerar_plano_703_barryp(cfg)
    if method_key == "SweetSpot_703":
        return gerar_plano_703_sweetspot(cfg)
    raise ValueError(f"Método 70.3 desconhecido: {method_key}")
