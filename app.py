import os, io, json, math
from datetime import datetime, date, timedelta
from dateutil.parser import parse as dtparse

import pandas as pd
import numpy as np
import streamlit as st
from fpdf import FPDF
import matplotlib.pyplot as plt
import unicodedata
import random  # NEW

# ------------------------------------------------------------
# Planner de Treinos (versão turbo, single-file)
# Upgrades implementados:
# - Unidade travada por modalidade (validação forte)
# - Wizard de metas semanais a partir de meta total
# - Templates parametrizados (séries geradas por volume/pace)
# - Status do dia + KPIs de aderência
# - Alertas (pico de carga, desequilíbrio, estagnação)
# - Paces alvos por modalidade (corrida, bike, natação)
# - Periodização 4-12 semanas com fases (Base/Build/Peak/Recovery)
# - Modo mobile (form por dia) além do editor em tabela
# - Auditoria de mudanças (ChangeLog JSON) + LastEditedAt
# - Export .ics (semana e ciclo), PDF semanal omitindo zeros
# - Persistência CSV (Sheets opcional se ligar depois)
# ------------------------------------------------------------


PDF_REPLACE = str.maketrans({
    "—": "-",  # em dash
    "–": "-",  # en dash
    "“": '"',
    "”": '"',
    "’": "'",
    "•": "-",
})

def pdf_safe(s: str) -> str:
    if s is None:
        return ""
    t = str(s).translate(PDF_REPLACE)
    # normaliza e remove o que não é latin-1
    return unicodedata.normalize("NFKD", t).encode("latin-1", "ignore").decode("latin-1")

def estimate_duration_minutes(row: pd.Series, paces: dict) -> int:
    """
    Estima a duração (min) para .ics com base em volume e paces:
    - Corrida km → min: pace_min_per_km
    - Ciclismo km → min: 60 * km / kmh
    - Natação m → min: (m/100)* (sec/100m) / 60
    - Força/Mobilidade min → já é minuto
    Fallback: 60 min.
    """
    try:
        mod = (row.get("Modalidade", "") or "").strip()
        vol = float(row.get("Volume", 0) or 0)
        unit = (row.get("Unidade", "") or "").strip()
        rp = float(paces.get("run_pace_min_per_km") or 0)
        bk = float(paces.get("bike_kmh") or 0)
        sp = float(paces.get("swim_sec_per_100m") or 0)

        if mod == "Corrida" and unit == "km" and vol > 0 and rp > 0:
            return int(round(vol * rp))
        if mod == "Ciclismo" and unit == "km" and vol > 0 and bk > 0:
            return int(round(60.0 * vol / bk))
        if mod == "Natação" and unit == "m" and vol > 0 and sp > 0:
            return int(round((vol / 100.0) * (sp / 60.0)))
        if mod in ("Força/Calistenia", "Mobilidade") and unit == "min" and vol > 0:
            return int(round(vol))
    except Exception:
        pass
    return 60

# -----------------------
# CONSTANTES / SCHEMA
# -----------------------
DATA_DIR = "data"
EXPORT_DIR = "exports"
CSV_PATH = os.path.join(DATA_DIR, "treinos.csv")

SCHEMA_COLS = [
    "Data", "Modalidade", "Tipo de Treino", "Volume", "Unidade", "RPE",
    "Detalhamento", "Observações", "Status", "adj",
    "AdjAppliedAt", "ChangeLog", "LastEditedAt", "WeekStart"
]

UNITS_ALLOWED = {
    "Corrida": "km",
    "Ciclismo": "km",
    "Natação": "m",
    "Força/Calistenia": "min",
    "Mobilidade": "min",
}
MODALIDADES = list(UNITS_ALLOWED.keys())
STATUS_CHOICES = ["Planejado", "Realizado", "Adiado", "Cancelado"]

# Coeficientes para Carga Interna (ajustáveis)
LOAD_COEFF = {
    "Corrida": 1.0,
    "Ciclismo": 0.6,
    "Natação": 1.2,           # metros/1000 * 1.2
    "Força/Calistenia": 0.3,  # minutos
    "Mobilidade": 0.2         # minutos
}

# Tipos/descrições base (serão refinadas por templates)
TIPOS_MODALIDADE = {
    "Corrida": ["Regenerativo", "Força", "Longão", "Tempo Run"],
    "Ciclismo": ["Endurance", "Intervalado", "Cadência", "Força/Subida"],
    "Natação": ["Técnica", "Ritmo", "Intervalado", "Contínuo"],
    "Força/Calistenia": ["Força máxima", "Resistência muscular", "Core/Estabilidade", "Mobilidade/Recuperação"],
    "Mobilidade": ["Soltura", "Recuperação", "Prevenção"]
}

# -----------------------
# HELPERS
# -----------------------
def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)

def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())

def week_range(start_monday: date):
    return [start_monday + timedelta(days=i) for i in range(7)]

def today() -> date:
    return date.today()


def get_week_key(week_start: date) -> str:
    return pd.Timestamp(week_start).strftime("%Y-%m-%d")

def get_frozen_weekly_targets(week_start: date, live_targets: dict) -> dict:
    """
    Retorna as metas 'congeladas' dessa semana (se existirem); se não, congela agora com base nos live_targets.
    Usa st.session_state["frozen_targets"] (dict[str -> dict]).
    """
    if "frozen_targets" not in st.session_state:
        st.session_state["frozen_targets"] = {}
    wk = get_week_key(week_start)
    if wk not in st.session_state["frozen_targets"]:
        st.session_state["frozen_targets"][wk] = {
            k: float(live_targets.get(k, 0.0) or 0.0) for k in MODALIDADES
        }
    return st.session_state["frozen_targets"][wk]

def set_frozen_weekly_targets(week_start: date, targets: dict):
    if "frozen_targets" not in st.session_state:
        st.session_state["frozen_targets"] = {}
    wk = get_week_key(week_start)
    st.session_state["frozen_targets"][wk] = {
        k: float(targets.get(k, 0.0) or 0.0) for k in MODALIDADES
    }


def init_csv_if_needed():
    ensure_dirs()
    if not os.path.exists(CSV_PATH):
        df = pd.DataFrame(columns=SCHEMA_COLS)
        df.to_csv(CSV_PATH, index=False)

def load_all() -> pd.DataFrame:
    init_csv_if_needed()
    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    if not df.empty:
        df["Data"] = pd.to_datetime(df["Data"]).dt.date
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date
        for col in ["Volume", "RPE", "adj"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        for col in ["ChangeLog", "Detalhamento", "Observações"]:
            df[col] = df[col].astype(str)
        # Unidade coerente por modalidade (autocorreção leve)
        for i, r in df.iterrows():
            mod = r.get("Modalidade", "")
            if mod in UNITS_ALLOWED and r.get("Unidade", "") != UNITS_ALLOWED[mod]:
                df.at[i, "Unidade"] = UNITS_ALLOWED[mod]
    return df

def save_all(df: pd.DataFrame):
    df_out = df.copy()
    df_out["Data"] = pd.to_datetime(df_out["Data"]).dt.date.astype(str)
    if "WeekStart" in df_out.columns:
        df_out["WeekStart"] = pd.to_datetime(df_out["WeekStart"]).dt.date.astype(str)
    df_out.to_csv(CSV_PATH, index=False)

def default_week_df(week_start: date) -> pd.DataFrame:
    recs = []
    for d in week_range(week_start):
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
            "WeekStart": week_start
        })
    return pd.DataFrame(recs, columns=SCHEMA_COLS)

def lock_unit(modalidade: str) -> str:
    return UNITS_ALLOWED.get(modalidade, "")

def unit_is_valid(mod: str, unit: str) -> bool:
    return UNITS_ALLOWED.get(mod, None) == unit

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

