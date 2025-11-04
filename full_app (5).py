import os
import json
from datetime import date, timedelta, datetime

import numpy as np
import pandas as pd
import streamlit as st
from fpdf import FPDF
import matplotlib.pyplot as plt  # needed for charts


# -----------------------------------------------------------------------------
# Rerun helper
#
# In some Streamlit versions, `st.experimental_rerun()` may not exist or may be
# inaccessible in deployed environments.  Conversely, newer versions expose
# `st.rerun()`.  To avoid AttributeError exceptions, we define a helper
# that checks which function is available and calls it accordingly.  All
# rerun calls in this app should go through `safe_rerun()` so that generating
# a week or cycle always triggers a refresh without crashing.

def safe_rerun() -> None:
    """Trigger a rerun of the Streamlit app if supported.

    This helper first tries to call `st.rerun()` (available in recent
    Streamlit versions).  If that doesn't exist, it falls back to
    `st.experimental_rerun()`.  If neither method is available, the
    function silently does nothing.  Using this helper prevents
    AttributeError when deploying to environments that don't expose
    experimental APIs.
    """
    # Newer Streamlit versions provide st.rerun()
    if hasattr(st, "rerun") and callable(getattr(st, "rerun")):
        try:
            st.rerun()  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    # Older versions expose st.experimental_rerun()
    if hasattr(st, "experimental_rerun") and callable(getattr(st, "experimental_rerun")):
        try:
            st.experimental_rerun()  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    # If neither is available, do nothing
    return


"""
Single‑file Streamlit application for a triathlon training planner.

This script includes:
  • Data persistence using a CSV file under the `data` directory.
  • A weekly planner with a data editor and automatic session prescription
    based on user‑defined weekly targets.
  • Dashboard showing summary metrics and rolling 4‑week volume charts.
  • Periodization wizard to generate a multi‑week cycle.
  • Export functions for PDF and calendar (.ics) outputs.
  • Navigation via a sidebar radio menu.

To run:
  pip install -r requirements.txt
  streamlit run full_app.py

The app uses minimal Streamlit features (no external CSS) for simplicity.
"""

# ----------------------------------------------------------------------------
# Constants and schema
# ----------------------------------------------------------------------------
DATA_DIR = "data"
EXPORT_DIR = "exports"
CSV_PATH = os.path.join(DATA_DIR, "treinos.csv")

# Modalities and units
UNITS_ALLOWED = {
    "Corrida": "km",
    "Ciclismo": "km",
    "Natação": "m",
    "Força/Calistenia": "min",
    "Mobilidade": "min",
}
MODALIDADES = list(UNITS_ALLOWED.keys())
STATUS_CHOICES = ["Planejado", "Realizado", "Adiado", "Cancelado"]

SCHEMA_COLS = [
    "Data", "Modalidade", "Tipo de Treino", "Volume", "Unidade", "RPE",
    "Detalhamento", "Observações", "Status", "adj",
    "AdjAppliedAt", "ChangeLog", "LastEditedAt", "WeekStart"
]

# Templates for session types (per modality)
TIPOS_MODALIDADE = {
    "Corrida": ["Regenerativo", "Força", "Longão", "Tempo Run"],
    "Ciclismo": ["Endurance", "Intervalado", "Cadência", "Força/Subida"],
    "Natação": ["Técnica", "Ritmo", "Intervalado", "Contínuo"],
    "Força/Calistenia": ["Força máxima", "Resistência muscular", "Core/Estabilidade", "Mobilidade/Recuperação"],
    "Mobilidade": ["Soltura", "Recuperação", "Prevenção"],
}

# ----------------------------------------------------------------------------
# Helper functions: data persistence
# ----------------------------------------------------------------------------

def ensure_dirs():
    """Ensure the data and export directories exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)


def init_csv_if_needed():
    """Create an empty CSV file if it doesn't exist."""
    ensure_dirs()
    if not os.path.exists(CSV_PATH):
        df = pd.DataFrame(columns=SCHEMA_COLS)
        df.to_csv(CSV_PATH, index=False)


