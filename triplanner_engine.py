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
import re

# ---------------------------------------------------------------------------
# Constants — tables extracted from the training brief
# ---------------------------------------------------------------------------

BASE_TRAINING_TYPE_INFO = {
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

RUN_TRAINING_TYPE_INFO = {
    "rodagem_regenerativa": {
        "nome": "Rodagem regenerativa",
        "zona": "Z1–Z2",
        "descricao": (
            "Corrida muito leve pós-longão ou após treinos fortes para recuperar "
            "mantendo mobilidade e fluxo sanguíneo."
        ),
    },
    "corrida_continua_leve": {
        "nome": "Corrida contínua leve",
        "zona": "Z2",
        "descricao": "Rodagem aeróbica confortável que compõe a base do volume semanal.",
    },
    "corrida_continua_moderada": {
        "nome": "Corrida contínua moderada",
        "zona": "Z3",
        "descricao": "Z3 controlado, próximo ao limiar inferior, para ritmo sustentável.",
    },
    "tempo_run": {
        "nome": "Tempo Run (limiar)",
        "zona": "Z3–Z4",
        "descricao": "Segmento contínuo de 20–30 min em ritmo de limiar funcional.",
    },
    "fartlek": {
        "nome": "Fartlek",
        "zona": "Z3–Z4",
        "descricao": "Blocos alternados forte/leve sem pace fixo para variar estímulos.",
    },
    "intervalado_vo2max": {
        "nome": "Intervalado (VO₂máx)",
        "zona": "Z4–Z5",
        "descricao": "Séries curtas ou médias acima do limiar para elevar VO₂máx.",
    },
    "longao": {
        "nome": "Longão",
        "zona": "Z2",
        "descricao": "Sessão mais longa da semana, foco em endurance e confiança.",
    },
    "educativos_tecnicos": {
        "nome": "Educativos técnicos",
        "zona": "Neutro",
        "descricao": "Drills de coordenação (skipping, avanços, elevação de joelho).",
    },
    "prova": {
        "nome": "Prova",
        "zona": "Z3–Z4",
        "descricao": "Evento alvo. Execute o aquecimento e corra no plano de prova.",
    },
}

TRAINING_TYPE_INFO = {**BASE_TRAINING_TYPE_INFO, **RUN_TRAINING_TYPE_INFO}


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
    "21k": {"completar": (30, 55), "performar": (45, 75)},
    "42k": {"completar": (45, 70), "performar": (65, 95)},
}

RUN_PHASE_VOLUME_TABLE = {
    "5k": {
        "iniciante": {
            "Base": (6, 10),
            "Construção": (8, 12),
            "Específica": (10, 12),
            "Taper": (4, 8),
        },
        "intermediario": {
            "Base": (18, 25),
            "Construção": (22, 30),
            "Específica": (25, 32),
            "Taper": (15, 20),
        },
        "avancado": {
            "Base": (25, 35),
            "Construção": (30, 40),
            "Específica": (35, 45),
            "Taper": (20, 30),
        },
    },
    "10k": {
        "iniciante": {
            "Base": (8, 12),
            "Construção": (12, 16),
            "Específica": (16, 25),
            "Taper": (12, 20),
        },
        "intermediario": {
            "Base": (25, 35),
            "Construção": (30, 40),
            "Específica": (35, 45),
            "Taper": (20, 28),
        },
        "avancado": {
            "Base": (35, 45),
            "Construção": (40, 55),
            "Específica": (45, 60),
            "Taper": (30, 40),
        },
    },
    "21k": {
        "iniciante": {
            "Base": (17, 24),
            "Construção": (25, 34),
            "Específica": (36, 40),
            "Taper": (18, 24),
        },
        "intermediario": {
            "Base": (30, 40),
            "Construção": (40, 50),
            "Específica": (45, 55),
            "Taper": (30, 40),
        },
        "avancado": {
            "Base": (40, 55),
            "Construção": (55, 70),
            "Específica": (60, 75),
            "Taper": (40, 50),
        },
    },
    "42k": {
        "iniciante": {
            "Base": (32, 45),
            "Construção": (45, 60),
            "Específica": (55, 70),
            "Taper": (38, 48),
        },
        "intermediario": {
            "Base": (40, 50),
            "Construção": (50, 65),
            "Específica": (55, 70),
            "Taper": (35, 45),
        },
        "avancado": {
            "Base": (55, 65),
            "Construção": (65, 80),
            "Específica": (70, 90),
            "Taper": (45, 60),
        },
    },
}

