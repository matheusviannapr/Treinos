
# app.py — TriPlano (evolução do TriCiclo Planner)
# ----------------------------------------------------------------------------
# Funcionalidades:
# - Login/cadastro multiusuário (CSV)
# - Treinos multiusuário com UserID + UID estável
# - Metas, sessões, preferências por modalidade
# - Geração automática de semana
# - Periodização multi-semanal (generate_cycle)
# - Exportações: PDF / ICS
# - Disponibilidade persistida em availability.csv
# - Calendário semanal (streamlit-calendar):
#     - Seleção cria slots "Livre"
#     - Clique em "Livre" remove slot
#     - Uso opcional dos horários livres ou ignorar
#     - Treinos com UID estável, drag & drop e resize atualizam horários
#     - Pop-up do treino:
#           - Editar Modalidade, Tipo, Volume
#           - Editar data/hora/duração
#           - RPE, Comentário
#           - Marcar FEITO / NÃO FEITO / salvar
# - Quando um horário Livre é ocupado por treino, o slot é removido/ajustado.
# - Botão "Salvar Semana Atual" para persistir qualquer ajuste.
# - PDF:
#     - Página 1: tabela colorida
#     - Página 2: calendário semanal em paisagem (timeGridWeek-like).
# - ICS e PDF usam EXATAMENTE o mesmo conjunto de treinos exibidos no calendário.
# - Descanso com volume 0 nunca aparece no calendário/ICS/PDF.
# - Calendário de front é SIEMPRE derivado de canonical_week_df (fonte única).
# ----------------------------------------------------------------------------

import os
import json
import math
from datetime import datetime, date, timedelta, time

import pandas as pd
import numpy as np
import streamlit as st
from fpdf import FPDF
import matplotlib.pyplot as plt
import unicodedata

from streamlit_calendar import calendar  # pip install streamlit-calendar

# ----------------------------------------------------------------------------
# Utilitários básicos
# ----------------------------------------------------------------------------

def safe_rerun():
    try:
        st.rerun()
    except Exception:
        if hasattr(st, "experimental_rerun"):
            try:
                st.experimental_rerun()
            except Exception:
                pass

DATA_DIR = "data"
EXPORT_DIR = "exports"
CSV_PATH = os.path.join(DATA_DIR, "treinos.csv")
USERS_CSV_PATH = os.path.join(DATA_DIR, "usuarios.csv")
AVAIL_CSV_PATH = os.path.join(DATA_DIR, "availability.csv")

SCHEMA_COLS = [
    "UserID",
    "UID",
    "Data",
    "Start",
    "End",
    "Modalidade",
    "Tipo de Treino",
    "Volume",
    "Unidade",
    "RPE",
    "Detalhamento",
    "Observações",
    "Status",
    "adj",
    "AdjAppliedAt",
    "ChangeLog",
    "LastEditedAt",
    "WeekStart",
]

MODALITY_COLORS = {
    "Corrida": (255, 0, 0),
    "Ciclismo": (64, 64, 64),
    "Natação": (75, 0, 130),
    "Força/Calistenia": (34, 139, 34),
    "Mobilidade": (255, 140, 0),
    "Descanso": (201, 201, 201),
}
MODALITY_TEXT_COLORS = {
    "Ciclismo": (255, 255, 255),
}

PDF_REPLACE = str.maketrans({
    "—": "-",
    "–": "-",
    "“": '"',
    "”": '"',
    "’": "'",
    "•": "-",
})

def pdf_safe(s: str) -> str:
    if s is None:
        return ""
    t = str(s).translate(PDF_REPLACE)
    return unicodedata.normalize("NFKD", t).encode("latin-1", "ignore").decode("latin-1")

UNITS_ALLOWED = {
    "Corrida": "km",
    "Ciclismo": "km",
    "Natação": "m",
    "Força/Calistenia": "min",
    "Mobilidade": "min",
}
MODALIDADES = list(UNITS_ALLOWED.keys())
STATUS_CHOICES = ["Planejado", "Realizado", "Adiado", "Cancelado"]

LOAD_COEFF = {
    "Corrida": 1.0,
    "Ciclismo": 0.6,
    "Natação": 1.2,
    "Força/Calistenia": 0.3,
    "Mobilidade": 0.2,
}

TIPOS_MODALIDADE = {
    "Corrida": ["Regenerativo", "Força", "Longão", "Tempo Run"],
    "Ciclismo": ["Endurance", "Intervalado", "Cadência", "Força/Subida"],
    "Natação": ["Técnica", "Ritmo", "Intervalado", "Contínuo"],
    "Força/Calistenia": ["Força máxima", "Resistência muscular", "Core/Estabilidade", "Mobilidade/Recuperação"],
    "Mobilidade": ["Soltura", "Recuperação", "Prevenção"],
}

PHASES = ["Base", "Build", "Peak", "Recovery"]

DEFAULT_TRAINING_DURATION_MIN = 60

# ----------------------------------------------------------------------------
# Diretórios
# ----------------------------------------------------------------------------

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)

# ----------------------------------------------------------------------------
# Usuários
# ----------------------------------------------------------------------------

def init_users_if_needed():
    ensure_dirs()
    if not os.path.exists(USERS_CSV_PATH):
        df = pd.DataFrame(columns=["user_id", "nome", "created_at"])
        df.to_csv(USERS_CSV_PATH, index=False)

@st.cache_data(show_spinner=False)
def load_users_df() -> pd.DataFrame:
    init_users_if_needed()
    return pd.read_csv(USERS_CSV_PATH, dtype=str).fillna("")

def save_users_df(df: pd.DataFrame):
    df.to_csv(USERS_CSV_PATH, index=False)
    load_users_df.clear()

def get_user(user_id: str):
    df = load_users_df()
    row = df[df["user_id"] == user_id]
    return row.iloc[0] if not row.empty else None