@st.cache_data(show_spinner=False)
def load_all() -> pd.DataFrame:
    """Load all training data from the CSV file."""
    init_csv_if_needed()
    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    if not df.empty:
        df["Data"] = pd.to_datetime(df["Data"]).dt.date
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date
        # numeric columns
        for col in ["Volume", "RPE", "adj"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        # fix unit by modality
        for i, r in df.iterrows():
            mod = r.get("Modalidade", "")
            if mod in UNITS_ALLOWED and r.get("Unidade", "") != UNITS_ALLOWED[mod]:
                df.at[i, "Unidade"] = UNITS_ALLOWED[mod]
    return df


def save_all(df: pd.DataFrame):
    """Save the given DataFrame to the CSV file."""
    ensure_dirs()
    df_out = df.copy()
    df_out["Data"] = pd.to_datetime(df_out["Data"]).dt.date.astype(str)
    df_out["WeekStart"] = pd.to_datetime(df_out["WeekStart"]).dt.date.astype(str)
    df_out.to_csv(CSV_PATH, index=False)


# ----------------------------------------------------------------------------
# Date helpers
# ----------------------------------------------------------------------------

def monday_of_week(d: date) -> date:
    """Return the Monday of the week containing the given date."""
    return d - timedelta(days=d.weekday())


def week_range(start_monday: date):
    """Return list of dates (Monday..Sunday) for the week starting at start_monday."""
    return [start_monday + timedelta(days=i) for i in range(7)]


def week_slice(df: pd.DataFrame, start: date) -> pd.DataFrame:
    """Return the subset of df for the given week [start, start+7 days)."""
    end = start + timedelta(days=7)
    return df[(df["Data"] >= start) & (df["Data"] < end)].copy()


def prev_week_slice(df: pd.DataFrame, start: date) -> pd.DataFrame:
    """Return the subset for the previous week."""
    return week_slice(df, start - timedelta(days=7))


def default_week_df(week_start: date) -> pd.DataFrame:
    """Create an empty week template for the given start date."""
    recs = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        recs.append({
            "Data": d,
            "Modalidade": "",
            "Tipo de Treino": "",
            "Volume": 0.0,
            "Unidade": "",
            "RPE": 0,
            "Detalhamento": "",
            "Observações": "",
            "Status": "Planejado",
            "adj": 0.0,
            "AdjAppliedAt": "",
            "ChangeLog": "[]",
            "LastEditedAt": "",
            "WeekStart": week_start,
        })
    return pd.DataFrame(recs, columns=SCHEMA_COLS)


# ----------------------------------------------------------------------------
# Prescription and session utilities
# ----------------------------------------------------------------------------

def lock_unit(modalidade: str) -> str:
    """Return the allowed unit for a modality."""
    return UNITS_ALLOWED.get(modalidade, "")


def _unit_step(unit: str) -> float:
    if unit == "m": return 50.0
    if unit == "km": return 0.1
    return 1.0


def _round_by_unit(vol: float, unit: str) -> float:
    if unit == "m": return float(int(round(vol / 50.0) * 50))
    if unit == "km": return round(vol, 1)
    return round(vol, 0)


def _round_to_step_sum(total: float, unit: str) -> float:
    step = _unit_step(unit)
    v = float(total)
    if step == 50.0: return round(v / step) * step
    if step == 0.1: return round(v, 1)
    return round(v, 0)


def prescribe_detail(mod: str, tipo: str, volume: float, unit: str, paces: dict) -> str:
    """Return a text description for a session given the modality, type, volume and paces."""
    vol = float(volume or 0)
    rp = float(paces.get("run_pace_min_per_km", 0))
    bk = float(paces.get("bike_kmh", 0))
    sp = float(paces.get("swim_sec_per_100m", 0))

    if mod == "Corrida":
        if tipo == "Regenerativo":
            dur = int(round(vol * rp)) if unit == "km" and rp > 0 else ""
            return f"Reg Z1/Z2 {vol:g} km (~{dur} min)."
        if tipo == "Força":
            reps = max(6, min(12, int(vol)))
            return f"Força em subida {reps}×(60s forte) rec 2min."
        if tipo == "Longão":
            dur = int(round(vol * rp)) if unit == "km" and rp > 0 else ""
            return f"Longão {vol:g} km Z2/Z3 (~{dur} min)."
        if tipo == "Tempo Run":
            bloco = max(20, min(40, int(vol * 6)))
            return f"Tempo {bloco}min em Z3/Z4."
    elif mod == "Ciclismo":
        if tipo == "Endurance":
            h = vol / (bk or 28.0)
            return f"Endurance {vol:g} km (~{h:.1f}h a {bk or 28} km/h)."
        if tipo == "Intervalado":
            n = max(4, min(6, int(vol / 5)))
            return f"Intervalado {n}×(6min Z4) rec 3min."
        if tipo == "Cadência": return "Cadência: 5×(3min @100–110rpm) rec 2min."
        if tipo == "Força/Subida": return "Torque: 6×(4min baixa cadência) rec 3min."
    elif mod == "Natação":
        if tipo == "Técnica": return "Drills: respiração bilateral, EVF; 8×50m, 20s."
        if tipo == "Ritmo":
            reps = max(6, min(10, int(vol / 200)))
            return f"{reps}×200m sustentável, 20–30s."
        if tipo == "Intervalado":
            reps = max(12, min(20, int(vol / 50)))
            alvo = f"{int(sp)}s/100m" if sp > 0 else "—"
            return f"{reps}×50m forte, 20–30s. Alvo ~{alvo}."
        if tipo == "Contínuo": return f"Contínuo {vol / 1000.0:.1f} km Z2/Z3."
    elif mod == "Força/Calistenia":
        if tipo == "Força máxima": return "Força: 5×3 (barra, paralela, agacho) rec 2–3min."
        if tipo == "Resistência muscular": return "Resistência: 4×12–20 empurrar/puxar/perna."
        if tipo == "Core/Estabilidade": return "Core: circuito pranchas/anti-rotação/hollow 15–20min."
        if tipo == "Mobilidade/Recuperação": return "Mobilidade ativa 15–25min."
    elif mod == "Mobilidade":
        if tipo == "Soltura": return "Soltura 15–25min com alongamentos dinâmicos."
        if tipo == "Recuperação": return "Respiração + alongamentos leves 10–20min."
        if tipo == "Prevenção": return "Mobilidade ombro/quadril/torácica 15–20min."
    return ""


def distribute_week_by_targets(
    week_start: date,
    weekly_targets: dict,
    sessions_per_mod: dict,
    default_mix: dict,
    paces: dict,
    user_preferred_days: dict | None = None,
) -> pd.DataFrame:
    """Generate a weekly plan given targets and number of sessions per modality."""
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
        "Corrida": [2, 4, 6],  # Wed, Fri, Sun
        "Ciclismo": [1, 3, 5],
        "Natação": [0, 2],
        "Força/Calistenia": [1, 4],
        "Mobilidade": [0, 6],
    }
    for mod, weekly_vol in weekly_targets.items():
        weekly_vol = float(weekly_vol or 0)
        if weekly_vol <= 0: continue
        n = int(sessions_per_mod.get(mod, 0))
        if n <= 0: continue
        unit = UNITS_ALLOWED[mod]
        target_total = _round_to_step_sum(weekly_vol, unit)
        # Day selection
        prefs = user_preferred_days.get(mod) if user_preferred_days else default_days.get(mod, list(range(7)))
        # Build sequence of day indices
        day_idx = prefs + [i for i in range(7) if i not in prefs]
        day_idx = day_idx[:n]
        # Types
        types_pref = default_mix.get(mod) or TIPOS_MODALIDADE.get(mod, [])
        if mod == "Corrida":
            # Ensure long run first
            others = [t for t in types_pref if t != "Longão"]
            tipos_seq = ["Longão"] + [others[i % len(others)] for i in range(n - 1)] if n >= 1 else []
        else:
            tipos_seq = [types_pref[i % len(types_pref)] for i in range(n)]
        # Weights per session
        base_w = np.array(weights.get(mod, [1.0] * n), dtype=float)[:n]
        if base_w.sum() == 0: base_w = np.ones(n)
        base_w /= base_w.sum()
        if mod == "Corrida" and "Longão" in tipos_seq:
            # Put highest weight on Longão
            max_pos = int(np.argmax(base_w))
            base_w[0], base_w[max_pos] = base_w[max_pos], base_w[0]
        raw = (target_total * base_w).tolist()
        # Round to unit step
        rounded = [_round_by_unit(v, unit) for v in raw]
        # Adjust sum difference
        diff = target_total - sum(rounded)
        step = _unit_step(unit)
        if diff > 0:
            j = 0
            while diff > 1e-9:
                rounded[j] += step
                diff -= step
                j = (j + 1) % len(rounded)
        elif diff < 0:
            need = -diff
            order = np.argsort(rounded)[::-1]
            for j in order:
                while need > 1e-9 and rounded[j] - step >= 0:
                    rounded[j] -= step
                    need -= step
                if need <= 1e-9: break
        # Generate sessions
        for i in range(n):
            d = days[day_idx[i]]
            tipo = tipos_seq[i]
            vol = float(rounded[i])
            det = prescribe_detail(mod, tipo, vol, unit, paces)
            rows.append({
                "Data": d,
                "Modalidade": mod,
                "Tipo de Treino": tipo,
                "Volume": vol,
                "Unidade": unit,
                "RPE": 5,
                "Detalhamento": det,
                "Observações": "",
                "Status": "Planejado",
                "adj": 0.0,
                "AdjAppliedAt": "",
                "ChangeLog": "[]",
                "LastEditedAt": "",
                "WeekStart": week_start,
            })
    df = pd.DataFrame(rows, columns=SCHEMA_COLS)
    return df