def prev_week_slice(df: pd.DataFrame, start: date) -> pd.DataFrame:
    return week_slice(df, start - timedelta(days=7))

def append_changelog(old_row: pd.Series, new_row: pd.Series) -> str:
    try:
        log = json.loads(old_row.get("ChangeLog", "[]") or "[]")
    except Exception:
        log = []
    changes = {}
    for col in ["Modalidade", "Tipo de Treino", "Volume", "Unidade", "RPE", "Detalhamento", "Observações", "Status", "adj"]:
        if str(old_row.get(col, "")) != str(new_row.get(col, "")):
            changes[col] = {"old": str(old_row.get(col, "")), "new": str(new_row.get(col, ""))}
    if changes:
        log.append({"at": datetime.now().isoformat(timespec="seconds"), "changes": changes})
    return json.dumps(log, ensure_ascii=False)

# -----------------------
# Prescrição (templates parametrizados)
# -----------------------
def prescribe_detail(mod, tipo, volume, unit, paces):
    """
    Gera detalhamento com séries, usando volume e paces alvos:
      paces = {"run_pace_min_per_km": 5.0, "swim_sec_per_100m": 110, "bike_kmh": 32}
    """
    vol = float(volume or 0)
    rp = paces.get("run_pace_min_per_km", 0)
    sp = paces.get("swim_sec_per_100m", 0)
    bk = paces.get("bike_kmh", 0)

    if mod == "Corrida":
        if tipo == "Regenerativo":
            dur_min = math.ceil(vol * rp) if unit == "km" and rp > 0 else ""
            return f"Regenerativo Z1/Z2 {vol:g} km (~{dur_min} min). Cadência solta, respiração fácil."
        if tipo == "Força":
            reps = max(6, min(12, int(vol)))  # aproxima # de tiros pela km
            return f"Força em subida: {reps}×(60s forte Z4/Z5) rec 2min trote. Aquecer 10min, desaquec 10min."
        if tipo == "Longão":
            dur_min = math.ceil(vol * rp) if unit == "km" and rp > 0 else ""
            return f"Longão contínuo {vol:g} km (Z2/Z3) ~{dur_min} min. Hidratação a cada 20min."
        if tipo == "Tempo Run":
            bloco = max(20, min(40, int(vol * 6)))  # 20-40min
            return f"Tempo Run: {bloco}min em Z3/Z4. Aquecer 10min, desaquec 10min."
    if mod == "Ciclismo":
        if tipo == "Endurance":
            dur_h = vol / (bk if bk > 0 else 28)
            return f"Endurance {vol:g} km (~{dur_h:.1f} h a {bk or 28} km/h). Z2 constante; nutrição 20–30min."
        if tipo == "Intervalado":
            blocos = max(4, min(6, int(vol / 5)))  # 1 bloco ~5km útil
            return f"Intervalado: {blocos}×(6min Z4) rec 3min Z1/Z2. Cadência estável."
        if tipo == "Cadência":
            return "Cadência: 5×(3min @100–110rpm) com 2min leve. Postura e fluidez."
        if tipo == "Força/Subida":
            return "Torque/Força: 6×(4min baixa cadência 60–70rpm Z3/Z4) rec 3min."
    if mod == "Natação":
        if tipo == "Técnica":
            return "Drills: respiração bilateral, 'polegar na coxa', EVF; 8×50m educativos, pausas 20s."
        if tipo == "Ritmo":
            reps = max(6, min(10, int(vol / 200)))  # 200m reps
            return f"{reps}×200m pace sustentável (desc 20–30s). Foco em alinhamento."
        if tipo == "Intervalado":
            reps = max(12, min(20, int(vol / 50)))
            alvo = f"{(sp and int(sp)) or '—'} s/100m"
            return f"{reps}×50m forte (desc 20–30s). Alvo ~{alvo}."
        if tipo == "Contínuo":
            km = vol / 1000.0
            return f"Contínuo {km:.1f} km Z2/Z3. Braçada eficiente, respiração relaxada."
    if mod == "Força/Calistenia":
        if tipo == "Força máxima":
            return "Força: 5×3 (barra, paralela, agacho/padrão) — descanso 2–3min. Técnica impecável."
        if tipo == "Resistência muscular":
            return "Resistência: 4×12–20 (empurrar/puxar/perna). Descanso 60–90s."
        if tipo == "Core/Estabilidade":
            return "Core: circuito pranchas/anti-rotação/hollow 15–20min."
        if tipo == "Mobilidade/Recuperação":
            return "Mobilidade ativa 15–25min. Foco quadril/torácica/ombro."
    if mod == "Mobilidade":
        if tipo == "Soltura":
            return "Soltura 15–25min, alongamentos dinâmicos áreas tensas."
        if tipo == "Recuperação":
            return "Respiração+alongamentos leves 10–20min, sensação de alívio guiada."
        if tipo == "Prevenção":
            return "Mobilidade ombro/quadril/torácica 15–20min, controle motor."
    return ""

# -----------------------
# Variação de tipos (equivalências)
# -----------------------
SUBSTS_EQUIV = {
    "Corrida": {
        "Regenerativo": ["Regenerativo", "Tempo Run"],
        "Tempo Run": ["Tempo Run", "Regenerativo", "Força"],
        "Força": ["Força", "Tempo Run"],
        "Longão": ["Longão", "Regenerativo"]
    },
    "Ciclismo": {
        "Endurance": ["Endurance", "Cadência"],
        "Intervalado": ["Intervalado", "Força/Subida"],
        "Cadência": ["Cadência", "Endurance"],
        "Força/Subida": ["Força/Subida", "Intervalado"]
    },
    "Natação": {
        "Ritmo": ["Ritmo", "Contínuo", "Intervalado"],
        "Técnica": ["Técnica", "Ritmo"],
        "Intervalado": ["Intervalado", "Ritmo"],
        "Contínuo": ["Contínuo", "Ritmo"]
    },
    "Força/Calistenia": {
        "Força máxima": ["Força máxima", "Resistência muscular"],
        "Resistência muscular": ["Resistência muscular", "Core/Estabilidade"],
        "Core/Estabilidade": ["Core/Estabilidade", "Resistência muscular"],
        "Mobilidade/Recuperação": ["Mobilidade/Recuperação"]
    },
    "Mobilidade": {
        "Soltura": ["Soltura", "Recuperação", "Prevenção"],
        "Recuperação": ["Recuperação", "Soltura"],
        "Prevenção": ["Prevenção", "Soltura"]
    }
}

def vary_type(mod: str, tipo_atual: str) -> str:
    mod = (mod or "").strip()
    tipo_atual = (tipo_atual or "").strip()
    opts = SUBSTS_EQUIV.get(mod, {}).get(tipo_atual, None)
    if not opts:
        return random.choice(TIPOS_MODALIDADE.get(mod, [tipo_atual or ""]))
    choices = [t for t in opts if t != tipo_atual] or opts
    return random.choice(choices)

# -----------------------
# Resumos/Alertas
# -----------------------
def calc_week_summary(df_week: pd.DataFrame, df_prev: pd.DataFrame):
    vol_by_mod = {m: float(df_week.loc[df_week["Modalidade"] == m, "Volume"].sum()) for m in MODALIDADES}
    load_this = 0.0
    for _, r in df_week.iterrows():
        load_this += normalize_volume_for_load(r["Modalidade"], float(r["Volume"]), r["Unidade"]) * float(r["RPE"])
    load_prev = 0.0
    for _, r in df_prev.iterrows():
        load_prev += normalize_volume_for_load(r["Modalidade"], float(r["Volume"]), r["Unidade"]) * float(r["RPE"])
    var_pct = None if load_prev <= 0 else (load_this - load_prev) / load_prev * 100.0
    alert_pico = var_pct is not None and var_pct > 20.0
    load_mods = {}
    for m in ["Corrida", "Ciclismo", "Natação"]:
        sub = df_week[df_week["Modalidade"] == m]
        l = 0.0
        for _, r in sub.iterrows():
            l += normalize_volume_for_load(m, float(r["Volume"]), r["Unidade"]) * float(r["RPE"])
        load_mods[m] = l
    total_three = sum(load_mods.values())
    alert_deseq = any(total_three > 0 and (v / total_three) > 0.65 for v in load_mods.values())
    alert_estag = var_pct is not None and abs(var_pct) <= 2.0
    return vol_by_mod, load_this, load_prev, var_pct, alert_pico, alert_deseq, alert_estag