def create_user(user_id: str, nome: str) -> bool:
    df = load_users_df()
    if (df["user_id"] == user_id).any():
        return False
    new_row = pd.DataFrame([{
        "user_id": user_id,
        "nome": nome,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    save_users_df(df)
    return True

def logout():
    for key in list(st.session_state.keys()):
        if key.startswith("login_") or key.startswith("cal_") or key in [
            "user_id", "user_name", "df", "all_df",
            "current_week_start", "frozen_targets"
        ]:
            del st.session_state[key]
    safe_rerun()

# ----------------------------------------------------------------------------
# Treinos CSV (multiusuário)
# ----------------------------------------------------------------------------

def init_csv_if_needed():
    ensure_dirs()
    if not os.path.exists(CSV_PATH):
        df = pd.DataFrame(columns=SCHEMA_COLS)
        df.to_csv(CSV_PATH, index=False)

@st.cache_data(show_spinner=False)
def load_all() -> pd.DataFrame:
    init_csv_if_needed()
    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")

    for col in SCHEMA_COLS:
        if col not in df.columns:
            if col in ["Volume", "RPE", "adj"]:
                df[col] = 0.0
            elif col == "UserID":
                df[col] = "default"
            else:
                df[col] = ""

    if not df.empty:
        df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date

        for c in ["Volume", "RPE", "adj"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

        for c in ["ChangeLog", "Detalhamento", "Observações"]:
            df[c] = df[c].astype(str)

        for i, r in df.iterrows():
            mod = r.get("Modalidade", "")
            if mod in UNITS_ALLOWED:
                unit_ok = UNITS_ALLOWED[mod]
                if r.get("Unidade", "") != unit_ok:
                    df.at[i, "Unidade"] = unit_ok

    if "UID" not in df.columns:
        df["UID"] = ""
    if "UserID" not in df.columns:
        df["UserID"] = "default"

    return df[SCHEMA_COLS].copy()

def save_all(df: pd.DataFrame):
    df_out = df.copy()
    if not df_out.empty:
        df_out["Data"] = pd.to_datetime(df_out["Data"], errors="coerce").dt.date.astype(str)
        df_out["WeekStart"] = pd.to_datetime(df_out["WeekStart"], errors="coerce").dt.date.astype(str)
    df_out.to_csv(CSV_PATH, index=False)
    load_all.clear()

def generate_uid(user_id: str) -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    rand = np.random.randint(1000, 9999)
    return f"{user_id}-{ts}-{rand}"

def save_user_df(user_id: str, user_df: pd.DataFrame):
    all_df = load_all()

    if "UserID" not in user_df.columns:
        user_df["UserID"] = user_id
    else:
        user_df.loc[user_df["UserID"] == "", "UserID"] = user_id

    if "UID" not in user_df.columns:
        user_df["UID"] = ""
    for i, r in user_df[user_df["UID"] == ""].iterrows():
        user_df.at[i, "UID"] = generate_uid(user_id)

    others = all_df[all_df["UserID"] != user_id]
    merged = pd.concat([others, user_df[SCHEMA_COLS]], ignore_index=True)
    save_all(merged)

    st.session_state["all_df"] = merged
    st.session_state["df"] = merged[merged["UserID"] == user_id].copy()

# ----------------------------------------------------------------------------
# Disponibilidade
# ----------------------------------------------------------------------------

def init_availability_if_needed():
    ensure_dirs()
    if not os.path.exists(AVAIL_CSV_PATH):
        df = pd.DataFrame(columns=["UserID", "WeekStart", "Start", "End"])
        df.to_csv(AVAIL_CSV_PATH, index=False)

@st.cache_data(show_spinner=False)
def load_all_availability() -> pd.DataFrame:
    init_availability_if_needed()
    df = pd.read_csv(AVAIL_CSV_PATH, dtype=str).fillna("")
    if not df.empty:
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date
        df["Start"] = pd.to_datetime(df["Start"], errors="coerce")
        df["End"] = pd.to_datetime(df["End"], errors="coerce")
    return df

def save_all_availability(df: pd.DataFrame):
    df_out = df.copy()
    if not df_out.empty:
        df_out["WeekStart"] = pd.to_datetime(df_out["WeekStart"], errors="coerce").dt.date.astype(str)
        df_out["Start"] = pd.to_datetime(df_out["Start"], errors="coerce").astype(str)
        df_out["End"] = pd.to_datetime(df_out["End"], errors="coerce").astype(str)
    df_out.to_csv(AVAIL_CSV_PATH, index=False)
    load_all_availability.clear()

def normalize_slots(slots):
    if not slots:
        return []
    slots = sorted(slots, key=lambda s: s["start"])
    merged = [slots[0]]
    for s in slots[1:]:
        last = merged[-1]
        if s["start"] < last["end"]:
            last["end"] = max(last["end"], s["end"])
        else:
            merged.append(s)
    return merged

def get_week_availability(user_id: str, week_start: date):
    df = load_all_availability()
    user_df = df[(df["UserID"] == user_id) & (df["WeekStart"] == week_start)]
    slots = []
    for _, r in user_df.iterrows():
        s = pd.to_datetime(r["Start"], errors="coerce")
        e = pd.to_datetime(r["End"], errors="coerce")
        if pd.notna(s) and pd.notna(e) and e > s:
            slots.append({"start": s, "end": e})
    return normalize_slots(slots)

def set_week_availability(user_id: str, week_start: date, slots):
    all_df = load_all_availability()
    all_df = all_df[~((all_df["UserID"] == user_id) & (all_df["WeekStart"] == week_start))]

    rows = []
    for s in normalize_slots(slots):
        rows.append({
            "UserID": user_id,
            "WeekStart": week_start,
            "Start": s["start"],
            "End": s["end"],
        })
    if rows:
        all_df = pd.concat([all_df, pd.DataFrame(rows)], ignore_index=True)

    save_all_availability(all_df)

# ----------------------------------------------------------------------------
# Helpers gerais
# ----------------------------------------------------------------------------

def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())

def week_range(start_monday: date):
    return [start_monday + timedelta(days=i) for i in range(7)]

def today() -> date:
    return date.today()

def normalize_volume_for_load(mod: str, vol: float, unit: str) -> float:
    if mod == "Natação":
        km = (vol or 0.0) / 1000.0
        return km * LOAD_COEFF.get(mod, 1.0)
    elif mod in ("Força/Calistenia", "Mobilidade"):
        return (vol or 0.0) * LOAD_COEFF.get(mod, 1.0)
    else:
        return (vol or 0.0) * LOAD_COEFF.get(mod, 1.0)

def week_slice(df: pd.DataFrame, start: date) -> pd.DataFrame:
    end = start + timedelta(days=7)
    return df[(df["Data"] >= start) & (df["Data"] < end)].copy()

def parse_iso(dt_str: str):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", ""))
    except Exception:
        return None

def append_changelog(old_row: pd.Series, new_row: pd.Series) -> str:
    try:
        log = json.loads(old_row.get("ChangeLog", "[]") or "[]")
    except Exception:
        log = []
    changes = {}
    for col in [
        "Modalidade", "Tipo de Treino", "Volume", "Unidade", "RPE",
        "Detalhamento", "Observações", "Status", "adj",
        "Start", "End", "Data"
    ]:
        if str(old_row.get(col, "")) != str(new_row.get(col, "")):
            changes[col] = {
                "old": str(old_row.get(col, "")),
                "new": str(new_row.get(col, "")),
            }
    if changes:
        log.append({"at": datetime.now().isoformat(timespec="seconds"), "changes": changes})
    return json.dumps(log, ensure_ascii=False)

# ----------------------------------------------------------------------------
# Prescrição / distribuição
# ----------------------------------------------------------------------------

def _unit_step(unit: str) -> float:
    if unit == "m":
        return 50.0
    if unit == "km":
        return 0.1
    return 1.0

def _round_to_step_sum(total: float, unit: str) -> float:
    step = _unit_step(unit)
    v = float(total)
    if step == 50.0:
        return round(v / step) * step
    if step == 0.1:
        return round(v, 1)
    return round(v, 0)

def prescribe_detail(mod, tipo, volume, unit, paces):
    vol = float(volume or 0)
    rp = paces.get("run_pace_min_per_km", 0)
    sp = paces.get("swim_sec_per_100m", 0)
    bk = paces.get("bike_kmh", 0)

    if mod == "Corrida":
        if tipo == "Regenerativo":
            dur = math.ceil(vol * rp) if unit == "km" and rp > 0 else ""
            return f"Regenerativo Z1/Z2 {vol:g} km (~{dur} min)."
        if tipo == "Força":
            reps = max(6, min(12, int(vol)))
            return f"Força em subida: {reps}×(60s forte) rec 2min."
        if tipo == "Longão":
            dur = math.ceil(vol * rp) if unit == "km" and rp > 0 else ""
            return f"Longão {vol:g} km (Z2/Z3) ~{dur} min."
        if tipo == "Tempo Run":
            bloco = max(20, min(40, int(vol * 6)))
            return f"Tempo Run {bloco}min Z3/Z4."

    if mod == "Ciclismo":
        if tipo == "Endurance":
            vel = bk if bk > 0 else 28
            dur_h = vol / vel if vel > 0 else 0
            return f"Endurance {vol:g} km (~{dur_h:.1f}h Z2)."
        if tipo == "Intervalado":
            blocos = max(4, min(6, int(vol / 5)))
            return f"{blocos}×(6min Z4) rec 3min."
        if tipo == "Cadência":
            return "5×(3min 100–110rpm) rec 2min."
        if tipo == "Força/Subida":
            return "6×(4min 60–70rpm Z3/Z4) rec 3min."

    if mod == "Natação":
        if tipo == "Técnica":
            return "Drills técnicos + 8×50m educativos."
        if tipo == "Ritmo":
            reps = max(6, min(10, int(vol / 200)))
            return f"{reps}×200m ritmo controlado."
        if tipo == "Intervalado":
            reps = max(12, min(20, int(vol / 50)))
            alvo = f"{(sp and int(sp)) or '—'} s/100m"
            return f"{reps}×50m forte. Alvo ~{alvo}."
        if tipo == "Contínuo":
            km = vol / 1000.0
            return f"{km:.1f} km contínuos Z2/Z3."

    if mod == "Força/Calistenia":
        if tipo == "Força máxima":
            return "5×3 básicos pesados."
        if tipo == "Resistência muscular":
            return "4×12–20 em circuito."
        if tipo == "Core/Estabilidade":
            return "Core 15–20min."
        if tipo == "Mobilidade/Recuperação":
            return "Mobilidade 15–25min."

    if mod == "Mobilidade":
        if tipo == "Soltura":
            return "Soltura dinâmica 15–25min."
        if tipo == "Recuperação":
            return "Alongamentos leves 10–20min."
        if tipo == "Prevenção":
            return "Mobilidade ombro/quadril 15–20min."

    return ""

def _expand_to_n(pattern_list, n):
    if n <= 0:
        return []
    if not pattern_list:
        return [1.0 / n] * n
    k = len(pattern_list)
    reps = n // k
    rem = n % k
    return pattern_list * reps + pattern_list[:rem]

def default_week_df(week_start: date, user_id: str) -> pd.DataFrame:
    recs = []
    for d in week_range(week_start):
        recs.append({
            "UserID": user_id,
            "UID": generate_uid(user_id),
            "Data": d,
            "Start": "",
            "End": "",
            "Modalidade": "Descanso",
            "Tipo de Treino": "Ativo/Passivo",
            "Volume": 0.0,
            "Unidade": "min",
            "RPE": 0,
            "Detalhamento": "Dia de descanso. Foco em recuperação.",
            "Observações": "",
            "Status": "Planejado",
            "adj": 0.0,
            "AdjAppliedAt": "",
            "ChangeLog": "[]",
            "LastEditedAt": "",
            "WeekStart": week_start,
        })
    return pd.DataFrame(recs, columns=SCHEMA_COLS)

def distribute_week_by_targets(
    week_start: date,
    weekly_targets: dict,
    sessions_per_mod: dict,
    key_sessions: dict,
    paces: dict,
    user_preferred_days: dict | None,
    user_id: str,
) -> pd.DataFrame:
    days = week_range(week_start)
    rows = []

    weights = {
        "Corrida": [0.25, 0.20, 0.55],
        "Ciclismo": [0.40, 0.35, 0.25],
        "Natação": [0.60, 0.40],
        "Força/Calistenia": [0.60, 0.40],
        "Mobilidade": [0.60, 0.40],
    }
    default_days = {
        "Corrida": [2, 4, 6],
        "Ciclismo": [1, 3, 5],
        "Natação": [0, 2],
        "Força/Calistenia": [1, 4],
        "Mobilidade": [0, 6],
    }

    mod_volumes = {}
    for mod, weekly_vol in weekly_targets.items():
        weekly_vol = float(weekly_vol or 0.0)
        n = int(sessions_per_mod.get(mod, 0))
        if weekly_vol <= 0 or n <= 0:
            continue

        unit = UNITS_ALLOWED[mod]
        target_total = _round_to_step_sum(weekly_vol, unit)

        w_template = weights.get(mod)
        if w_template is None:
            w = [1.0 / n] * n
        else:
            w = _expand_to_n(w_template, n)
            s = sum(w)
            w = [1.0 / n] * n if s == 0 else [x / s for x in w]

        volumes = [_round_to_step_sum(target_total * wi, unit) for wi in w]
        diff = target_total - sum(volumes)
        if abs(diff) > 1e-9:
            max_idx = max(range(len(volumes)), key=lambda i: volumes[i])
            volumes[max_idx] = _round_to_step_sum(volumes[max_idx] + diff, unit)

        mod_volumes[mod] = volumes

    session_assignments = {i: [] for i in range(7)}
    for mod, volumes in mod_volumes.items():
        n = len(volumes)
        prefs = (user_preferred_days or {}).get(mod, default_days.get(mod, list(range(7))))
        day_idx = prefs + [i for i in range(7) if i not in prefs]
        day_idx = day_idx[:n]

        tipos_base = TIPOS_MODALIDADE.get(mod, ["Treino"])
        tipos = _expand_to_n(tipos_base, n)

        key_tipo = (key_sessions or {}).get(mod, "")
        if key_tipo and key_tipo in tipos:
            max_i = max(range(n), key=lambda i: volumes[i])
            tipos[max_i] = key_tipo

        for i in range(n):
            session_assignments[day_idx[i]].append((mod, volumes[i], tipos[i]))

    for i, d in enumerate(days):
        sessions = session_assignments.get(i, [])
        if not sessions:
            rows.append({
                "UserID": user_id,
                "UID": generate_uid(user_id),
                "Data": d,
                "Start": "",
                "End": "",
                "Modalidade": "Descanso",
                "Tipo de Treino": "Ativo/Passivo",
                "Volume": 0.0,
                "Unidade": "min",
                "RPE": 0,
                "Detalhamento": "Dia de descanso.",
                "Observações": "",
                "Status": "Planejado",
                "adj": 0.0,
                "AdjAppliedAt": "",
                "ChangeLog": "[]",
                "LastEditedAt": "",
                "WeekStart": week_start,
            })
        else:
            for mod, vol, tipo in sessions:
                unit = UNITS_ALLOWED[mod]
                detail = prescribe_detail(mod, tipo, vol, unit, paces)
                rows.append({
                    "UserID": user_id,
                    "UID": generate_uid(user_id),
                    "Data": d,
                    "Start": "",
                    "End": "",
                    "Modalidade": mod,
                    "Tipo de Treino": tipo,
                    "Volume": vol,
                    "Unidade": unit,
                    "RPE": 0,
                    "Detalhamento": detail,
                    "Observações": "",
                    "Status": "Planejado",
                    "adj": 0.0,
                    "AdjAppliedAt": "",
                    "ChangeLog": "[]",
                    "LastEditedAt": "",
                    "WeekStart": week_start,
                })

    return pd.DataFrame(rows, columns=SCHEMA_COLS)

# ----------------------------------------------------------------------------
# Horários x disponibilidade
# ----------------------------------------------------------------------------

def assign_times_to_week(week_df: pd.DataFrame, slots, use_availability: bool):
    df = week_df.copy()
    if "Start" not in df.columns:
        df["Start"] = ""
    if "End" not in df.columns:
        df["End"] = ""

    free = normalize_slots(slots) if use_availability else slots
    dur = timedelta(minutes=DEFAULT_TRAINING_DURATION_MIN)

    for idx, row in df.iterrows():
        if row["Modalidade"] == "Descanso":
            df.at[idx, "Start"] = ""
            df.at[idx, "End"] = ""
            continue

        if use_availability:
            assigned = False
            for si, slot in enumerate(free):
                if slot["start"].date() != row["Data"]:
                    continue
                if slot["end"] - slot["start"] >= dur:
                    start_dt = slot["start"]
                    end_dt = start_dt + dur
                    df.at[idx, "Start"] = start_dt.isoformat()
                    df.at[idx, "End"] = end_dt.isoformat()
                    if slot["end"] == end_dt:
                        free.pop(si)
                    else:
                        free[si]["start"] = end_dt
                    assigned = True
                    break
            if not assigned:
                s = datetime.combine(row["Data"], time(6, 0))
                df.at[idx, "Start"] = s.isoformat()
                df.at[idx, "End"] = (s + dur).isoformat()
        else:
            s = datetime.combine(row["Data"], time(6, 0))
            df.at[idx, "Start"] = s.isoformat()
            df.at[idx, "End"] = (s + dur).isoformat()

    return df, (free if use_availability else slots)

def subtract_trainings_from_slots(week_df: pd.DataFrame, slots):
    trainings = []
    for _, r in week_df.iterrows():
        if r["Modalidade"] == "Descanso":
            continue
        s = parse_iso(r.get("Start", ""))
        e = parse_iso(r.get("End", ""))
        if s and e and e > s:
            trainings.append({"start": s, "end": e})

    if not trainings or not slots:
        return normalize_slots(slots)

    trainings = sorted(trainings, key=lambda x: x["start"])
    new_slots = []
    for slot in normalize_slots(slots):
        segs = [slot]
        for t in trainings:
            tmp = []
            for seg in segs:
                s, e = seg["start"], seg["end"]
                ts, te = t["start"], t["end"]
                if te <= s or ts >= e:
                    tmp.append(seg)
                else:
                    if ts <= s and te >= e:
                        pass
                    elif ts <= s < te < e:
                        tmp.append({"start": te, "end": e})
                    elif s < ts < e <= te:
                        tmp.append({"start": s, "end": ts})
                    elif s < ts and te < e:
                        tmp.append({"start": s, "end": ts})
                        tmp.append({"start": te, "end": e})
            segs = tmp
        new_slots.extend(segs)
    return normalize_slots(new_slots)

def update_availability_from_current_week(user_id: str, week_start: date):
    slots = get_week_availability(user_id, week_start)
    if not slots:
        return
    df = st.session_state.get("df", pd.DataFrame()).copy()
    if df.empty:
        return
    week_df = week_slice(df[df["UserID"] == user_id], week_start)
    new_slots = subtract_trainings_from_slots(week_df, slots)
    set_week_availability(user_id, week_start, new_slots)

# ----------------------------------------------------------------------------
# Exportações
# ----------------------------------------------------------------------------

def generate_ics(df: pd.DataFrame) -> str:
    ics = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//TriPlano//Planner//EN\n"
    for _, row in df.iterrows():
        start = row["StartDT"]
        end = row["EndDT"]
        summary = f"{row['Modalidade']} - {row['Tipo de Treino']}"
        vol_val = float(row["Volume"]) if str(row["Volume"]).strip() != "" else 0.0
        description = (
            f"Volume: {vol_val:g} {row['Unidade']}\n"
            f"{row['Detalhamento']}\n"
            f"Status: {row['Status']}"
        )
        ics += "BEGIN:VEVENT\n"
        ics += f"UID:{start.strftime('%Y%m%d%H%M%S')}-{hash(summary)}@triplano.app\n"
        ics += f"DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%SZ')}\n"
        ics += f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}\n"
        ics += f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}\n"
        ics += f"SUMMARY:{summary}\n"
        ics += f"DESCRIPTION:{description}\n"
        ics += "END:VEVENT\n"
    ics += "END:VCALENDAR\n"
    return ics

class PDF(FPDF):
    def header(self):
        if self.page_no() == 1:
            self.set_font("Arial", "B", 15)
            self.cell(0, 10, pdf_safe("Plano de Treino Semanal"), 0, 1, "C")
            self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.cell(
            0,
            10,
            pdf_safe(
                f"Página {self.page_no()}/{{nb}} | Gerado em {datetime.now().strftime('%d/%m/%Y')}"
            ),
            0,
            0,
            "C",
        )

def generate_pdf(df: pd.DataFrame, week_start: date) -> bytes:
    if df.empty:
        pdf = PDF()
        pdf.alias_nb_pages()
        pdf.add_page()
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 10, pdf_safe("Sem treinos para esta semana."), 0, 1, "L")
        return pdf.output(dest="S").encode("latin-1")

    df = df.copy()
    df = df.sort_values(["Data", "StartDT"]).reset_index(drop=True)

    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Arial", "", 10)
    pdf.cell(
        0,
        5,
        pdf_safe(
            f"Semana: {week_start.strftime('%d/%m/%Y')} a "
            f"{(week_start + timedelta(days=6)).strftime('%d/%m/%Y')}"
        ),
        0,
        1,
    )
    pdf.ln(5)

    # Página 1: tabela com horários
    col_widths = [22, 16, 16, 26, 32, 16, 12, 70]
    headers = ["Data", "Início", "Fim", "Modalidade", "Tipo", "Volume", "Unid.", "Detalhamento"]

    pdf.set_font("Arial", "B", 9)
    pdf.set_fill_color(220, 220, 220)
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, pdf_safe(h), 1, 0, "C", 1)
    pdf.ln()

    pdf.set_font("Arial", "", 8)
    for _, row in df.iterrows():
        vol_val = float(row["Volume"]) if str(row["Volume"]).strip() != "" else 0.0
        mod = row["Modalidade"]
        if mod == "Descanso" and vol_val <= 0:
            continue

        color = MODALITY_COLORS.get(mod, (255, 255, 255))
        data_val = row["Data"]
        if isinstance(data_val, str):
            try:
                data_val = datetime.fromisoformat(data_val).date()
            except Exception:
                data_val = week_start

        data_str = data_val.strftime("%d/%m (%a)")
        ini_str = row["StartDT"].strftime("%H:%M")
        fim_str = row["EndDT"].strftime("%H:%M")
        tipo = str(row["Tipo de Treino"])
        vol = f"{vol_val:g}"
        unit = row["Unidade"]
        detail = str(row["Detalhamento"])

        text_color = MODALITY_TEXT_COLORS.get(mod, (0, 0, 0))
        line_h = 5

        pdf.set_fill_color(*color)
        pdf.set_text_color(*text_color)
        pdf.cell(col_widths[0], line_h, pdf_safe(data_str), 1, 0, "L", 1)
        pdf.cell(col_widths[1], line_h, pdf_safe(ini_str), 1, 0, "C", 1)
        pdf.cell(col_widths[2], line_h, pdf_safe(fim_str), 1, 0, "C", 1)
        pdf.cell(col_widths[3], line_h, pdf_safe(mod), 1, 0, "L", 1)
        pdf.cell(col_widths[4], line_h, pdf_safe(tipo), 1, 0, "L", 1)
        pdf.cell(col_widths[5], line_h, pdf_safe(vol), 1, 0, "R", 1)
        pdf.cell(col_widths[6], line_h, pdf_safe(unit), 1, 0, "C", 1)

        pdf.set_text_color(0, 0, 0)
        pdf.set_fill_color(255, 255, 255)
        x = pdf.get_x()
        y = pdf.get_y()
        pdf.multi_cell(col_widths[7], line_h, pdf_safe(detail), 1, "L")
        pdf.set_xy(10, y + line_h)
        pdf.ln(0)

    # Página 2: calendário visual alinhado ao timeGridWeek
    if not df.empty:
        pdf.add_page(orientation="L")
        pdf.set_auto_page_break(auto=False)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, pdf_safe("Calendário Semanal (visual)"), 0, 1, "C")
        pdf.ln(2)

        left_margin = 10
        top_margin = 18
        right_margin = 10
        bottom_margin = 10

        page_w = pdf.w
        page_h = pdf.h

        grid_left = left_margin
        grid_top = top_margin + 6
        grid_right = page_w - right_margin
        grid_bottom = page_h - bottom_margin

        grid_w = grid_right - grid_left
        grid_h = grid_bottom - grid_top

        days = week_range(week_start)
        n_days = 7
        col_w = grid_w / n_days

        start_hour = 5
        end_hour = 21
        hours_range = end_hour - start_hour
        if hours_range <= 0:
            hours_range = 1

        pdf.set_font("Arial", "B", 8)
        for i, d in enumerate(days):
            x = grid_left + i * col_w
            pdf.set_xy(x, top_margin)
            label = d.strftime("%a %d/%m")
            pdf.cell(col_w, 6, pdf_safe(label), 0, 0, "C")

        pdf.set_draw_color(230, 230, 230)

        for h in range(start_hour, end_hour + 1):
            y = grid_top + (h - start_hour) / hours_range * grid_h
            pdf.line(grid_left, y, grid_right, y)
            pdf.set_font("Arial", "", 6)
            pdf.set_xy(grid_left - 8, y - 2)
            pdf.cell(7, 4, f"{h:02d}h", 0, 0, "R")

        for i in range(n_days + 1):
            x = grid_left + i * col_w
            pdf.line(x, grid_top, x, grid_bottom)

        pdf.set_font("Arial", "", 6)
        for _, row in df.iterrows():
            vol_val = float(row["Volume"]) if str(row["Volume"]).strip() != "" else 0.0
            mod = row["Modalidade"]
            if mod == "Descanso" and vol_val <= 0:
                continue

            start = row["StartDT"]
            end = row["EndDT"]
            day_idx = (start.date() - week_start).days
            if day_idx < 0 or day_idx >= 7:
                continue

            s_hour = start.hour + start.minute / 60
            e_hour = end.hour + end.minute / 60
            if e_hour <= start_hour or s_hour >= end_hour:
                continue
            s_hour = max(s_hour, start_hour)
            e_hour = min(e_hour, end_hour)
            if e_hour <= s_hour:
                e_hour = s_hour + 0.25

            y1 = grid_top + (s_hour - start_hour) / hours_range * grid_h
            y2 = grid_top + (e_hour - start_hour) / hours_range * grid_h
            x1 = grid_left + day_idx * col_w + 0.7
            w = col_w - 1.4
            h = max(y2 - y1, 2)

            tipo = str(row["Tipo de Treino"])
            unit = row["Unidade"]
            txt_vol = f"{vol_val:g}{unit}" if vol_val > 0 else ""
            title = f"{mod} {tipo} {txt_vol}".strip()

            color = MODALITY_COLORS.get(mod, (200, 200, 200))
            pdf.set_fill_color(*color)
            pdf.set_draw_color(255, 255, 255)
            pdf.rect(x1, y1, w, h, "F")

            txt_color = MODALITY_TEXT_COLORS.get(mod, (255, 255, 255))
            pdf.set_text_color(*txt_color)
            pdf.set_xy(x1 + 0.8, y1 + 0.6)
            max_chars = int(w / 1.7)
            pdf.multi_cell(w - 1, 3, pdf_safe(title[:max_chars]), 0, "L")

        pdf.set_text_color(0, 0, 0)
        pdf.set_draw_color(0, 0, 0)

    return pdf.output(dest="S").encode("latin-1")