# ----------------------------------------------------------------------------
# KPI and charts
# ----------------------------------------------------------------------------

LOAD_COEFF = {
    "Corrida": 1.0,
    "Ciclismo": 0.6,
    "Natação": 1.2,
    "Força/Calistenia": 0.3,
    "Mobilidade": 0.2,
}


def _norm(mod, vol, unit):
    if mod == "Natação": return (vol or 0.0) / 1000.0 * LOAD_COEFF.get(mod, 1.0)
    if mod in ("Força/Calistenia", "Mobilidade"): return (vol or 0.0) * LOAD_COEFF.get(mod, 1.0)
    return (vol or 0.0) * LOAD_COEFF.get(mod, 1.0)


def calc_week_summary(df_week: pd.DataFrame, df_prev: pd.DataFrame):
    vol_by_mod = {m: float(df_week.loc[df_week["Modalidade"] == m, "Volume"].sum()) for m in MODALIDADES}
    load_this = 0.0
    for _, r in df_week.iterrows():
        load_this += _norm(r["Modalidade"], float(r["Volume"]), r["Unidade"]) * float(r["RPE"])
    load_prev = 0.0
    for _, r in df_prev.iterrows():
        load_prev += _norm(r["Modalidade"], float(r["Volume"]), r["Unidade"]) * float(r["RPE"])
    var_pct = None if load_prev <= 0 else (load_this - load_prev) / load_prev * 100.0
    alert_pico = var_pct is not None and var_pct > 20.0
    # desequilíbrio: se carga de corrida/bike/natação >65%
    load_mods = {}
    for m in ["Corrida", "Ciclismo", "Natação"]:
        sub = df_week[df_week["Modalidade"] == m]
        l = 0.0
        for _, r in sub.iterrows():
            l += _norm(m, float(r["Volume"]), r["Unidade"]) * float(r["RPE"])
        load_mods[m] = l
    total_three = sum(load_mods.values())
    alert_deseq = any(total_three > 0 and (v / total_three) > 0.65 for v in load_mods.values())
    alert_estag = var_pct is not None and abs(var_pct) <= 2.0
    return vol_by_mod, load_this, load_prev, var_pct, alert_pico, alert_deseq, alert_estag