def rolling_4week(df_all: pd.DataFrame, modalidade: str):
    if df_all.empty:
        return pd.DataFrame(columns=["WeekStart", "Volume", "Rolling4"])
    df = df_all[df_all["Modalidade"] == modalidade].copy()
    if df.empty:
        return pd.DataFrame(columns=["WeekStart", "Volume", "Rolling4"])
    df["WeekStart"] = pd.to_datetime(df["Data"]).dt.date.apply(monday_of_week)
    agg = df.groupby("WeekStart")["Volume"].sum().reset_index()
    agg = agg.sort_values("WeekStart")
    agg["Rolling4"] = agg["Volume"].rolling(4, min_periods=1).mean()
    return agg

# -----------------------
# PDF & ECS (modified for durations)
# -----------------------
def build_week_pdf(df_week: pd.DataFrame, week_start: date) -> bytes:
    df = df_week.copy()
    df["Data"] = pd.to_datetime(df["Data"]).dt.date
    days = sorted(df["Data"].unique())

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    title = f"Plano Semanal ({week_start} a {week_start + timedelta(days=6)})"
    pdf.cell(0, 10, pdf_safe(title), ln=1)

    pdf.set_font("Helvetica", size=11)
    for d in days:
        sub = df[df["Data"] == d].copy()
        sub = sub[pd.to_numeric(sub["Volume"], errors="coerce").fillna(0) > 0]
        if sub.empty:
            continue

        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, pdf_safe(d.strftime("%d/%m/%Y (%a)")), ln=1)

        pdf.set_font("Helvetica", size=11)
        for _, r in sub.iterrows():
            modalidade = (r.get("Modalidade", "") or "").strip()
            tipo = (r.get("Tipo de Treino", "") or "").strip()
            vol = float(r.get("Volume", 0) or 0.0)
            unidade = (r.get("Unidade", "") or "").strip()
            det = (r.get("Detalhamento", "") or "").strip()

            if modalidade and vol > 0:
                line = f"- {modalidade} — {tipo} - {vol:g} {unidade}" if tipo else f"- {modalidade} - {vol:g} {unidade}"
                pdf.multi_cell(0, 6, pdf_safe(line))

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

def build_cycle_pdf(df_all: pd.DataFrame, cycle_df: pd.DataFrame) -> bytes:
    if cycle_df is None or cycle_df.empty:
        return b""

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    cycle_sorted = cycle_df.sort_values("WeekStart")

    for _, w in cycle_sorted.iterrows():
        ws = pd.to_datetime(w["WeekStart"]).date()
        we = ws + timedelta(days=6)
        df_week = week_slice(df_all, ws)
        if df_week.empty:
            df_week = default_week_df(ws)
        df_week = df_week.copy()
        df_week["Data"] = pd.to_datetime(df_week["Data"]).dt.date
        days = sorted(df_week["Data"].unique())

        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        title = f"Plano Semanal ({ws} a {we})"
        pdf.cell(0, 10, pdf_safe(title), ln=1)

        pdf.set_font("Helvetica", size=11)
        for d in days:
            sub = df_week[df_week["Data"] == d].copy()
            sub = sub[pd.to_numeric(sub["Volume"], errors="coerce").fillna(0) > 0]
            if sub.empty:
                continue
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, pdf_safe(d.strftime("%d/%m/%Y (%a)")), ln=1)
            pdf.set_font("Helvetica", size=11)
            for _, r in sub.iterrows():
                modalidade = (r["Modalidade"] or "").strip()
                tipo = (r["Tipo de Treino"] or "").strip()
                vol = float(r["Volume"] or 0)
                unidade = (r["Unidade"] or "").strip()
                det = (r["Detalhamento"] or "").strip()
                if modalidade and vol > 0:
                    line = f"- {modalidade} — {tipo} - {vol:g} {unidade}"
                    pdf.multi_cell(0, 6, pdf_safe(line))
                    if det:
                        pdf.set_font("Helvetica", size=10)
                        pdf.multi_cell(0, 5, pdf_safe(f"   • {det}"))
                        pdf.set_font("Helvetica", size=11)
            pdf.ln(2)
        total = df_week[pd.to_numeric(df_week["Volume"], errors="coerce").fillna(0) > 0]
        totals = total.groupby(["Modalidade", "Unidade"])["Volume"].sum().reset_index()
        if not totals.empty:
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(0, 8, pdf_safe("Totais da semana:"), ln=1)
            pdf.set_font("Helvetica", size=11)
            for _, r in totals.iterrows():
                pdf.cell(0, 6, pdf_safe(f"- {r['Modalidade']}: {r['Volume']:g} {r['Unidade']}"), ln=1)

    return bytes(pdf.output(dest="S").encode("latin1"))

def ics_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

def make_week_ics(df_week: pd.DataFrame, week_start: date, title_prefix="Treino"):
    df = df_week.copy()
    df["Data"] = pd.to_datetime(df["Data"]).dt.date
    buf = []
    buf.append("BEGIN:VCALENDAR")
    buf.append("VERSION:2.0")
    buf.append("PRODID:-//Planner//BR//PT-BR")
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
        dur_min = estimate_duration_minutes(r, paces)
        end_dt = start_dt + timedelta(minutes=dur_min)
        buf.append("BEGIN:VEVENT")
        buf.append(f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}")
        buf.append(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}")
        buf.append(f"SUMMARY:{ics_escape(summary)}")
        buf.append(f"DESCRIPTION:{ics_escape(desc)}")
        buf.append("END:VEVENT")
    buf.append("END:VCALENDAR")
    return "\r\n".join(buf).encode("utf-8")

def make_cycle_ics(df_cycle: pd.DataFrame, title_prefix="Ciclo Treinos"):
    buf = []
    buf.append("BEGIN:VCALENDAR")
    buf.append("VERSION:2.0")
    buf.append("PRODID:-//Planner//BR//PT-BR")
    for _, r in df_cycle.iterrows():
        try:
            if float(r["Volume"]) <= 0:
                continue
        except Exception:
            continue
        d = pd.to_datetime(r["Data"]).date()
        summary = f"{title_prefix}: {r['Modalidade']} — {r['Tipo de Treino']}"
        desc = f"{r.get('Detalhamento', '')}"
        start_dt = datetime(d.year, d.month, d.day, 6, 30)
        dur_min = estimate_duration_minutes(r, paces)
        end_dt = start_dt + timedelta(minutes=dur_min)
        buf.append("BEGIN:VEVENT")
        buf.append(f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}")
        buf.append(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}")
        buf.append(f"SUMMARY:{ics_escape(summary)}")
        buf.append(f"DESCRIPTION:{ics_escape(desc)}")
        buf.append("END:VEVENT")
    buf.append("END:VCALENDAR")
    return "\r\n".join(buf).encode("utf-8")

