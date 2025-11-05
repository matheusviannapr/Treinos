# app.py — Planner de Treinos (versão turbo, single-file)
# ------------------------------------------------------------
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

import os, io, json, math
from datetime import datetime, date, timedelta
from dateutil.parser import parse as dtparse

import pandas as pd
import numpy as np
import streamlit as st
from fpdf import FPDF
import matplotlib.pyplot as plt
import unicodedata

# ----------------------------------------------------------------------------
# 1. Correção: Função safe_rerun() para substituir st.experimental_rerun()
# ----------------------------------------------------------------------------
def safe_rerun():
    """
    Função auxiliar para forçar o recarregamento da página.
    Usa st.rerun() se disponível, com fallback para st.experimental_rerun().
    """
    try:
        st.rerun()
    except AttributeError:
        # Fallback para versões mais antigas do Streamlit
        st.experimental_rerun()

# ----------------------------------------------------------------------------
# Constantes e Schema
# ----------------------------------------------------------------------------

# Cores para o PDF (por modalidade)
MODALITY_COLORS = {
    "Corrida": (255, 0, 0),         # Vermelho Puro
    "Ciclismo": (64, 64, 64),       # Cinza Escuro
    "Natação": (75, 0, 130),        # Azul Índigo
    "Força/Calistenia": (34, 139, 34), # Verde Floresta
    "Mobilidade": (255, 140, 0),   # Laranja Escuro
    "Descanso": (201, 201, 201),    # Cinza Claro
}

# Cores de texto para o PDF (para contraste)
MODALITY_TEXT_COLORS = {
    "Ciclismo": (255, 255, 255),    # Branco para fundo cinza escuro
}
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

DATA_DIR = "data"
EXPORT_DIR = "exports"
CSV_PATH = os.path.join(DATA_DIR, "treinos.csv")

SCHEMA_COLS = [
    "Data","Modalidade","Tipo de Treino","Volume","Unidade","RPE",
    "Detalhamento","Observações","Status","adj",
    "AdjAppliedAt","ChangeLog","LastEditedAt","WeekStart"
]

UNITS_ALLOWED = {
    "Corrida": "km",
    "Ciclismo": "km",
    "Natação": "m",
    "Força/Calistenia": "min",
    "Mobilidade": "min",
}
MODALIDADES = list(UNITS_ALLOWED.keys())
STATUS_CHOICES = ["Planejado","Realizado","Adiado","Cancelado"]

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
    "Corrida": ["Regenerativo","Força","Longão","Tempo Run"],
    "Ciclismo": ["Endurance","Intervalado","Cadência","Força/Subida"],
    "Natação": ["Técnica","Ritmo","Intervalado","Contínuo"],
    "Força/Calistenia": ["Força máxima","Resistência muscular","Core/Estabilidade","Mobilidade/Recuperação"],
    "Mobilidade": ["Soltura","Recuperação","Prevenção"]
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
        # cria snapshot inicial com os alvos atuais da sidebar
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