def rolling_4week(df_all: pd.DataFrame, modalidade: str) -> pd.DataFrame:
    """Return rolling 4‑week volume summary for a modality."""
    if df_all.empty:
        return pd.DataFrame(columns=["WeekStart", "Volume", "Rolling4"])
    df = df_all[df_all["Modalidade"] == modalidade].copy()
    if df.empty:
        return pd.DataFrame(columns=["WeekStart", "Volume", "Rolling4"])
    df["WeekStart"] = pd.to_datetime(df["Data"]).dt.to_period("W").apply(lambda p: p.start_time.date())
    agg = df.groupby("WeekStart")["Volume"].sum().reset_index().sort_values("WeekStart")
    agg["Rolling4"] = agg["Volume"].rolling(4, min_periods=1).mean()
    return agg


# ----------------------------------------------------------------------------
# Export functions: PDF and calendar
# ----------------------------------------------------------------------------

PDF_REPLACE = str.maketrans({"—": "-", "–": "-", "“": '"', "”": '"', "’": "'", "•": "-"})


def pdf_safe(s: str) -> str:
    if s is None:
        return ""
    t = str(s).translate(PDF_REPLACE)
    return (t.encode("latin-1", "ignore").decode("latin-1"))


def estimate_duration_minutes(row: pd.Series, paces: dict) -> int:
    mod = (row.get("Modalidade", "") or "").strip()
    vol = float(row.get("Volume", 0) or 0)
    unit = (row.get("Unidade", "") or "").strip()
    rp = float(paces.get("run_pace_min_per_km", 0))
    bk = float(paces.get("bike_kmh", 0))
    sp = float(paces.get("swim_sec_per_100m", 0))
    if mod == "Corrida" and unit == "km" and vol > 0 and rp > 0:
        return int(round(vol * rp))
    if mod == "Ciclismo" and unit == "km" and vol > 0 and bk > 0:
        return int(round(60.0 * vol / bk))
    if mod == "Natação" and unit == "m" and vol > 0 and sp > 0:
        return int(round((vol / 100.0) * (sp / 60.0)))
    if mod in ("Força/Calistenia", "Mobilidade") and unit == "min" and vol > 0:
        return int(round(vol))
    return 60


def ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def make_week_ics(df_week: pd.DataFrame, title_prefix="Treino", paces=None) -> bytes:
    paces = paces or {}
    df = df_week.copy()
    df["Data"] = pd.to_datetime(df["Data"]).dt.date
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Planner//BR//PT-BR"]
    for _, r in df.iterrows():
        try:
            if float(r["Volume"]) <= 0:
                continue
        except Exception:
            continue
        d = r["Data"]
        summary = f"{title_prefix}: {r['Modalidade']} — {r['Tipo de Treino']}"
        desc = f"{r.get('Detalhamento', '')}"
        start_dt = datetime(d.year, d.month, d.day, 6, 30)
        dur = estimate_duration_minutes(r, paces)
        end_dt = start_dt + timedelta(minutes=dur)
        lines += [
            "BEGIN:VEVENT",
            f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{ics_escape(summary)}",
            f"DESCRIPTION:{ics_escape(desc)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode("utf-8")


def build_week_pdf(df_week: pd.DataFrame, week_start: date) -> bytes:
    df = df_week.copy()
    df["Data"] = pd.to_datetime(df["Data"]).dt.date
    days = sorted(df["Data"].unique())
    pdf = FPDF("P", "mm", "A4")
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, pdf_safe(f"Plano Semanal ({week_start} a {week_start + timedelta(days=6)})"), ln=1)
    pdf.set_font("Helvetica", size=11)
    for d in days:
        sub = df[df["Data"] == d].copy()
        sub = sub[pd.to_numeric(sub["Volume"], errors="coerce").fillna(0) > 0]
        if sub.empty: continue
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, pdf_safe(d.strftime("%d/%m/%Y (%a)")), ln=1)
        pdf.set_font("Helvetica", size=11)
        for _, r in sub.iterrows():
            line = f"- {r['Modalidade']} — {r['Tipo de Treino']} - {float(r['Volume']):g} {r['Unidade']}"
            pdf.multi_cell(0, 6, pdf_safe(line))
            det = (r.get("Detalhamento", "") or "").strip()
            if det:
                pdf.set_font("Helvetica", size=10)
                pdf.multi_cell(0, 5, pdf_safe(f"   • {det}"))
                pdf.set_font("Helvetica", size=11)
        pdf.ln(2)
    total = df[pd.to_numeric(df["Volume"], errors="coerce").fillna(0) > 0]
    totals = total.groupby(["Modalidade", "Unidade"])["Volume"].sum().reset_index()
    if not totals.empty:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, pdf_safe("Totais da semana:"), ln=1)
        pdf.set_font("Helvetica", size=11)
        for _, r in totals.iterrows():
            pdf.cell(0, 6, pdf_safe(f"- {r['Modalidade']}: {r['Volume']:g} {r['Unidade']}"), ln=1)
    return bytes(pdf.output(dest="S").encode("latin1"))


# ----------------------------------------------------------------------------
# Main Streamlit app
# ----------------------------------------------------------------------------