# -----------------------
# PERIODIZAÇÃO / WIZARD
# -----------------------
def distribute_week_by_targets(week_start: date,
                               weekly_targets: dict,
                               sessions_per_mod: dict,
                               default_mix: dict,
                               paces: dict,
                               user_preferred_days: dict | None = None) -> pd.DataFrame:
    """
    Gera o rascunho da semana garantindo que a soma por modalidade
    seja exatamente a meta semanal (respeitando o passo da unidade),
    priorizando o Longão da Corrida no primeiro slot/dia preferido.
    """
    weights = {
        "Corrida": [0.25, 0.20, 0.55],          # Reg, Tempo, Longão
        "Ciclismo": [0.40, 0.35, 0.25],          # Endurance > Intervalado > Força/Subida
        "Natação": [0.60, 0.40],                # Ritmo > Técnica/Intervalado
        "Força/Calistenia": [0.60, 0.40],
        "Mobilidade": [0.60, 0.40],
    }
    fallback_types = {
        "Corrida": ["Tempo Run", "Regenerativo", "Longão"],
        "Ciclismo": ["Endurance", "Intervalado", "Força/Subida"],
        "Natação": ["Ritmo", "Técnica", "Intervalado", "Contínuo"],
        "Força/Calistenia": ["Força máxima", "Resistência muscular", "Core/Estabilidade", "Mobilidade/Recuperação"],
        "Mobilidade": ["Prevenção", "Soltura", "Recuperação"]
    }
    default_days = {
        "Corrida": [2, 4, 6],   # qua, sex, dom
        "Ciclismo": [1, 3, 5],   # ter, qui, sáb
        "Natação": [0, 2],      # seg, qua
        "Força/Calistenia": [1, 4],  # ter, sex
        "Mobilidade": [0, 6],   # seg, dom
    }
    days = week_range(week_start)
    rows = []
    for mod, weekly_vol in weekly_targets.items():
        weekly_vol = float(weekly_vol or 0.0)
        if weekly_vol <= 0:
            continue
        n_sessions = int(sessions_per_mod.get(mod, 0))
        if n_sessions <= 0:
            continue
        unit = UNITS_ALLOWED[mod]
        step = _unit_step(unit)
        target_total = _round_to_step_sum(weekly_vol, unit)
        if user_preferred_days and mod in user_preferred_days and user_preferred_days[mod]:
            day_idx_pref = list(user_preferred_days[mod])
        else:
            day_idx_pref = list(default_days.get(mod, list(range(7))))
        if len(day_idx_pref) < n_sessions:
            remaining = [i for i in range(7) if i not in day_idx_pref]
            day_idx = (day_idx_pref + remaining)[:n_sessions]
        else:
            day_idx = day_idx_pref[:n_sessions]
        types_pref = (default_mix.get(mod) or fallback_types[mod]).copy()
        if mod == "Corrida":
            others = [t for t in types_pref if t != "Longão"]
            if n_sessions >= 1:
                tipos_seq = ["Longão"] + [others[i % len(others)] for i in range(n_sessions - 1)]
            else:
                tipos_seq = []
        else:
            tipos_seq = [types_pref[i % len(types_pref)] for i in range(n_sessions)]
        base_w = weights.get(mod, [1.0] * n_sessions)
        if len(base_w) < n_sessions:
            base_w = base_w + [base_w[-1]] * (n_sessions - len(base_w))
        w = np.array(base_w[:n_sessions], dtype=float)
        w = w / (w.sum() if w.sum() > 0 else 1.0)
        if mod == "Corrida" and n_sessions >= 1 and "Longão" in tipos_seq:
            max_pos = int(np.argmax(w))
            if max_pos != 0:
                w[0], w[max_pos] = w[max_pos], w[0]
        raw = (target_total * w).tolist()
        rounded = [_round_by_unit(v, unit) for v in raw]
        sum_round = float(sum(rounded))
        diff = target_total - sum_round
        if abs(diff) > 1e-9:
            if step == 50.0:
                diff = float(int(round(diff / step)) * step)
            elif step == 0.1:
                diff = round(diff, 1)
            else:
                diff = round(diff, 0)
        if diff > 0:
            j, n = 0, len(rounded)
            while diff > 1e-9 and n > 0:
                rounded[j] += step
                diff -= step
                j = (j + 1) % n
        elif diff < 0:
            need = -diff
            order = np.argsort(rounded)[::-1]
            for j in order:
                while need > 1e-9 and rounded[j] - step >= 0:
                    rounded[j] -= step
                    need -= step
                if need <= 1e-9:
                    break
        rounded = [max(0.0, float(v)) for v in rounded]
        sum_final = float(sum(rounded))
        drift = target_total - sum_final
        if abs(drift) > 1e-9 and len(rounded) > 0:
            k = int(np.argmax(rounded))
            rounded[k] = max(0.0, _round_to_step_sum(rounded[k] + drift, unit))
        if n_sessions == 1:
            rounded = [target_total]
        for i in range(n_sessions):
            d = days[day_idx[i]]
            tipo = tipos_seq[i]
            vol = float(rounded[i])
            detail = prescribe_detail(mod, tipo, vol, unit, paces)
            rows.append({
                "Data": d,
                "Modalidade": mod,
                "Tipo de Treino": tipo,
                "Volume": vol,
                "Unidade": unit,
                "RPE": 5,
                "Detalhamento": detail,
                "Observações": "",
                "Status": "Planejado",
                "adj": 0.0,
                "AdjAppliedAt": "",
                "ChangeLog": "[]",
                "LastEditedAt": "",
                "WeekStart": week_start
            })
    df = pd.DataFrame(rows, columns=SCHEMA_COLS)
    return df


WEEKDAY_NAMES = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

def _round_by_unit(vol, unit):
    if unit == "m":
        return float(int(round(float(vol) / 50.0) * 50))
    if unit == "km":
        return round(float(vol), 1)
    return round(float(vol), 0)

def _unit_step(unit: str) -> float:
    if unit == "m":
        return 50.0
    if unit == "km":
        return 0.1
    return 1.0

def _round_to_step_sum(total, unit):
    step = _unit_step(unit)
    v = float(total)
    if step == 50.0:
        return round(v / step) * step
    if step == 0.1:
        return round(v, 1)
    return round(v, 0)