RUN_ZONE_DISTRIBUTION = {
    "5k": {
        "completar": {"Z1_Z2": 80, "Z3": 15, "Z4_Z5": 5},
        "performar": {"Z1_Z2": 60, "Z3": 25, "Z4_Z5": 15},
    },
    "10k": {
        "completar": {"Z1_Z2": 75, "Z3": 20, "Z4_Z5": 5},
        "performar": {"Z1_Z2": 65, "Z3": 25, "Z4_Z5": 10},
    },
    "21k": {
        "completar": {"Z1_Z2": 75, "Z3": 25, "Z4_Z5": 0},
        "performar": {"Z1_Z2": 65, "Z3": 30, "Z4_Z5": 5},
    },
    "42k": {
        "completar": {"Z1_Z2": 85, "Z3": 15, "Z4_Z5": 0},
        "performar": {"Z1_Z2": 75, "Z3": 20, "Z4_Z5": 5},
    },
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


def _distance_to_km(distance: str) -> float:
    mapping = {"5k": 5.0, "10k": 10.0, "21k": 21.097, "42k": 42.195}
    try:
        return mapping[str(distance).lower()]
    except KeyError:
        try:
            return float(str(distance).lower().replace("k", ""))
        except Exception:
            return 10.0


def _time_str_to_seconds(time_str: str | None) -> float | None:
    if not time_str:
        return None
    t = str(time_str).strip().lower()
    t = t.replace("/km", "").replace("km", "")
    t = (
        t.replace("\"", "")
        .replace("'", ":")
        .replace("’", ":")
        .replace("min", ":")
    )
    if "h" in t:
        parts = t.replace("h", ":").replace("m", ":").replace("s", "").split(":")
    else:
        parts = t.replace("s", "").split(":")
    parts = [p for p in parts if p != ""]
    try:
        parts_f = list(map(float, parts))
    except ValueError:
        return None
    if len(parts_f) == 1:
        return parts_f[0]
    if len(parts_f) == 2:
        minutes, seconds = parts_f
        return minutes * 60 + seconds
    hours, minutes, seconds = (parts_f + [0, 0, 0])[:3]
    return hours * 3600 + minutes * 60 + seconds


def _pace_str_to_seconds(pace_str: str | None) -> float | None:
    secs = _time_str_to_seconds(pace_str)
    if secs is None:
        return None
    # pace é informado como min/km -> já em segundos por km
    return secs


def _derive_current_pace_seconds(
    tempo_recente: str | None, distance: str, pace_medio: str | None
) -> float:
    distance_km = _distance_to_km(distance)
    tempo_secs = _time_str_to_seconds(tempo_recente)
    if tempo_secs and distance_km > 0:
        return tempo_secs / distance_km
    pace_secs = _pace_str_to_seconds(pace_medio)
    if pace_secs:
        return pace_secs
    return 360.0  # padrão ~6:00/km


def _format_pace(seconds: float) -> str:
    seconds = max(1, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    return f"{int(minutes):02d}:{int(sec):02d}/km"


def _primary_pace_token(pace_str: str | None) -> str | None:
    if not pace_str:
        return None
    text = str(pace_str).replace("→", " ").replace("-", " ").replace("a", " ")
    tokens = [tok for tok in re.split(r"\s+", text) if tok]
    for token in tokens:
        if ":" in token:
            return token
    return str(pace_str)


def _estimate_session_duration_minutes(
    volume_km: float, pace_hint: str | None, fallback: str | None = None
) -> float | None:
    if volume_km is None or volume_km <= 0:
        return None
    target = _primary_pace_token(pace_hint) or _primary_pace_token(fallback)
    secs = _pace_str_to_seconds(target)
    if secs is None:
        return None
    minutes = (volume_km * secs) / 60.0
    return round(minutes, 1)


def _running_reference_paces(
    tempo_recente: str | None, distance: str, pace_medio: str | None
) -> dict[str, str]:
    base = _derive_current_pace_seconds(tempo_recente, distance, pace_medio)
    easy = base + 45
    moderado = max(base - 5, base * 0.95)
    tempo = max(base - 15, base * 0.9)
    vo2 = max(base - 35, base * 0.82)
    longao = easy + 30
    regenerativo = easy + 25
    return {
        "rodagem_regenerativa": _format_pace(regenerativo),
        "corrida_continua_leve": _format_pace(easy),
        "corrida_continua_moderada": _format_pace(moderado),
        "longao": _format_pace(longao),
        "tempo_run": _format_pace(tempo),
        "fartlek": f"Entre {_format_pace(easy)} e {_format_pace(tempo)}",
        "intervalado_vo2max": _format_pace(vo2),
        "prova": _format_pace(base),
    }


def _normalize_goal(goal: str) -> str:
    return "performar" if str(goal).strip().lower().startswith("per") else "completar"


def _normalize_level(nivel: str | None) -> str:
    nivel_str = str(nivel or "").strip().lower()
    if nivel_str.startswith("ini"):
        return "iniciante"
    if nivel_str.startswith("av"):
        return "avancado"
    if nivel_str.startswith("inter") or "mé" in nivel_str:
        return "intermediario"
    return "intermediario" if nivel_str else "intermediario"


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


def _volume_range(modality: str, distance: str, goal: str, nivel: str | None) -> tuple[float, float]:
    goal_norm = _normalize_goal(goal)
    nivel_norm = _normalize_level(nivel)
    if modality == "corrida":
        phase_ranges = RUN_PHASE_VOLUME_TABLE.get(distance, {}).get(nivel_norm)
        if phase_ranges:
            mins = [rng[0] for rng in phase_ranges.values()]
            maxs = [rng[1] for rng in phase_ranges.values()]
            return (min(mins), max(maxs))
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


def _phase_volume_ranges_for_running(
    distance: str, nivel: str | None, phases: list[PhaseSlice]
) -> dict[str, tuple[float, float]]:
    nivel_norm = _normalize_level(nivel)
    table = RUN_PHASE_VOLUME_TABLE.get(distance, {}).get(nivel_norm)
    if not table:
        return {}
    ranges: dict[str, tuple[float, float]] = {}
    for phase in phases:
        canonical = _canonical_phase_name(phase.name)
        if canonical in table:
            ranges[phase.name] = table[canonical]
    return ranges


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

    # Permite que semanas de recuperação (–30%) ainda avancem na escala de
    # distância, em vez de ficarem sempre no mínimo quando o volume semanal
    # cai abaixo do volume mínimo planejado.
    recovery_floor = plan_volume_min * 0.7
    span = max(plan_volume_max - recovery_floor, 1e-6)
    progress = _clamp((week_volume - recovery_floor) / span, 0.0, 1.0)

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
            ("Base 1", 0.2),
            ("Base 2", 0.12),
            ("Base 3", 0.08),
            ("Build", 0.35),
            ("Peak", 0.15),
            ("Taper", 0.10),
        ]

    total_prop = sum(prop for _, prop in scheme)
    if total_prop <= 0:
        total_prop = 1.0
    return [(name, prop / total_prop) for name, prop in scheme]


def _required_taper_weeks(distance: str, total_weeks: int) -> int:
    dist = str(distance).lower()
    if total_weeks < 1:
        return 0
    if total_weeks < 8:
        return 1
    base = 1
    if dist == "42k":
        base = 3 if total_weeks >= 16 else 2
    elif dist == "21k":
        base = 2
    elif dist == "10k":
        base = 2 if total_weeks >= 10 else 1
    elif dist == "5k":
        base = 1
    else:
        base = 1
    return max(1, min(total_weeks, base))


def _taper_reduction_sequence(taper_weeks: int) -> list[float]:
    if taper_weeks <= 0:
        return []
    presets = {
        1: [0.65],
        2: [0.7, 0.45],
        3: [0.9, 0.78, 0.62],
    }
    if taper_weeks in presets:
        return presets[taper_weeks]
    seq = [0.9] * max(taper_weeks - 2, 0)
    seq.extend([0.78, 0.65])
    return seq[-taper_weeks:]


def _apply_taper_to_phases(
    phases: list[PhaseSlice], taper_weeks: int, total_weeks: int
) -> list[PhaseSlice]:
    if taper_weeks <= 0 or not phases:
        return phases
    week_labels: list[str] = []
    for phase in phases:
        week_labels.extend([phase.name] * phase.weeks)
    if not week_labels:
        return phases
    taper_weeks = min(taper_weeks, len(week_labels), total_weeks)
    for i in range(1, taper_weeks + 1):
        week_labels[-i] = "Taper"

    new_phases: list[PhaseSlice] = []
    cursor = 1
    current = week_labels[0]
    length = 1
    for label in week_labels[1:]:
        if label == current:
            length += 1
            continue
        new_phases.append(
            PhaseSlice(name=current, weeks=length, start_week=cursor, end_week=cursor + length - 1)
        )
        cursor += length
        current = label
        length = 1
    new_phases.append(
        PhaseSlice(name=current, weeks=length, start_week=cursor, end_week=cursor + length - 1)
    )
    new_phases[-1].end_week = total_weeks
    return new_phases


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


def _apply_taper_volume_curve(
    week_volumes: list[float], taper_weeks: int
) -> list[float]:
    if not week_volumes or taper_weeks <= 0:
        return week_volumes
    tapered = list(week_volumes)
    taper_weeks = min(taper_weeks, len(tapered))
    baseline_idx = len(tapered) - taper_weeks - 1
    baseline = tapered[baseline_idx] if baseline_idx >= 0 else tapered[0]
    seq = _taper_reduction_sequence(taper_weeks)
    for offset, factor in enumerate(seq):
        idx = len(tapered) - taper_weeks + offset
        desired = baseline * factor
        desired = min(desired, week_volumes[idx])
        if idx > 0:
            desired = min(desired, tapered[idx - 1] * 0.98)
        floor = baseline * 0.4 if factor <= 0.7 else baseline * 0.5
        tapered[idx] = round(max(desired, floor, 0.0), 2)
    return tapered


def _canonical_phase_name(phase_name: str) -> str:
    if phase_name.startswith("Base"):
        return "Base"
    if phase_name == "Build":
        return "Construção"
    if phase_name == "Peak":
        return "Específica"
    if phase_name == "Taper":
        return "Taper"
    return phase_name


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


def _build_volume_curve(
    total_weeks: int,
    vol_min: float,
    vol_max: float,
    phases: list[PhaseSlice],
    phase_volume_ranges: dict[str, tuple[float, float]] | None = None,
) -> list[float]:
    if total_weeks <= 0:
        return []
    ranges = phase_volume_ranges or {}
    weekly_targets: list[float] = []
    weekly_phases: list[str] = []
    for phase in phases:
        local_min, local_max = ranges.get(phase.name, (vol_min, vol_max))
        span = max(local_max - local_min, max(local_min, 1) * 0.25)
        for offset in range(phase.weeks):
            factor = _phase_factor(phase.name, offset, phase.weeks)
            target = local_min + span * _clamp(factor, 0.0, 1.2)
            weekly_targets.append(target)
            weekly_phases.append(phase.name)

    weekly_targets = weekly_targets[:total_weeks]
    weekly_phases = weekly_phases[:total_weeks]

    progressed: list[float] = []
    for idx, target in enumerate(weekly_targets):
        phase_name = weekly_phases[idx]
        local_min, local_max = ranges.get(phase_name, (vol_min, vol_max))
        target = _clamp(target, local_min, local_max)
        if idx == 0:
            progressed.append(target)
            continue
        previous = progressed[-1]
        if target >= previous:
            desired_growth = target - previous
            growth = _clamp(desired_growth, previous * 0.05, previous * 0.1)
            new_val = min(previous + growth, target, local_max)
        else:
            desired_drop = previous - target
            drop = min(desired_drop, previous * 0.15)
            new_val = max(previous - drop, target, local_min)
        progressed.append(min(max(new_val, local_min), local_max))

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


def _running_zone_distribution(distance: str, goal: str, is_recovery: bool) -> dict[str, float]:
    goal_norm = _normalize_goal(goal)
    dist_key = distance if distance in RUN_ZONE_DISTRIBUTION else "10k"
    base = RUN_ZONE_DISTRIBUTION.get(dist_key, RUN_ZONE_DISTRIBUTION["10k"]).get(goal_norm, {})
    dist = dict(base)
    if is_recovery and dist:
        z4 = dist.get("Z4_Z5", 0)
        z3 = dist.get("Z3", 0)
        dist["Z4_Z5"] = max(0, z4 * 0.5)
        dist["Z3"] = max(0, z3 * 0.7)
        dist["Z1_Z2"] = 100 - dist.get("Z3", 0) - dist.get("Z4_Z5", 0)
    return dist


def _long_run_share_bounds(distance: str, nivel: str | None) -> tuple[float, float]:
    dist_key = str(distance).lower()
    nivel_norm = _normalize_level(nivel)
    base_ranges = {
        "21k": (0.32, 0.42),
        "42k": (0.32, 0.46),
    }
    base_min, base_max = base_ranges.get(dist_key, (0.22, 0.32))
    if nivel_norm == "iniciante":
        base_min += 0.01
        if dist_key == "21k":
            base_max = min(0.45, base_max + 0.03)
        else:
            base_max = min(base_max + 0.02, 0.48 if dist_key == "42k" else 0.37)
    elif nivel_norm == "avancado":
        base_max = max(base_min + 0.02, base_max - 0.07)
        base_min = max(base_min - 0.02, base_max - 0.08)
    return (round(base_min, 3), round(base_max, 3))


def _canonical_long_run_sequence(distance: str) -> list[float]:
    dist_key = str(distance).lower()
    if dist_key == "42k":
        # Conservador para iniciantes, com cutbacks frequentes e pico em 32 km
        return [14, 16, 18, 12, 20, 22, 16, 24, 26, 20, 28, 30, 32, 20]
    if dist_key == "21k":
        # Progressão de 12 semanas com cutbacks e pico em 16 km na semana 9-10
        return [8, 9, 10, 7, 11, 12, 13, 10, 14, 16, 10, 6]
    # fallback genérico
    return [max(5, round((RUN_VOLUMES.get(dist_key, {"completar": (10, 20)})["completar"][0]) * 0.35))]


def _resample_sequence(base: list[float], weeks: int) -> list[float]:
    if not base:
        return [0.0] * weeks
    if weeks <= len(base):
        return [float(base[i]) for i in range(weeks)]
    if weeks <= 1:
        return [float(base[0])]
    span = len(base) - 1
    scaled: list[float] = []
    for i in range(weeks):
        pos = i * span / max(weeks - 1, 1)
        low = int(math.floor(pos))
        high = min(span, low + 1)
        frac = pos - low
        value = base[low] + (base[high] - base[low]) * frac
        scaled.append(round(value, 1))
    return scaled


def _apply_half_marathon_long_run_rules(long_runs: list[float]) -> list[float]:
    total = len(long_runs)
    if total == 0:
        return long_runs
    adjusted = [round(min(16.0, max(0.0, val)), 1) for val in long_runs]
    if total == 1:
        return adjusted
    adjusted[-1] = 0.0
    penultimate_idx = total - 2
    adjusted[penultimate_idx] = 10.0
    if total >= 3:
        if total >= 10:
            target_idx = min(max(8, total - 3), 9)
        else:
            target_idx = max(0, total - 3)
        adjusted[target_idx] = min(16.0, max(adjusted[target_idx], 12.0))
        for idx in range(target_idx + 1, penultimate_idx):
            adjusted[idx] = min(adjusted[idx], 14.0)
        for idx in range(1, target_idx + 1):
            prev = adjusted[idx - 1]
            if adjusted[idx] < prev and (idx + 1) % 4 != 0:
                adjusted[idx] = min(16.0, max(prev, adjusted[idx - 1]) + 1)
    return adjusted


def _long_run_progression(distance: str, weeks: int) -> list[float]:
    base = _canonical_long_run_sequence(distance)
    dist_key = str(distance).lower()
    if weeks <= 1:
        cap = 32.0 if dist_key == "42k" else 16.0 if dist_key == "21k" else base[-1]
        return [round(min(base[0], cap), 1)]
    if dist_key == "21k":
        scaled = _resample_sequence(base, weeks)
        return _apply_half_marathon_long_run_rules(scaled)
    cap = 32.0 if dist_key == "42k" else base[-1]
    idxs = [round(i * (len(base) - 1) / (weeks - 1)) for i in range(weeks)]
    return [round(min(cap, base[i]), 1) for i in idxs]


def _running_long_run_plan(
    distance: str,
    nivel: str | None,
    week_volumes: list[float],
    phases: list[PhaseSlice],
    taper_weeks: int,
) -> list[float]:
    dist_key = str(distance).lower()
    if not week_volumes:
        return []

    base_min, base_max = _long_run_share_bounds(dist_key, nivel)

    phase_by_week: list[str] = []
    for idx in range(len(week_volumes)):
        phase = next((p for p in phases if p.start_week <= idx + 1 <= p.end_week), phases[-1])
        phase_by_week.append(phase.name)

    long_runs: list[float] = []
    progression_targets = _long_run_progression(dist_key, len(week_volumes))
    for idx, volume in enumerate(week_volumes):
        phase_name = phase_by_week[idx]
        is_recovery = (idx + 1) % 4 == 0
        is_taper = "Taper" in phase_name
        share_low, share_high = base_min, base_max
        if is_recovery:
            share_high = max(share_low, share_high - 0.06)
        if is_taper:
            share_low *= 0.7
            share_high *= 0.8
        share = _clamp(share_high if not is_recovery else share_high - 0.01, share_low, share_high)
        base_target = progression_targets[idx]
        long_km = volume * share
        cap = 32.0 if dist_key == "42k" else 16.0 if dist_key == "21k" else volume * 0.45
        if dist_key == "21k":
            if base_target <= 0:
                long_km = 0.0
            else:
                feasible = min(cap, volume * share_high)
                target = min(feasible, base_target)
                long_km = max(target, volume * share_low)
        else:
            long_km = min(long_km, cap, volume * share_high)
            long_km = max(long_km, volume * share_low, base_target)
        value = round(long_km, 1)
        if dist_key == "21k":
            value = round(value * 2) / 2.0
        long_runs.append(value)

    if dist_key == "42k":
        goal_km = 42.195
        threshold = round(min(goal_km * 0.7, 32.0), 1)
        required_hits = 3
        non_taper_weeks = [i for i, name in enumerate(phase_by_week) if "Taper" not in name]
        candidates = sorted(non_taper_weeks, key=lambda i: (week_volumes[i], i), reverse=True)
        hits = sum(1 for km in long_runs if km >= threshold)
        for idx in candidates:
            if hits >= required_hits:
                break
            volume = week_volumes[idx]
            cap = 32.0 if dist_key == "42k" else 16.0
            flex_share = 0.6 if dist_key == "42k" else 0.5 if dist_key == "21k" else base_max
            allowed_max = min(cap, volume * max(base_max, flex_share))
            if allowed_max <= long_runs[idx]:
                continue
            target = threshold if allowed_max >= threshold else allowed_max
            long_runs[idx] = round(max(long_runs[idx], target), 1)
            hits = sum(1 for km in long_runs if km >= threshold)

        if hits < required_hits:
            remaining = [i for i in range(len(week_volumes)) if i not in candidates]
            for idx in sorted(remaining, key=lambda i: (week_volumes[i], i), reverse=True):
                if hits >= required_hits:
                    break
                volume = week_volumes[idx]
                cap = 32.0 if dist_key == "42k" else 16.0
                flex_share = 0.6 if dist_key == "42k" else 0.5 if dist_key == "21k" else base_max
                allowed_max = min(cap, volume * max(base_max, flex_share))
                if allowed_max <= long_runs[idx]:
                    continue
                target = threshold if allowed_max >= threshold else allowed_max
                long_runs[idx] = round(max(long_runs[idx], target), 1)
                hits = sum(1 for km in long_runs if km >= threshold)

    cutoff = _last_long_run_offsets(distance, taper_weeks)
    if cutoff and len(long_runs) > cutoff:
        last_idx = len(long_runs) - 1
        last_long_idx = max(0, last_idx - cutoff)
        for idx in range(last_long_idx + 1, len(long_runs)):
            long_runs[idx] = 0.0

    if dist_key == "21k" and len(long_runs) >= 2:
        long_runs[-2] = 10.0

    if dist_key == "21k":
        long_runs = [round(val * 2) / 2.0 for val in long_runs]

    return long_runs


def _last_long_run_offsets(distance: str, taper_weeks: int) -> int:
    dist_key = str(distance).lower()
    if dist_key == "42k":
        return 2
    if dist_key == "21k":
        return 1
    if dist_key == "10k":
        return 1
    if taper_weeks > 0:
        return 1
    return 0


def _intensity_slots_from_days(dias_treino: int | None, is_recovery: bool) -> int:
    days = int(dias_treino or 4)
    if days <= 3:
        slots = 1
    elif days <= 4:
        slots = 1
    elif days <= 5:
        slots = 2
    elif days <= 6:
        slots = 2
    else:
        slots = 3
    if is_recovery:
        slots = max(0, slots - 1)
    return slots


def _select_running_intensity_types(
    phase: str,
    distance: str,
    goal: str,
    slots: int,
    is_recovery: bool,
    nivel: str | None,
) -> list[str]:
    if slots <= 0:
        return []
    phase_key = phase.split()[0]
    dist = str(distance).lower()
    nivel_norm = _normalize_level(nivel)
    types: list[str] = []
    primary = "fartlek" if phase_key.startswith("Base") or is_recovery else "tempo_run"
    if dist in {"5k", "10k"} and not phase_key.startswith("Base"):
        primary = "tempo_run"
    if slots > 0:
        types.append(primary)

    remaining = slots - len(types)
    if remaining <= 0:
        return types

    if phase_key.startswith("Base"):
        candidate_pool = ["tempo_run", "fartlek"]
    elif phase_key in {"Build", "Peak"}:
        candidate_pool = ["tempo_run", "intervalado_vo2max", "fartlek"]
    elif phase_key == "Taper":
        candidate_pool = ["tempo_run"]
    else:
        candidate_pool = ["tempo_run", "fartlek"]

    allow_vo2 = nivel_norm != "iniciante" or (phase_key in {"Build", "Peak"} and not is_recovery)
    seen = set(types)
    vo2_added = False
    for cand in candidate_pool:
        if cand == "intervalado_vo2max" and not allow_vo2:
            continue
        if cand == "intervalado_vo2max" and vo2_added:
            continue
        if cand in seen:
            continue
        types.append(cand)
        seen.add(cand)
        if cand == "intervalado_vo2max":
            vo2_added = True
        remaining -= 1
        if remaining <= 0:
            break

    return types


def _training_zone(tag: str) -> str:
    return RUN_TRAINING_TYPE_INFO.get(tag, {}).get("zona", "")


def _training_type_name(tag: str) -> str:
    return RUN_TRAINING_TYPE_INFO.get(tag, {}).get("nome", tag.title())


def _running_week_sessions(
    week_volume: float,
    longao_volume: float | None,
    phase: str,
    distance: str,
    goal: str,
    nivel: str | None,
    dias_treino: int | None,
    is_recovery: bool,
    paces: dict[str, str],
    is_final_week: bool,
) -> list[dict]:
    dist_key = str(distance).lower()
    total_sessions = min(7, max(3, int(dias_treino or 4)))
    if is_final_week:
        total_sessions = min(total_sessions, 3)
    if dist_key == "42k":
        total_sessions = max(total_sessions, 4)
    is_taper_phase = "taper" in phase.lower()
    intensity_slots = _intensity_slots_from_days(total_sessions, is_recovery)
    if intensity_slots <= 0 and week_volume > 0:
        intensity_slots = 1
    intensity_types = _select_running_intensity_types(
        phase, distance, goal, intensity_slots, is_recovery, nivel
    )
    if is_taper_phase:
        allowed = {"tempo_run", "fartlek"}
        intensity_types = [t for t in intensity_types if t in allowed]
        if not intensity_types and intensity_slots > 0:
            intensity_types = ["tempo_run"]
        if is_final_week:
            intensity_types = intensity_types[:1]
    zone_dist = _running_zone_distribution(distance, goal, is_recovery)
    z1z2_km = week_volume * zone_dist.get("Z1_Z2", 0) / 100
    z3_km = week_volume * zone_dist.get("Z3", 0) / 100
    z45_km = week_volume * zone_dist.get("Z4_Z5", 0) / 100

    share_low, share_high = _long_run_share_bounds(dist_key, nivel)

    if longao_volume is None:
        longao_volume = _clamp(week_volume * share_high, week_volume * share_low, week_volume * share_high)
    if longao_volume > 0 and z1z2_km:
        longao_volume = min(longao_volume, z1z2_km * 0.8)
    if longao_volume > 0 and dist_key == "42k":
        ceiling = min(32.0, z1z2_km * 0.82 if z1z2_km else week_volume * share_high)
        floor = max(longao_volume * 0.85, week_volume * share_low)
        longao_volume = _clamp(longao_volume, floor, ceiling)
    elif longao_volume > 0 and dist_key == "21k":
        ceiling = min(16.0, z1z2_km * 0.8 if z1z2_km else week_volume * share_high)
        floor = max(longao_volume * 0.85, week_volume * share_low)
        longao_volume = _clamp(longao_volume, floor, ceiling)

    include_long_run = not is_final_week and longao_volume > 0
    non_long_ceiling = longao_volume * 0.9 if include_long_run else None
    if not include_long_run:
        longao_volume = 0.0

    sessions: list[dict] = []
    if include_long_run:
        session = {
            "tipo": "longao",
            "tipo_nome": _training_type_name("longao"),
            "zona": _training_zone("longao"),
            "volume_km": round(longao_volume, 1),
            "ritmo": paces.get("longao"),
            "descricao": RUN_TRAINING_TYPE_INFO.get("longao", {}).get("descricao", ""),
        }
        duration = _estimate_session_duration_minutes(
            session["volume_km"], session["ritmo"], paces.get("corrida_continua_leve")
        )
        if duration is not None:
            session["duracao_estimada_min"] = duration
        sessions.append(session)

    race_distance = _distance_to_km(distance) if is_final_week else 0.0
    remaining_volume = max(week_volume - longao_volume - race_distance, 0)
    mandatory_slots = (1 if include_long_run else 0) + (1 if race_distance > 0 else 0)
    if len(intensity_types) + mandatory_slots > total_sessions:
        allowed = max(0, total_sessions - mandatory_slots)
        intensity_types = intensity_types[:allowed]
    z3_types = [t for t in intensity_types if "Z3" in _training_zone(t)]
    z4_types = [t for t in intensity_types if "Z4" in _training_zone(t)]
    z3_per = z3_km / max(len(z3_types), 1) if z3_types else 0
    z4_per = z45_km / max(len(z4_types), 1) if z4_types else 0

    for t in intensity_types:
        zone = _training_zone(t)
        if "Z4" in zone and "Z3" not in zone:
            vol = max(z4_per, remaining_volume * 0.1)
        elif "Z3" in zone:
            vol = max(z3_per, remaining_volume * 0.12)
        else:
            vol = max(z1z2_km * 0.1, week_volume * 0.08)
        if is_taper_phase:
            vol = min(vol, week_volume * (0.2 if not is_final_week else 0.15))
        if non_long_ceiling is not None:
            vol = min(vol, non_long_ceiling)
        session = {
            "tipo": t,
            "zona": zone,
            "volume_km": round(vol, 1),
            "tipo_nome": _training_type_name(t),
            "ritmo": paces.get(t) or paces.get("corrida_continua_leve"),
            "descricao": RUN_TRAINING_TYPE_INFO.get(t, {}).get("descricao", ""),
        }
        duration = _estimate_session_duration_minutes(
            session["volume_km"], session["ritmo"], paces.get("corrida_continua_leve")
        )
        if duration is not None:
            session["duracao_estimada_min"] = duration
        sessions.append(session)
        remaining_volume = max(0, remaining_volume - vol)

    race_slot = 1 if race_distance > 0 else 0
    easy_sessions = total_sessions - len(sessions) - race_slot
    min_easy = 1 if total_sessions - race_slot >= 2 else 0
    if is_taper_phase and is_final_week:
        min_easy = 1
    easy_sessions = max(easy_sessions, min_easy)
    easy_volume = max(week_volume - race_distance - sum(sess["volume_km"] for sess in sessions), 0)
    per_easy = easy_volume / max(easy_sessions, 1)
    if is_taper_phase:
        per_easy = min(per_easy, max(week_volume * 0.25, 3.0))
    default_easy_tag = "rodagem_regenerativa" if (is_recovery or is_final_week) else "corrida_continua_leve"
    easy_tags: list[str]
    if max(easy_sessions, 0) == 0:
        easy_tags = []
    elif default_easy_tag == "corrida_continua_leve" and easy_sessions > 1:
        easy_tags = ["corrida_continua_moderada"] + [default_easy_tag] * (easy_sessions - 1)
    else:
        easy_tags = [default_easy_tag] * easy_sessions
    for tag in easy_tags:
        session = {
            "tipo": tag,
            "tipo_nome": _training_type_name(tag),
            "zona": _training_zone(tag),
            "volume_km": round(per_easy, 1),
            "ritmo": paces.get(tag, paces.get("corrida_continua_leve")),
            "descricao": RUN_TRAINING_TYPE_INFO.get(tag, {}).get("descricao", ""),
        }
        duration = _estimate_session_duration_minutes(
            session["volume_km"], session["ritmo"], paces.get("corrida_continua_leve")
        )
        if duration is not None:
            session["duracao_estimada_min"] = duration
        sessions.append(session)

    if race_distance > 0:
        session = {
            "tipo": "prova",
            "tipo_nome": _training_type_name("prova"),
            "zona": _training_zone("prova"),
            "volume_km": round(race_distance, 1),
            "ritmo": paces.get("prova") or paces.get("tempo_run"),
            "descricao": RUN_TRAINING_TYPE_INFO.get("prova", {}).get("descricao", ""),
        }
        duration = _estimate_session_duration_minutes(
            session["volume_km"], session["ritmo"], paces.get("tempo_run")
        )
        if duration is not None:
            session["duracao_estimada_min"] = duration
        sessions.append(session)

    return sessions


def build_triplanner_plan(
    modality: str,
    distance: str,
    goal: str,
    cycle_weeks: int,
    start_date: date,
    tempo_recente: str | None = None,
    pace_medio: str | None = None,
    dias_treino: int | None = None,
    nivel: str | None = None,
    notes: str | None = None,
) -> dict:
    modality_norm = _normalize_modality(modality)
    goal_norm = _normalize_goal(goal)
    nivel_norm = _normalize_level(nivel)
    cycle_weeks = int(cycle_weeks)
    unit = _volume_unit(modality_norm)
    method = _resolve_method(modality_norm, distance)
    running_paces = (
        _running_reference_paces(tempo_recente, distance, pace_medio)
        if modality_norm == "corrida"
        else {}
    )
    vol_min, vol_max = _volume_range(modality_norm, distance, goal_norm, nivel)
    taper_weeks = _required_taper_weeks(distance, cycle_weeks)
    phases = _allocate_phases(cycle_weeks)
    phases = _apply_taper_to_phases(phases, taper_weeks, cycle_weeks)
    phase_volume_ranges = (
        _phase_volume_ranges_for_running(distance, nivel, phases)
        if modality_norm == "corrida"
        else {}
    )
    week_volumes = _build_volume_curve(
        cycle_weeks, vol_min, vol_max, phases, phase_volume_ranges
    )
    week_volumes = _apply_three_one(week_volumes)
    week_volumes = _apply_taper_volume_curve(week_volumes, taper_weeks)

    long_runs = (
        _running_long_run_plan(distance, nivel, week_volumes, phases, taper_weeks)
        if modality_norm == "corrida"
        else []
    )

    weeks_payload = []
    for idx in range(cycle_weeks):
        phase = next((p for p in phases if p.start_week <= idx + 1 <= p.end_week), phases[-1])
        week_start = start_date + timedelta(days=7 * idx)
        is_recovery = (idx + 1) % 4 == 0
        is_final_week = idx == cycle_weeks - 1
        is_taper_week = "Taper" in phase.name
        race_distance = _distance_to_km(distance) if (modality_norm == "corrida" and is_final_week) else 0.0
        if modality_norm == "corrida":
            intensity = _running_zone_distribution(distance, goal_norm, is_recovery)
            if is_taper_week:
                intensity = intensity.copy()
                z4z5 = intensity.get("Z4_Z5", 0)
                z3 = intensity.get("Z3", 0)
                intensity["Z4_Z5"] = min(z4z5, 5)
                intensity["Z3"] = min(z3, 20)
                intensity["Z1_Z2"] = max(0, 100 - intensity.get("Z3", 0) - intensity.get("Z4_Z5", 0))
            focus = _week_focus(modality_norm, method, is_recovery, idx + 1)
            sessions = _running_week_sessions(
                week_volumes[idx],
                long_runs[idx] if idx < len(long_runs) else None,
                phase.name,
                distance,
                goal_norm,
                nivel,
                dias_treino,
                is_recovery,
                running_paces,
                is_final_week,
            )
            if sessions:
                derived: list[str] = []
                for s in sessions:
                    if s.get("tipo") == "longao":
                        continue
                    nome = s.get("tipo_nome") or _training_type_name(s.get("tipo", ""))
                    if nome and nome not in derived:
                        derived.append(nome)
                    if len(derived) == 4:
                        break
                if derived:
                    focus = derived
            if is_taper_week:
                tempo_nome = RUN_TRAINING_TYPE_INFO.get("tempo_run", {}).get("nome", "Tempo Run")
                regen_nome = RUN_TRAINING_TYPE_INFO.get("rodagem_regenerativa", {}).get("nome", "Rodagem regenerativa")
                if tempo_nome not in focus:
                    focus = [tempo_nome, regen_nome] + [f for f in focus if f not in {tempo_nome, regen_nome}]
            if is_final_week:
                regen_nome = RUN_TRAINING_TYPE_INFO.get("rodagem_regenerativa", {}).get("nome", "Rodagem regenerativa")
                focus = [regen_nome, "Confiança", "Mobilidade", "Prova"]
        else:
            intensity = _intensity_for_week(method, is_recovery, phase.name)
            focus = _week_focus(modality_norm, method, is_recovery, idx + 1)
            sessions = []
        focus = focus[:4]
        volume_total = round(week_volumes[idx] + race_distance, 2)
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
                "status": "taper" if is_taper_week else ("recuperação" if is_recovery else "carga"),
                "volume_total": volume_total,
                "unidade": unit,
                "volume_por_modalidade": volume_modalidades,
                "intensidade": intensity,
                "focos_da_semana": focus,
                "distribuicao_zonas": intensity if modality_norm == "corrida" else {},
                "treinos": sessions,
                "ritmos_referencia": running_paces if modality_norm == "corrida" else {},
            }
        )

    training_catalog = TRAINING_TYPE_INFO
    if modality_norm == "corrida":
        training_catalog = {**BASE_TRAINING_TYPE_INFO, **RUN_TRAINING_TYPE_INFO}

    cycle_recommendation: dict[str, int | str] = {}
    if modality_norm == "corrida" and str(distance).lower() == "21k" and nivel_norm == "iniciante":
        cycle_recommendation = {
            "ideal_semanas": 12,
            "semanas_planejadas": cycle_weeks,
        }
        if cycle_weeks < 12:
            cycle_recommendation[
                "observacao"
            ] = "Plano mais agressivo por ter menos de 12 semanas até a prova."
        else:
            cycle_recommendation[
                "observacao"
            ] = "Ciclo alinhado com as 12 semanas ideais para iniciantes em meia maratona."

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
        "tipos_de_treino": training_catalog,
        "observacoes": notes or "",
        "nivel": nivel_norm or "",
        "dias_treino": dias_treino or 0,
    }
    if cycle_recommendation:
        plan["recomendacao_ciclo"] = cycle_recommendation
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