def main():
    st.set_page_config(page_title="Treino Planner", page_icon="🏁", layout="wide")
    st.title("🏁 Treino Planner — App Único")
    st.sidebar.title("Menu")
    page = st.sidebar.radio(
        "Navegar",
        ["Dashboard", "Planejamento semanal", "Ciclo/Periodização", "Análise & Evolução", "Configurações"],
        index=0,
    )
    # Shared state: paces and weekly targets saved in st.session_state
    if "paces" not in st.session_state:
        st.session_state["paces"] = {"run_pace_min_per_km": 0.0, "bike_kmh": 0.0, "swim_sec_per_100m": 0.0}
    if "weekly_targets" not in st.session_state:
        st.session_state["weekly_targets"] = {
            "Corrida": 30.0,
            "Ciclismo": 150.0,
            "Natação": 2000.0,
            "Força/Calistenia": 60.0,
            "Mobilidade": 30.0,
        }
    if "sessions_guess" not in st.session_state:
        st.session_state["sessions_guess"] = {"Corrida": 3, "Ciclismo": 3, "Natação": 2, "Força/Calistenia": 2, "Mobilidade": 2}
    # Data
    df_all = load_all()
    hoje = date.today()

    # The top-level `safe_rerun()` is defined above.  No local helper needed here.

    if page == "Dashboard":
        st.header("Dashboard")
        if df_all.empty:
            st.info("Nenhum treino cadastrado ainda. Gere semanas na aba Planejamento.")
        else:
            cols = st.columns(len(MODALIDADES))
            for i, mod in enumerate(MODALIDADES):
                with cols[i]:
                    agg = rolling_4week(df_all, mod)
                    last_vol = agg["Volume"].iloc[-1] if not agg.empty else 0.0
                    st.metric(f"{mod} (última sem)", f"{last_vol:.1f}")
                    fig, ax = plt.subplots()
                    if not agg.empty:
                        ax.plot(agg["WeekStart"], agg["Volume"], marker="o", label="Volume")
                        ax.plot(agg["WeekStart"], agg["Rolling4"], marker="x", label="Média 4s")
                    ax.set_title(mod)
                    ax.legend()
                    st.pyplot(fig, clear_figure=True)

    elif page == "Planejamento semanal":
        st.header("Planejamento semanal")
        # Explanation for how to generate a week
        st.info(
            "Defina suas metas de volume e número de sessões na barra lateral e, em seguida, "
            "use o botão **Gerar semana** para criar automaticamente uma semana de treinos.\n\n"
            "Você pode editar as sessões na tabela abaixo ou salvar diretamente."
        )
        # Choose week
        week_start = st.date_input("Semana (segunda-feira)", value=monday_of_week(hoje))
        if week_start.weekday() != 0:
            st.info("Data ajustada para segunda-feira da semana.")
            week_start = monday_of_week(week_start)
        # Paces & targets from session state, show form to edit
        with st.expander("Paces e Metas Semanais"):
            st.session_state["paces"]["run_pace_min_per_km"] = st.number_input(
                "Pace corrida (min/km)", 0.0, 20.0, st.session_state["paces"].get("run_pace_min_per_km", 0.0), step=0.1
            )
            st.session_state["paces"]["bike_kmh"] = st.number_input(
                "Velocidade ciclismo (km/h)", 0.0, 50.0, st.session_state["paces"].get("bike_kmh", 0.0), step=0.5
            )
            st.session_state["paces"]["swim_sec_per_100m"] = st.number_input(
                "Pace natação (s/100m)", 0.0, 300.0, st.session_state["paces"].get("swim_sec_per_100m", 0.0), step=5.0
            )
            st.write("---")
            for mod in MODALIDADES:
                val = st.session_state["weekly_targets"].get(mod, 0.0)
                st.session_state["weekly_targets"][mod] = st.number_input(
                    f"{mod} (meta)", 0.0, 10000.0, float(val), step=5.0 if mod in ("Ciclismo",) else 1.0
                )
            st.write("---")
            for mod in MODALIDADES:
                val = st.session_state["sessions_guess"].get(mod, 0)
                st.session_state["sessions_guess"][mod] = st.slider(
                    f"Sessões {mod}", 0, 7, int(val)
                )
        # Load week
        df_week = week_slice(df_all, week_start)
        if df_week.empty:
            df_week = default_week_df(week_start)
        # Data editor
        edited = st.data_editor(
            df_week.copy(),
            column_config={
                "Data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
                "Modalidade": st.column_config.SelectboxColumn("Modalidade", options=MODALIDADES, required=True),
                "Tipo de Treino": st.column_config.SelectboxColumn(
                    "Tipo", options=sorted(set(sum([TIPOS_MODALIDADE[m] for m in MODALIDADES], [])))
                ),
                "Volume": st.column_config.NumberColumn("Volume"),
                "Unidade": st.column_config.TextColumn("Unidade"),
                "RPE": st.column_config.NumberColumn("RPE", min_value=0, max_value=10, step=1),
                "Detalhamento": st.column_config.TextColumn("Detalhamento"),
                "Observações": st.column_config.TextColumn("Observações"),
                "Status": st.column_config.SelectboxColumn("Status", options=STATUS_CHOICES),
                "adj": st.column_config.NumberColumn("Adj (futuro)"),
                "WeekStart": st.column_config.DateColumn("WeekStart", format="DD/MM/YYYY"),
            },
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
        )
        # Ensure unit and auto‑fill details
        for i, r in edited.iterrows():
            mod = (r["Modalidade"] or "").strip()
            if mod:
                edited.at[i, "Unidade"] = lock_unit(mod)
                tipo = (r["Tipo de Treino"] or "").strip()
                if tipo and not r["Detalhamento"]:
                    edited.at[i, "Detalhamento"] = prescribe_detail(mod, tipo, r["Volume"], edited.at[i, "Unidade"], st.session_state["paces"])
        # Buttons for actions
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            if st.button("💾 Salvar semana"):
                # replace week with edited
                out = df_all[(df_all["Data"] < week_start) | (df_all["Data"] >= week_start + timedelta(days=7))]
                df_new = pd.concat([out, edited], ignore_index=True)
                save_all(df_new)
                st.success("Semana salva.")
        with col2:
            # Button to generate a full week from the targets
            if st.button("📆 Gerar semana"):
                # Generate a draft week using the current targets and sessions guesses
                draft = distribute_week_by_targets(
                    week_start,
                    weekly_targets=st.session_state["weekly_targets"],
                    sessions_per_mod=st.session_state["sessions_guess"],
                    default_mix=TIPOS_MODALIDADE,
                    paces=st.session_state["paces"],
                    user_preferred_days=None,
                )
                # Remove existing sessions for this week and add the draft
                out = df_all[(df_all["Data"] < week_start) | (df_all["Data"] >= week_start + timedelta(days=7))]
                df_new = pd.concat([out, draft], ignore_index=True)
                save_all(df_new)
                # Update df_all and reload page so the table reflects the new plan
                df_all = df_new
                st.success("Semana gerada e salva.")
                # Trigger a rerun to refresh the table after saving the new week
                safe_rerun()
        with col3:
            if st.button("📤 Exportar .ICS"):
                ics = make_week_ics(edited, title_prefix="Treino", paces=st.session_state["paces"])
                st.download_button(
                    "⬇️ Baixar .ics",
                    data=ics,
                    file_name=f"semana_{week_start}.ics",
                    mime="text/calendar",
                )
        with col4:
            if st.button("📕 Exportar PDF"):
                pdf = build_week_pdf(edited, week_start)
                st.download_button(
                    "⬇️ Baixar PDF",
                    data=pdf,
                    file_name=f"semana_{week_start}.pdf",
                    mime="application/pdf",
                )
        # KPIs
        st.markdown("---")
        df_all_after = load_all()
        df_week_saved = week_slice(df_all_after, week_start)
        df_prev = prev_week_slice(df_all_after, week_start)
        vol_by_mod, load_this, load_prev, var_pct, alert_pico, alert_deseq, alert_estag = calc_week_summary(df_week_saved, df_prev)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Corrida (km)", f"{vol_by_mod['Corrida']:.1f}")
        m2.metric("Ciclismo (km)", f"{vol_by_mod['Ciclismo']:.1f}")
        m3.metric("Natação (m)", f"{vol_by_mod['Natação']:.0f}")
        m4.metric("Força/Cal (min)", f"{vol_by_mod['Força/Calistenia']:.0f}")
        m5.metric("Mobilidade (min)", f"{vol_by_mod['Mobilidade']:.0f}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Carga (sem)", f"{load_this:.1f}")
        c2.metric("Carga anterior", f"{load_prev:.1f}")
        c3.metric("Variação", f"{(var_pct if var_pct is not None else 0):+.1f}%")
        if alert_pico: st.error("⚠️ Pico de carga (>20%)")
        if alert_deseq: st.warning("⚠️ Desequilíbrio >65% em corrida/bike/natação")
        if alert_estag: st.info("ℹ️ Estagnação: variação ~0%")

    elif page == "Ciclo/Periodização":
        st.header("Ciclo / Periodização")
        start_date = st.date_input("Início do ciclo (segunda-feira)", value=monday_of_week(hoje))
        if start_date.weekday() != 0:
            st.info("Ajustado para segunda-feira.")
            start_date = monday_of_week(start_date)
        n_weeks = st.slider("Semanas no ciclo", 4, 12, 8)
        base_load = st.number_input(
            "Carga base (proxy da meta total)",
            0.0,
            500.0,
            50.0,
            step=1.0,
            help="A carga base combina volume e intensidade de todas as modalidades: 1 km de corrida = 1.0, 1 km de ciclismo = 0.6, 1.000 m de natação = 1.2, 60 min de força = 0.3 e 60 min de mobilidade = 0.2."
        )
        st.markdown("#### Proporções da carga por modalidade")
        # Explain proportions
        st.info(
            "A 'carga base' (proxy) é uma estimativa da carga semanal total considerando todas as modalidades. "
            "Ela combina volume e intensidade: 1 km de corrida corresponde a 1.0, 1 km de ciclismo a 0.6, 1000 m de natação a 1.2, "
            "60 minutos de força a 0.3 e 60 minutos de mobilidade a 0.2. "
            "As proporções indicam como essa carga total será dividida entre as modalidades no ciclo. A soma das proporções deve ser 1.0."
        )
        base_split = {}
        default_split = {"Corrida": 0.35, "Ciclismo": 0.40, "Natação": 0.15, "Força/Calistenia": 0.07, "Mobilidade": 0.03}
        for mod in MODALIDADES:
            default_val = default_split.get(mod, 0.0)
            base_split[mod] = st.number_input(
                f"{mod} (fração da carga)",
                0.0,
                1.0,
                default_val,
                0.01,
                help="Frações sugeridas: Corrida 0.35, Ciclismo 0.40, Natação 0.15, Força/Calistenia 0.07, Mobilidade 0.03"
            )
        if st.button("📈 Gerar ciclo"):
            # Regenerate all weeks in the cycle based on the defined base load and fractions
            df_all_local = df_all.copy()
            def week_targets(load, split):
                out = {}
                for mod, frac in split.items():
                    part = load * frac
                    if mod == "Natação":
                        out[mod] = round(part * 1000)
                    elif mod in ("Força/Calistenia", "Mobilidade"):
                        out[mod] = round(part * 60)
                    else:
                        out[mod] = round(part, 1)
                return out
            for i in range(n_weeks):
                ws = start_date + timedelta(days=7 * i)
                # Determine load for this week (periodization phases)
                if i < max(1, n_weeks - 3):
                    if i < (n_weeks // 2):
                        load = base_load * (1 + 0.05 * i)
                    else:
                        load = base_load * (1 + 0.05 * (n_weeks // 2) + 0.07 * (i - (n_weeks // 2)))
                elif i == n_weeks - 2:
                    load = base_load * 1.20
                else:
                    load = base_load * 0.65
                targets = week_targets(load, base_split)
                draft = distribute_week_by_targets(
                    ws,
                    weekly_targets=targets,
                    sessions_per_mod=st.session_state["sessions_guess"],
                    default_mix=TIPOS_MODALIDADE,
                    paces=st.session_state["paces"],
                    user_preferred_days=None,
                )
                # Remove existing entries for that week and add the new draft
                df_all_local = df_all_local[(df_all_local["Data"] < ws) | (df_all_local["Data"] >= ws + timedelta(days=7))]
                df_all_local = pd.concat([df_all_local, draft], ignore_index=True)
            save_all(df_all_local)
            st.success("Ciclo gerado e salvo. Consulte as semanas em Planejamento semanal.")
            # Trigger a rerun to update pages after generating the cycle
            safe_rerun()

    elif page == "Análise & Evolução":
        st.header("Análise & Evolução")
        if df_all.empty:
            st.info("Sem dados ainda. Gere semanas em Planejamento.")
        else:
            for mod in MODALIDADES:
                agg = rolling_4week(df_all, mod)
                st.subheader(mod)
                fig, ax = plt.subplots()
                if not agg.empty:
                    ax.plot(agg["WeekStart"], agg["Volume"], marker="o", label="Volume")
                    ax.plot(agg["WeekStart"], agg["Rolling4"], marker="x", label="Média 4s")
                ax.set_xlabel("Semana")
                ax.set_ylabel("Volume")
                ax.legend()
                st.pyplot(fig, clear_figure=True)

    elif page == "Configurações":
        st.header("Configurações")
        st.markdown("- O app utiliza Streamlit puro, sem CSS extra.\n- Ajuste paces e metas dentro da aba Planejamento semanal.")


if __name__ == "__main__":
    main()