# ----------------------------------------------------------------------------
# Métricas & Dashboard
# ----------------------------------------------------------------------------

def calculate_metrics(df: pd.DataFrame):
    if df.empty:
        return pd.DataFrame(), df

    df = df.copy()
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0)
    df["Load"] = df.apply(
        lambda r: normalize_volume_for_load(r["Modalidade"], r["Volume"], r["Unidade"]),
        axis=1,
    )
    weekly = df.groupby("WeekStart").agg(
        TotalLoad=("Load", "sum"),
        TotalVolume=("Volume", "sum"),
        NumSessions=("Data", "count"),
    ).reset_index()
    weekly = weekly.sort_values("WeekStart").reset_index(drop=True)
    weekly["CTL"] = weekly["TotalLoad"].rolling(window=6, min_periods=1).mean()
    weekly["ATL"] = weekly["TotalLoad"].rolling(window=2, min_periods=1).mean()
    weekly["TSB"] = weekly["CTL"] - weekly["ATL"]
    return weekly, df

def plot_load_chart(weekly_metrics: pd.DataFrame):
    if weekly_metrics.empty:
        st.warning("Sem dados de carga para gerar o gráfico.")
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(weekly_metrics["WeekStart"], weekly_metrics["CTL"], label="CTL")
    ax.plot(weekly_metrics["WeekStart"], weekly_metrics["ATL"], label="ATL")
    ax2 = ax.twinx()
    ax2.bar(
        weekly_metrics["WeekStart"],
        weekly_metrics["TSB"],
        alpha=0.3,
        width=5,
        label="TSB",
    )
    ax.set_xlabel("Semana")
    ax.set_ylabel("Carga")
    ax2.set_ylabel("TSB")
    ax.legend(loc="upper left")
    ax2.legend(loc="upper right")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    st.pyplot(fig)