# ----------------------------------------------------------------------------
# 2. Correção: Adicionar @st.cache_data e lógica de cache
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_all() -> pd.DataFrame:
    init_csv_if_needed()
    df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
    if not df.empty:
        df["Data"] = pd.to_datetime(df["Data"]).dt.date
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date
        for col in ["Volume","RPE","adj"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        for col in ["ChangeLog","Detalhamento","Observações"]:
            df[col] = df[col].astype(str)
        # Unidade coerente por modalidade (autocorreção leve)
        for i, r in df.iterrows():
            mod = r.get("Modalidade","")
            if mod in UNITS_ALLOWED and r.get("Unidade","") != UNITS_ALLOWED[mod]:
                df.at[i,"Unidade"] = UNITS_ALLOWED[mod]
    return df

def save_all(df: pd.DataFrame):
    df_out = df.copy()
    df_out["Data"] = pd.to_datetime(df_out["Data"]).dt.date.astype(str)
    if "WeekStart" in df_out.columns:
        df_out["WeekStart"] = pd.to_datetime(df_out["WeekStart"]).dt.date.astype(str)
    df_out.to_csv(CSV_PATH, index=False)
    # 3. Correção: Limpar o cache após salvar
    load_all.clear()

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
    elif mod in ("Força/Calistenia","Mobilidade"):
        return (vol or 0.0) * LOAD_COEFF.get(mod, 1.0)
    else:
        return (vol or 0.0) * LOAD_COEFF.get(mod, 1.0)

def week_slice(df: pd.DataFrame, start: date) -> pd.DataFrame:
    end = start + timedelta(days=7)
    return df[(df["Data"]>=start) & (df["Data"]<end)].copy()

def prev_week_slice(df: pd.DataFrame, start: date) -> pd.DataFrame:
    return week_slice(df, start - timedelta(days=7))

def append_changelog(old_row: pd.Series, new_row: pd.Series) -> str:
    try:
        log = json.loads(old_row.get("ChangeLog","[]") or "[]")
    except Exception:
        log = []
    changes = {}
    for col in ["Modalidade","Tipo de Treino","Volume","Unidade","RPE","Detalhamento","Observações","Status","adj"]:
        if str(old_row.get(col,"")) != str(new_row.get(col,"")):
            changes[col] = {"old": str(old_row.get(col,"")), "new": str(new_row.get(col,""))}
    if changes:
        log.append({"at": datetime.now().isoformat(timespec="seconds"), "changes": changes})
    return json.dumps(log, ensure_ascii=False)

# -----------------------
# Prescrição (templates parametrizados)
# -----------------------
def _unit_step(unit: str) -> float:
    if unit == "m": return 50.0
    if unit == "km": return 0.1
    return 1.0

def _round_to_step_sum(total: float, unit: str) -> float:
    step = _unit_step(unit)
    v = float(total)
    if step == 50.0: return round(v / step) * step
    if step == 0.1: return round(v, 1)
    return round(v, 0)

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
            dur_min = math.ceil(vol * rp) if unit=="km" and rp>0 else ""
            return f"Regenerativo Z1/Z2 {vol:g} km (~{dur_min} min). Cadência solta, respiração fácil."
        if tipo == "Força":
            reps = max(6, min(12, int(vol)))  # aproxima # de tiros pela km
            return f"Força em subida: {reps}×(60s forte Z4/Z5) rec 2min trote. Aquecer 10min, desaquecer 10min."
        if tipo == "Longão":
            dur_min = math.ceil(vol * rp) if unit=="km" and rp>0 else ""
            return f"Longão contínuo {vol:g} km (Z2/Z3) ~{dur_min} min. Hidratação a cada 20min."
        if tipo == "Tempo Run":
            bloco = max(20, min(40, int(vol*6)))  # 20-40min
            return f"Tempo Run: {bloco}min em Z3/Z4. Aquecer 10min, desaquecer 10min."
    if mod == "Ciclismo":
        if tipo == "Endurance":
            dur_h = vol/ (bk if bk>0 else 28)
            return f"Endurance {vol:g} km (~{dur_h:.1f} h a {bk or 28} km/h). Z2 constante; nutrição 20–30min."
        if tipo == "Intervalado":
            blocos = max(4, min(6, int(vol/5)))  # 1 bloco ~5km útil
            return f"Intervalado: {blocos}×(6min Z4) rec 3min Z1/Z2. Cadência estável."
        if tipo == "Cadência":
            return "Cadência: 5×(3min @100–110rpm) com 2min leve. Postura e fluidez."
        if tipo == "Força/Subida":
            return "Torque/Força: 6×(4min baixa cadência 60–70rpm Z3/Z4) rec 3min."
    if mod == "Natação":
        if tipo == "Técnica":
            return "Drills: respiração bilateral, 'polegar na coxa', EVF; 8×50m educativos, pausas 20s."
        if tipo == "Ritmo":
            reps = max(6, min(10, int(vol/200)))  # 200m reps
            return f"{reps}×200m pace sustentável (desc 20–30s). Foco em alinhamento."
        if tipo == "Intervalado":
            reps = max(12, min(20, int(vol/50)))
            alvo = f"{(sp and int(sp)) or '—'} s/100m"
            return f"{reps}×50m forte (desc 20–30s). Alvo ~{alvo}."
        if tipo == "Contínuo":
            km = vol/1000.0
            return f"Contínuo {km:.1f} km Z2/Z3. Braçada eficiente, respiração relaxada."
    if mod == "Força/Calistenia":
        if tipo == "Força máxima":
            return "Força: 5×3 (barra, paralela, agacho) rec 2–3min. Foco em progressão de carga."
        if tipo == "Resistência muscular":
            return "Resistência: 4×12–20 (empurrar/puxar/perna). Circuito com 60s de descanso."
        if tipo == "Core/Estabilidade":
            return "Core: circuito pranchas/anti-rotação/hollow 15–20min. 30s on / 15s off."
        if tipo == "Mobilidade/Recuperação":
            return "Mobilidade ativa 15–25min. Foco em quadril e ombros."
    if mod == "Mobilidade":
        if tipo == "Soltura":
            return "Soltura 15–25min com alongamentos dinâmicos. Antes do treino."
        if tipo == "Recuperação":
            return "Respiração + alongamentos leves 10–20min. Pós-treino ou à noite."
        if tipo == "Prevenção":
            return "Mobilidade ombro/quadril/torácica 15–20min. Foco em pontos fracos."
    return ""

# ---------- NOVO: utilitário para expandir listas ao tamanho n ----------
def _expand_to_n(pattern_list, n):
    """Repete o padrão até atingir n itens (preservando ordem)."""
    if n <= 0:
        return []
    if not pattern_list:
        return [1.0 / n] * n  # fallback numérico quando usado com pesos
    k = len(pattern_list)
    reps = n // k
    rem = n % k
    return pattern_list * reps + pattern_list[:rem]

def distribute_week_by_targets(
    week_start: date,
    weekly_targets: dict,
    sessions_per_mod: dict,
    key_sessions: dict,
    paces: dict,
    user_preferred_days: dict | None = None,
) -> pd.DataFrame:
    """Generate a weekly plan given targets and number of sessions per modality."""
    days = week_range(week_start)
    rows = []
    weights = {
        "Corrida": [0.25, 0.20, 0.55], # 3 sessões: Regenerativo, Força, Longão
        "Ciclismo": [0.40, 0.35, 0.25], # 3 sessões: Endurance, Intervalado, Cadência/Força
        "Natação": [0.60, 0.40], # 2 sessões: Técnica, Ritmo/Intervalado
        "Força/Calistenia": [0.60, 0.40], # 2 sessões: Força/Resistência, Core/Mobilidade
        "Mobilidade": [0.60, 0.40], # 2 sessões: Soltura, Recuperação
    }
    default_days = {
        "Corrida": [2, 4, 6],  # Wed, Fri, Sun
        "Ciclismo": [1, 3, 5], # Tue, Thu, Sat
        "Natação": [0, 2],     # Mon, Wed
        "Força/Calistenia": [1, 4], # Tue, Fri
        "Mobilidade": [0, 6],  # Mon, Sun
    }
    
    # 1. Calcular a carga total e a distribuição de volume
    mod_volumes = {}
    for mod, weekly_vol in weekly_targets.items():
        weekly_vol = float(weekly_vol or 0)
        n = int(sessions_per_mod.get(mod, 0))
        if weekly_vol <= 0 or n <= 0:
            continue
        unit = UNITS_ALLOWED[mod]
        target_total = _round_to_step_sum(weekly_vol, unit)

        # --- PATCH: expandir pesos para exatamente n e normalizar ---
        w_template = weights.get(mod, None)
        if w_template is None:
            w = [1.0 / n] * n
        else:
            w = _expand_to_n(w_template, n)
            s = sum(w)
            w = [1.0 / n] * n if s == 0 else [x / s for x in w]

        volumes = [_round_to_step_sum(target_total * wi, unit) for wi in w]

        # Ajuste fino para garantir soma exata após arredondamentos
        diff = target_total - sum(volumes)
        if abs(diff) > 1e-9:
            max_idx = max(range(len(volumes)), key=lambda i: volumes[i])
            volumes[max_idx] = _round_to_step_sum(volumes[max_idx] + diff, unit)

        mod_volumes[mod] = volumes

    # 2. Atribuir sessões aos dias
    session_assignments = {i: [] for i in range(7)} # {day_index: [(mod, vol, tipo)]}
    for mod, volumes in mod_volumes.items():
        n = len(volumes)
        prefs = (user_preferred_days or {}).get(mod, default_days.get(mod, list(range(7))))
        # completa a lista de dias com os restantes e limita a n
        day_idx = prefs + [i for i in range(7) if i not in prefs]
        day_idx = day_idx[:n]

        # --- PATCH: expandir tipos para exatamente n ---
        tipos_base = TIPOS_MODALIDADE.get(mod, ["Treino"])
        tipos = _expand_to_n(tipos_base, n)

        # se houver treino chave definido, posicione-o na maior sessão (opcional)
        key_tipo = (key_sessions or {}).get(mod, "")
        if key_tipo and key_tipo in tipos:
            max_i = max(range(n), key=lambda i: volumes[i])
            tipos[max_i] = key_tipo

        for i in range(n):
            day = day_idx[i]
            vol = volumes[i]
            tipo = tipos[i]
            session_assignments[day].append((mod, vol, tipo))

    # 3. Criar o DataFrame final
    for i in range(7):
        d = days[i]
        # Ordenar as sessões do dia (ex: Corrida > Ciclismo > Natação > Força > Mobilidade)
        day_sessions = sorted(session_assignments[i], key=lambda x: MODALIDADES.index(x[0]) if x[0] in MODALIDADES else 99)
        
        if not day_sessions:
            # Dia de descanso
            rows.append({
                "Data": d,
                "Modalidade": "Descanso",
                "Tipo de Treino": "Ativo/Passivo",
                "Volume": 0.0,
                "Unidade": "min",
                "RPE": 0,
                "Detalhamento": "Dia de descanso. Foco em recuperação e nutrição.",
                "Observações": "",
                "Status": "Planejado",
                "adj": 0.0,
                "AdjAppliedAt": "",
                "ChangeLog": "[]",
                "LastEditedAt": "",
                "WeekStart": week_start,
            })
            continue
            
        for mod, vol, tipo in day_sessions:
            unit = UNITS_ALLOWED[mod]
            detail = prescribe_detail(mod, tipo, vol, unit, paces)
            
            rows.append({
                "Data": d,
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

# -----------------------
# Periodização
# -----------------------
PHASES = ["Base", "Build", "Peak", "Recovery"]

def generate_cycle(
    start_week: date,
    num_weeks: int,
    base_load: float,
    phase_proportions: dict,
    sessions_per_mod: dict,
    paces: dict,
    user_preferred_days: dict | None = None,
    key_sessions: dict | None = None,
) -> pd.DataFrame:
    """
    Gera um ciclo de treinamento (multi-semanas) com periodização.
    
    base_load: Carga proxy total da semana 1 (Base).
    phase_proportions: {Modalidade: {Fase: proporção_da_carga_total}}
    """
    all_weeks_df = []
    current_week_start = start_week
    
    # 1. Definir a progressão da carga semanal (ex: 3 semanas de carga, 1 de recuperação)
    load_progression = []
    for i in range(num_weeks):
        phase_idx = i % len(PHASES)
        phase = PHASES[phase_idx]
        
        # Fator de progressão (ex: Base: 1.0, Build: 1.1, Peak: 1.2, Recovery: 0.7)
        if phase == "Base":
            factor = 1.0
        elif phase == "Build":
            factor = 1.0 + (i // len(PHASES)) * 0.05 # Aumenta 5% a cada ciclo Base/Build/Peak
        elif phase == "Peak":
            factor = 1.0 + (i // len(PHASES)) * 0.05 + 0.05
        elif phase == "Recovery":
            factor = 0.7
        
        load_progression.append((phase, factor))
        
    # 2. Gerar cada semana
    for i in range(num_weeks):
        phase, factor = load_progression[i]
        week_load = base_load * factor
        
        # 3. Distribuir a carga total da semana (week_load) pelas modalidades
        weekly_targets = {}
        total_load_check = 0.0
        
        for mod in MODALIDADES:
            # Proporção da carga total para esta modalidade nesta fase
            prop = phase_proportions.get(mod, {}).get(phase, 0.0)
            if prop <= 0: continue
            
            # Carga proxy para a modalidade
            mod_load = week_load * prop
            
            # Converter carga proxy de volta para volume (km/m/min)
            coeff = LOAD_COEFF.get(mod, 1.0)
            if coeff == 0: continue
            
            volume = mod_load / coeff
            unit = UNITS_ALLOWED[mod]
            
            # Ajuste para Natação (volume está em km, precisa ser em metros)
            if mod == "Natação":
                volume *= 1000.0
            
            # Arredondar para o passo da unidade
            volume = _round_to_step_sum(volume, unit)
            
            if volume > 0:
                weekly_targets[mod] = volume
                total_load_check += normalize_volume_for_load(mod, volume, unit)
        
        # Gerar a semana com os targets calculados
        key_sessions = {mod: st.session_state.get(f"key_sess_{mod}", "") for mod in MODALIDADES}
        week_df = distribute_week_by_targets(
            current_week_start,
            weekly_targets,
            sessions_per_mod,
            key_sessions,
            paces,
            user_preferred_days,
        )
        
        # Adicionar metadados da periodização
        week_df["Phase"] = phase
        week_df["LoadFactor"] = factor
        week_df["WeekLoad"] = total_load_check # Carga real após arredondamento
        
        all_weeks_df.append(week_df)
        current_week_start += timedelta(days=7)
        
    return pd.concat(all_weeks_df, ignore_index=True)

# -----------------------
# Funções de Exportação
# -----------------------

def generate_ics(df: pd.DataFrame, filename: str) -> str:
    """Gera o conteúdo do arquivo .ics a partir do DataFrame."""
    ics_content = "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Manus//TriathlonPlanner//EN\n"
    
    for _, row in df.iterrows():
        if row["Volume"] <= 0 and row["Modalidade"] != "Descanso":
            continue
            
        dt_start = datetime.combine(row["Data"], datetime.min.time())
        dt_end = dt_start + timedelta(hours=1) # Duração padrão de 1h
        
        summary = f"{row['Modalidade']} - {row['Tipo de Treino']}"
        description = f"Volume: {row['Volume']:g} {row['Unidade']}\nDetalhes: {row['Detalhamento']}\nStatus: {row['Status']}"
        
        ics_content += "BEGIN:VEVENT\n"
        ics_content += f"UID:{dt_start.strftime('%Y%m%d%H%M%S')}-{hash(summary)}@triathlonplanner.com\n"
        ics_content += f"DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%SZ')}\n"
        ics_content += f"DTSTART;VALUE=DATE:{dt_start.strftime('%Y%m%d')}\n"
        ics_content += f"DTEND;VALUE=DATE:{dt_end.strftime('%Y%m%d')}\n"
        ics_content += f"SUMMARY:{summary}\n"
        ics_content += f"DESCRIPTION:{description}\n"
        ics_content += "END:VEVENT\n"
        
    ics_content += "END:VCALENDAR\n"
    return ics_content

class PDF(FPDF):
    def header(self):
        self.set_font("Arial", "B", 15)
        self.cell(0, 10, pdf_safe("Plano de Treino Semanal"), 0, 1, "C")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.cell(0, 10, pdf_safe(f"Página {self.page_no()}/{{nb}} | Gerado em {datetime.now().strftime('%d/%m/%Y')}"), 0, 0, "C")

def generate_pdf(df: pd.DataFrame, week_start: date) -> bytes:
    """Gera o PDF da semana com cores por modalidade."""
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 5, pdf_safe(f"Semana: {week_start.strftime('%d/%m/%Y')} a {(week_start + timedelta(days=6)).strftime('%d/%m/%Y')}"), 0, 1)
    pdf.ln(5)
    
    # Tabela de treinos
    col_widths = [20, 25, 35, 15, 15, 80]
    headers = ["Data", "Modalidade", "Tipo", "Volume", "Unid.", "Detalhamento"]
    
    pdf.set_font("Arial", "B", 9)
    pdf.set_fill_color(220, 220, 220) # Cor de fundo para o cabeçalho
    for i, header in enumerate(headers):
        pdf.cell(col_widths[i], 7, pdf_safe(header), 1, 0, "C", 1)
    pdf.ln()
    
    pdf.set_font("Arial", "", 8)
    for _, row in df.iterrows():
        if row["Volume"] <= 0 and row["Modalidade"] != "Descanso":
            continue
            
        mod = row["Modalidade"]
        color = MODALITY_COLORS.get(mod, (255, 255, 255)) # Cor de fundo da linha
        pdf.set_fill_color(*color)
        
        data = row["Data"].strftime("%d/%m (%a)")
        tipo = row["Tipo de Treino"]
        vol = f"{row['Volume']:g}"
        unit = row["Unidade"]
        detail = row["Detalhamento"]
        
        # Altura da célula baseada no detalhamento
        detail_lines = pdf.get_string_width(pdf_safe(detail)) / col_widths[5]
        line_height = 5
        cell_height = max(line_height, line_height * math.ceil(detail_lines))
        
        # Coluna 1-5 (fixas)
        # Cor do texto
        text_color = MODALITY_TEXT_COLORS.get(mod, (0, 0, 0)) # Preto padrão
        pdf.set_text_color(*text_color)

        pdf.cell(col_widths[0], cell_height, pdf_safe(data), 1, 0, "L", 1)
        pdf.cell(col_widths[1], cell_height, pdf_safe(mod), 1, 0, "L", 1)
        pdf.cell(col_widths[2], cell_height, pdf_safe(tipo), 1, 0, "L", 1)
        pdf.cell(col_widths[3], cell_height, pdf_safe(vol), 1, 0, "R", 1)
        pdf.cell(col_widths[4], cell_height, pdf_safe(unit), 1, 0, "C", 1)

        # Restaura a cor do texto para preto para o detalhamento
        pdf.set_text_color(0, 0, 0)
        
        # Coluna 6 (multiline)
        pdf.set_fill_color(255, 255, 255) # Detalhamento sem cor de fundo
        x = pdf.get_x()
        y = pdf.get_y()
        pdf.multi_cell(col_widths[5], line_height, pdf_safe(detail), 1, "L")
        
        # Retorna para a próxima linha da tabela
        pdf.set_xy(10, y + cell_height)
        pdf.ln(0)
        
    # Geração do PDF em memória
    return pdf.output(dest="S").encode("latin-1")


# -----------------------
# Funções de Análise e Dashboard
# -----------------------

def calculate_metrics(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calcula métricas semanais e rolling 4-week load."""
    
    # 1. Carga Semanal (Load)
    df["Load"] = df.apply(
        lambda row: normalize_volume_for_load(row["Modalidade"], row["Volume"], row["Unidade"]),
        axis=1
    )
    
    # Agrupar por semana
    weekly_metrics = df.groupby("WeekStart").agg(
        TotalLoad=("Load", "sum"),
        TotalVolume=("Volume", "sum"), # Volume total sem normalização (apenas para referência)
        NumSessions=("Data", "count"),
    ).reset_index()
    
    # 2. Rolling 4-week Load (ATL, CTL, TSB)
    # Ordenar por WeekStart
    weekly_metrics = weekly_metrics.sort_values("WeekStart").reset_index(drop=True)
    
    # Constantes (ajustáveis)
    ATL_TAU = 7 # Acute Training Load (curto prazo)
    CTL_TAU = 42 # Chronic Training Load (longo prazo)
    
    # Simplificação: usar rolling mean (aproximação)
    weekly_metrics["CTL"] = weekly_metrics["TotalLoad"].rolling(window=6, min_periods=1).mean()
    weekly_metrics["ATL"] = weekly_metrics["TotalLoad"].rolling(window=2, min_periods=1).mean()
    
    # TSB (Training Stress Balance) = CTL - ATL
    weekly_metrics["TSB"] = weekly_metrics["CTL"] - weekly_metrics["ATL"]
    
    return weekly_metrics, df

def plot_load_chart(weekly_metrics: pd.DataFrame):
    """Gera o gráfico de carga (CTL, ATL, TSB)."""
    
    if weekly_metrics.empty:
        st.warning("Sem dados de carga para gerar o gráfico.")
        return
        
    fig, ax = plt.subplots(figsize=(10, 4))
    
    # CTL e ATL
    ax.plot(weekly_metrics["WeekStart"], weekly_metrics["CTL"], label="CTL (Carga Crônica)")
    ax.plot(weekly_metrics["WeekStart"], weekly_metrics["ATL"], label="ATL (Carga Aguda)")
    
    # TSB (em barras)
    ax2 = ax.twinx()
    ax2.bar(weekly_metrics["WeekStart"], weekly_metrics["TSB"], label="TSB (Balanço)", alpha=0.3, width=5)
    
    ax.set_xlabel("Semana")
    ax.set_ylabel("Carga (Proxy Load)")
    ax2.set_ylabel("TSB")
    
    ax.legend(loc="upper left")
    ax2.legend(loc="upper right")
    
    plt.title("Métricas de Carga de Treinamento (CTL, ATL, TSB)")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    st.pyplot(fig)
    
    st.markdown("""
    **Explicação das Métricas de Carga:**
    - **Carga Proxy (Load):** É uma estimativa da carga interna do treino, combinando volume e intensidade. A fórmula usada é:
      - Corrida: km * 1.0
      - Ciclismo: km * 0.6
      - Natação: metros/1000 * 1.2
      - Força/Calistenia: minutos * 0.3
      - Mobilidade: minutos * 0.2
    - **CTL (Chronic Training Load):** Representa a sua **aptidão** (fitness) de longo prazo. É a média ponderada da carga das últimas 6 semanas. Um CTL crescente indica que você está ficando mais apto.
    - **ATL (Acute Training Load):** Representa a sua **fadiga** (fatigue) de curto prazo. É a média ponderada da carga da última semana. Um ATL alto indica fadiga recente.
    - **TSB (Training Stress Balance):** É o **balanço** entre aptidão e fadiga (CTL - ATL).
      - **TSB Positivo:** Indica que você está descansado e pronto para competir (peaking).
      - **TSB Negativo:** Indica que você está em uma fase de treinamento pesado (overreaching funcional).
    """)

# -----------------------
# Interface Streamlit
# -----------------------

def main():
    st.set_page_config(
        page_title="Triathlon Planner",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    
    # Inicialização de estado
    if "df" not in st.session_state:
        st.session_state["df"] = load_all()
    if "current_week_start" not in st.session_state:
        st.session_state["current_week_start"] = monday_of_week(today())
    if "frozen_targets" not in st.session_state:
        st.session_state["frozen_targets"] = {}
        
    df = st.session_state["df"]
    
    # -----------------------
    # Sidebar (Apenas Navegação)
    # -----------------------
    st.sidebar.title("🏊‍♂️🚴‍♀️🏃‍♀️ Triathlon Planner")
    
    # Navegação
    menu = st.sidebar.radio("Navegação", ["📅 Planejamento Semanal", "📈 Dashboard", "⚙️ Periodização"], index=0)
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("Desenvolvido por [Matheus Vianna](https://matheusvianna.com)")
    
    # -----------------------
    # Conteúdo Principal
    # -----------------------
    
    if menu == "📅 Planejamento Semanal":
        st.header("📅 Planejamento Semanal")
        
        # Controles movidos para o corpo principal
        
        st.subheader("1. Parâmetros de Prescrição")
        
        col_pace1, col_pace2, col_pace3 = st.columns(3)
        paces = {
            "run_pace_min_per_km": col_pace1.number_input("Corrida (min/km)", value=5.0, min_value=3.0, max_value=10.0, step=0.1, format="%.1f"),
            "swim_sec_per_100m": col_pace2.number_input("Natação (seg/100m)", value=110, min_value=60, max_value=200, step=5),
            "bike_kmh": col_pace3.number_input("Ciclismo (km/h)", value=32.0, min_value=15.0, max_value=50.0, step=0.5, format="%.1f"),
        }
        
        st.subheader("2. Metas Semanais (Volume e Sessões)")
        
        weekly_targets = {}
        sessions_per_mod = {}
        
        # Layout para as metas e sessões
        cols_mod = st.columns(len(MODALIDADES))
        cols_sess = st.columns(len(MODALIDADES))
        
        for i, mod in enumerate(MODALIDADES):
            unit = UNITS_ALLOWED[mod]
            
            # Volume
            weekly_targets[mod] = cols_mod[i].number_input(
                f"Volume {mod} ({unit})",
                value=float(st.session_state.get(f"target_{mod}", 0.0)), # Mantém o valor anterior se existir
                min_value=0.0,
                step=_unit_step(unit),
                format="%.1f" if unit == "km" else "%g",
                key=f"target_{mod}"
            )

            # Dias preferidos e Treino Chave
            dias_semana_options = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sáb": 5, "Dom": 6}
            default_days = {
                "Corrida": [2, 4, 6],  # Qua, Sex, Dom
                "Ciclismo": [1, 3, 5], # Ter, Qui, Sáb
                "Natação": [0, 2], # Seg, Qua
                "Força/Calistenia": [1, 4], # Ter, Sex
                "Mobilidade": [0, 6], # Seg, Dom
            }
            
            # Mapeia os dias padrão para as abreviações em português
            default_selected_days = [
                abrev for abrev, index in dias_semana_options.items() 
                if index in default_days.get(mod, [])
            ]
            
            cols_mod[i].multiselect(
                f"Dias Preferidos {mod}",
                options=list(dias_semana_options.keys()),
                key=f"pref_days_{mod}",
                default=default_selected_days
            )
            
            cols_sess[i].selectbox(
                f"Treino Chave {mod}",
                options=[""] + TIPOS_MODALIDADE.get(mod, []),
                key=f"key_sess_{mod}"
            )
            
            # Sessões
            default_sessions = 3 if mod in ["Corrida", "Ciclismo"] else 2
            sessions_per_mod[mod] = cols_sess[i].number_input(
                f"Sessões {mod}",
                value=int(st.session_state.get(f"sess_{mod}", default_sessions)),
                min_value=0,
                max_value=5,
                step=1,
                key=f"sess_{mod}"
            )
            
        st.markdown("---")
        
        # Seletor de semana
        col1, col2, col3 = st.columns([1, 2, 1])
        
        if col1.button("⬅️ Semana Anterior"):
            st.session_state["current_week_start"] -= timedelta(days=7)
            safe_rerun()
            
        week_start = st.session_state["current_week_start"]
        col2.subheader(f"Semana de {week_start.strftime('%d/%m/%Y')}")
        
        if col3.button("Semana Seguinte ➡️"):
            st.session_state["current_week_start"] += timedelta(days=7)
            safe_rerun()
            
        # Carregar dados da semana
        week_df = week_slice(df, week_start)
        
        # Se a semana estiver vazia, criar o template
        if week_df.empty:
            week_df = default_week_df(week_start)
            
        # Congelar metas para esta semana (se ainda não estiverem congeladas)
        frozen_targets = get_frozen_weekly_targets(week_start, weekly_targets)
        
        st.markdown("---")
        
        # Botões de Ação
        col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)
        
        # Botão Gerar Semana
        if col_btn1.button("📆 Gerar Semana (Prescrição Automática)", help="Gera o plano semanal com base nas metas definidas acima."):
            # 1. Gerar o novo DF
            dias_semana_map = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sáb": 5, "Dom": 6}
            current_preferred_days = {mod: [dias_semana_map[d] for d in st.session_state.get(f"pref_days_{mod}", [])] for mod in MODALIDADES}
            key_sessions = {mod: st.session_state.get(f"key_sess_{mod}", "") for mod in MODALIDADES}

            new_week_df = distribute_week_by_targets(
                week_start,
                weekly_targets,
                sessions_per_mod,
                key_sessions,
                paces,
                current_preferred_days,
            )
            
            # 2. Mesclar com o DF principal (substituir a semana)
            df_before = df[df["WeekStart"] != week_start]
            df_new = pd.concat([df_before, new_week_df], ignore_index=True)
            
            # 3. Salvar e recarregar
            save_all(df_new)
            st.session_state["df"] = load_all()
            set_frozen_weekly_targets(week_start, weekly_targets) # Congela as metas usadas
            st.success(f"Semana de {week_start.strftime('%d/%m')} gerada e salva com sucesso!")
            safe_rerun()
            
        # Botão Salvar Edições
        if col_btn2.button("💾 Salvar Edições Manuais", help="Salva as alterações feitas diretamente na tabela."):
            st.info("As edições são salvas automaticamente ao interagir com a tabela.")
            
        # Botão Exportar ICS
        if col_btn3.download_button(
            label="📤 Exportar .ICS",
            data=generate_ics(week_df, f"treino_{week_start.strftime('%Y%m%d')}.ics"),
            file_name=f"treino_{week_start.strftime('%Y%m%d')}.ics",
            mime="text/calendar",
            help="Exporta o plano semanal para o formato de calendário (.ics)."
        ):
            st.info("Arquivo .ICS gerado com sucesso!")
            
        # Botão Exportar PDF
        pdf_bytes = generate_pdf(week_df, week_start)
        if col_btn4.download_button(
            label="📕 Exportar PDF",
            data=pdf_bytes,
            file_name=f"treino_{week_start.strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
            help="Exporta o plano semanal para PDF."
        ):
            st.info("PDF gerado com sucesso!")
            
        st.markdown("---")
        
        # Tabela de Treinos (Data Editor)
        
        # Colunas editáveis
        editable_cols = {
            "Modalidade": st.column_config.SelectboxColumn(
                "Modalidade",
                options=MODALIDADES + ["Descanso"],
                required=True,
            ),
            "Tipo de Treino": st.column_config.SelectboxColumn(
                "Tipo de Treino",
                options=sum(TIPOS_MODALIDADE.values(), ["Ativo/Passivo"]),
            ),
            "Volume": st.column_config.NumberColumn(
                "Volume",
                min_value=0.0,
                format="%.1f",
            ),
            "Unidade": st.column_config.TextColumn("Unidade"),
            "RPE": st.column_config.NumberColumn(
                "RPE",
                min_value=0,
                max_value=10,
                step=1,
            ),
            "Detalhamento": st.column_config.TextColumn("Detalhamento"),
            "Observações": st.column_config.TextColumn("Observações"),
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=STATUS_CHOICES,
            ),
        }
        
        # Callback para salvar as edições do data_editor
        def handle_editor_change():
            if st.session_state.get("editor_key"):
                changes = st.session_state["editor_key"]["edited_rows"]
                if changes:
                    df_current = st.session_state["df"].copy()
                    week_df_current = week_slice(df_current, week_start)
                    
                    for idx, new_values in changes.items():
                        old_row = week_df_current.iloc[idx]
                        
                        # Aplicar as mudanças
                        for col, val in new_values.items():
                            week_df_current.at[week_df_current.index[idx], col] = val
                            
                        # Atualizar ChangeLog e LastEditedAt
                        new_row = week_df_current.iloc[idx]
                        week_df_current.at[week_df_current.index[idx], "ChangeLog"] = append_changelog(old_row, new_row)
                        week_df_current.at[week_df_current.index[idx], "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
                        
                        # Garantir unidade correta
                        mod = new_row["Modalidade"]
                        unit = lock_unit(mod)
                        if unit:
                            week_df_current.at[week_df_current.index[idx], "Unidade"] = unit
                            
                    # Mesclar de volta ao DF principal
                    df_before = df_current[df_current["WeekStart"] != week_start]
                    df_new = pd.concat([df_before, week_df_current], ignore_index=True)
                    
                    # Salvar e recarregar
                    save_all(df_new)
                    st.session_state["df"] = load_all()
                    st.success("Edições salvas com sucesso!")
                    safe_rerun() # Recarrega para mostrar o DF atualizado
                    
        # Exibir o editor
        dias_semana = {"Mon": "Seg", "Tue": "Ter", "Wed": "Qua", "Thu": "Qui", "Fri": "Sex", "Sat": "Sáb", "Sun": "Dom"}
        week_df_display = week_df.copy()
        week_df_display["Data"] = week_df_display["Data"].apply(lambda d: f"{d.strftime('%d/%m')} ({dias_semana.get(d.strftime('%a'), '')})")

        edited_df = st.data_editor(
            week_df_display,
            column_config=editable_cols,
            hide_index=True,
            num_rows="fixed",
            key="editor_key",
            on_change=handle_editor_change,
        )
        
        # Exibir as metas congeladas
        st.markdown("---")
        st.subheader("Metas da Semana (Congeladas)")
        
        col_t1, col_t2, col_t3, col_t4, col_t5 = st.columns(5)
        cols_t = [col_t1, col_t2, col_t3, col_t4, col_t5]
        
        for i, mod in enumerate(MODALIDADES):
            vol = frozen_targets.get(mod, 0.0)
            unit = UNITS_ALLOWED[mod]
            cols_t[i].metric(mod, f"{vol:g} {unit}")
            
        # Exibir a carga proxy da semana
        week_load = week_df["Load"].sum() if "Load" in week_df.columns else 0.0
        st.info(f"**Carga Proxy Planejada:** {week_load:.1f}")
    elif menu == "📈 Dashboard":
        st.header("📈 Dashboard de Análise")
        
        # Calcular métricas
        weekly_metrics, df_with_load = calculate_metrics(df)
        
        # Gráfico de Carga
        plot_load_chart(weekly_metrics)
        
        st.markdown("---")
        
        # Tabela de Métricas Semanais
        st.subheader("Métricas Semanais")
        st.dataframe(
            weekly_metrics.sort_values("WeekStart", ascending=False).head(12),
            column_config={
                "WeekStart": st.column_config.DateColumn("Semana", format="DD/MM/YYYY"),
                "TotalLoad": st.column_config.NumberColumn("Carga Total", format="%.1f"),
                "CTL": st.column_config.NumberColumn("CTL", format="%.1f"),
                "ATL": st.column_config.NumberColumn("ATL", format="%.1f"),
                "TSB": st.column_config.NumberColumn("TSB", format="%.1f"),
                "NumSessions": st.column_config.NumberColumn("Sessões"),
            },
            hide_index=True,
        )
        
        st.markdown("---")
        st.subheader("Registro de Treinos Realizados (Edição de Status)")
        
        # Colunas visíveis e editáveis no Dashboard
        dashboard_cols = [
            "Data", "Modalidade", "Tipo de Treino", "Volume", "Unidade", "Status", "RPE", "Observações"
        ]
        
        # Configuração de colunas para o editor do Dashboard
        dashboard_col_config = {
            "Data": st.column_config.DateColumn("Data", format="DD/MM/YYYY", disabled=True),
            "Modalidade": st.column_config.TextColumn("Modalidade", disabled=True),
            "Tipo de Treino": st.column_config.TextColumn("Tipo de Treino", disabled=True),
            "Volume": st.column_config.NumberColumn("Volume", disabled=True),
            "Unidade": st.column_config.TextColumn("Unidade", disabled=True),
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=STATUS_CHOICES,
                required=True,
            ),
            "RPE": st.column_config.NumberColumn(
                "RPE",
                min_value=0,
                max_value=10,
                step=1,
            ),
            "Observações": st.column_config.TextColumn("Observações"),
        }
        
        # Callback para salvar as edições do data_editor do Dashboard
        def handle_dashboard_editor_change():
            if st.session_state.get("dashboard_editor_key"):
                changes = st.session_state["dashboard_editor_key"]["edited_rows"]
                if changes:
                    df_current = st.session_state["df"].copy()
                    
                    for idx, new_values in changes.items():
                        # Mapeamento simples: assume mesma ordenação/linhas visíveis (OK para este fluxo)
                        original_index = df_current.index[idx]
                        old_row = df_current.loc[original_index]
                        
                        # Aplicar as mudanças
                        for col, val in new_values.items():
                            df_current.at[original_index, col] = val
                            
                        # Atualizar ChangeLog e LastEditedAt
                        new_row = df_current.loc[original_index]
                        df_current.at[original_index, "ChangeLog"] = append_changelog(old_row, new_row)
                        df_current.at[original_index, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
                        
                    # Salvar e recarregar
                    save_all(df_current)
                    st.session_state["df"] = load_all()
                    st.success("Status e observações salvas com sucesso!")
                    safe_rerun() # Recarrega para mostrar o DF atualizado e recalcular métricas
        
        # Exibir o editor no Dashboard
        st.data_editor(
            df_with_load[dashboard_cols].sort_values("Data", ascending=False),
            column_config=dashboard_col_config,
            hide_index=True,
            num_rows="fixed",
            key="dashboard_editor_key",
            on_change=handle_dashboard_editor_change,
        )
        
    elif menu == "⚙️ Periodização":
        st.header("⚙️ Periodização (Ciclo Multi-Semanal)")
        
        st.markdown("""
        Use esta seção para gerar um ciclo de treinamento de múltiplas semanas (ex: 4, 8, 12 semanas)
        com progressão de carga e distribuição de volume por fase (Base, Build, Peak, Recovery).
        """)
        
        st.markdown("---")
        
        # Parâmetros do Ciclo
        col_p1, col_p2, col_p3 = st.columns(3)
        
        cycle_start_week = col_p1.date_input("Semana de Início do Ciclo", value=monday_of_week(today()))
        num_weeks = col_p2.number_input("Número de Semanas no Ciclo", value=4, min_value=4, max_value=12, step=4)
        
        # Carga Proxy Total (Base) - Movido da sidebar
        total_load_target = col_p3.number_input("Carga Proxy Total (Base)", value=100.0, min_value=0.0, step=5.0)
        
        st.subheader("Distribuição de Carga por Fase (%)")
        
        # Explicação da Carga Proxy
        st.info(f"""
        **Carga Proxy (Base):** Combina volume e intensidade. Os coeficientes são:
        - Corrida: 1 km = **1.0**
        - Ciclismo: 1 km = **0.6**
        - Natação: 1000 m = **1.2**
        - Força/Calistenia: 60 min = **0.3**
        - Mobilidade: 60 min = **0.2**
        
        A **Carga Proxy Total (Base)** ({total_load_target:.1f}) é a carga da primeira semana (Fase Base).
        As frações abaixo definem como essa carga será distribuída entre as modalidades ao longo do ciclo.
        """)
        
        # Tabela de Proporções por Modalidade e Fase
        phase_proportions = {}
        
        for mod in MODALIDADES:
            st.markdown(f"**{mod}**")
            cols = st.columns(len(PHASES))
            
            phase_proportions[mod] = {}
            total_mod_prop = 0
            
            for i, phase in enumerate(PHASES):
                default_val = 0
                if mod in ["Corrida", "Ciclismo", "Natação"]:
                    if phase in ["Base", "Build", "Peak"]: default_val = 25
                    if phase == "Recovery": default_val = 5
                elif mod in ["Força/Calistenia", "Mobilidade"]:
                    if phase in ["Base", "Build"]: default_val = 40
                    if phase in ["Peak", "Recovery"]: default_val = 10
                    
                prop = cols[i].number_input(
                    f"{phase} (%)",
                    value=default_val,
                    min_value=0,
                    max_value=100,
                    step=5,
                    key=f"prop_{mod}_{phase}"
                )
                phase_proportions[mod][phase] = prop / 100.0
                total_mod_prop += prop
                
            if total_mod_prop != 100:
                st.warning(f"Soma das proporções de {mod}: {total_mod_prop}%. Ajuste para 100%.")
                
        st.markdown("---")
        
        # Botão Gerar Ciclo
        if st.button("📈 Gerar Ciclo (Multi-Semanal)", help="Gera o plano para o número de semanas e periodização definidos."):
            
            # 1. Gerar o novo DF do ciclo
            dias_semana_map = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sáb": 5, "Dom": 6}
            current_preferred_days = {mod: [dias_semana_map[d] for d in st.session_state.get(f"pref_days_{mod}", [])] for mod in MODALIDADES}
            key_sessions = {mod: st.session_state.get(f"key_sess_{mod}", "") for mod in MODALIDADES}

            current_sessions_per_mod = {mod: st.session_state.get(f"sess_{mod}", 0) for mod in MODALIDADES}
            current_paces = {
                "run_pace_min_per_km": st.session_state.get("target_Corrida", 5.0),
                "swim_sec_per_100m": st.session_state.get("target_Natação", 110),
                "bike_kmh": st.session_state.get("target_Ciclismo", 32.0),
            }
            
            cycle_df = generate_cycle(
                cycle_start_week,
                num_weeks,
                total_load_target,
                phase_proportions,
                current_sessions_per_mod,
                current_paces,
                current_preferred_days,
                key_sessions,
            )
            
            # 2. Mesclar com o DF principal (substituir as semanas do ciclo)
            cycle_end_week = cycle_start_week + timedelta(days=7 * num_weeks)
            df_before = df[df["WeekStart"] < cycle_start_week]
            df_after = df[df["WeekStart"] >= cycle_end_week]
            
            df_new = pd.concat([df_before, cycle_df, df_after], ignore_index=True)
            
            # 3. Salvar e recarregar
            save_all(df_new)
            st.session_state["df"] = load_all()
            st.success(f"Ciclo de {num_weeks} semanas gerado e salvo com sucesso!")
            
            # Redirecionar para a primeira semana do ciclo
            st.session_state["current_week_start"] = cycle_start_week
            safe_rerun()
            
        st.markdown("---")
        
        # Pré-visualização do ciclo (opcional)
        if "cycle_df_preview" not in st.session_state:
            st.session_state["cycle_df_preview"] = pd.DataFrame()
            
        # Exibir o plano do ciclo gerado
        if not st.session_state["cycle_df_preview"].empty:
            st.subheader("Pré-visualização do Ciclo Gerado")
            st.dataframe(st.session_state["cycle_df_preview"])

if __name__ == "__main__":
    # Garantir que o diretório de dados exista antes de qualquer operação de arquivo
    ensure_dirs()
    
    # O Streamlit precisa que a função principal seja chamada
    try:
        main()
    except Exception as e:
        st.error(f"Ocorreu um erro: {e}")
        st.stop()
