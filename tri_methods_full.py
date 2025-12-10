"""Plan generators for Ironman Full (140.6) training methodologies.

This module implements three classic methods for Ironman preparation:
- MAF_Full: Mark Allen / MAF base-first approach
- EN_Full: Endurance Nation "Fast Before Far" model
- CTS_Full: Carmichael Training Systems with TSS guidance

Each generator returns a pandas DataFrame that can be consumed by the Streamlit UI.
The functions are purposely self-contained so new methods can be added easily.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import Iterable

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

SWIM_SPEED_KMH_BY_LEVEL = {"iniciante": 2.2, "intermediario": 2.6, "avancado": 3.1}
BIKE_SPEED_KMH_BY_LEVEL = {"iniciante": 25.0, "intermediario": 28.0, "avancado": 32.0}
RUN_SPEED_KMH_BY_LEVEL = {"iniciante": 9.0, "intermediario": 10.0, "avancado": 11.5}

INTENSITY_IF = {
    "Z1": 0.55,
    "Z2": 0.7,
    "Z3": 0.85,
    "Z4": 0.95,
    "Z5": 1.05,
    "MAF": 0.68,
    "Tempo": 0.88,
    "Threshold": 0.98,
    "Race Pace": 0.8,
}


@dataclass
class PlanFullConfig:
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
    target_full_time_hours: float | None = None
    target_marathon_pace_full: float | None = None
    uses_power_meter: bool = False
    uses_hr_zones: bool = True


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
    tss_estimate: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _weeks_to_race(race_date: date) -> int:
    today = date.today()
    days = max(7, (race_date - today).days)
    return math.ceil(days / 7)


def _monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _start_date_for_cycle(race_date: date, total_weeks: int) -> date:
    race_week_monday = _monday_of_week(race_date)
    return race_week_monday - timedelta(weeks=total_weeks - 1)


def _sport_speed(cfg: PlanFullConfig, sport: str) -> float:
    level = cfg.athlete_level
    if sport == "swim":
        return SWIM_SPEED_KMH_BY_LEVEL.get(level, 2.6)
    if sport == "bike":
        return BIKE_SPEED_KMH_BY_LEVEL.get(level, 28.0)
    return RUN_SPEED_KMH_BY_LEVEL.get(level, 10.0)


def _estimate_hours_from_current(cfg: PlanFullConfig) -> float:
    swim_hours = cfg.current_weekly_swim_km / _sport_speed(cfg, "swim")
    bike_hours = cfg.current_weekly_bike_km / _sport_speed(cfg, "bike")
    run_hours = cfg.current_weekly_run_km / _sport_speed(cfg, "run")
    return swim_hours + bike_hours + run_hours


def _phase_allocation(total_weeks: int, desired: list[int]) -> list[int]:
    total_desired = sum(desired)
    if total_desired == 0:
        return [0] * len(desired)
    raw = [w * total_weeks / total_desired for w in desired]
    rounded = [max(1, int(round(x))) for x in raw]
    diff = total_weeks - sum(rounded)
    # fix rounding drift
    idx = 0
    while diff != 0 and idx < len(rounded):
        rounded[idx] += 1 if diff > 0 else -1
        diff = total_weeks - sum(rounded)
        idx = (idx + 1) % len(rounded)
    return rounded


def _linear_progression(start: float, peak: float, weeks: int, taper_weeks: int = 2) -> list[float]:
    values: list[float] = []
    ramp_weeks = max(1, weeks - taper_weeks)
    inc = (peak - start) / max(1, ramp_weeks - 1)
    for idx in range(weeks):
        if idx < ramp_weeks:
            val = start + inc * idx
        else:
            factor = 0.7 if idx == weeks - 2 else 0.55
            val = peak * factor
        if (idx + 1) % 4 == 0:
            val *= 0.9
        values.append(round(val, 2))
    return values


def _progressive_hours(total_weeks: int, cfg: PlanFullConfig, peak_multiplier: float = 1.45) -> list[float]:
    base_hours = max(7.0, min(cfg.available_hours_per_week * 0.75, _estimate_hours_from_current(cfg) * 1.08))
    peak_hours = min(cfg.available_hours_per_week, base_hours * peak_multiplier)
    hours: list[float] = []
    for idx in range(total_weeks):
        if idx >= total_weeks - 2:
            target = peak_hours * 0.6
        elif idx >= total_weeks - 4:
            target = peak_hours * 0.82
        else:
            target = base_hours + (peak_hours - base_hours) * (idx / max(1, total_weeks - 4))
        if (idx + 1) % 4 == 0:
            target *= 0.85
        hours.append(round(target, 2))
    return hours


def _cap_sessions(sess: list[_Session], cfg: PlanFullConfig) -> list[_Session]:
    sport_limits = {
        "swim": cfg.swim_sessions_per_week,
        "bike": cfg.bike_sessions_per_week,
        "run": cfg.run_sessions_per_week,
        "strength": 2,
        "rest": 7,
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
    factor = hours_cap / max(total_hours, 0.1)
    scaled: list[_Session] = []
    for s in sess:
        new_duration = round(s.duration_min * factor, 1)
        new_distance = s.distance_km
        if new_distance is not None:
            new_distance = round(new_distance * factor, 2)
        new_tss = s.tss_estimate
        if new_tss is not None:
            new_tss = round(new_tss * factor, 1)
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
                tss_estimate=new_tss,
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
                "tss_estimate": sess.tss_estimate,
                "description": sess.description,
                "method": sess.method,
            }
        )
    return rows


def _tss_from_session(duration_min: float, intensity_zone: str) -> float:
    if intensity_zone not in INTENSITY_IF:
        return round((duration_min / 60) * 50, 1)
    intensity_factor = INTENSITY_IF[intensity_zone]
    return round((duration_min / 60) * (intensity_factor**2) * 100, 1)


# ---------------------------------------------------------------------------
# Method generators
# ---------------------------------------------------------------------------


def gerar_plano_full_maf(cfg: PlanFullConfig) -> pd.DataFrame:
    total_weeks = _weeks_to_race(cfg.race_date)
    start_date = _start_date_for_cycle(cfg.race_date, total_weeks)
    phase_weeks = _phase_allocation(total_weeks, [14, 5, 5, 2])
    week_hours = _progressive_hours(total_weeks, cfg, peak_multiplier=1.6)

    long_run_prog = _linear_progression(cfg.current_long_run_km, 30.0, total_weeks)
    long_ride_prog_hours = _linear_progression(
        cfg.current_long_ride_km / _sport_speed(cfg, "bike"), 5.0, total_weeks
    )

    rows: list[dict] = []
    phase_bounds = [sum(phase_weeks[:i]) for i in range(len(phase_weeks) + 1)]
    for w in range(total_weeks):
        phase = "Base"
        if w >= phase_bounds[1]:
            phase = "Transição"
        if w >= phase_bounds[2]:
            phase = "Específica"
        if w >= phase_bounds[3]:
            phase = "Taper"

        week_sessions: list[_Session] = []
        long_run_km = long_run_prog[w]
        long_ride_h = long_ride_prog_hours[w]
        long_ride_km = long_ride_h * _sport_speed(cfg, "bike")

        # Monday rest / mobility
        week_sessions.append(
            _Session(
                day_offset=0,
                sport="rest",
                session_label="Rest",
                duration_min=0,
                distance_km=None,
                intensity_zone="Z1",
                key_focus="recovery",
                description="Dia de descanso ativo ou alongamentos leves.",
                method="MAF_Full",
            )
        )

        # Swim technique midweek
        week_sessions.append(
            _Session(
                day_offset=1,
                sport="swim",
                session_label="Técnica + Contínuo",
                duration_min=55,
                distance_km=2.2,
                intensity_zone="Z2",
                key_focus="aerobic_base",
                description="Drills de técnica seguido de nado contínuo fácil (MAF/Z2).",
                method="MAF_Full",
            )
        )

        # Bike endurance
        bike_intensity = "Z2" if phase in {"Base", "Transição"} else "Z3"
        week_sessions.append(
            _Session(
                day_offset=2,
                sport="bike",
                session_label="Endurance Ride",
                duration_min=80,
                distance_km=round(80 / 60 * _sport_speed(cfg, "bike"), 1),
                intensity_zone=bike_intensity if phase != "Taper" else "Z2",
                key_focus="aerobic_base" if phase == "Base" else "tempo",
                description="Pedal contínuo focado em cadência estável e baixa RPE.",
                method="MAF_Full",
            )
        )

        # Run easy / tempo depending on phase
        run_intensity = "MAF" if phase == "Base" else ("Z3" if phase == "Transição" else "Z2")
        run_key = "aerobic_base" if phase == "Base" else "tempo"
        week_sessions.append(
            _Session(
                day_offset=3,
                sport="run",
                session_label="Corrida Aeróbica",
                duration_min=50 if phase == "Base" else 60,
                distance_km=round(_sport_speed(cfg, "run") * (50 if phase == "Base" else 60) / 60, 1),
                intensity_zone=run_intensity,
                key_focus=run_key,
                description="Rodagem em MAF/Z2; adicionar 15-20 min Z3 se em Transição/Específica.",
                method="MAF_Full",
            )
        )

        # Swim aerobic longer
        swim_long_dur = 70 if phase != "Taper" else 50
        week_sessions.append(
            _Session(
                day_offset=4,
                sport="swim",
                session_label="Endurance Swim",
                duration_min=swim_long_dur,
                distance_km=round(swim_long_dur / 60 * _sport_speed(cfg, "swim"), 2),
                intensity_zone="Z2",
                key_focus="aerobic_base",
                description="Séries longas contínuas focando eficiência e respiração bilateral.",
                method="MAF_Full",
            )
        )

        # Long ride + brick
        brick_run_duration = 25 if phase in {"Base", "Transição"} else 50
        brick_intensity = "Z2" if phase != "Específica" else "Race Pace"
        week_sessions.append(
            _Session(
                day_offset=5,
                sport="bike",
                session_label="Long Ride",
                duration_min=round(long_ride_h * 60, 1),
                distance_km=round(long_ride_km, 1),
                intensity_zone="Z2" if phase in {"Base", "Transição"} else "Z2",
                key_focus="endurance" if phase in {"Base", "Transição"} else "race_specific",
                description="Pedal longo contínuo; últimos 45-60 min em ritmo de prova nas fases finais.",
                method="MAF_Full",
            )
        )
        week_sessions.append(
            _Session(
                day_offset=5,
                sport="run",
                session_label="Brick Run",
                duration_min=brick_run_duration,
                distance_km=round(brick_run_duration / 60 * _sport_speed(cfg, "run"), 1),
                intensity_zone=brick_intensity,
                key_focus="brick",
                description="Transição imediata do pedal para corrida controlada.",
                method="MAF_Full",
            )
        )

        # Long run Sunday
        week_sessions.append(
            _Session(
                day_offset=6,
                sport="run",
                session_label="Long Run",
                duration_min=round(long_run_km / _sport_speed(cfg, "run") * 60, 1),
                distance_km=round(long_run_km, 1),
                intensity_zone="MAF" if phase == "Base" else "Z2",
                key_focus="endurance",
                description="Longão progressivo controlado; manter MAF/Z2, últimos km steady.",
                method="MAF_Full",
            )
        )

        if phase == "Específica":
            week_sessions.append(
                _Session(
                    day_offset=2,
                    sport="run",
                    session_label="Tempo Run",
                    duration_min=45,
                    distance_km=round(_sport_speed(cfg, "run") * 45 / 60, 1),
                    intensity_zone="Z3",
                    key_focus="race_specific",
                    description="15-20 min a ritmo de prova no meio da rodagem.",
                    method="MAF_Full",
                )
            )

        week_sessions = _cap_sessions(week_sessions, cfg)
        week_sessions = _scale_durations_to_cap(week_sessions, week_hours[w])
        rows.extend(_session_rows(week_sessions, w, start_date))

    return pd.DataFrame(rows)


def gerar_plano_full_en(cfg: PlanFullConfig) -> pd.DataFrame:
    total_weeks = _weeks_to_race(cfg.race_date)
    start_date = _start_date_for_cycle(cfg.race_date, total_weeks)
    phase_weeks = _phase_allocation(total_weeks, [7, 9, 6, 2])
    week_hours = _progressive_hours(total_weeks, cfg, peak_multiplier=1.5)

    long_run_prog = _linear_progression(cfg.current_long_run_km, 30.0, total_weeks)
    long_ride_prog_hours = _linear_progression(
        cfg.current_long_ride_km / _sport_speed(cfg, "bike"), 5.0, total_weeks
    )

    rows: list[dict] = []
    phase_bounds = [sum(phase_weeks[:i]) for i in range(len(phase_weeks) + 1)]
    for w in range(total_weeks):
        phase = "OutSeason"
        if w >= phase_bounds[1]:
            phase = "Build"
        if w >= phase_bounds[2]:
            phase = "RacePrep"
        if w >= phase_bounds[3]:
            phase = "Taper"

        week_sessions: list[_Session] = []
        long_run_km = long_run_prog[w]
        long_ride_h = long_ride_prog_hours[w]

        # Rest / optional swim tech
        week_sessions.append(
            _Session(
                day_offset=0,
                sport="rest",
                session_label="Rest",
                duration_min=0,
                distance_km=None,
                intensity_zone="Z1",
                key_focus="recovery",
                description="Descanso ou alongamentos. Em OS pode fazer técnica de natação leve.",
                method="EN_Full",
            )
        )

        # Bike intensity (FTP/VO2 or sweet spot)
        bike_label = "FTP/VO2 Bike" if phase == "OutSeason" else "Sweet Spot Bike"
        bike_zone = "Z4" if phase == "OutSeason" else "Z3"
        bike_focus = "ftp_dev" if phase == "OutSeason" else "sweet_spot"
        week_sessions.append(
            _Session(
                day_offset=1,
                sport="bike",
                session_label=bike_label,
                duration_min=75,
                distance_km=round(75 / 60 * _sport_speed(cfg, "bike"), 1),
                intensity_zone=bike_zone,
                key_focus=bike_focus,
                description="Intervalos fortes (2x12-15min Z4) na OS; 2x20min sweet spot nas demais fases.",
                method="EN_Full",
            )
        )

        # Run tempo/interval
        run_zone = "Z4" if phase == "OutSeason" else "Z3"
        run_focus = "speed" if phase == "OutSeason" else "tempo"
        week_sessions.append(
            _Session(
                day_offset=2,
                sport="run",
                session_label="Tempo/Interval Run",
                duration_min=55,
                distance_km=round(55 / 60 * _sport_speed(cfg, "run"), 1),
                intensity_zone=run_zone,
                key_focus=run_focus,
                description="Blocos de 8-10min Z4 na OS; tempo contínuo de 20-30min Z3 no Build e Race Prep.",
                method="EN_Full",
            )
        )

        # Swim quality
        swim_desc = "Técnica + séries curtas" if phase == "OutSeason" else "Séries longas em ritmo de prova"
        week_sessions.append(
            _Session(
                day_offset=3,
                sport="swim",
                session_label="Quality Swim",
                duration_min=60,
                distance_km=round(60 / 60 * _sport_speed(cfg, "swim"), 2),
                intensity_zone="Z3" if phase != "OutSeason" else "Z2",
                key_focus="threshold" if phase != "OutSeason" else "técnica",
                description=swim_desc,
                method="EN_Full",
            )
        )

        # Long ride / Big Day bricks in Race Prep
        brick_run_after = 35 if phase == "RacePrep" else 20
        brick_focus = "race_specific" if phase == "RacePrep" else "brick"
        week_sessions.append(
            _Session(
                day_offset=5,
                sport="bike",
                session_label="Long Ride",
                duration_min=round(long_ride_h * 60, 1),
                distance_km=round(long_ride_h * _sport_speed(cfg, "bike"), 1),
                intensity_zone="Z2" if phase != "RacePrep" else "Race Pace",
                key_focus="endurance" if phase != "RacePrep" else "race_specific",
                description="Long ride subindo de 3h para 5h; inserir blocos a ritmo de prova no Race Prep.",
                method="EN_Full",
            )
        )
        week_sessions.append(
            _Session(
                day_offset=5,
                sport="run",
                session_label="Brick Run",
                duration_min=brick_run_after,
                distance_km=round(brick_run_after / 60 * _sport_speed(cfg, "run"), 1),
                intensity_zone="Z2" if phase != "RacePrep" else "Z3",
                key_focus=brick_focus,
                description="Corrida steady após o pedal; manter controle e foco em técnica de transição.",
                method="EN_Full",
            )
        )

        # Long run
        week_sessions.append(
            _Session(
                day_offset=6,
                sport="run",
                session_label="Long Run",
                duration_min=round(long_run_km / _sport_speed(cfg, "run") * 60, 1),
                distance_km=round(long_run_km, 1),
                intensity_zone="Z2",
                key_focus="endurance" if phase != "RacePrep" else "race_specific",
                description="Longão steady; manter ritmo controlado, sem exceder Z3 baixo.",
                method="EN_Full",
            )
        )

        # Extra easy sessions to hit frequency
        if cfg.swim_sessions_per_week >= 2:
            week_sessions.append(
                _Session(
                    day_offset=4,
                    sport="swim",
                    session_label="Endurance Swim",
                    duration_min=55,
                    distance_km=round(55 / 60 * _sport_speed(cfg, "swim"), 2),
                    intensity_zone="Z2",
                    key_focus="aerobic_base",
                    description="Nado contínuo ou séries de 400-800m em ritmo controlado.",
                    method="EN_Full",
                )
            )
        if cfg.bike_sessions_per_week >= 3:
            week_sessions.append(
                _Session(
                    day_offset=4,
                    sport="bike",
                    session_label="Tempo Bike",
                    duration_min=70,
                    distance_km=round(70 / 60 * _sport_speed(cfg, "bike"), 1),
                    intensity_zone="Z3",
                    key_focus="tempo",
                    description="3x12-15min tempo/SST; foco em cadência e potência steady.",
                    method="EN_Full",
                )
            )

        week_sessions = _cap_sessions(week_sessions, cfg)
        week_sessions = _scale_durations_to_cap(week_sessions, week_hours[w])
        rows.extend(_session_rows(week_sessions, w, start_date))

    return pd.DataFrame(rows)


def gerar_plano_full_cts(cfg: PlanFullConfig) -> pd.DataFrame:
    total_weeks = _weeks_to_race(cfg.race_date)
    start_date = _start_date_for_cycle(cfg.race_date, total_weeks)
    phase_weeks = _phase_allocation(total_weeks, [7, 6, 7, 2])
    week_hours = _progressive_hours(total_weeks, cfg, peak_multiplier=1.5)

    long_run_prog = _linear_progression(cfg.current_long_run_km, 32.0, total_weeks)
    long_ride_prog_hours = _linear_progression(
        cfg.current_long_ride_km / _sport_speed(cfg, "bike"), 5.5, total_weeks
    )

    rows: list[dict] = []
    phase_bounds = [sum(phase_weeks[:i]) for i in range(len(phase_weeks) + 1)]
    for w in range(total_weeks):
        phase = "BikePower"
        if w >= phase_bounds[1]:
            phase = "RunDurability"
        if w >= phase_bounds[2]:
            phase = "RaceSpecific"
        if w >= phase_bounds[3]:
            phase = "Taper"

        week_sessions: list[_Session] = []
        long_run_km = long_run_prog[w]
        long_ride_h = long_ride_prog_hours[w]

        # Rest day
        week_sessions.append(
            _Session(
                day_offset=0,
                sport="rest",
                session_label="Rest",
                duration_min=0,
                distance_km=None,
                intensity_zone="Z1",
                key_focus="recovery",
                description="Descanso ou mobilidade leve para absorver carga.",
                method="CTS_Full",
                tss_estimate=0,
            )
        )

        # Bike power/FTP focus sessions
        bike_zone = "Z4" if phase == "BikePower" else ("Z3" if phase == "RaceSpecific" else "Z3")
        bike_focus = "ftp_dev" if phase == "BikePower" else "sweet_spot"
        bike_duration = 80
        tss_bike = _tss_from_session(bike_duration, bike_zone)
        week_sessions.append(
            _Session(
                day_offset=1,
                sport="bike",
                session_label="Bike Intervals",
                duration_min=bike_duration,
                distance_km=round(bike_duration / 60 * _sport_speed(cfg, "bike"), 1),
                intensity_zone=bike_zone,
                key_focus=bike_focus,
                description="Intervalos de 2x15min Z4 na fase Bike Power; 2x20min SST nas demais.",
                method="CTS_Full",
                tss_estimate=tss_bike,
            )
        )

        # Run durability / tempo
        run_zone = "Z2" if phase == "BikePower" else ("Z3" if phase != "Taper" else "Z2")
        run_focus = "aerobic_base" if phase == "BikePower" else "tempo"
        run_duration = 55
        tss_run = _tss_from_session(run_duration, run_zone)
        week_sessions.append(
            _Session(
                day_offset=2,
                sport="run",
                session_label="Run Durability",
                duration_min=run_duration,
                distance_km=round(run_duration / 60 * _sport_speed(cfg, "run"), 1),
                intensity_zone=run_zone,
                key_focus=run_focus,
                description="Rodagem controlada; inserir 15-20min tempo nas fases posteriores.",
                method="CTS_Full",
                tss_estimate=tss_run,
            )
        )

        # Swim endurance/threshold
        swim_zone = "Z2" if phase in {"BikePower", "Taper"} else "Z3"
        swim_duration = 60 if phase != "Taper" else 45
        tss_swim = _tss_from_session(swim_duration, swim_zone)
        week_sessions.append(
            _Session(
                day_offset=3,
                sport="swim",
                session_label="Swim Aeróbico",
                duration_min=swim_duration,
                distance_km=round(swim_duration / 60 * _sport_speed(cfg, "swim"), 2),
                intensity_zone=swim_zone,
                key_focus="aerobic_base" if swim_zone == "Z2" else "threshold",
                description="Séries longas contínuas; incluir blocos de 400-800m a ritmo controlado.",
                method="CTS_Full",
                tss_estimate=tss_swim,
            )
        )

        # Long ride with race simulation blocks
        ride_zone = "Z2" if phase != "RaceSpecific" else "Race Pace"
        ride_duration = round(long_ride_h * 60, 1)
        ride_tss = _tss_from_session(ride_duration, ride_zone)
        week_sessions.append(
            _Session(
                day_offset=5,
                sport="bike",
                session_label="Long Ride / Race Sim",
                duration_min=ride_duration,
                distance_km=round(long_ride_h * _sport_speed(cfg, "bike"), 1),
                intensity_zone=ride_zone,
                key_focus="endurance" if phase != "RaceSpecific" else "race_specific",
                description="Pedal longo; últimos 60-90min em ritmo de prova durante Race Specific.",
                method="CTS_Full",
                tss_estimate=ride_tss,
            )
        )

        # Brick run after long ride
        brick_duration = 30 if phase != "RaceSpecific" else 60
        brick_zone = "Z2" if phase != "RaceSpecific" else "Z3"
        brick_tss = _tss_from_session(brick_duration, brick_zone)
        week_sessions.append(
            _Session(
                day_offset=5,
                sport="run",
                session_label="Brick Run",
                duration_min=brick_duration,
                distance_km=round(brick_duration / 60 * _sport_speed(cfg, "run"), 1),
                intensity_zone=brick_zone,
                key_focus="brick",
                description="Corrida steady após pedal; manter forma relaxada.",
                method="CTS_Full",
                tss_estimate=brick_tss,
            )
        )

        # Long run with steady finish
        run_zone_long = "Z2" if phase != "RaceSpecific" else "Z3"
        long_run_duration = round(long_run_km / _sport_speed(cfg, "run") * 60, 1)
        long_run_tss = _tss_from_session(long_run_duration, run_zone_long)
        week_sessions.append(
            _Session(
                day_offset=6,
                sport="run",
                session_label="Long Run",
                duration_min=long_run_duration,
                distance_km=round(long_run_km, 1),
                intensity_zone=run_zone_long,
                key_focus="endurance" if phase != "RaceSpecific" else "race_specific",
                description="Longão com parte final steady; não passar de Z3 baixo.",
                method="CTS_Full",
                tss_estimate=long_run_tss,
            )
        )

        # Additional maintenance sessions to spread TSS without spikes
        if cfg.bike_sessions_per_week >= 3:
            extra_bike_zone = "Z2"
            extra_bike_duration = 65
            extra_tss = _tss_from_session(extra_bike_duration, extra_bike_zone)
            week_sessions.append(
                _Session(
                    day_offset=4,
                    sport="bike",
                    session_label="Endurance Bike",
                    duration_min=extra_bike_duration,
                    distance_km=round(extra_bike_duration / 60 * _sport_speed(cfg, "bike"), 1),
                    intensity_zone=extra_bike_zone,
                    key_focus="aerobic_base",
                    description="Pedal endurance para somar carga sem intensidade alta.",
                    method="CTS_Full",
                    tss_estimate=extra_tss,
                )
            )
        if cfg.run_sessions_per_week >= 4:
            easy_run_duration = 40
            easy_run_tss = _tss_from_session(easy_run_duration, "Z2")
            week_sessions.append(
                _Session(
                    day_offset=4,
                    sport="run",
                    session_label="Easy Run",
                    duration_min=easy_run_duration,
                    distance_km=round(easy_run_duration / 60 * _sport_speed(cfg, "run"), 1),
                    intensity_zone="Z2",
                    key_focus="aerobic_base",
                    description="Rodagem leve para acumular volume sem impacto de intensidade.",
                    method="CTS_Full",
                    tss_estimate=easy_run_tss,
                )
            )

        # Cap to availability and control weekly TSS ramp
        week_sessions = _cap_sessions(week_sessions, cfg)
        week_sessions = _scale_durations_to_cap(week_sessions, week_hours[w])
        rows.extend(_session_rows(week_sessions, w, start_date))

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def gerar_plano_full(method_key: str, cfg: PlanFullConfig) -> pd.DataFrame:
    """
    method_key: "MAF_Full" | "EN_Full" | "CTS_Full"
    """
    if method_key == "MAF_Full":
        return gerar_plano_full_maf(cfg)
    elif method_key == "EN_Full":
        return gerar_plano_full_en(cfg)
    elif method_key == "CTS_Full":
        return gerar_plano_full_cts(cfg)
    else:
        raise ValueError(f"Método Ironman Full desconhecido: {method_key}")


__all__ = [
    "PlanFullConfig",
    "gerar_plano_full",
    "gerar_plano_full_maf",
    "gerar_plano_full_en",
    "gerar_plano_full_cts",
]