# ----------------------------------------------------------------------------
# Periodização — generate_cycle
# ----------------------------------------------------------------------------

def generate_cycle(
    cycle_start_week: date,
    num_weeks: int,
    base_load: float,
    phase_proportions: dict,
    sessions_per_mod: dict,
    paces: dict,
    user_preferred_days: dict,
    key_sessions: dict,
    user_id: str,
) -> pd.DataFrame:
    all_weeks = []
    for w in range(num_weeks):
        ws = cycle_start_week + timedelta(days=7 * w)
        phase = PHASES[w % 4]

        weekly_targets = {}
        for mod in MODALIDADES:
            prop = phase_proportions.get(mod, {}).get(phase, 0.0)
            weekly_targets[mod] = base_load * float(prop)

        week_df = distribute_week_by_targets(
            ws,
            weekly_targets,
            sessions_per_mod,
            key_sessions,
            paces,
            user_preferred_days,
            user_id,
        )
        week_df, _ = assign_times_to_week(week_df, [], use_availability=False)
        all_weeks.append(week_df)

    if not all_weeks:
        return pd.DataFrame(columns=SCHEMA_COLS)
    return pd.concat(all_weeks, ignore_index=True)[SCHEMA_COLS]

# ----------------------------------------------------------------------------
# UI Principal
# ----------------------------------------------------------------------------

