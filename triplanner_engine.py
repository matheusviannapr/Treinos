"""TriPlanner training cycle engine.

This module implements the rules described in the TriPlanner specification that
was previously executed manually via chat.  It exposes helpers to build a
structured JSON payload that the Streamlit app can render directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
import math

# ---------------------------------------------------------------------------
# Constants — tables extracted from the training brief
# ---------------------------------------------------------------------------

TRAINING_TYPE_INFO = {
    "endurance": {
        "nome": "Endurance",
        "descricao": (
            "Treino leve e contínuo em Z1–Z2. Desenvolve base aeróbica, melhora "
            "capacidade de sustentar esforço e acelera recuperação."
        ),
    },
    "sweet_spot": {
        "nome": "Sweet Spot",
        "descricao": (
            "Z3 alto/Z4 baixo. O melhor equilíbrio entre esforço e retorno. "
            "Aumenta FTP e ritmo sustentável."
        ),
    },
    "tempo": {
        "nome": "Tempo / Ritmo de Prova",
        "descricao": (
            "Ritmo próximo ao da prova. Ensina o corpo a sustentar exatamente a "
            "intensidade específica do evento."
        ),
    },
    "vo2": {
        "nome": "VO₂ Máx",
        "descricao": (
            "Intervalos curtos e intensos (Z4/Z5). Alta melhora de VO₂ máx e "
            "rapidez neuromuscular."
        ),
    },
    "técnica": {
        "nome": "Técnica",
        "descricao": (
            "Foco em melhorar economia, postura, respiração e eficiência de "
            "movimento. Especialmente importante na natação."
        ),
    },
    "longo": {
        "nome": "Longo",
        "descricao": (
            "Sessão mais longa da semana na modalidade. Desenvolve resistência "
            "específica e mental."
        ),
    },
    "brick": {
        "nome": "Brick",
        "descricao": (
            "Treino combinado (geralmente bike → corrida). Crucial para triathlon, "
            "reduz ‘pernas duras’."
        ),
    },
}


TRI_METHODS = {
    "Sprint": "Polarizado 80/20",
    "Olímpico": "Sweet Spot (SST)",
    "70.3": "Piramidal",
    "Ironman": "Base Aeróbia (Z2 dominante)",
}

MODALITY_METHOD = {
    "triathlon": TRI_METHODS,
    "corrida": "Polarizado 80/20",
    "bike": "Sweet Spot (SST)",
    "natação": "Técnica + USRPT Light",
}

RUN_VOLUMES = {
    "5k": {"completar": (8, 15), "performar": (15, 25)},
    "10k": {"completar": (12, 22), "performar": (22, 32)},
    "21k": {"completar": (16, 30), "performar": (30, 45)},
    "42k": {"completar": (25, 40), "performar": (40, 55)},
}

TRI_VOLUMES = {
    "Sprint": {"completar": (37, 74), "performar": (64, 113)},
    "Olímpico": {"completar": (56.5, 103.5), "performar": (93.5, 160)},
    "70.3": {"completar": (105.8, 171.5), "performar": (151, 257)},
    "Ironman": {"completar": (179.5, 279.5), "performar": (259, 390)},
}

BIKE_VOLUMES = {"default": {"completar": (80, 150), "performar": (150, 220)}}

SWIM_VOLUMES = {"default": {"completar": (2000, 3500), "performar": (3000, 5000)}}

TRI_SPLITS = {
    "Sprint": {"Natação": 0.2, "Ciclismo": 0.45, "Corrida": 0.35},
    "Olímpico": {"Natação": 0.2, "Ciclismo": 0.5, "Corrida": 0.3},
    "70.3": {"Natação": 0.18, "Ciclismo": 0.52, "Corrida": 0.3},
    "Ironman": {"Natação": 0.17, "Ciclismo": 0.55, "Corrida": 0.28},
}

PHASE_DESCRIPTIONS = {
    "Base": "Construção de base aeróbia e técnica.",
    "Base 1": "Adaptação geral e retomada de rotina.",
    "Base 2": "Consolidação de base aeróbia e força específica.",
    "Base 3": "Base avançada com foco em volume sustentável.",
    "Build": "Elevação de intensidade e integração de sessões específicas.",
    "Peak": "Polimento com intensidade controlada e afinamento de ritmo.",
    "Taper": "Redução de volume mantendo toques intensos para chegar descansado.",
}

PHASE_FACTOR_RANGE = {
    "Base": (0.25, 0.45),
    "Base 1": (0.2, 0.35),
    "Base 2": (0.35, 0.55),
    "Base 3": (0.55, 0.7),
    "Build": (0.6, 0.9),
    "Peak": (0.85, 1.0),
    "Taper": (0.6, 0.4),  # inverso proposital: reduz volume
}

INTENSITY_PRESETS = {
    "Polarizado 80/20": {"Z1_Z2": 80, "Z3": 0, "Z4_Z5": 20},
    "Sweet Spot (SST)": {"Z2": 45, "SST": 30, "Z4_Z5": 8, "Z1": 17},
    "Piramidal": {"Z1": 45, "Z2": 30, "Z3": 15, "Z4_Z5": 10},
    "Base Aeróbia (Z2 dominante)": {"Z2": 78, "Z3": 15, "Z4": 7},
    "Técnica + USRPT Light": {
        "Técnica": 35,
        "Ritmo_de_prova": 45,
        "Leve": 20,
    },
}

SESSION_FOCUS_BY_METHOD = {
    "Polarizado 80/20": ["endurance", "vo2", "longo"],
    "Sweet Spot (SST)": ["sweet_spot", "tempo", "endurance"],
    "Piramidal": ["endurance", "tempo", "vo2", "longo"],
    "Base Aeróbia (Z2 dominante)": ["endurance", "longo", "tempo"],
    "Técnica + USRPT Light": ["técnica", "tempo", "vo2"],
}

DISTANCE_RANGES = {
    "triathlon": {
        "Sprint": {
            "completar": {
                "Natação": (1000, 2000),
                "Ciclismo": (30, 60),
                "Corrida": (6, 12),
            },
            "performar": {
                "Natação": (2000, 3000),
                "Ciclismo": (50, 90),
                "Corrida": (12, 20),
            },
        },
        "Olímpico": {
            "completar": {
                "Natação": (1500, 2500),
                "Ciclismo": (50, 90),
                "Corrida": (10, 18),
            },
            "performar": {
                "Natação": (2500, 4000),
                "Ciclismo": (90, 140),
                "Corrida": (18, 30),
            },
        },
        "70.3": {
            "completar": {
                "Natação": (2000, 3500),
                "Ciclismo": (80, 150),
                "Corrida": (15, 28),
            },
            "performar": {
                "Natação": (3000, 5000),
                "Ciclismo": (150, 220),
                "Corrida": (28, 42),
            },
        },
        "Ironman": {
            "completar": {
                "Natação": (2500, 4500),
                "Ciclismo": (120, 200),
                "Corrida": (20, 35),
            },
            "performar": {
                "Natação": (4000, 6000),
                "Ciclismo": (200, 320),
                "Corrida": (35, 55),
            },
        },
    },
    "corrida": {
        "5k": {
            "completar": {"Corrida": (6, 12)},
            "performar": {"Corrida": (12, 20)},
        },
        "10k": {
            "completar": {"Corrida": (10, 18)},
            "performar": {"Corrida": (18, 30)},
        },
        "21k": {
            "completar": {"Corrida": (15, 28)},
            "performar": {"Corrida": (28, 42)},
        },
        "42k": {
            "completar": {"Corrida": (20, 35)},
            "performar": {"Corrida": (35, 55)},
        },
    },
    "bike": {
        "default": {
            "completar": {"Ciclismo": (80, 150)},
            "performar": {"Ciclismo": (150, 220)},
        }
    },
    "natação": {
        "default": {
            "completar": {"Natação": (2000, 3500)},
            "performar": {"Natação": (3000, 5000)},
        }
    },
}


@dataclass
class PhaseSlice:
    name: str
    weeks: int
    start_week: int
    end_week: int


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_goal(goal: str) -> str:
    return "performar" if str(goal).strip().lower().startswith("per") else "completar"


def _normalize_modality(modality: str) -> str:
    mod = str(modality or "").strip().lower()
    if mod.startswith("tri"):
        return "triathlon"
    if mod.startswith("cor"):
        return "corrida"
    if mod.startswith("bi"):
        return "bike"
    if mod.startswith("na"):
        return "natação"
    return "corrida"


def _volume_unit(modality: str) -> str:
    if modality == "natação":
        return "metros"
    if modality in ("triathlon", "bike"):
        return "km"
    return "km"


def _resolve_method(modality: str, distance: str) -> str:
    method_info = MODALITY_METHOD.get(modality)
    if isinstance(method_info, dict):
        return method_info.get(distance, list(method_info.values())[0])
    return method_info


def _volume_range(modality: str, distance: str, goal: str) -> tuple[float, float]:
    goal_norm = _normalize_goal(goal)
    if modality == "corrida":
        return RUN_VOLUMES.get(distance, RUN_VOLUMES["10k"])[goal_norm]
    if modality == "triathlon":
        return TRI_VOLUMES.get(distance, TRI_VOLUMES["Olímpico"])[goal_norm]
    if modality == "bike":
        return BIKE_VOLUMES["default"][goal_norm]
    if modality == "natação":
        return SWIM_VOLUMES["default"][goal_norm]
    return (10, 20)


def _discipline_distance_ranges(modality: str, distance: str, goal: str) -> dict[str, tuple[float, float]]:
    mod = _normalize_modality(modality)
    goal_norm = _normalize_goal(goal)
    distance_key = distance if distance in DISTANCE_RANGES.get(mod, {}) else "default"
    ranges = DISTANCE_RANGES.get(mod, {}).get(distance_key, {})
    return ranges.get(goal_norm, {})


def plan_week_targets_in_distance(
    modality: str,
    distance: str,
    goal: str,
    plan_volume_min: float,
    plan_volume_max: float,
    week_volume: float,
) -> dict[str, float]:
    ranges = _discipline_distance_ranges(modality, distance, goal)
    if not ranges:
        return {}

    span = max(plan_volume_max - plan_volume_min, 1e-6)
    progress = _clamp((week_volume - plan_volume_min) / span, 0.0, 1.0)

    targets = {}
    for discipline, (vmin, vmax) in ranges.items():
        value = vmin + (vmax - vmin) * progress
        if discipline == "Natação":
            targets[discipline] = round(value / 50) * 50
        else:
            targets[discipline] = round(value, 1)
    return targets


def _phase_scheme(total_weeks: int) -> list[tuple[str, float]]:
    if total_weeks < 8:
        scheme = [("Base", 0.4), ("Build", 0.4), ("Taper", 0.2)]
    elif total_weeks <= 14:
        peak_prop = 0.15 if total_weeks >= 12 else 0.10
        scheme = [
            ("Base 1", 0.30),
            ("Base 2", 0.20),
            ("Build", 0.30),
            ("Peak", peak_prop),
            ("Taper", 0.10),
        ]
    else:
        scheme = [
            ("Base 1", 0.25 / 3),
            ("Base 2", 0.25 / 3),
            ("Base 3", 0.25 / 3),
            ("Build", 0.35),
            ("Peak", 0.15),
            ("Taper", 0.10),
        ]

    total_prop = sum(prop for _, prop in scheme)
    if total_prop <= 0:
        total_prop = 1.0
    return [(name, prop / total_prop) for name, prop in scheme]


def _allocate_phases(total_weeks: int) -> list[PhaseSlice]:
    scheme = _phase_scheme(total_weeks)
    raw = []
    remainder = total_weeks
    for name, prop in scheme:
        exact = total_weeks * prop
        base = int(math.floor(exact))
        raw.append([name, base, exact - base])
        remainder -= base

    # Distribui semanas restantes para as maiores frações
    raw.sort(key=lambda item: item[2], reverse=True)
    idx = 0
    while remainder > 0 and raw:
        raw[idx % len(raw)][1] += 1
        remainder -= 1
        idx += 1

    # Garante que nenhuma fase tenha zero semana
    for item in raw:
        if item[1] <= 0:
            item[1] = 1

    # Ordena na sequência original do esquema
    order = {name: i for i, (name, _) in enumerate(scheme)}
    raw.sort(key=lambda item: order[item[0]])

    phases: list[PhaseSlice] = []
    cursor = 1
    for name, weeks, _fraction in raw:
        end = cursor + weeks - 1
        phases.append(PhaseSlice(name=name, weeks=weeks, start_week=cursor, end_week=end))
        cursor = end + 1
    if phases:
        phases[-1].end_week = total_weeks
    return phases


def _phase_factor(phase_name: str, week_index: int, total_weeks: int) -> float:
    start, end = PHASE_FACTOR_RANGE.get(phase_name, (0.3, 0.6))
    if total_weeks <= 1:
        return end
    progress = week_index / max(total_weeks - 1, 1)
    if start < end:
        return start + (end - start) * progress
    # taper — reduz conforme avança
    return end + (start - end) * (1 - progress)


def _apply_three_one(volumes: list[float]) -> list[float]:
    adjusted = []
    for idx, vol in enumerate(volumes, start=1):
        if idx % 4 == 0:
            prev = adjusted[-1] if adjusted else vol
            adjusted.append(prev * 0.7)
        else:
            adjusted.append(vol)
    return adjusted


def _build_volume_curve(total_weeks: int, vol_min: float, vol_max: float, phases: list[PhaseSlice]) -> list[float]:
    if total_weeks <= 0:
        return []
    span = max(vol_max - vol_min, vol_min * 0.25)
    week_volumes: list[float] = []
    for phase in phases:
        for offset in range(phase.weeks):
            factor = _phase_factor(phase.name, offset, phase.weeks)
            target = vol_min + span * _clamp(factor, 0.0, 1.2)
            week_volumes.append(target)

    week_volumes = week_volumes[:total_weeks]
    progressed: list[float] = []
    for idx, target in enumerate(week_volumes):
        if idx == 0:
            progressed.append(vol_min)
            continue
        previous = progressed[-1]
        if (idx + 1) % 4 == 0:
            progressed.append(max(vol_min * 0.7, previous * 0.7))
            continue
        growth = previous * 0.07
        new_val = min(previous + growth, previous * 1.1, target)
        new_val = max(new_val, previous * 1.05)
        progressed.append(min(new_val, vol_max))

    return progressed[:total_weeks]


def _intensity_for_week(method: str, is_recovery: bool, phase_name: str) -> dict[str, float]:
    base = INTENSITY_PRESETS.get(method, {"Z1_Z2": 70, "Z4_Z5": 30}).copy()
    if is_recovery:
        for key in list(base.keys()):
            if "Z4" in key or "Z5" in key or key in {"SST", "Ritmo_de_prova"}:
                base[key] = max(0, base[key] - 5)
        low_key = next(iter(base.keys()))
        base[low_key] = base.get(low_key, 0) + 5
    elif phase_name == "Peak" and "Z4" in "".join(base.keys()):
        for key in list(base.keys()):
            if "Z4" in key or "Z5" in key:
                base[key] = max(0, base[key] - 3)
        key = next(iter(base.keys()))
        base[key] = base.get(key, 0) + 3
    return base


def _week_focus(modality: str, method: str, is_recovery: bool, week_number: int) -> list[str]:
    base_focus = SESSION_FOCUS_BY_METHOD.get(method, ["endurance", "tempo"])
    focus = list(base_focus)
    if modality == "triathlon" and week_number % 2 == 0:
        if "brick" not in focus:
            focus.append("brick")
    if is_recovery:
        focus = [tag for tag in focus if tag not in {"vo2", "brick"}]
        if "técnica" not in focus:
            focus.append("técnica")
    return focus[:4]


def _volume_split(modality: str, distance: str, total_volume: float) -> dict[str, float]:
    if modality == "triathlon":
        split = TRI_SPLITS.get(distance, TRI_SPLITS["Olímpico"])
        return {
            discipline: round(total_volume * ratio, 2)
            for discipline, ratio in split.items()
        }
    if modality == "corrida":
        return {"Corrida": round(total_volume, 1)}
    if modality == "bike":
        return {"Ciclismo": round(total_volume, 2)}
    if modality == "natação":
        return {"Natação": round(total_volume, 2)}
    return {"Treino": round(total_volume, 2)}


def build_triplanner_plan(
    modality: str,
    distance: str,
    goal: str,
    cycle_weeks: int,
    start_date: date,
    notes: str | None = None,
) -> dict:
    modality_norm = _normalize_modality(modality)
    goal_norm = _normalize_goal(goal)
    cycle_weeks = int(cycle_weeks)
    unit = _volume_unit(modality_norm)
    method = _resolve_method(modality_norm, distance)
    vol_min, vol_max = _volume_range(modality_norm, distance, goal_norm)
    phases = _allocate_phases(cycle_weeks)
    week_volumes = _build_volume_curve(cycle_weeks, vol_min, vol_max, phases)
    week_volumes = _apply_three_one(week_volumes)

    weeks_payload = []
    for idx in range(cycle_weeks):
        phase = next((p for p in phases if p.start_week <= idx + 1 <= p.end_week), phases[-1])
        week_start = start_date + timedelta(days=7 * idx)
        is_recovery = (idx + 1) % 4 == 0
        intensity = _intensity_for_week(method, is_recovery, phase.name)
        focus = _week_focus(modality_norm, method, is_recovery, idx + 1)
        volume_total = round(week_volumes[idx], 2)
        volume_modalidades = plan_week_targets_in_distance(
            modality_norm,
            distance,
            goal_norm,
            vol_min,
            vol_max,
            volume_total,
        ) or _volume_split(modality_norm, distance, volume_total)
        weeks_payload.append(
            {
                "semana": idx + 1,
                "inicio": week_start.isoformat(),
                "fase": phase.name,
                "fase_objetivo": PHASE_DESCRIPTIONS.get(phase.name, ""),
                "status": "recuperação" if is_recovery else "carga",
                "volume_total": volume_total,
                "unidade": unit,
                "volume_por_modalidade": volume_modalidades,
                "intensidade": intensity,
                "focos_da_semana": focus,
            }
        )

    plan = {
        "modalidade": modality_norm,
        "distancia": distance,
        "objetivo": goal_norm,
        "metodo": method,
        "unidade_volume": unit,
        "ciclo": {
            "semanas": cycle_weeks,
            "inicio": start_date.isoformat(),
            "fases": [
                {
                    "nome": phase.name,
                    "semanas": phase.weeks,
                    "inicio_semana": phase.start_week,
                    "fim_semana": phase.end_week,
                    "descricao": PHASE_DESCRIPTIONS.get(phase.name, ""),
                }
                for phase in phases
            ],
        },
        "volume_estimado": {
            "min": vol_min,
            "max": vol_max,
        },
        "intensidade_base": INTENSITY_PRESETS.get(method, {}),
        "semanas": weeks_payload,
        "tipos_de_treino": TRAINING_TYPE_INFO,
        "observacoes": notes or "",
    }
    return plan


def plan_to_json(plan: dict) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)


def compute_weeks_from_date(event_date: date, start_date: date) -> int:
    delta_days = (event_date - start_date).days
    weeks = math.ceil(delta_days / 7)
    return max(1, weeks)


def required_weeks_message() -> str:
    return "Deseja informar o número de semanas de preparação ou a data da prova?"


__all__ = [
    "TRAINING_TYPE_INFO",
    "build_triplanner_plan",
    "plan_week_targets_in_distance",
    "plan_to_json",
    "compute_weeks_from_date",
    "required_weeks_message",
]

