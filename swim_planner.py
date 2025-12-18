"""Swim plan generation for TriPlanner."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
from typing import List

import pandas as pd


@dataclass
class PlanSwimConfig:
    start_date: date
    race_date: date

    athlete_level: str
    goal_distance: str
    pool_length_m: int

    sessions_per_week: int
    available_km_per_week: float
    current_km_per_week: float

    t200_sec: int | None = None
    t400_sec: int | None = None

    prefer_openwater: bool = False
    includes_strength: bool = False

    max_weekly_increase_pct: float = 0.10
    deload_every_n_weeks: int = 4
    taper_weeks: int = 2
    css_pace_sec_per_100: float | None = None


def calc_css_sec_per_100(t200_sec: int, t400_sec: int) -> float:
    """Calcula CSS (ritmo/100m) usando teste 200/400m."""

    return (t400_sec - t200_sec) / 2.0


def _format_pace_min_per_100(css_sec: float | None) -> str:
    if not css_sec:
        return ""
    minutes = int(css_sec // 60)
    seconds = int(round(css_sec % 60))
    return f"{minutes}:{seconds:02d}/100m"


def _css_from_cfg(cfg: PlanSwimConfig) -> float | None:
    if cfg.css_pace_sec_per_100:
        return float(cfg.css_pace_sec_per_100)
    if cfg.t200_sec and cfg.t400_sec:
        return calc_css_sec_per_100(cfg.t200_sec, cfg.t400_sec)
    return None


def _total_weeks(cfg: PlanSwimConfig) -> int:
    delta_days = (cfg.race_date - cfg.start_date).days
    if delta_days < 0:
        return 1
    return math.ceil(delta_days / 7) + 1


def _volume_progression(cfg: PlanSwimConfig, total_weeks: int) -> list[float]:
    volumes: list[float] = []
    current = max(cfg.current_km_per_week, 1.0)
    current = min(current, cfg.available_km_per_week)
    for wk in range(total_weeks):
        is_taper = wk >= total_weeks - cfg.taper_weeks
        if is_taper:
            base_ref = volumes[-1] if volumes else current
            reduction = 0.6 if wk == total_weeks - cfg.taper_weeks else 0.5
            target = max(1.0, base_ref * reduction)
        elif cfg.deload_every_n_weeks and (wk + 1) % cfg.deload_every_n_weeks == 0:
            prev = volumes[-1] if volumes else current
            target = max(1.0, prev * 0.7)
        else:
            prev = volumes[-1] if volumes else current
            target = prev * (1 + cfg.max_weekly_increase_pct)
        target = min(target, cfg.available_km_per_week)
        volumes.append(round(target, 2))
    return volumes


def _distribute_volume(total_km: float, sessions: int) -> list[float]:
    if sessions <= 0:
        return []
    if sessions == 2:
        fractions = [0.35, 0.65]
    elif sessions == 3:
        fractions = [0.25, 0.35, 0.4]
    elif sessions == 4:
        fractions = [0.2, 0.25, 0.25, 0.3]
    elif sessions == 5:
        fractions = [0.15, 0.2, 0.2, 0.2, 0.25]
    else:
        fractions = [0.15, 0.2, 0.2, 0.15, 0.15, 0.15][:sessions]
    scale = total_km / sum(fractions)
    return [round(f * scale, 3) for f in fractions]


def _schedule_days(sessions: int) -> List[int]:
    if sessions <= 2:
        return [1, 5][:sessions]
    if sessions == 3:
        return [1, 3, 5]
    if sessions == 4:
        return [0, 2, 4, 6]
    days = [0, 1, 3, 5, 6]
    if sessions == 6:
        days.append(4)
    return days[:sessions]


def warmup_easy(meters: int) -> str:
    return f"Aquecimento: {meters}m leve (200 crawl, 100 costas, 4x50 progressivo)."


def drills_block(meters: int) -> str:
    return f"Técnica: {meters}m com 25m drill + 25m nado (pulo, polegar na axila, respiração bilateral)."


def mainset_css_intervals(level: str, css_pace: str) -> str:
    if level == "iniciante":
        return f"Série principal: 12-16x50 @ ritmo CSS ({css_pace}) com 15-25s descanso."
    if level == "avancado":
        return f"Série principal: 6-8x200 @ CSS ({css_pace}) com 20-30s descanso."
    return f"Série principal: 10x100 @ CSS ({css_pace}) com 15-20s descanso."


def mainset_endurance_ladders(total_m: int, css_hint: str | None = None) -> str:
    hint = f" ritmo {css_hint}" if css_hint else " ritmo Z2/Z3"
    return (
        f"Série principal: ladder 100-200-300-400-300-200-100 em{hint}, focando braçada longa."
    )


def mainset_threshold_100s(reps: int = 10, rest_s: int = 20, css_pace: str | None = None) -> str:
    target = f"@CSS/threshold ({css_pace})" if css_pace else "Z4"
    return f"Série principal: {reps}x100 {target} com {rest_s}s descanso."


def openwater_continuous(distance_m: int, include_surges: bool = True) -> str:
    surge_txt = " + 20s forte a cada 10'" if include_surges else ""
    return (
        f"Contínuo: 1x{distance_m}m ritmo steady, a cada 4' 10 braçadas com cabeça alta (sighting){surge_txt}."
    )


def cooldown_easy(meters: int) -> str:
    return f"Soltar: {meters}m bem leve (nado livre + costas)."


def _estimate_duration(distance_km: float, css_sec: float | None) -> float | None:
    if not css_sec or distance_km <= 0:
        return None
    total_sec = (distance_km * 1000 / 100.0) * css_sec
    return round(total_sec / 60.0, 1)


def _goal_peak_distance(goal_distance: str) -> int:
    mapping = {
        "1500m": 1500,
        "3km": 2400,
        "5km": 3200,
        "10km": 4200,
        "Ironman": 3800,
    }
    return mapping.get(goal_distance, 3000)


def _phase_css(week_idx: int, total_weeks: int, taper_weeks: int) -> str:
    train_weeks = max(total_weeks - taper_weeks, 1)
    base_weeks = max(1, int(train_weeks * 0.55))
    build_weeks = max(1, int(train_weeks * 0.3))
    peak_start = base_weeks + build_weeks
    if week_idx >= total_weeks - taper_weeks:
        return "Taper"
    if week_idx < base_weeks:
        return "Base"
    if week_idx < peak_start:
        return "Build"
    return "Peak"


def _long_main_distance_for_week(cfg: PlanSwimConfig, weekly_km: float, phase: str) -> int:
    goal_peak = _goal_peak_distance(cfg.goal_distance)
    weekly_m = weekly_km * 1000
    cap = weekly_m * 0.75
    if phase == "Peak":
        target = max(goal_peak * 0.65, cap)
    elif phase == "Build":
        target = min(goal_peak * 0.8, cap)
    else:
        target = min(goal_peak * 0.6, cap)
    return int(min(target, goal_peak * 1.05))


def _css_session_plan(sessions: int, phase: str) -> List[str]:
    if sessions == 2:
        return ["technique", "css_long"]
    if sessions == 3:
        return ["technique", "css_interval", "endurance"]
    plan = ["technique", "css_interval", "endurance", "threshold"]
    if sessions >= 5:
        plan.append("recovery")
    if phase == "Peak" and "threshold" not in plan:
        plan.append("threshold")
    return plan[:sessions]


def _base_session_plan(sessions: int, week_idx: int) -> List[str]:
    plan = ["technique", "endurance", "aerobic"]
    if sessions == 2:
        return ["technique", "endurance"]
    if sessions == 3:
        return plan
    plan.append("long_continuous")
    if sessions >= 5:
        plan.append("recovery")
    if sessions >= 6:
        plan.append("technique2")
    return plan[:sessions]


def _polarized_plan(sessions: int, phase: str, level: str) -> List[str]:
    hard_blocks = 1 if sessions <= 3 else 1
    if sessions >= 4 and level == "avancado" and phase in {"Build", "Peak"}:
        hard_blocks = 2
    easy_blocks = sessions - hard_blocks
    plan = ["easy"] * easy_blocks + ["hard"] * hard_blocks
    return plan


def _openwater_plan(sessions: int) -> List[str]:
    if sessions == 2:
        return ["skills", "ow_long"]
    if sessions == 3:
        return ["skills", "ow_long", "tempo"]
    plan = ["skills", "ow_long", "tempo", "start_set"]
    if sessions >= 5:
        plan.append("recovery")
    return plan[:sessions]


def _session_entry(
    week: int,
    date_val: date,
    session_label: str,
    distance_km: float,
    intensity: str,
    key_focus: str,
    description: str,
    method: str,
    css_sec: float | None,
) -> dict:
    return {
        "week": week,
        "date": date_val,
        "day_name": date_val.strftime("%A"),
        "sport": "swim",
        "session_label": session_label,
        "distance_km": round(distance_km, 3),
        "duration_min": _estimate_duration(distance_km, css_sec),
        "intensity_zone": intensity,
        "key_focus": key_focus,
        "description": description,
        "method": method,
    }


def _session_distance_km(total_meters: int) -> float:
    """Converts total meters to kilometers rounded to 3 decimals."""

    return round(total_meters / 1000.0, 3)


def gerar_plano_swim_css(cfg: PlanSwimConfig) -> pd.DataFrame:
    css_sec = _css_from_cfg(cfg)
    css_pace = _format_pace_min_per_100(css_sec)
    total_weeks = _total_weeks(cfg)
    volumes = _volume_progression(cfg, total_weeks)
    days_template = _schedule_days(cfg.sessions_per_week)
    rows = []
    for wk in range(total_weeks):
        week_num = wk + 1
        phase = _phase_css(wk, total_weeks, cfg.taper_weeks)
        weekly_km = volumes[wk]
        dist_per_session = _distribute_volume(weekly_km, cfg.sessions_per_week)
        long_main_m = _long_main_distance_for_week(cfg, weekly_km, phase)
        session_plan = _css_session_plan(cfg.sessions_per_week, phase)
        week_start = cfg.start_date + timedelta(days=wk * 7)

        for idx, sess_type in enumerate(session_plan):
            day_offset = days_template[idx % len(days_template)]
            session_date = week_start + timedelta(days=day_offset)
            distance_km = dist_per_session[idx] if idx < len(dist_per_session) else weekly_km / len(session_plan)
            warmup_m = 400
            cooldown_m = 200
            drills_m = 0
            main_m = 0
            description_parts = [warmup_easy(warmup_m)]
            intensity = "Z2"
            focus = "aerobic_base"
            label = "Técnica"
            if sess_type == "technique":
                drills_m = 400
                main_m = max(int(distance_km * 1000 * 0.6), 800)
                description_parts.append(drills_block(drills_m))
                description_parts.append(mainset_endurance_ladders(main_m, css_pace))
                label = "Technique"
                intensity = "Z1"
                focus = "technique"
            elif sess_type == "css_interval":
                drills_m = 200
                if cfg.athlete_level == "iniciante":
                    main_m = 14 * 50
                elif cfg.athlete_level == "avancado":
                    main_m = 7 * 200
                else:
                    main_m = 10 * 100
                description_parts.append(drills_block(drills_m))
                description_parts.append(mainset_css_intervals(cfg.athlete_level, css_pace or "CSS"))
                label = "CSS Main Set"
                intensity = "CSS"
                focus = "css"
            elif sess_type == "endurance":
                main_m = max(int(distance_km * 1000 * 0.7), 800)
                description_parts.append(mainset_endurance_ladders(main_m, css_pace))
                label = "Endurance"
                intensity = "Z2"
                focus = "aerobic_base"
            elif sess_type == "css_long":
                main_m = max(long_main_m, int(distance_km * 1000 * 0.8))
                description_parts.append(openwater_continuous(main_m, include_surges=False))
                label = "CSS Long"
                intensity = "Z3"
                focus = "css"
            elif sess_type == "threshold":
                main_m = 10 * 100
                description_parts.append(mainset_threshold_100s(10, 20, css_pace))
                label = "Threshold"
                intensity = "Z4"
                focus = "threshold"
            elif sess_type == "recovery":
                drills_m = 200
                main_m = 8 * 50
                description_parts.append(drills_block(drills_m))
                description_parts.append("Série principal: 8x50 Z1/Z2 concentrando na técnica.")
                label = "Recovery/Skills"
                intensity = "Z1"
                focus = "recovery"
            if main_m == 0:
                main_m = max(int(distance_km * 1000 * 0.7), 600)
            total_m = warmup_m + drills_m + main_m + cooldown_m
            distance_km = _session_distance_km(total_m)
            description_parts.append(cooldown_easy(cooldown_m))
            description_parts.append("Nota técnica: alongar braçada, manter cotovelo alto.")
            desc = " ".join(description_parts)
            rows.append(
                _session_entry(
                    week_num,
                    session_date,
                    label,
                    distance_km,
                    intensity,
                    focus,
                    desc,
                    "CSS_Endurance",
                    css_sec,
                )
            )
    df = pd.DataFrame(rows)
    return df


def gerar_plano_swim_base(cfg: PlanSwimConfig) -> pd.DataFrame:
    css_sec = _css_from_cfg(cfg)
    css_pace = _format_pace_min_per_100(css_sec)
    total_weeks = _total_weeks(cfg)
    volumes = _volume_progression(cfg, total_weeks)
    days_template = _schedule_days(cfg.sessions_per_week)
    rows = []
    for wk in range(total_weeks):
        week_num = wk + 1
        weekly_km = volumes[wk]
        session_plan = _base_session_plan(cfg.sessions_per_week, wk)
        dist_per_session = _distribute_volume(weekly_km, len(session_plan))
        week_start = cfg.start_date + timedelta(days=wk * 7)
        include_z3 = wk % 2 == 1 and len(session_plan) >= 3 and wk < total_weeks - cfg.taper_weeks
        for idx, sess_type in enumerate(session_plan):
            day_offset = days_template[idx % len(days_template)]
            session_date = week_start + timedelta(days=day_offset)
            distance_km = dist_per_session[idx]
            warmup_m = 300
            cooldown_m = 200
            drills_m = 0
            main_m = 0
            description_parts = [warmup_easy(warmup_m)]
            intensity = "Z2"
            focus = "aerobic_base"
            label = "Base"
            if sess_type.startswith("technique"):
                drills_m = 500
                main_m = 12 * 50 + 8 * 25
                description_parts.append(drills_block(drills_m))
                description_parts.append("Série principal: 12x50 (25 drill/25 nado) + 8x25 scull, respiração bilateral.")
                intensity = "Z1"
                focus = "technique"
                label = "Technique"
            elif sess_type == "endurance":
                main_m = max(int(distance_km * 1000 * 0.8), 1200)
                description_parts.append(f"Contínuo: 1x{main_m}m Z2 com respiração controlada.")
                label = "Endurance"
            elif sess_type == "aerobic":
                main_m = 8 * 200
                description_parts.append("Série principal: 8x200 Z2 (20s) mantendo braçada estável.")
                label = "Aeróbio Intervalado"
            elif sess_type == "long_continuous":
                main_m = max(int(distance_km * 1000 * 0.85), 2000)
                description_parts.append(f"Contínuo longo: 1x{main_m}m Z2, foco em deslize.")
                label = "Contínuo Longo"
            elif sess_type == "recovery":
                main_m = 6 * 100
                description_parts.append("Série principal: 6x100 Z1/Z2, saindo a cada 2'30\".")
                intensity = "Z1"
                focus = "recovery"
                label = "Recuperação"
            if include_z3 and sess_type == "aerobic":
                description_parts[-1] = "Série principal: 8x100 Z3 (20s) a cada 2 semanas para estímulo leve."
                intensity = "Z3"
                focus = "threshold"
            if main_m == 0:
                main_m = max(int(distance_km * 1000 * 0.75), 1000)
            total_m = warmup_m + drills_m + main_m + cooldown_m
            distance_km = _session_distance_km(total_m)
            description_parts.append(cooldown_easy(cooldown_m))
            description_parts.append("Nota técnica: manter linha hidrodinâmica e respiração suave.")
            rows.append(
                _session_entry(
                    week_num,
                    session_date,
                    label,
                    distance_km,
                    intensity,
                    focus,
                    " ".join(description_parts),
                    "Base_Technique",
                    css_sec,
                )
            )
    return pd.DataFrame(rows)


def gerar_plano_swim_polarized(cfg: PlanSwimConfig) -> pd.DataFrame:
    css_sec = _css_from_cfg(cfg)
    css_pace = _format_pace_min_per_100(css_sec)
    total_weeks = _total_weeks(cfg)
    volumes = _volume_progression(cfg, total_weeks)
    days_template = _schedule_days(cfg.sessions_per_week)
    rows = []
    for wk in range(total_weeks):
        week_num = wk + 1
        phase = _phase_css(wk, total_weeks, cfg.taper_weeks)
        weekly_km = volumes[wk]
        session_plan = _polarized_plan(cfg.sessions_per_week, phase, cfg.athlete_level)
        dist_per_session = _distribute_volume(weekly_km, len(session_plan))
        week_start = cfg.start_date + timedelta(days=wk * 7)
        for idx, sess_type in enumerate(session_plan):
            day_offset = days_template[idx % len(days_template)]
            session_date = week_start + timedelta(days=day_offset)
            distance_km = dist_per_session[idx]
            warmup_m = 300
            cooldown_m = 200
            drills_m = 0
            main_m = 0
            description_parts = [warmup_easy(warmup_m)]
            if sess_type == "hard":
                if wk >= total_weeks - cfg.taper_weeks:
                    main_m = 6 * 50
                    description_parts.append("Série principal: 6x50 forte (15s) para acordar, sem acumular fadiga.")
                    intensity = "Z4"
                    focus = "threshold"
                    label = "Prime"
                elif wk % 2 == 0:
                    main_m = 12 * 100
                    description_parts.append(mainset_threshold_100s(12, 20, css_pace))
                    intensity = "Z4"
                    focus = "threshold"
                    label = "Threshold"
                else:
                    main_m = 20 * 50
                    description_parts.append("Série principal: 20x50 Z5 (15-20s) mantendo técnica sob alta cadência.")
                    intensity = "Z5"
                    focus = "threshold"
                    label = "VO2"
            else:
                drills_m = 300
                main_m = max(int(distance_km * 1000 * 0.7), 1000)
                description_parts.append(drills_block(drills_m))
                description_parts.append(mainset_endurance_ladders(main_m, css_pace))
                intensity = "Z2"
                focus = "aerobic_base"
                label = "Easy/Drills"
            if main_m == 0:
                main_m = max(int(distance_km * 1000 * 0.75), 800)
            total_m = warmup_m + drills_m + main_m + cooldown_m
            distance_km = _session_distance_km(total_m)
            description_parts.append(cooldown_easy(cooldown_m))
            description_parts.append("Nota técnica: manter cotovelo alto mesmo em tiros fortes.")
            rows.append(
                _session_entry(
                    week_num,
                    session_date,
                    label,
                    distance_km,
                    intensity,
                    focus,
                    " ".join(description_parts),
                    "Polarized_8020",
                    css_sec,
                )
            )
    return pd.DataFrame(rows)


def gerar_plano_swim_openwater(cfg: PlanSwimConfig) -> pd.DataFrame:
    css_sec = _css_from_cfg(cfg)
    css_pace = _format_pace_min_per_100(css_sec)
    total_weeks = _total_weeks(cfg)
    volumes = _volume_progression(cfg, total_weeks)
    days_template = _schedule_days(cfg.sessions_per_week)
    rows = []
    for wk in range(total_weeks):
        week_num = wk + 1
        phase = _phase_css(wk, total_weeks, cfg.taper_weeks)
        weekly_km = volumes[wk]
        session_plan = _openwater_plan(cfg.sessions_per_week)
        dist_per_session = _distribute_volume(weekly_km, len(session_plan))
        week_start = cfg.start_date + timedelta(days=wk * 7)
        for idx, sess_type in enumerate(session_plan):
            day_offset = days_template[idx % len(days_template)]
            session_date = week_start + timedelta(days=day_offset)
            distance_km = dist_per_session[idx]
            warmup_m = 400
            cooldown_m = 200
            drills_m = 0
            main_m = 0
            description_parts = [warmup_easy(warmup_m)]
            intensity = "Z2"
            focus = "openwater"
            label = "Open Water Skills"
            if sess_type == "skills":
                drills_m = 300
                main_m = 10 * 100
                description_parts.append(drills_block(drills_m))
                description_parts.append("Série principal: 10x100 sem pegar parede, respiração bilateral, sighting a cada 8 braçadas.")
            elif sess_type == "ow_long":
                main_m = max(int(distance_km * 1000 * 0.85), 2000)
                description_parts.append(openwater_continuous(main_m, include_surges=True))
                label = "Endurance Continuous"
                focus = "race_specific"
            elif sess_type == "tempo":
                main_m = 3 * 1000
                description_parts.append(f"Série principal: 3x1000 (45s) ritmo de prova ({css_pace or 'Z3'}), sighting no último 200.")
                label = "Race Tempo"
                focus = "race_specific"
            elif sess_type == "start_set":
                main_m = 10 * 50 + 1500
                description_parts.append("Largada: 10x50 forte (20s) + 1x1500 steady simulando prova com poucas viradas.")
                label = "Start + Settle"
                intensity = "Z3"
                focus = "race_specific"
            elif sess_type == "recovery":
                main_m = 8 * 75
                description_parts.append("Série principal: 8x75 Z1/Z2 com 15s, focar navegação suave.")
                label = "Recovery"
                intensity = "Z1"
                focus = "recovery"
            if main_m == 0:
                main_m = max(int(distance_km * 1000 * 0.8), 1200)
            total_m = warmup_m + drills_m + main_m + cooldown_m
            distance_km = _session_distance_km(total_m)
            description_parts.append(cooldown_easy(cooldown_m))
            description_parts.append("Nota técnica: simular águas abertas (sighting, sem parede).")
            rows.append(
                _session_entry(
                    week_num,
                    session_date,
                    label,
                    distance_km,
                    intensity,
                    focus,
                    " ".join(description_parts),
                    "OpenWater_Specific",
                    css_sec,
                )
            )
    return pd.DataFrame(rows)


def gerar_plano_swim(method_key: str, cfg: PlanSwimConfig) -> pd.DataFrame:
    if method_key == "CSS_Endurance":
        return gerar_plano_swim_css(cfg)
    if method_key == "Base_Technique":
        return gerar_plano_swim_base(cfg)
    if method_key == "Polarized_8020":
        return gerar_plano_swim_polarized(cfg)
    if method_key == "OpenWater_Specific":
        return gerar_plano_swim_openwater(cfg)
    raise ValueError(f"Método natação desconhecido: {method_key}")