def get_week_key(d: date) -> str:
    return d.strftime("%Y-%W")

@st.cache_data(show_spinner=False)
def canonical_week_df(user_id: str, week_start: date) -> pd.DataFrame:
    # Sempre partimos do df persistido
    base_df = st.session_state["df"].copy()

    # Filtra apenas a semana e o usuário
    week_end = week_start + timedelta(days=7)
    mask = (
        (base_df["UserID"] == user_id)
        & (base_df["Data"] >= week_start)
        & (base_df["Data"] < week_end)
    )

    week_df = base_df[mask].copy()
    if week_df.empty:
        return pd.DataFrame(columns=SCHEMA_COLS)

    # Normaliza tipos
    if not np.issubdtype(week_df["Data"].dtype, np.datetime64):
        week_df["Data"] = pd.to_datetime(week_df["Data"]).dt.date

    week_df["Volume"] = pd.to_numeric(week_df["Volume"], errors="coerce").fillna(0.0)

    # Garante UID estável: qualquer UID vazio ganha um novo e isso é salvo no base_df
    if "UID" not in week_df.columns:
        week_df["UID"] = ""

    missing_uid_mask = (week_df["UID"] == "") | week_df["UID"].isna()
    if missing_uid_mask.any():
        for idx in week_df[missing_uid_mask].index:
            new_uid = generate_uid(user_id)
            week_df.at[idx, "UID"] = new_uid
            base_df.at[idx, "UID"] = new_uid

        # Atualiza sessão + CSV para que os handlers (eventDrop/eventClick) enxerguem os mesmos UIDs do calendário
        save_user_df(user_id, base_df)

    # StartDT / EndDT canônicos
    week_df["StartDT"] = week_df["Start"].apply(parse_iso)
    week_df["StartDT"] = week_df.apply(
        lambda r: r["StartDT"] or datetime.combine(r["Data"], time(6, 0)),
        axis=1,
    )

    week_df["EndDT"] = week_df["End"].apply(parse_iso)
    week_df["EndDT"] = week_df.apply(
        lambda r: r["EndDT"] or (r["StartDT"] + timedelta(minutes=DEFAULT_TRAINING_DURATION_MIN)),
        axis=1,
    )

    # Remove Descanso puro (como combinado para calendário/PDF/ICS)
    mask_valid = ~((week_df["Modalidade"] == "Descanso") & (week_df["Volume"] <= 0))
    week_df = week_df[mask_valid]

    # Ordena
    week_df = week_df.sort_values(["Data", "StartDT"]).reset_index(drop=True)

    return week_df