def rebalance_remaining_week(df_all: pd.DataFrame,
                             week_start: date,
                             live_weekly_targets: dict,
                             paces: dict) -> pd.DataFrame:
    if df_all is None:
        return default_week_df(week_start)
    ws, we = week_start, week_start + timedelta(days=7)
    today_dt = date.today()
    frozen = get_frozen_weekly_targets(week_start, live_weekly_targets)
    df = df_all.copy()
    mask_week = (
        (pd.to_datetime(df["Data"]).dt.date >= ws) &
        (pd.to_datetime(df["Data"]).dt.date < we)
    )
    week = df.loc[mask_week].copy()
    if week.empty:
        return df
    week["Data"] = pd.to_datetime(week["Data"]).dt.date
    week["Volume"] = pd.to_numeric(week["Volume"], errors="coerce").fillna(0.0)
    week["RPE"] = pd.to_numeric(week["RPE"], errors="coerce").fillna(0.0)
    week["Status"] = week["Status"].fillna("").astype(str).str.strip()
    dcol = week["Data"]
    realized_lock_mask = (week["Status"] == "Realizado")
    realized_snapshot = week.loc[realized_lock_mask, [
        "Data", "Modalidade", "Tipo de Treino", "Volume", "Unidade",
        "RPE", "Detalhamento", "Observações", "Status", "adj",
        "AdjAppliedAt", "ChangeLog", "LastEditedAt", "WeekStart"
    ]].copy()
    default_tipo = {
        "Corrida": "Regenerativo",
        "Ciclismo": "Endurance",
        "Natação": "Contínuo",
        "Força/Calistenia": "Resistência muscular",
        "Mobilidade": "Soltura",
    }
    def _distribute_in_steps(total_q: float, step: float, weights: np.ndarray) -> np.ndarray:
        n = len(weights)
        if n == 0:
            return np.zeros(0, dtype=float)
        total_q = max(0.0, float(total_q))
        steps_total = int(round(total_q / step))
        if steps_total <= 0:
            return np.zeros(n, dtype=float)
        w = np.array(weights, dtype=float)
        if not np.isfinite(w).all() or w.sum() <= 0:
            w = np.ones(n, dtype=float)
        w = w / w.sum()
        alloc = np.floor(w * steps_total).astype(int)
        rest = steps_total - int(alloc.sum())
        if rest > 0:
            order = np.argsort(-w)
            i = 0
            while rest > 0:
                alloc[order[i % n]] += 1
                rest -= 1
                i += 1
        return alloc.astype(float) * step
    for mod in MODALIDADES:
        unit = UNITS_ALLOWED[mod]
        step = _unit_step(unit)
        target = float(frozen.get(mod, 0.0) or 0.0)
        if target <= 0:
            continue
        realized = float(week.loc[
            (week["Modalidade"] == mod) & (dcol <= today_dt) & (week["Status"] == "Realizado"),
            "Volume"
        ].sum())
        future_mask = (
            (week["Modalidade"] == mod) &
            ((dcol > today_dt) | ((dcol == today_dt) & (week["Status"] != "Realizado")))
        )
        future_rows = week.loc[future_mask].copy()
        future_idx = future_rows.index.tolist()
        planned_future = future_rows["Volume"].to_numpy(dtype=float) if len(future_rows) else np.array([], dtype=float)
        total_planned_future = float(planned_future.sum())
        desired_future_total = _round_to_step_sum(max(0.0, target - realized), unit)
        if len(future_idx) == 0:
            if desired_future_total > 0:
                d_new = min(we - timedelta(days=1), max(today_dt, ws))
                tipo_new = default_tipo.get(mod, "")
                det_new = prescribe_detail(mod, tipo_new, desired_future_total, unit, paces)
                new_row = {
                    "Data": d_new, "Modalidade": mod, "Tipo de Treino": tipo_new,
                    "Volume": float(desired_future_total), "Unidade": unit, "RPE": 5,
                    "Detalhamento": det_new, "Observações": "", "Status": "Planejado",
                    "adj": 0.0, "AdjAppliedAt": "", "ChangeLog": "[]",
                    "LastEditedAt": datetime.now().isoformat(timespec="seconds"),
                    "WeekStart": ws
                }
                week = pd.concat([week, pd.DataFrame([new_row], columns=SCHEMA_COLS)], ignore_index=True)
            continue
        weights = planned_future if total_planned_future > 0 else np.ones(len(future_idx), dtype=float)
        new_vals = _distribute_in_steps(desired_future_total, step, weights)
        for i, idx in enumerate(future_idx):
            before = week.loc[idx].copy()
            newv = float(new_vals[i])
            week.at[idx, "Volume"] = newv
            tipo = (week.at[idx, "Tipo de Treino"] or "").strip()
            week.at[idx, "Detalhamento"] = prescribe_detail(mod, tipo, newv, unit, paces)
            week.at[idx, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
            week.at[idx, "ChangeLog"] = append_changelog(before, week.loc[idx])
    if not realized_snapshot.empty:
        week.loc[realized_snapshot.index, realized_snapshot.columns] = realized_snapshot
    out = df.loc[~mask_week].copy()
    out = pd.concat([out, week], ignore_index=True)
    return out

def periodize_cycle(start_monday: date, n_weeks: int, base_load: float):
    weeks = []
    for i in range(n_weeks):
        ws = start_monday + timedelta(days=7 * i)
        if i < max(1, n_weeks - 3):
            if i < (n_weeks // 2):
                load = base_load * (1 + 0.05 * i)
                phase = "Base"
            else:
                load = base_load * (1 + 0.05 * (n_weeks // 2) + 0.07 * (i - (n_weeks // 2)))
                phase = "Build"
        elif i == n_weeks - 2:
            load = base_load * 1.20
            phase = "Peak"
        else:
            load = base_load * 0.65
            phase = "Recovery"
        weeks.append({"WeekStart": ws, "TargetLoad": round(load, 1), "Phase": phase})
    return pd.DataFrame(weeks)

# -----------------------
# STREAMLIT UI
# -----------------------
st.set_page_config(page_title="Planner de Treinos Turbo", layout="wide")
if "cycle" not in st.session_state:
    st.session_state["cycle"] = None
st.title("🏁 Planner de Treinos — versão turbo")
st.caption("Unit lock • Wizard • Templates • Status • Alerts • Paces • Periodização • Mobile • Audit • ICS • PDF")

hoje = today()
default_week = monday_of_week(hoje)
week_start = st.sidebar.date_input("Semana (segunda-feira)", value=default_week)
if week_start.weekday() != 0:
    st.sidebar.info("Ajustado para a segunda-feira da semana escolhida.")
    week_start = monday_of_week(week_start)

st.sidebar.markdown("---")
st.sidebar.subheader("Paces/Targets (opcional)")
run_pace = st.sidebar.number_input("Corrida pace (min/km)", min_value=0.0, value=0.0, step=0.1)
bike_kmh = st.sidebar.number_input("Ciclismo alvo (km/h)", min_value=0.0, value=0.0, step=0.5)
swim_100 = st.sidebar.number_input("Natação (seg/100m)", min_value=0.0, value=0.0, step=1.0)
paces = {"run_pace_min_per_km": run_pace, "bike_kmh": bike_kmh, "swim_sec_per_100m": swim_100}

st.sidebar.markdown("---")
st.sidebar.subheader("Metas semanais (por modalidade)")

weekly_targets = {
    "Corrida": st.sidebar.number_input("Corrida (km/sem)", min_value=0.0, value=30.0, step=1.0),
    "Ciclismo": st.sidebar.number_input("Ciclismo (km/sem)", min_value=0.0, value=150.0, step=5.0),
    "Natação": st.sidebar.number_input("Natação (m/sem)", min_value=0.0, value=2000.0, step=50.0),
    "Força/Calistenia": st.sidebar.number_input("Força/Calistenia (min/sem)", min_value=0.0, value=60.0, step=5.0),
    "Mobilidade": st.sidebar.number_input("Mobilidade (min/sem)", min_value=0.0, value=30.0, step=5.0),
}

st.sidebar.markdown("Sessões por semana (por modalidade)")
sessions_guess = {
    "Corrida": st.sidebar.slider("Sessões Corrida", 0, 7, 3),
    "Ciclismo": st.sidebar.slider("Sessões Ciclismo", 0, 7, 3),
    "Natação": st.sidebar.slider("Sessões Natação", 0, 7, 2),
    "Força/Calistenia": st.sidebar.slider("Sessões Força", 0, 7, 2),
    "Mobilidade": st.sidebar.slider("Sessões Mobilidade", 0, 7, 2),
}

st.sidebar.markdown("---")
st.sidebar.subheader("Preferências de dias por modalidade (opcional)")

WEEKDAY_OPTIONS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
def _days_to_idx(days_list):
    mapping = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sáb": 5, "Dom": 6}
    return [mapping[d] for d in days_list if d in mapping]

user_day_prefs = {}
user_day_prefs["Corrida"] = st.sidebar.multiselect("Dias preferidos — Corrida", WEEKDAY_OPTIONS, default=["Qua", "Sex", "Dom"])
user_day_prefs["Ciclismo"] = st.sidebar.multiselect("Dias preferidos — Ciclismo", WEEKDAY_OPTIONS, default=["Ter", "Qui", "Sáb"])
user_day_prefs["Natação"] = st.sidebar.multiselect("Dias preferidos — Natação", WEEKDAY_OPTIONS, default=["Seg", "Qua"])
user_day_prefs["Força/Calistenia"] = st.sidebar.multiselect("Dias preferidos — Força/Cal", WEEKDAY_OPTIONS, default=["Ter", "Sex"])
user_day_prefs["Mobilidade"] = st.sidebar.multiselect("Dias preferidos — Mobilidade", WEEKDAY_OPTIONS, default=["Seg", "Dom"])

user_day_idx = {mod: _days_to_idx(v) for mod, v in user_day_prefs.items()}

st.sidebar.markdown("**Atalhos de destaque**")
longao_dia = st.sidebar.selectbox("Dia do Longão (Corrida)", WEEKDAY_OPTIONS, index=WEEKDAY_OPTIONS.index("Dom"))
bike_longo_dia = st.sidebar.selectbox("Dia do Longo (Ciclismo)", WEEKDAY_OPTIONS, index=WEEKDAY_OPTIONS.index("Sáb"))
swim_key_dia = st.sidebar.selectbox("Dia-chave de Natação", WEEKDAY_OPTIONS, index=WEEKDAY_OPTIONS.index("Qua"))

def _ensure_first(mod, day_label):
    idx = WEEKDAY_OPTIONS.index(day_label)
    cur = user_day_idx.get(mod, [])
    if idx in cur:
        cur = [idx] + [i for i in cur if i != idx]
    else:
        cur = [idx] + cur
    user_day_idx[mod] = cur

_ensure_first("Corrida", longao_dia)
_ensure_first("Ciclismo", bike_longo_dia)
_ensure_first("Natação", swim_key_dia)

default_mix = {
    "Corrida": ["Tempo Run", "Regenerativo", "Longão", "Força"],
    "Ciclismo": ["Endurance", "Intervalado", "Força/Subida", "Cadência"],
    "Natação": ["Ritmo", "Técnica", "Intervalado", "Contínuo"],
    "Força/Calistenia": ["Força máxima", "Resistência muscular", "Core/Estabilidade"],
    "Mobilidade": ["Prevenção", "Soltura", "Recuperação"]
}

apply_adj_future = st.sidebar.checkbox("Aplicar 'adj' (apenas dias futuros)", value=True)
mobile_mode = st.sidebar.toggle("Modo celular (form por dia)", value=False)

st.sidebar.markdown("---")
st.sidebar.subheader("Periodização (gerar rascunho multi-semanas)")
n_weeks = st.sidebar.slider("Semanas no ciclo", 4, 12, 8)
def _compute_base_load_from_targets(t: dict) -> float:
    return (
        float(t.get("Corrida", 0.0)) +
        float(t.get("Ciclismo", 0.0)) +
        float(t.get("Natação", 0.0)) / 1000.0 +
        float(t.get("Força/Calistenia", 0.0)) / 60.0 +
        float(t.get("Mobilidade", 0.0)) / 60.0
    )

base_load_default = _compute_base_load_from_targets(weekly_targets)
base_load = st.sidebar.number_input(
    "Carga base (proxy da meta total)",
    min_value=0.0,
    value=float(base_load_default),
    step=1.0
)
if st.sidebar.button("📈 Gerar periodização"):
    for key in ["frozen_targets", "cycle", "cycle_drafts"]:
        if key in st.session_state:
            del st.session_state[key]
    st.session_state["cycle"] = periodize_cycle(week_start, n_weeks, base_load)
    st.success("✅ Novo ciclo gerado com sucesso!")

# Banco externo de tipos (opcional)
st.sidebar.markdown("---")
st.sidebar.subheader("Banco externo de tipos (opcional)")
csv_types = st.sidebar.file_uploader("CSV com colunas: modality,type", type=["csv"])
if csv_types is not None:
    try:
        df_types = pd.read_csv(csv_types)
        for _, row in df_types.iterrows():
            mod = str(row.get("modality", "")).strip()
            typ = str(row.get("type", "")).strip()
            if mod and typ:
                TIPOS_MODALIDADE.setdefault(mod, [])
                if typ not in TIPOS_MODALIDADE[mod]:
                    TIPOS_MODALIDADE[mod].append(typ)
        st.sidebar.success("Tipos importados e mesclados.")
    except Exception as e:
        st.sidebar.error(f"Falha ao importar tipos: {e}")


# Carregar dados
df_all = load_all()
st.session_state["df_all"] = df_all

if st.sidebar.button("🧪 Gerar rascunho da semana (Wizard)"):
    frozen_targets = get_frozen_weekly_targets(week_start, weekly_targets)
    draft = distribute_week_by_targets(
        week_start=week_start,
        weekly_targets=frozen_targets,
        sessions_per_mod=sessions_guess,
        default_mix=default_mix,
        paces=paces,
        user_preferred_days=user_day_idx
    )
    df_all = df_all[~((df_all["Data"] >= week_start) & (df_all["Data"] < week_start + timedelta(days=7)))]
    df_all = pd.concat([df_all, draft], ignore_index=True)
    save_all(df_all)
    st.success("Rascunho semanal gerado a partir das metas e salvo.")

if st.session_state["cycle"] is not None and not st.session_state["cycle"].empty:
    st.markdown("### 🧭 Periodização Gerada")
    st.dataframe(st.session_state["cycle"], use_container_width=True)
    BASE_SPLIT = {
        "Corrida": 0.35,
        "Ciclismo": 0.40,
        "Natação": 0.15,
        "Força/Calistenia": 0.07,
        "Mobilidade": 0.03,
    }
    def cycle_weekly_targets(target_load: float, split: dict) -> dict:
        out = {}
        for mod, frac in split.items():
            part = float(target_load) * float(frac)
            if mod == "Natação":
                out[mod] = round(part * 1000)
            elif mod in ("Força/Calistenia", "Mobilidade"):
                out[mod] = round(part * 60)
            else:
                out[mod] = round(part, 1)
        return out
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        if st.button("🧩 Popular todas as semanas do ciclo (rascunhos)"):
            df_cycle_all = df_all.copy()
            for _, w in st.session_state["cycle"].iterrows():
                ws = w["WeekStart"]
                tgt = float(w["TargetLoad"])
                targets = cycle_weekly_targets(tgt, BASE_SPLIT)
                set_frozen_weekly_targets(ws, targets)
                draft = distribute_week_by_targets(
                    week_start=ws,
                    weekly_targets=targets,
                    sessions_per_mod=sessions_guess,
                    default_mix=default_mix,
                    paces=paces,
                    user_preferred_days=user_day_idx
                )
                df_cycle_all = df_cycle_all[~((df_cycle_all["Data"] >= ws) & (df_cycle_all["Data"] < ws + timedelta(days=7)))]
                df_cycle_all = pd.concat([df_cycle_all, draft], ignore_index=True)
            save_all(df_cycle_all)
            df_all = df_cycle_all
            st.success("Rascunhos do ciclo salvos.")
    with col_c2:
        if st.button("📤 Exportar .ics do ciclo"):
            df_cycle = df_all[
                (pd.to_datetime(df_all["Data"]).dt.date >= pd.to_datetime(st.session_state["cycle"]["WeekStart"]).dt.date.min()) &
                (pd.to_datetime(df_all["Data"]).dt.date < pd.to_datetime(st.session_state["cycle"]["WeekStart"]).dt.date.max() + timedelta(days=7))
            ]
            ics_bytes = make_cycle_ics(df_cycle)
            st.download_button("⬇️ Baixar ciclo.ics", data=ics_bytes, file_name="ciclo.ics", mime="text/calendar")
    if st.button("📕 Exportar PDF único do ciclo"):
        if st.session_state["cycle"] is None or st.session_state["cycle"].empty:
            st.warning("Gere a periodização primeiro.")
        else:
            pdf_cycle = build_cycle_pdf(df_all, st.session_state["cycle"])
            if not pdf_cycle:
                st.info("Não há semanas no ciclo para exportar.")
            else:
                st.download_button(
                    "⬇️ Baixar ciclo.pdf",
                    data=pdf_cycle,
                    file_name="ciclo.pdf",
                    mime="application/pdf"
                )
    st.info("Gere a **periodização** na sidebar para habilitar as ações do ciclo.")

# Fatia da semana corrente
df_week = week_slice(df_all, week_start)
if df_week.empty:
    df_week = default_week_df(week_start)

st.markdown("---")
st.subheader(f"🗓️ Semana {week_start.strftime('%d/%m/%Y')} a {(week_start + timedelta(days=6)).strftime('%d/%m/%Y')}")

def sanitize_df(df_in: pd.DataFrame) -> pd.DataFrame:
    out = df_in.copy()
    out["Data"] = pd.to_datetime(out["Data"]).dt.date
    for i, r in out.iterrows():
        mod = (r["Modalidade"] or "").strip()
        if mod:
            out.at[i, "Unidade"] = lock_unit(mod)
        if mod and r["Tipo de Treino"] and r["Tipo de Treino"] not in TIPOS_MODALIDADE[mod]:
            out.at[i, "Tipo de Treino"] = ""
    for col in ["Volume", "RPE", "adj"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    # aplica _round_by_unit no Volume com base na Unidade
    for i, r in out.iterrows():
        unit = (r["Unidade"] or "").strip()
        out.at[i, "Volume"] = _round_by_unit(out.at[i, "Volume"], unit) if unit else out.at[i, "Volume"]
    out["Status"] = out["Status"].apply(lambda s: s if s in STATUS_CHOICES else "Planejado")
    return out

def apply_adj_row(row):
    d = row["Data"]
    if d <= today():
        row["adj"] = 0.0
        return row
    if float(row.get("adj", 0.0)) != 0.0:
        row["Volume"] = float(row.get("Volume", 0.0)) + float(row["adj"])
        row["AdjAppliedAt"] = datetime.now().isoformat(timespec="seconds")
        row["adj"] = 0.0
    return row

edited = df_week.copy()

if not mobile_mode:
    edited = sanitize_df(edited)
    edited = st.data_editor(
        edited,
        column_config={
            "Data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "Modalidade": st.column_config.SelectboxColumn("Modalidade", options=MODALIDADES, required=True),
            "Tipo de Treino": st.column_config.SelectboxColumn("Tipo", options=sorted(set(sum([TIPOS_MODALIDADE[m] for m in MODALIDADES], [])))),
            "Volume": st.column_config.NumberColumn("Volume"),
            "Unidade": st.column_config.TextColumn("Unidade (travada)"),
            "RPE": st.column_config.NumberColumn("RPE (1–10)", min_value=0, max_value=10, step=1),
            "Detalhamento": st.column_config.TextColumn("Detalhamento (auto/editável)"),
            "Observações": st.column_config.TextColumn("Observações"),
            "Status": st.column_config.SelectboxColumn("Status", options=STATUS_CHOICES),
            "adj": st.column_config.NumberColumn("adj (futuro)"),
            "AdjAppliedAt": st.column_config.TextColumn("AdjAppliedAt"),
            "ChangeLog": st.column_config.TextColumn("ChangeLog (audit)"),
            "LastEditedAt": st.column_config.TextColumn("LastEditedAt"),
            "WeekStart": st.column_config.DateColumn("WeekStart", format="DD/MM/YYYY")
        },
        hide_index=True,
        use_container_width=True,
        num_rows="fixed"
    )
else:
    edited_rows = []
    for d in week_range(week_start):
        st.markdown(f"#### {d.strftime('%a %d/%m')}")
        day_rows = edited[edited["Data"] == d]
        if day_rows.empty:
            day_rows = pd.DataFrame([{ "Data": d, "Modalidade": "", "Tipo de Treino": "", "Volume": 0, "Unidade": "", "RPE": 0,
                                       "Detalhamento": "", "Observações": "", "Status": "Planejado", "adj": 0.0,
                                       "AdjAppliedAt": "", "ChangeLog": "[]", "LastEditedAt": "", "WeekStart": week_start }])
        for _, r in day_rows.iterrows():
            mod = st.selectbox("Modalidade", MODALIDADES, index=(MODALIDADES.index(r["Modalidade"]) if r["Modalidade"] in MODALIDADES else 0), key=f"mod_{d}_{_}")
            tipo = st.selectbox("Tipo", TIPOS_MODALIDADE[mod], index=(TIPOS_MODALIDADE[mod].index(r["Tipo de Treino"]) if r["Tipo de Treino"] in TIPOS_MODALIDADE[mod] else 0), key=f"tipo_{d}_{_}")
            unit = lock_unit(mod)
            vol = st.number_input(f"Volume [{unit}]", min_value=0.0, value=float(r["Volume"]), step=0.5, key=f"vol_{d}_{_}")
            rpe = st.number_input("RPE (1–10)", min_value=0, max_value=10, value=int(r["RPE"]), step=1, key=f"rpe_{d}_{_}")
            if st.button("Auto detalhar", key=f"auto_{d}_{_}"):
                st.session_state[f"det_{d}_{_}"] = prescribe_detail(mod, tipo, vol, unit, paces)
            det = st.text_area("Detalhamento", value=st.session_state.get(f"det_{d}_{_}", r["Detalhamento"]), key=f"deti_{d}_{_}")
            obs = st.text_input("Observações", value=r["Observações"], key=f"obs_{d}_{_}")
            status = st.selectbox("Status", STATUS_CHOICES, index=STATUS_CHOICES.index(r["Status"]) if r["Status"] in STATUS_CHOICES else 0, key=f"sts_{d}_{_}")
            adj = st.number_input("adj (futuro)", min_value=0.0, value=float(r["adj"]), step=0.5, key=f"adj_{d}_{_}")
            edited_rows.append({
                "Data": d, "Modalidade": mod, "Tipo de Treino": tipo, "Volume": vol, "Unidade": unit, "RPE": rpe,
                "Detalhamento": det, "Observações": obs, "Status": status, "adj": adj,
                "AdjAppliedAt": r["AdjAppliedAt"], "ChangeLog": r["ChangeLog"], "LastEditedAt": r["LastEditedAt"], "WeekStart": week_start
            })
    edited = pd.DataFrame(edited_rows, columns=SCHEMA_COLS)

# --- Variação automática de tipos (mantém volume/unidade, re-prescreve detalhamento)
if st.button("🎲 Variar tipos de treino (sutil)"):
    for i, r in edited.iterrows():
        mod = (r["Modalidade"] or "").strip()
        tipo = (r["Tipo de Treino"] or "").strip()
        if not mod or not tipo:
            continue
        novo_tipo = vary_type(mod, tipo)
        edited.at[i, "Tipo de Treino"] = novo_tipo
        unit = lock_unit(mod)
        edited.at[i, "Unidade"] = unit
        new_det = prescribe_detail(mod, novo_tipo, edited.at[i, "Volume"], unit, paces)
        if new_det:
            edited.at[i, "Detalhamento"] = new_det
    st.success("Tipos variados. Revise e salve a semana.")

for i, r in edited.iterrows():
    mod = (r["Modalidade"] or "").strip()
    tipo = (r["Tipo de Treino"] or "").strip()
    if mod:
        edited.at[i, "Unidade"] = lock_unit(mod)
    if mod and tipo:
        auto = prescribe_detail(mod, tipo, r["Volume"], edited.at[i, "Unidade"], paces)
        if not r["Detalhamento"] or r["Detalhamento"].strip() == "":
            edited.at[i, "Detalhamento"] = auto

if apply_adj_future:
    changed = False
    for i, r in edited.iterrows():
        if float(r["adj"]) != 0.0:
            if r["Data"] > today():
                before = df_week.loc[df_week.index == i].squeeze() if i in df_week.index else r
                edited.loc[i] = apply_adj_row(r)
                edited.at[i, "ChangeLog"] = append_changelog(before, edited.loc[i])
                edited.at[i, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
                changed = True
            else:
                edited.at[i, "adj"] = 0.0
                st.warning(f"Adj bloqueado em {r['Data'].strftime('%d/%m/%Y')} (somente futuro).")
    if changed:
        st.success("Adj aplicado em dias futuros e zerado.")

col1, col2, col3, col4 = st.columns(4)
with col1:
    if st.button("💾 Salvar semana"):
        df_merge = df_all.copy()
        mask = (df_merge["Data"] >= week_start) & (df_merge["Data"] < week_start + timedelta(days=7))
        old = df_merge[mask].reset_index(drop=True)
        new = edited.reset_index(drop=True)
        if len(old) < len(new):
            add = new.iloc[len(old):].copy()
            add["ChangeLog"] = "[]"
            old = pd.concat([old, add], ignore_index=True)
        for i in range(len(new)):
            new.at[i, "ChangeLog"] = append_changelog(old.iloc[i] if i < len(old) else new.iloc[i], new.iloc[i])
            new.at[i, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
            # reforço: volume coerente numérico
            try:
                new.at[i, "Volume"] = float(new.at[i, "Volume"] or 0.0)
            except Exception:
                new.at[i, "Volume"] = 0.0
            if not unit_is_valid(new.at[i, "Modalidade"], new.at[i, "Unidade"]):
                new.at[i, "Unidade"] = lock_unit(new.at[i, "Modalidade"])
        df_merge = df_merge[~mask]
        df_merge = pd.concat([df_merge, new], ignore_index=True)
        save_all(df_merge)
        st.success("Semana salva.")
        df_all = df_merge

with col2:
    if st.button("🧾 Gerar PDF da semana"):
        pdf_bytes = build_week_pdf(edited, week_start)
        st.download_button("⬇️ Baixar PDF", data=pdf_bytes, file_name=f"semana_{week_start}.pdf", mime="application/pdf")

with col3:
    if st.button("📤 Exportar .ics da semana"):
        ics_bytes = make_week_ics(edited, week_start)
        st.download_button("⬇️ Baixar semana.ics", data=ics_bytes, file_name=f"semana_{week_start}.ics", mime="text/calendar")

with col4:
    if st.button("🔄 Atualizar e reorganizar a semana"):
        try:
            if "df_all" not in st.session_state or st.session_state["df_all"] is None:
                st.session_state["df_all"] = load_all()
            ws = week_start
            we = ws + timedelta(days=7)
            frozen_targets = get_frozen_weekly_targets(ws, weekly_targets)
            MAX_PASSES = 10
            success = False
            last_diffs = []
            for _ in range(MAX_PASSES):
                df_before = st.session_state["df_all"].copy()
                df_reb = rebalance_remaining_week(
                    df_all=df_before,
                    week_start=ws,
                    live_weekly_targets=frozen_targets,
                    paces=paces
                )
                if df_reb is None or df_reb.empty:
                    break
                save_all(df_reb)
                st.session_state["df_all"] = load_all()
                df_all = st.session_state["df_all"]
                last_diffs = []
                for mod in MODALIDADES:
                    unit = UNITS_ALLOWED[mod]
                    step = _unit_step(unit)
                    dfm = df_all[(df_all["Modalidade"] == mod) & (pd.to_datetime(df_all["Data"]).dt.date >= ws) & (pd.to_datetime(df_all["Data"]).dt.date < we)].copy()
                    if dfm.empty and float(frozen_targets.get(mod, 0.0) or 0.0) == 0.0:
                        continue
                    dfm["Volume"] = pd.to_numeric(dfm["Volume"], errors="coerce").fillna(0.0)
                    dfm["Status"] = dfm["Status"].fillna("").astype(str).str.strip()
                    realized = float(dfm[dfm["Status"] == "Realizado"]["Volume"].sum())
                    future = float(dfm[dfm["Status"] != "Realizado"]["Volume"].sum())
                    total = realized + future
                    target = float(frozen_targets.get(mod, 0.0) or 0.0)
                    def _snap(v):
                        return _unit_step(unit) * round(v / _unit_step(unit))
                    if abs(_snap(total) - _snap(target)) > 1e-9:
                        last_diffs.append(f"{mod}: diferença {total - target:+.2f}")
                if not last_diffs:
                    success = True
                    break
            if success:
                st.success("✅ Semana reorganizada com sucesso! Volumes batendo com as metas.")
            else:
                if last_diffs:
                    st.warning("⚠️ Volume não bate 100% em algumas modalidades:\n" + "\n".join(last_diffs))
                else:
                    st.warning("⚠️ Não foi possível confirmar os ajustes desta vez.")
        except Exception as e:
            st.error(f"Erro ao reorganizar: {e}")

st.markdown("---")
st.subheader("📊 Resumo, KPIs e Alertas")
df_all = load_all()
df_week_saved = week_slice(df_all, week_start)
df_prev = prev_week_slice(df_all, week_start)
vol_by_mod, load_this, load_prev, var_pct, alert_pico, alert_deseq, alert_estag = calc_week_summary(df_week_saved, df_prev)
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Corrida (km)", f"{vol_by_mod['Corrida']:.1f}")
m2.metric("Ciclismo (km)", f"{vol_by_mod['Ciclismo']:.1f}")
m3.metric("Natação (m)", f"{vol_by_mod['Natação']:.0f}")
m4.metric("Força/Cal (min)", f"{vol_by_mod['Força/Calistenia']:.0f}")
m5.metric("Mobilidade (min)", f"{vol_by_mod['Mobilidade']:.0f}")
c1, c2, c3 = st.columns(3)
c1.metric("Carga (semana)", f"{load_this:.1f}")
c2.metric("Carga anterior", f"{load_prev:.1f}")
c3.metric("Variação vs ant.", f"{(var_pct if var_pct is not None else 0):+.1f}%")
if alert_pico: st.error("⚠️ Pico de carga (>20% vs semana anterior).")
if alert_deseq: st.warning("⚠️ Desequilíbrio de carga entre modalidades de endurance (>65% em uma delas).")
if alert_estag: st.info("ℹ️ Estagnação: variação ~0%. Considere introduzir novidade/estímulo.")
adherence = 0.0
done = df_week_saved[(df_week_saved["Status"] == "Realizado") & (pd.to_numeric(df_week_saved["Volume"], errors="coerce").fillna(0) > 0)]
planned = df_week_saved[pd.to_numeric(df_week_saved["Volume"], errors="coerce").fillna(0) > 0]
if len(planned) > 0:
    adherence = len(done) / len(planned) * 100.0
st.metric("Aderência (realizado/planejado)", f"{adherence:.0f}%")
st.markdown("#### Rolling 4 semanas — Volume por modalidade")
gcols = st.columns(len(MODALIDADES))
for idx, mod in enumerate(MODALIDADES):
    with gcols[idx]:
        agg = rolling_4week(df_all, mod)
        fig, ax = plt.subplots()
        ax.plot(agg["WeekStart"], agg["Volume"], marker="o", label="Volume")
        ax.plot(agg["WeekStart"], agg["Rolling4"], marker="x", label="Média 4s")
        ax.set_title(mod); ax.set_xlabel("Semana"); ax.set_ylabel("Volume"); ax.legend()
        st.pyplot(fig, clear_figure=True)
st.markdown("---")
st.caption("Dica: use os paces-alvo para tornar a sessão mais prescritiva; ajuste RPE para refletir carga real.")