def main():
    st.set_page_config(page_title="TriPlano", layout="wide")

    # LOGIN
    if "user_id" not in st.session_state:
        st.title("Bem-vindo ao TriPlano 🌀")
        st.markdown("Faça login ou crie sua conta para começar.")

        tab1, tab2 = st.tabs(["Entrar", "Criar Conta"])
        with tab1:
            with st.form("login_form"):
                email = st.text_input("E-mail", key="login_email")
                submitted = st.form_submit_button("Entrar")
                if submitted:
                    user = get_user(email)
                    if user is not None:
                        st.session_state["user_id"] = user["user_id"]
                        st.session_state["user_name"] = user["nome"]
                        st.success("Login bem-sucedido!")
                        safe_rerun()
                    else:
                        st.error("Usuário não encontrado. Verifique o e-mail ou crie uma conta.")

        with tab2:
            with st.form("signup_form"):
                email = st.text_input("E-mail", key="signup_email")
                nome = st.text_input("Seu Nome", key="signup_nome")
                submitted = st.form_submit_button("Criar Conta")
                if submitted:
                    user = get_user(email)
                    if user is not None:
                        st.warning("E-mail já cadastrado. Use Entrar.")
                    elif not nome:
                        st.error("Informe seu nome para criar a conta.")
                    else:
                        if create_user(email, nome):
                            st.session_state["user_id"] = email
                            st.session_state["user_name"] = nome
                            st.success("Conta criada com sucesso!")
                            safe_rerun()
                        else:
                            st.error("Erro ao criar conta.")
        st.stop()
    user_id = st.session_state["user_id"]
    user_name = st.session_state.get("user_name", user_id)
    if "all_df" not in st.session_state:
        all_df = load_all()
        st.session_state["all_df"] = all_df
        st.session_state["df"] = all_df[all_df["UserID"] == user_id].copy()
    # CONTEXTO
    if "df" not in st.session_state:
        all_df = st.session_state["all_df"]
        st.session_state["df"] = all_df[all_df["UserID"] == user_id].copy()

    if "current_week_start" not in st.session_state:
        st.session_state["current_week_start"] = monday_of_week(today())
    if "frozen_targets" not in st.session_state:
        st.session_state["frozen_targets"] = {}

    df = st.session_state["df"]

    # SIDEBAR
    st.sidebar.title("TriPlano 🌀")
    st.sidebar.markdown(f"👤 **{user_name}**  \n`{user_id}`")
    if st.sidebar.button("Sair"):
        logout()

    menu = st.sidebar.radio(
        "Navegação",
        ["📅 Planejamento Semanal", "📈 Dashboard", "⚙️ Periodização"],
        index=0,
    )
    st.sidebar.markdown("---")
    st.sidebar.markdown("Desenvolvido por **Matheus Vianna**")

    # ---------------- PLANEJAMENTO SEMANAL ----------------
    if menu == "📅 Planejamento Semanal":
        st.header("📅 Planejamento Semanal")

        # 1. Paces
        st.subheader("1. Parâmetros de Prescrição")
        col_p1, col_p2, col_p3 = st.columns(3)
        paces = {
            "run_pace_min_per_km": col_p1.number_input(
                "Corrida (min/km)", value=5.0, min_value=3.0, max_value=10.0, step=0.1, format="%.1f"
            ),
            "swim_sec_per_100m": col_p2.number_input(
                "Natação (seg/100m)", value=110, min_value=60, max_value=200, step=5
            ),
            "bike_kmh": col_p3.number_input(
                "Ciclismo (km/h)", value=32.0, min_value=15.0, max_value=50.0, step=0.5, format="%.1f"
            ),
        }

        # 2. Metas
        st.subheader("2. Metas Semanais (Volume, Sessões, Preferências)")
        weekly_targets = {}
        sessions_per_mod = {}
        cols_mod = st.columns(len(MODALIDADES))
        cols_sess = st.columns(len(MODALIDADES))

        dias_semana_options = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sáb": 5, "Dom": 6}
        default_days = {
            "Corrida": [2, 4, 6],
            "Ciclismo": [1, 3, 5],
            "Natação": [0, 2],
            "Força/Calistenia": [1, 4],
            "Mobilidade": [0, 6],
        }

        for i, mod in enumerate(MODALIDADES):
            unit = UNITS_ALLOWED[mod]

            weekly_targets[mod] = cols_mod[i].number_input(
                f"{mod} ({unit})/sem",
                value=float(st.session_state.get(f"target_{mod}", 0.0)),
                min_value=0.0,
                step=_unit_step(unit),
                format="%.1f" if unit == "km" else "%g",
                key=f"target_{mod}",
            )

            default_selected = [
                abrev for abrev, idx in dias_semana_options.items()
                if idx in default_days.get(mod, [])
            ]
            cols_mod[i].multiselect(
                f"Dias {mod}",
                options=list(dias_semana_options.keys()),
                key=f"pref_days_{mod}",
                default=default_selected,
            )

            cols_sess[i].selectbox(
                f"Treino chave {mod}",
                options=[""] + TIPOS_MODALIDADE.get(mod, []),
                key=f"key_sess_{mod}",
            )

            default_sessions = 3 if mod in ["Corrida", "Ciclismo"] else 2
            sessions_per_mod[mod] = cols_sess[i].number_input(
                f"Sessões {mod}",
                value=int(st.session_state.get(f"sess_{mod}", default_sessions)),
                min_value=0,
                max_value=5,
                step=1,
                key=f"sess_{mod}",
            )

        st.markdown("---")

        # 3. Semana atual
        col1, col2, col3 = st.columns([1, 2, 1])
        if col1.button("⬅️ Semana anterior"):
            st.session_state["current_week_start"] -= timedelta(days=7)
            canonical_week_df.clear()
            safe_rerun()
        week_start = st.session_state["current_week_start"]
        col2.subheader(f"Semana de {week_start.strftime('%d/%m/%Y')}")
        if col3.button("Semana seguinte ➡️"):
            st.session_state["current_week_start"] += timedelta(days=7)
            canonical_week_df.clear()
            safe_rerun()

        week_df_raw = week_slice(df, week_start)
        if week_df_raw.empty:
            week_df_raw = default_week_df(week_start, user_id)

        week_slots = get_week_availability(user_id, week_start)

        # 3.1 Modo de agendamento
        st.subheader("3. Como encaixar os treinos?")
        modo_agendamento = st.radio(
            "Opção de agendamento",
            ["Usar horários livres", "Ignorar horários livres"],
            horizontal=True,
        )

        st.markdown("---")

        # 4. Gerar semana automática
        col_btn1, _, _ = st.columns(3)
        if col_btn1.button("📆 Gerar Semana Automática"):
            dias_map = dias_semana_options
            current_preferred_days = {
                mod: [dias_map[d] for d in st.session_state.get(f"pref_days_{mod}", [])]
                for mod in MODALIDADES
            }
            key_sessions = {mod: st.session_state.get(f"key_sess_{mod}", "") for mod in MODALIDADES}

            new_week_df = distribute_week_by_targets(
                week_start,
                weekly_targets,
                sessions_per_mod,
                key_sessions,
                paces,
                current_preferred_days,
                user_id,
            )

            use_avail = (modo_agendamento == "Usar horários livres")
            new_week_df, updated_slots = assign_times_to_week(
                new_week_df,
                week_slots,
                use_avail,
            )

            if use_avail:
                updated_slots = subtract_trainings_from_slots(new_week_df, updated_slots)
                set_week_availability(user_id, week_start, updated_slots)

            user_df = st.session_state["df"]
            others = user_df[user_df["WeekStart"] != week_start]
            user_df_new = pd.concat([others, new_week_df], ignore_index=True)
            save_user_df(user_id, user_df_new)
            st.success("Semana gerada e salva!")
            canonical_week_df.clear()
            safe_rerun()

        st.markdown("---")

        # Recarrega df do usuário após geração
        df = st.session_state["df"]

        # 5. Calendário: usa df canônico (MESMO dataset do PDF/ICS)
        st.subheader("4. Calendário da Semana")

        week_df_can = canonical_week_df(user_id, week_start)
        events = []

        # Treinos
        for _, row in week_df_can.iterrows():
            uid = row["UID"]
            vol_val = float(row["Volume"]) if str(row["Volume"]).strip() != "" else 0.0

            title = f"{row['Modalidade']} - {row['Tipo de Treino']}"
            if vol_val > 0:
                title += f" ({vol_val:g} {row['Unidade']})"

            start_dt = row["StartDT"]
            end_dt = row["EndDT"]

            color_rgb = MODALITY_COLORS.get(row["Modalidade"])
            color = "#{:02X}{:02X}{:02X}".format(*color_rgb) if color_rgb else None

            ev = {
                "id": uid,
                "title": title,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "extendedProps": {
                    "uid": uid,
                    "type": "treino",
                },
            }
            if color:
                ev["color"] = color
            events.append(ev)

        # Slots livres
        for i, s in enumerate(week_slots):
            events.append({
                "id": f"free-{i}",
                "title": "Livre",
                "start": s["start"].isoformat(),
                "end": s["end"].isoformat(),
                "color": "#27AE60",
                "extendedProps": {
                    "type": "free",
                    "slot_index": i,
                },
            })

        options = {
            "initialView": "timeGridWeek",
            "locale": "pt-br",
            "firstDay": 1,
            "slotMinTime": "05:00:00",
            "slotMaxTime": "21:00:00",
            "allDaySlot": False,
            "selectable": True,
            "editable": True,
            "eventDurationEditable": True,
            "headerToolbar": {"left": "", "center": "", "right": ""},
            "height": "650px",
        }

        cal_state = calendar(
            events=events,
            options=options,
            key=f"cal_semana_{get_week_key(week_start)}",
        )

        if cal_state and "select" in cal_state:
            sel = cal_state["select"]
            s = parse_iso(sel.get("start"))
            e = parse_iso(sel.get("end"))
            if s and e and e > s:
                conflito = False
                for _, r in week_df_can.iterrows():
                    ts = r["StartDT"]
                    te = r["EndDT"]
                    if ts and te and not (te <= s or ts >= e):
                        conflito = True
                        break
                if not conflito:
                    week_slots.append({"start": s, "end": e})
                    set_week_availability(user_id, week_start, week_slots)
                    canonical_week_df.clear()
                    safe_rerun()

        # 5.2 Drag/resize treinos -> atualiza df base (logo afeta canonical e PDF/ICS)
        def handle_move_or_resize(ev_dict, action_label):
            ev = ev_dict.get("event", {}) if ev_dict else {}
            ext = ev.get("extendedProps", {}) or {}
            if ext.get("type") != "treino":
                return

            uid = ext.get("uid")
            start = parse_iso(ev.get("start"))
            end = parse_iso(ev.get("end"))

            if not uid or not start or not end or end <= start:
                st.toast(f"ERRO: Dados inválidos para {action_label} ({uid}).", icon="🚨")
                return

            # Acessa o DataFrame do session_state
            df_current = st.session_state["df"]
            mask = (df_current["UserID"] == user_id) & (df_current["UID"] == uid)
            
            if not mask.any():
                st.toast(f"ERRO: Treino {uid} não encontrado no DataFrame.", icon="🚨")
                return
            
            idx = df_current[mask].index[0]
            old_row = df_current.loc[idx].copy()

            # Atualiza os dados no DataFrame do session_state
            df_current.loc[idx, "Start"] = start.isoformat()
            df_current.loc[idx, "End"] = end.isoformat()
            df_current.loc[idx, "Data"] = start.date()
            df_current.loc[idx, "WeekStart"] = monday_of_week(start.date())
            df_current.loc[idx, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
            df_current.loc[idx, "ChangeLog"] = append_changelog(old_row, df_current.loc[idx])

            # Apenas atualiza o estado na memória. O salvamento será explícito.
            st.session_state["df"] = df_current
            st.toast(f"Treino {uid} {action_label}. Clique em 'Salvar Semana' para persistir.", icon="💾")

            # Limpa o cache e força o Streamlit a redesenhar a página
            canonical_week_df.clear()
            safe_rerun()

        if cal_state and "eventDrop" in cal_state:
            handle_move_or_resize(cal_state["eventDrop"], "movido")

        if cal_state and "eventResize" in cal_state:
            handle_move_or_resize(cal_state["eventResize"], "redimensionado")

        # 5.3 Clique eventos
        if cal_state and "eventClick" in cal_state:
            ev = cal_state["eventClick"]["event"]
            ext = ev.get("extendedProps", {}) or {}
            etype = ext.get("type")

            # Clique em Livre -> remove slot
            if etype == "free":
                s = parse_iso(ev.get("start"))
                e = parse_iso(ev.get("end"))
                new_slots = [
                    sl for sl in week_slots
                    if not (sl["start"] == s and sl["end"] == e)
                ]
                set_week_availability(user_id, week_start, new_slots)
                canonical_week_df.clear()
                safe_rerun()

            # Clique em treino -> popup edita treino e salva no df base (canonical lê daqui)
            if etype == "treino":
                uid = ext.get("uid")
                # Acessa o DataFrame do session_state
                df_current = st.session_state["df"]
                mask = (df_current["UserID"] == user_id) & (df_current["UID"] == uid)
                if not mask.any():
                    st.error("Treino não encontrado.")
                else:
                    idx = df_current[mask].index[0]
                    r = df_current.loc[idx]

                    st.markdown("---")
                    with st.container(border=True):
                        st.markdown("### 📝 Detalhes do treino")

                        # Garante que o horário lido para o pop-up é o mais recente
                        start_dt = parse_iso(r.get("Start", "")) or datetime.combine(r["Data"], time(6, 0))
                        end_dt = parse_iso(r.get("End", "")) or (start_dt + timedelta(minutes=DEFAULT_TRAINING_DURATION_MIN))
                        dur_min = int((end_dt - start_dt).total_seconds() / 60)

                        current_mod = r.get("Modalidade", "Corrida")
                        mod_options = MODALIDADES + ["Descanso"]
                        if current_mod not in mod_options:
                            current_mod = "Corrida"

                        new_mod = st.selectbox(
                            "Modalidade realizada",
                            options=mod_options,
                            index=mod_options.index(current_mod),
                            key=f"mod_{uid}",
                        )

                        tipos_opcoes = TIPOS_MODALIDADE.get(new_mod, ["Treino"])
                        current_tipo = r.get("Tipo de Treino", tipos_opcoes[0] if tipos_opcoes else "")
                        if current_tipo not in tipos_opcoes:
                            current_tipo = tipos_opcoes[0] if tipos_opcoes else ""

                        new_tipo = st.selectbox(
                            "Tipo de treino",
                            options=tipos_opcoes,
                            index=tipos_opcoes.index(current_tipo) if current_tipo in tipos_opcoes else 0,
                            key=f"tipo_{uid}",
                        )

                        unit = UNITS_ALLOWED.get(new_mod, r.get("Unidade", ""))
                        default_vol = float(r.get("Volume", 0.0) or 0.0)
                        new_vol = st.number_input(
                            f"Volume ({unit})",
                            min_value=0.0,
                            value=default_vol,
                            step=_unit_step(unit),
                            format="%.1f" if unit == "km" else "%g",
                            key=f"vol_{uid}",
                        )

                        st.markdown(
                            f"📅 **{start_dt.strftime('%d/%m/%Y')}** | "
                            f"⏰ {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
                        )

                        col_dt1, col_dt2 = st.columns(2)
                        new_date = col_dt1.date_input(
                            "Data do treino",
                            value=start_dt.date(),
                            key=f"dt_{uid}",
                        )
                        new_time = col_dt2.time_input(
                            "Horário de início",
                            value=start_dt.time(),
                            key=f"tm_{uid}",
                        )
                        new_dur = st.number_input(
                            "Duração (min)",
                            min_value=15,
                            max_value=300,
                            value=dur_min,
                            step=5,
                            key=f"dur_{uid}",
                        )

                        new_start = datetime.combine(new_date, new_time)
                        new_end = new_start + timedelta(minutes=int(new_dur))

                        new_rpe = st.slider(
                            "RPE (esforço percebido)",
                            0, 10,
                            int(r.get("RPE", 0) or 0),
                            key=f"rpe_{uid}",
                        )

                        new_obs = st.text_area(
                            "Comentário rápido",
                            value=str(r.get("Observações", "")),
                            key=f"obs_{uid}",
                        )

                        col_feito, col_nao, col_salvar = st.columns(3)

                        def apply_update(status_override=None):
                            df_upd = st.session_state["df"]
                            mask2 = (df_upd["UserID"] == user_id) & (df_upd["UID"] == uid)
                            if not mask2.any():
                                return
                            i2 = df_upd[mask2].index[0]
                            old_row = df_upd.loc[i2].copy()

                            df_upd.loc[i2, "Modalidade"] = new_mod
                            df_upd.loc[i2, "Tipo de Treino"] = new_tipo
                            df_upd.loc[i2, "Volume"] = new_vol
                            df_upd.loc[i2, "Unidade"] = UNITS_ALLOWED.get(new_mod, old_row.get("Unidade", ""))

                            df_upd.loc[i2, "Start"] = new_start.isoformat()
                            df_upd.loc[i2, "End"] = new_end.isoformat()
                            df_upd.loc[i2, "Data"] = new_start.date()
                            df_upd.loc[i2, "WeekStart"] = monday_of_week(new_start.date())

                            df_upd.loc[i2, "RPE"] = new_rpe
                            df_upd.loc[i2, "Observações"] = new_obs

                            if status_override is not None:
                                df_upd.loc[i2, "Status"] = status_override

                            df_upd.loc[i2, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
                            df_upd.loc[i2, "ChangeLog"] = append_changelog(old_row, df_upd.loc[i2])

                            save_user_df(user_id, df_upd)

                            ws_old = monday_of_week(old_row["Data"]) if not isinstance(old_row["Data"], str) else monday_of_week(datetime.fromisoformat(old_row["Data"]).date())
                            ws_new = monday_of_week(new_start.date())
                            update_availability_from_current_week(user_id, ws_old)
                            update_availability_from_current_week(user_id, ws_new)

                            canonical_week_df.clear()
                            safe_rerun()

                        if col_feito.button("✅ FEITO", key=f"feito_{uid}"):
                            apply_update("Realizado")
                        if col_nao.button("❌ NÃO FEITO", key=f"naofeito_{uid}"):
                            apply_update("Cancelado")
                        if col_salvar.button("💾 Salvar", key=f"save_{uid}"):
                            apply_update(None)

        # 5.4 Botão salvar semana (reforça persistência; canonical já lê direto de df)
        st.markdown("---")
        if st.button("💾 Salvar Semana Atual", key="save_week_changes"):
            try:
                # CORREÇÃO: Recuperar user_id do session_state para garantir o escopo
                current_user_id = st.session_state.get("user_id")
                
                if not current_user_id:
                    st.error("Erro: ID do usuário não encontrado na sessão. Por favor, faça login novamente.")
                    return

                # Acessa o DataFrame do usuário que está em memória (com as alterações)
                user_df_to_save = st.session_state["df"]
                
                save_user_df(current_user_id, user_df_to_save)

                # Após salvar, recarrega CSV para garantir que a memória reflita o disco
                df_from_csv = load_all()
                st.session_state["df"] = df_from_csv[df_from_csv["UserID"] == current_user_id].copy()
                st.session_state["all_df"] = df_from_csv
                # Salva o DataFrame completo no CSV
                
                
                st.success("As alterações da semana foram salvas com sucesso no CSV!")
                
                # Limpa o cache para forçar o recarregamento dos dados a partir do CSV na próxima interação
                canonical_week_df.clear()
                load_all.clear()

            except Exception as e:
                st.error(f"Ocorreu um erro ao salvar a semana: {e}")


        # 6. Exportações — usam SEMPRE o df canônico (mesmo do calendário)
        st.subheader("5. Exportar Semana Atual")

        # Força o recarregamento do canonical_week_df para garantir dados frescos para exportação
        week_df_export = canonical_week_df(user_id, week_start)
        col_exp1, col_exp2 = st.columns(2)

        if not week_df_export.empty:
            if col_exp1.download_button(
                "📤 Exportar .ICS",
                data=generate_ics(week_df_export),
                file_name=f"treino_{week_start.strftime('%Y%m%d')}.ics",
                mime="text/calendar",
            ):
                st.info("ICS gerado a partir do calendário atual.")

            pdf_bytes = generate_pdf(week_df_export, week_start)
            if col_exp2.download_button(
                "📕 Exportar PDF",
                data=pdf_bytes,
                file_name=f"treino_{week_start.strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
            ):
                st.info("PDF gerado a partir do calendário atual.")
        else:
            st.info("Nenhum treino (além de descanso) nesta semana.")

        # Metas congeladas (placeholder)
        st.markdown("---")
        st.subheader("Metas da Semana (Congeladas)")
        if st.button("Congelar Metas da Semana"):
            targets_key = get_week_key(week_start)
            current_targets = {mod: st.session_state.get(f"target_{mod}", 0.0) for mod in MODALIDADES}
            st.session_state["frozen_targets"][targets_key] = current_targets
            st.info(f"Metas para a semana de {week_start.strftime('%d/%m')} congeladas.")

        frozen_key = get_week_key(week_start)
        if frozen_key in st.session_state["frozen_targets"]:
            st.write("Metas congeladas para esta semana:")
            st.json(st.session_state["frozen_targets"][frozen_key])

    # ---------------- DASHBOARD ----------------
    elif menu == "📈 Dashboard":
        st.header("📈 Dashboard de Performance")
        weekly_metrics, df_with_load = calculate_metrics(df)
        plot_load_chart(weekly_metrics)
        st.dataframe(df_with_load)

    # ---------------- PERIODIZAÇÃO ----------------
    elif menu == "⚙️ Periodização":
        st.header("⚙️ Gerador de Periodização")
        with st.form("periodization_form"):
            st.markdown("### Definições do Ciclo")
            p_col1, p_col2, p_col3 = st.columns(3)
            cycle_start = p_col1.date_input("Início do ciclo", value=monday_of_week(today()))
            num_weeks = p_col2.number_input("Duração (semanas)", min_value=4, max_value=24, value=12, step=1)
            base_load = p_col3.number_input("Carga base (TSS/semana)", min_value=100, max_value=1000, value=300, step=10)

            st.markdown("### Proporção de Carga por Fase (% da carga base)")
            phase_props = {}
            cols_phase = st.columns(len(PHASES))
            for i, phase in enumerate(PHASES):
                phase_props[phase] = {}
                cols_phase[i].markdown(f"**{phase}**")
                for mod in MODALIDADES:
                    default_prop = {"Base": 0.8, "Build": 1.0, "Peak": 1.2, "Recovery": 0.6}.get(phase, 0.8)
                    phase_props[phase][mod] = cols_phase[i].number_input(
                        f"% {mod}",
                        min_value=0.0, max_value=2.0, value=default_prop, step=0.1, format="%.1f",
                        key=f"prop_{phase}_{mod}"
                    )

            submitted = st.form_submit_button("Gerar Ciclo de Treinamento")
            if submitted:
                dias_map = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sáb": 5, "Dom": 6}
                pref_days = {mod: [dias_map[d] for d in st.session_state.get(f"pref_days_{mod}", [])] for mod in MODALIDADES}
                key_sess = {mod: st.session_state.get(f"key_sess_{mod}", "") for mod in MODALIDADES}
                sess_per_mod = {mod: st.session_state.get(f"sess_{mod}", 2) for mod in MODALIDADES}

                new_cycle_df = generate_cycle(
                    cycle_start,
                    num_weeks,
                    base_load,
                    phase_props,
                    sess_per_mod,
                    paces,
                    pref_days,
                    key_sess,
                    user_id,
                )

                # Remove semanas existentes que serão substituídas
                existing_df = st.session_state["df"]
                cycle_end = cycle_start + timedelta(weeks=num_weeks)
                df_outside_cycle = existing_df[
                    (existing_df["WeekStart"] < cycle_start) | (existing_df["WeekStart"] >= cycle_end)
                ]
                
                final_df = pd.concat([df_outside_cycle, new_cycle_df], ignore_index=True)
                save_user_df(user_id, final_df)
                st.success(f"{num_weeks} semanas de treino geradas e salvas!")
                canonical_week_df.clear()
                safe_rerun()

if __name__ == "__main__":
    main()
