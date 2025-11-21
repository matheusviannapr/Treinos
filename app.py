
# app.py — TriPlano (evolução do TriCiclo Planner)
# ----------------------------------------------------------------------------
# Funcionalidades:
# - Login/cadastro multiusuário (SQLite)
# - Treinos multiusuário com UserID + UID estável
# - Metas, sessões, preferências por modalidade
# - Geração automática de semana
# - Periodização multi-semanal (generate_cycle)
# - Exportações: PDF / ICS
# - Disponibilidade persistida no banco SQLite
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
import re
import calendar as py_calendar
import urllib.parse
from datetime import datetime, date, timedelta, time, timezone
from typing import Optional

import pandas as pd
import numpy as np
import streamlit as st
import requests
from fpdf import FPDF
import matplotlib.pyplot as plt
import unicodedata
import secrets
import folium
from streamlit_folium import st_folium

from streamlit_calendar import calendar as st_calendar  # pip install streamlit-calendar

import db
import triplanner_engine

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
CSV_PATH = os.path.join(DATA_DIR, "treinos.csv")
USERS_CSV_PATH = os.path.join(DATA_DIR, "usuarios.csv")
AVAIL_CSV_PATH = os.path.join(DATA_DIR, "availability.csv")
TIMEPATTERN_CSV_PATH = os.path.join(DATA_DIR, "time_patterns.csv")
PREFERENCES_CSV_PATH = os.path.join(DATA_DIR, "preferences.csv")
DAILY_NOTES_CSV_PATH = os.path.join(DATA_DIR, "daily_notes.csv")

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
    "TempoEstimadoMin",
    "Observações",
    "Status",
    "adj",
    "AdjAppliedAt",
    "ChangeLog",
    "LastEditedAt",
    "WeekStart",
    "TSS",
    "IF",
    "ATL",
    "CTL",
    "TSB",
    "StravaID",
    "StravaURL",
    "DuracaoRealMin",
    "DistanciaReal",
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
    "Natação": (255, 255, 255),
}

MODALITY_EMOJIS = {
    "Corrida": "🏃",
    "Ciclismo": "🚴",
    "Natação": "🏊",
    "Força/Calistenia": "💪",
    "Mobilidade": "🤸",
    "Descanso": "😴",
}

STRAVA_TO_PLAN_MODALITY = {
    "run": "corrida",
    "run_workout": "corrida",
    "virtual_run": "corrida",
    "ride": "ciclismo",
    "virtual_ride": "ciclismo",
    "bike": "ciclismo",
    "swim": "natação",
    "lap_swim": "natação",
    "open_water_swim": "natação",
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


def modality_label(mod: str | None) -> str:
    if not mod:
        return ""
    emoji = MODALITY_EMOJIS.get(mod, "")
    return f"{emoji} {mod}" if emoji else mod

UNITS_ALLOWED = {
    "Corrida": "km",
    "Ciclismo": "km",
    "Natação": "m",
    "Força/Calistenia": "min",
    "Mobilidade": "min",
}
MODALIDADES = list(UNITS_ALLOWED.keys())
STATUS_CHOICES = ["Planejado", "Realizado", "Adiado", "Cancelado"]

SUPPORT_WORK_DEFAULTS = {
    "Força/Calistenia": 90.0,
    "Mobilidade": 60.0,
}

LOAD_COEFF = {
    "Corrida": 1.0,
    "Ciclismo": 0.6,
    "Natação": 1.2,
    "Força/Calistenia": 0.3,
    "Mobilidade": 0.2,
}

TIPOS_MODALIDADE = {
    "Corrida": [
        "Rodagem regenerativa",
        "Corrida contínua leve",
        "Corrida contínua moderada",
        "Tempo Run (limiar)",
        "Fartlek",
        "Intervalado (VO₂máx)",
        "Longão",
        "Educativos técnicos",
        "Prova",
    ],
    "Ciclismo": ["Endurance", "Intervalado", "Cadência", "Força/Subida"],
    "Natação": ["Técnica", "Ritmo", "Intervalado", "Contínuo"],
    "Força/Calistenia": ["Força máxima", "Resistência muscular", "Core/Estabilidade", "Mobilidade/Recuperação"],
    "Mobilidade": ["Soltura", "Recuperação", "Prevenção"],
}

PHASES = ["Base", "Build", "Peak", "Recovery"]

DEFAULT_TRAINING_DURATION_MIN = 60

TIME_OF_DAY_WINDOWS = {
    "Manhã": time(6, 0),
    "Tarde": time(12, 0),
    "Noite": time(18, 0),
    "Indiferente": time(8, 0),
}

OFF_DAY_LABELS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]

# ----------------------------------------------------------------------------
# Diretórios
# ----------------------------------------------------------------------------

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(EXPORT_DIR, exist_ok=True)


def initialize_schema():
    ensure_dirs()
    try:
        db.init_db()
        migrate_from_csv()
    except db.DatabaseConfigError:
        st.error("Configuração do banco de dados ausente.")
        st.info(
            "Defina a variável DATABASE_URL em um arquivo .env na raiz do projeto "
            "durante o desenvolvimento ou configure st.secrets['db']['url'] com a "
            "string de conexão do Neon no Streamlit Cloud."
        )
        st.code(
            """# .env (desenvolvimento)\nDATABASE_URL=postgresql://usuario:senha@host/neondb?sslmode=require\n\n# .streamlit/secrets.toml (produção)\n[db]\nurl = \"postgresql://usuario:senha@host/neondb?sslmode=require\"""",
            language="toml",
        )
        st.stop()


def migrate_from_csv():
    def _already_migrated(key: str) -> bool:
        row = db.fetch_one("SELECT value FROM meta WHERE key = :key", {"key": key})
        return row is not None and str(row.get("value", "")) == "1"

    def _mark_migrated(key: str):
        db.execute(
            """
            INSERT INTO meta (key, value)
            VALUES (:key, :value)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            {"key": key, "value": "1"},
        )

    if os.path.exists(USERS_CSV_PATH) and not _already_migrated("users"):
        df = pd.read_csv(USERS_CSV_PATH, dtype=str).fillna("")
        if not df.empty:
            records = df.to_dict(orient="records")
            db.execute_many(
                """
                INSERT INTO users (user_id, nome, created_at)
                VALUES (:user_id, :nome, :created_at)
                ON CONFLICT (user_id)
                DO UPDATE SET nome = EXCLUDED.nome, created_at = EXCLUDED.created_at
                """,
                [
                    {
                        "user_id": rec.get("user_id", ""),
                        "nome": rec.get("nome", ""),
                        "created_at": rec.get("created_at", ""),
                    }
                    for rec in records
                ],
            )
        _mark_migrated("users")

    if os.path.exists(CSV_PATH) and not _already_migrated("treinos"):
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("")
        if not df.empty:
            for col in ["Volume", "RPE", "adj"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

            def _normalize_date(val):
                parsed = pd.to_datetime(val, errors="coerce")
                if pd.isna(parsed):
                    return None
                return parsed.date()

            for col in ["Data", "WeekStart"]:
                if col in df.columns:
                    df[col] = df[col].apply(_normalize_date)

            records = df[SCHEMA_COLS].to_dict(orient="records")
            db.execute_many(
                """
                INSERT INTO treinos (
                    "UserID", "UID", "Data", "Start", "End", "Modalidade",
                    "Tipo de Treino", "Volume", "Unidade", "RPE", "Detalhamento",
                    "Observações", "Status", "adj", "AdjAppliedAt", "ChangeLog",
                    "LastEditedAt", "WeekStart"
                ) VALUES (
                    :user_id, :uid, :data, :start, :end, :modalidade,
                    :tipo_treino, :volume, :unidade, :rpe, :detalhamento,
                    :observacoes, :status, :adj, :adj_applied_at, :changelog,
                    :last_edited_at, :week_start
                )
                ON CONFLICT ("UID") DO UPDATE SET
                    "UserID" = EXCLUDED."UserID",
                    "Data" = EXCLUDED."Data",
                    "Start" = EXCLUDED."Start",
                    "End" = EXCLUDED."End",
                    "Modalidade" = EXCLUDED."Modalidade",
                    "Tipo de Treino" = EXCLUDED."Tipo de Treino",
                    "Volume" = EXCLUDED."Volume",
                    "Unidade" = EXCLUDED."Unidade",
                    "RPE" = EXCLUDED."RPE",
                    "Detalhamento" = EXCLUDED."Detalhamento",
                    "Observações" = EXCLUDED."Observações",
                    "Status" = EXCLUDED."Status",
                    "adj" = EXCLUDED."adj",
                    "AdjAppliedAt" = EXCLUDED."AdjAppliedAt",
                    "ChangeLog" = EXCLUDED."ChangeLog",
                    "LastEditedAt" = EXCLUDED."LastEditedAt",
                    "WeekStart" = EXCLUDED."WeekStart"
                """,
                [
                    {
                        "user_id": (rec.get("UserID", "") or "default"),
                        "uid": rec.get("UID")
                        or generate_uid(rec.get("UserID", "") or "default"),
                        "data": rec.get("Data"),
                        "start": rec.get("Start") or None,
                        "end": rec.get("End") or None,
                        "modalidade": rec.get("Modalidade", ""),
                        "tipo_treino": rec.get("Tipo de Treino", ""),
                        "volume": float(rec.get("Volume", 0.0) or 0.0),
                        "unidade": rec.get("Unidade", ""),
                        "rpe": float(rec.get("RPE", 0.0) or 0.0),
                        "detalhamento": rec.get("Detalhamento", ""),
                        "observacoes": rec.get("Observações", ""),
                        "status": rec.get("Status", ""),
                        "adj": float(rec.get("adj", 0.0) or 0.0),
                        "adj_applied_at": rec.get("AdjAppliedAt", ""),
                        "changelog": rec.get("ChangeLog", ""),
                        "last_edited_at": rec.get("LastEditedAt", ""),
                        "week_start": rec.get("WeekStart"),
                    }
                    for rec in records
                ],
            )
        _mark_migrated("treinos")

    if os.path.exists(AVAIL_CSV_PATH) and not _already_migrated("availability"):
        df = pd.read_csv(AVAIL_CSV_PATH, dtype=str).fillna("")
        if not df.empty:
            records = df.to_dict(orient="records")
            db.execute_many(
                """
                INSERT INTO availability ("UserID", "WeekStart", "Start", "End")
                VALUES (:user_id, :week_start, :start, :end)
                ON CONFLICT ("UserID", "WeekStart", "Start", "End") DO NOTHING
                """,
                [
                    {
                        "user_id": rec.get("UserID", ""),
                        "week_start": rec.get("WeekStart", ""),
                        "start": rec.get("Start", ""),
                        "end": rec.get("End", ""),
                    }
                    for rec in records
                ],
            )
        _mark_migrated("availability")

    if os.path.exists(TIMEPATTERN_CSV_PATH) and not _already_migrated("time_patterns"):
        df = pd.read_csv(TIMEPATTERN_CSV_PATH, dtype=str).fillna("")
        if not df.empty:
            records = df.to_dict(orient="records")
            db.execute_many(
                """
                INSERT INTO time_patterns ("UserID", "PatternJSON")
                VALUES (:user_id, :pattern_json)
                ON CONFLICT ("UserID") DO UPDATE SET "PatternJSON" = EXCLUDED."PatternJSON"
                """,
                [
                    {
                        "user_id": rec.get("UserID", ""),
                        "pattern_json": rec.get("PatternJSON", ""),
                    }
                    for rec in records
                ],
            )
        _mark_migrated("time_patterns")

    if os.path.exists(PREFERENCES_CSV_PATH) and not _already_migrated("preferences"):
        df = pd.read_csv(PREFERENCES_CSV_PATH, dtype=str).fillna("")
        if not df.empty:
            records = df.to_dict(orient="records")
            db.execute_many(
                """
                INSERT INTO preferences ("UserID", "PreferencesJSON")
                VALUES (:user_id, :preferences_json)
                ON CONFLICT ("UserID") DO UPDATE SET "PreferencesJSON" = EXCLUDED."PreferencesJSON"
                """,
                [
                    {
                        "user_id": rec.get("UserID", ""),
                        "preferences_json": rec.get("PreferencesJSON", ""),
                    }
                    for rec in records
                ],
            )
        _mark_migrated("preferences")

    if os.path.exists(DAILY_NOTES_CSV_PATH) and not _already_migrated("daily_notes"):
        df = pd.read_csv(DAILY_NOTES_CSV_PATH, dtype=str).fillna("")
        if not df.empty:
            records = df.to_dict(orient="records")
            db.execute_many(
                """
                INSERT INTO daily_notes ("UserID", "Date", "Note", "UpdatedAt")
                VALUES (:user_id, :date, :note, :updated_at)
                ON CONFLICT ("UserID", "Date")
                DO UPDATE SET "Note" = EXCLUDED."Note", "UpdatedAt" = EXCLUDED."UpdatedAt"
                """,
                [
                    {
                        "user_id": rec.get("UserID", ""),
                        "date": rec.get("Date", ""),
                        "note": rec.get("Note", ""),
                        "updated_at": rec.get("UpdatedAt", ""),
                    }
                    for rec in records
                ],
            )
        _mark_migrated("daily_notes")


@st.cache_resource(show_spinner=False)
def init_database():
    initialize_schema()
    return True

# ----------------------------------------------------------------------------
# Usuários
# ----------------------------------------------------------------------------

def init_users_if_needed():
    init_database()

@st.cache_data(show_spinner=False)
def load_users_df() -> pd.DataFrame:
    init_database()
    df = db.fetch_dataframe(
        "SELECT user_id, nome, created_at FROM users ORDER BY created_at"
    )
    if df.empty:
        df = pd.DataFrame(columns=["user_id", "nome", "created_at"])
    return df.fillna("")

def save_users_df(user_id: str, user_df: pd.DataFrame):
    all_df = load_all()

    # Garante colunas obrigatórias
    for col in SCHEMA_COLS:
        if col not in user_df.columns:
            user_df[col] = ""

    # Garante UserID/UID
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

    save_all(merged)  # persiste no banco e limpa cache

    st.session_state["all_df"] = merged
    st.session_state["df"] = merged[merged["UserID"] == user_id].copy()

def get_user(user_id: str):
    df = load_users_df()
    row = df[df["user_id"] == user_id]
    return row.iloc[0] if not row.empty else None

def save_users_book(df_users: pd.DataFrame):
    """Substitui a base de usuários persistida no banco."""
    init_database()
    df_out = df_users.copy().fillna("")
    records = df_out.to_dict(orient="records")
    db.execute("DELETE FROM users")
    if records:
        db.execute_many(
            """
            INSERT INTO users (user_id, nome, created_at)
            VALUES (:user_id, :nome, :created_at)
            """,
            [
                {
                    "user_id": rec.get("user_id", ""),
                    "nome": rec.get("nome", ""),
                    "created_at": rec.get("created_at", ""),
                }
                for rec in records
            ],
        )
    load_users_df.clear()

def create_user(user_id: str, nome: str) -> bool:
    init_database()
    row = db.fetch_one(
        "SELECT 1 FROM users WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    if row:
        return False
    created_at = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO users (user_id, nome, created_at) VALUES (:user_id, :nome, :created_at)",
        {"user_id": user_id, "nome": nome, "created_at": created_at},
    )
    load_users_df.clear()
    return True

def logout():
    for key in list(st.session_state.keys()):
        if key.startswith("login_") or key.startswith("cal_") or key in [
            "user_id", "user_name", "df", "all_df", "current_week_start"
        ]:
            del st.session_state[key]
    safe_rerun()

# ----------------------------------------------------------------------------
# Treinos (multiusuário)
# ----------------------------------------------------------------------------

def init_csv_if_needed():
    init_database()

@st.cache_data(show_spinner=False)
def load_all() -> pd.DataFrame:
    init_database()
    df = db.fetch_dataframe(
        "SELECT "
        "    \"UserID\", \"UID\", \"Data\"::text AS \"Data\", \"Start\"::text AS \"Start\", \"End\"::text AS \"End\", \"Modalidade\"," 
        "    \"Tipo de Treino\", \"Volume\", \"Unidade\", \"RPE\", \"Detalhamento\", \"TempoEstimadoMin\"," 
        "    \"Observações\", \"Status\", \"adj\", \"AdjAppliedAt\", \"ChangeLog\"," 
        "    \"LastEditedAt\", \"WeekStart\"::text AS \"WeekStart\", \"TSS\", \"IF\", \"ATL\", \"CTL\", \"TSB\", \"StravaID\", \"StravaURL\", \"DuracaoRealMin\", \"DistanciaReal\""
        " FROM treinos"
    )
    if df.empty:
        df = pd.DataFrame(columns=SCHEMA_COLS)

    numeric_cols = [
        "Volume",
        "RPE",
        "adj",
        "TempoEstimadoMin",
        "TSS",
        "IF",
        "ATL",
        "CTL",
        "TSB",
        "DuracaoRealMin",
        "DistanciaReal",
    ]

    for col in SCHEMA_COLS:
        if col not in df.columns:
            if col in numeric_cols:
                df[col] = 0.0
            else:
                df[col] = ""

    if not df.empty:
        df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date

        for c in numeric_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

        for c in ["ChangeLog", "Detalhamento", "Observações"]:
            df[c] = df[c].astype(str)

        for i, r in df.iterrows():
            mod = r.get("Modalidade", "")
            if mod in UNITS_ALLOWED:
                unit_ok = UNITS_ALLOWED[mod]
                if r.get("Unidade", "") != unit_ok:
                    df.at[i, "Unidade"] = unit_ok

    return df[SCHEMA_COLS].copy()

def save_all(df: pd.DataFrame):
    init_database()
    df_out = df.copy()
    if not df_out.empty:
        data_series = pd.to_datetime(df_out["Data"], errors="coerce")
        week_series = pd.to_datetime(df_out["WeekStart"], errors="coerce")
        df_out["Data"] = data_series.dt.date.astype(str)
        df_out["WeekStart"] = week_series.dt.date.astype(str)
        df_out.loc[data_series.isna(), "Data"] = ""
        df_out.loc[week_series.isna(), "WeekStart"] = ""
    records = df_out.fillna("").to_dict(orient="records")
    db.execute("DELETE FROM treinos")
    if records:
        db.execute_many(
            """
            INSERT INTO treinos (
                "UserID", "UID", "Data", "Start", "End", "Modalidade",
                "Tipo de Treino", "Volume", "Unidade", "RPE", "Detalhamento", "TempoEstimadoMin",
                "Observações", "Status", "adj", "AdjAppliedAt", "ChangeLog",
                "LastEditedAt", "WeekStart", "TSS", "IF", "ATL", "CTL", "TSB", "StravaID", "StravaURL", "DuracaoRealMin", "DistanciaReal"
            ) VALUES (
                :user_id, :uid, :data, :start, :end, :modalidade,
                :tipo_treino, :volume, :unidade, :rpe, :detalhamento, :tempo_estimado_min,
                :observacoes, :status, :adj, :adj_applied_at, :changelog,
                :last_edited_at, :week_start, :tss, :intensity, :atl, :ctl, :tsb, :strava_id, :strava_url, :duracao_real, :distancia_real
            )
            """,
            [
                {
                    "user_id": rec.get("UserID", ""),
                    "uid": rec.get("UID", ""),
                    "data": rec.get("Data") or None,
                    "start": rec.get("Start") or None,
                    "end": rec.get("End") or None,
                    "modalidade": rec.get("Modalidade", ""),
                    "tipo_treino": rec.get("Tipo de Treino", ""),
                    "volume": float(rec.get("Volume", 0.0) or 0.0),
                    "unidade": rec.get("Unidade", ""),
                    "rpe": float(rec.get("RPE", 0.0) or 0.0),
                    "detalhamento": rec.get("Detalhamento", ""),
                    "tempo_estimado_min": float(rec.get("TempoEstimadoMin", 0.0) or 0.0),
                    "observacoes": rec.get("Observações", ""),
                    "status": rec.get("Status", ""),
                    "adj": float(rec.get("adj", 0.0) or 0.0),
                    "adj_applied_at": rec.get("AdjAppliedAt", ""),
                    "changelog": rec.get("ChangeLog", ""),
                    "last_edited_at": rec.get("LastEditedAt", ""),
                    "week_start": rec.get("WeekStart") or None,
                    "tss": float(rec.get("TSS", 0.0) or 0.0),
                    "intensity": float(rec.get("IF", 0.0) or 0.0),
                    "atl": float(rec.get("ATL", 0.0) or 0.0),
                    "ctl": float(rec.get("CTL", 0.0) or 0.0),
                    "tsb": float(rec.get("TSB", 0.0) or 0.0),
                    "strava_id": rec.get("StravaID", ""),
                    "strava_url": rec.get("StravaURL", ""),
                    "duracao_real": float(rec.get("DuracaoRealMin", 0.0) or 0.0),
                    "distancia_real": float(rec.get("DistanciaReal", 0.0) or 0.0),
                }
                for rec in records
            ],
        )
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
    init_database()

@st.cache_data(show_spinner=False)
def load_all_availability() -> pd.DataFrame:
    init_database()
    df = db.fetch_dataframe(
        "SELECT \"UserID\", \"WeekStart\"::text AS \"WeekStart\", \"Start\"::text AS \"Start\", \"End\"::text AS \"End\" FROM availability"
    )
    if df.empty:
        df = pd.DataFrame(columns=["UserID", "WeekStart", "Start", "End"])
    if not df.empty:
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date
        df["Start"] = pd.to_datetime(df["Start"], errors="coerce")
        df["End"] = pd.to_datetime(df["End"], errors="coerce")
    return df

def save_all_availability(df: pd.DataFrame):
    init_database()
    df_out = df.copy()
    if not df_out.empty:
        week_series = pd.to_datetime(df_out["WeekStart"], errors="coerce")
        start_series = pd.to_datetime(df_out["Start"], errors="coerce")
        end_series = pd.to_datetime(df_out["End"], errors="coerce")
        df_out["WeekStart"] = week_series.dt.date.astype(str)
        df_out["Start"] = start_series.astype(str)
        df_out["End"] = end_series.astype(str)
        df_out.loc[week_series.isna(), "WeekStart"] = ""
        df_out.loc[start_series.isna(), "Start"] = ""
        df_out.loc[end_series.isna(), "End"] = ""
    records = df_out.fillna("").to_dict(orient="records")
    db.execute("DELETE FROM availability")
    if records:
        db.execute_many(
            """
            INSERT INTO availability ("UserID", "WeekStart", "Start", "End")
            VALUES (:user_id, :week_start, :start, :end)
            """,
            [
                {
                    "user_id": rec.get("UserID", ""),
                    "week_start": rec.get("WeekStart") or None,
                    "start": rec.get("Start") or None,
                    "end": rec.get("End") or None,
                }
                for rec in records
            ],
        )
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
            "Start": _to_wall_naive(s["start"]),
            "End": _to_wall_naive(s["end"]),
        })
    if rows:
        all_df = pd.concat([all_df, pd.DataFrame(rows)], ignore_index=True)

    save_all_availability(all_df)


def clear_all_availability_for_user(user_id: str):
    """Remove qualquer disponibilidade salva para todas as semanas do usuário."""

    all_df = load_all_availability()
    if all_df.empty:
        return

    filtered = all_df[all_df["UserID"] != user_id]
    if len(filtered) == len(all_df):
        return

    save_all_availability(filtered)

# ----------------------------------------------------------------------------
# Padrões de horário por usuário
# ----------------------------------------------------------------------------

def init_timepattern_if_needed():
    init_database()


@st.cache_data(show_spinner=False)
def load_all_timepatterns() -> pd.DataFrame:
    init_database()
    df = db.fetch_dataframe(
        "SELECT \"UserID\", \"PatternJSON\" FROM time_patterns"
    )
    if df.empty:
        df = pd.DataFrame(columns=["UserID", "PatternJSON"])
    return df.fillna("")


def save_timepattern_for_user(user_id: str, pattern: dict):
    init_database()
    serialized = json.dumps(pattern, ensure_ascii=False)
    db.execute(
        "DELETE FROM time_patterns WHERE \"UserID\" = :user_id",
        {"user_id": user_id},
    )
    db.execute(
        "INSERT INTO time_patterns (\"UserID\", \"PatternJSON\") VALUES (:user_id, :pattern)",
        {"user_id": user_id, "pattern": serialized},
    )
    load_all_timepatterns.clear()


def load_timepattern_for_user(user_id: str):
    init_database()
    row = db.fetch_one(
        "SELECT \"PatternJSON\" FROM time_patterns WHERE \"UserID\" = :user_id",
        {"user_id": user_id},
    )
    if not row:
        return None
    try:
        value = row.get("PatternJSON") if row else None
        return json.loads(value) if value else None
    except Exception:
        return None

# ----------------------------------------------------------------------------
# Preferências do atleta
# ----------------------------------------------------------------------------


def init_preferences_if_needed():
    init_database()


@st.cache_data(show_spinner=False)
def load_all_preferences() -> pd.DataFrame:
    init_database()
    df = db.fetch_dataframe(
        "SELECT \"UserID\", \"PreferencesJSON\" FROM preferences"
    )
    if df.empty:
        df = pd.DataFrame(columns=["UserID", "PreferencesJSON"])
    return df.fillna("")


def load_preferences_for_user(user_id: str) -> dict:
    df = load_all_preferences()
    row = df[df["UserID"] == user_id]
    default = {
        "time_preferences": {},
        "daily_limit_minutes": None,
        "off_days": [],
    }
    if row.empty:
        return default
    try:
        prefs = json.loads(row.iloc[0]["PreferencesJSON"])
    except Exception:
        return default
    if not isinstance(prefs, dict):
        return default

    merged = prefs.copy()
    for key, default_value in default.items():
        merged.setdefault(key, default_value)
    return merged


def save_preferences_for_user(user_id: str, preferences: dict):
    init_database()
    serialized = json.dumps(preferences, ensure_ascii=False)
    db.execute(
        "DELETE FROM preferences WHERE \"UserID\" = :user_id",
        {"user_id": user_id},
    )
    db.execute(
        "INSERT INTO preferences (\"UserID\", \"PreferencesJSON\") VALUES (:user_id, :prefs)",
        {"user_id": user_id, "prefs": serialized},
    )
    load_all_preferences.clear()


# ----------------------------------------------------------------------------
# Integração Strava
# ----------------------------------------------------------------------------


def _get_query_params() -> dict:
    try:
        return dict(st.query_params)  # type: ignore[attr-defined]
    except Exception:
        try:
            return st.experimental_get_query_params()  # type: ignore[attr-defined]
        except Exception:
            return {}


def _set_query_params(**params):
    try:
        st.experimental_set_query_params(**params)  # type: ignore[attr-defined]
    except Exception:
        return


DEFAULT_STRAVA_REDIRECT_URI = os.getenv("DEFAULT_STRAVA_REDIRECT_URI") or "http://localhost:8501"
DEFAULT_STRAVA_CLIENT_ID = "186420"
DEFAULT_STRAVA_CLIENT_SECRET = "be2b6979209ada4f74cf347b33e17f2e43e41eae"
DEFAULT_STRAVA_ACCESS_TOKEN = "c1baef1b58be5f92951d117add5cd68fbd967659"
DEFAULT_STRAVA_REFRESH_TOKEN = "dfb851ddf3fe70bab71c03ec7c28ede74cb58f67"


def _normalize_redirect_uri(uri: str | None) -> str | None:
    if not uri:
        return None
    normalized = str(uri).strip().rstrip("/")
    return normalized


def seed_default_strava_config_if_missing():
    init_database()
    try:
        row = db.fetch_one("SELECT value FROM meta WHERE key = 'strava_config'")
    except Exception:
        return

    if row and row.get("value"):
        return

    redirect_uri = _normalize_redirect_uri(
        os.getenv("STRAVA_REDIRECT_URI") or DEFAULT_STRAVA_REDIRECT_URI
    )
    client_id = os.getenv("STRAVA_CLIENT_ID") or DEFAULT_STRAVA_CLIENT_ID
    client_secret = os.getenv("STRAVA_CLIENT_SECRET") or DEFAULT_STRAVA_CLIENT_SECRET

    if not client_id or not client_secret or not redirect_uri:
        return

    payload = {
        "client_id": str(client_id),
        "client_secret": str(client_secret),
        "redirect_uri": str(redirect_uri),
        "seed_access_token": DEFAULT_STRAVA_ACCESS_TOKEN,
        "seed_refresh_token": DEFAULT_STRAVA_REFRESH_TOKEN,
    }

    try:
        db.execute(
            """
            INSERT INTO meta (key, value)
            VALUES ('strava_config', :value)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            {"value": json.dumps(payload)},
        )
    except Exception:
        return


def get_strava_config() -> dict | None:
    init_database()
    seed_default_strava_config_if_missing()
    client_id = None
    client_secret = None
    redirect_uri = None

    try:
        row = db.fetch_one(
            "SELECT value FROM meta WHERE key = 'strava_config'"
        )
        if row and row.get("value"):
            payload = json.loads(row["value"])
            client_id = payload.get("client_id")
            client_secret = payload.get("client_secret")
            redirect_uri = payload.get("redirect_uri")
    except Exception:
        pass

    try:
        if "strava" in st.secrets:  # type: ignore[attr-defined]
            secrets_section = st.secrets["strava"]
            client_id = secrets_section.get("client_id") or client_id
            client_secret = secrets_section.get("client_secret") or client_secret
            redirect_uri = secrets_section.get("redirect_uri") or redirect_uri
    except Exception:
        pass

    client_id = os.getenv("STRAVA_CLIENT_ID") or client_id
    client_secret = os.getenv("STRAVA_CLIENT_SECRET") or client_secret
    redirect_uri = os.getenv("STRAVA_REDIRECT_URI") or redirect_uri

    redirect_uri = _normalize_redirect_uri(redirect_uri)
    if not client_id or not client_secret or not redirect_uri:
        return None

    return {
        "client_id": str(client_id),
        "client_secret": str(client_secret),
        "redirect_uri": str(redirect_uri),
    }


def build_strava_auth_url(user_id: str) -> str | None:
    cfg = get_strava_config()
    if not cfg:
        return None

    params = {
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": cfg["redirect_uri"],
        "scope": "read,activity:read_all",
        "approval_prompt": "auto",
        "state": user_id,
    }
    return "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode(params)


def _load_strava_data(user_id: str) -> dict:
    prefs = load_preferences_for_user(user_id)
    return prefs.get("strava", {}) if isinstance(prefs, dict) else {}


def _save_strava_data(user_id: str, strava_data: dict):
    prefs = load_preferences_for_user(user_id)
    prefs["strava"] = strava_data
    save_preferences_for_user(user_id, prefs)
    st.session_state["user_preferences_cache"] = prefs
    st.session_state["user_preferences_cache_user"] = user_id


def _render_strava_popup_button(auth_url: str):
    button_html = f"""
    <div style='display: flex; flex-direction: column; gap: 6px; align-items: flex-start;'>
        <button type='button'
            onclick="const win = window.open('{auth_url}', 'strava_auth', 'width=900,height=900'); if(win){{win.focus();}} else {{window.location.href='{auth_url}';}}"
            style='background-color: #fc4c02; color: white; border: none; padding: 10px 16px; border-radius: 6px; font-weight: 700; cursor: pointer;'>
            Conectar ao Strava
        </button>
        <span style='font-size: 13px; color: #4a4a4a;'>Uma janela do Strava será aberta para você autorizar o acesso. Se não abrir, <a href='{auth_url}' target='_blank' rel='noopener noreferrer'>clique aqui</a>.</span>
    </div>
    """
    st.components.v1.html(button_html, height=90)


def _decode_polyline_fallback(polyline_str: str) -> list[tuple[float, float]]:
    if not polyline_str:
        return []

    index = 0
    lat = 0
    lng = 0
    coordinates: list[tuple[float, float]] = []

    while index < len(polyline_str):
        result = 0
        shift = 0

        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break

        delta_lat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += delta_lat

        result = 0
        shift = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break

        delta_lng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += delta_lng

        coordinates.append((lat / 1e5, lng / 1e5))

    return coordinates


def _decode_polyline(polyline_str: str | None) -> list[tuple[float, float]]:
    if not polyline_str:
        return []
    try:
        import polyline as polyline_lib

        decoded = polyline_lib.decode(polyline_str)
    except Exception:
        decoded = _decode_polyline_fallback(polyline_str)

    coords: list[tuple[float, float]] = []
    for pair in decoded:
        try:
            lat, lon = pair
            coords.append((float(lat), float(lon)))
        except Exception:
            continue
    return coords


def _extract_activity_coords(act: pd.Series) -> list[tuple[float, float]]:
    polyline_str = act.get("Polyline") or act.get("summary_polyline")
    coords = _decode_polyline(polyline_str)
    if coords:
        return coords

    start_latlng = act.get("StartLatLng")
    end_latlng = act.get("EndLatLng")
    try:
        if start_latlng and end_latlng:
            return [
                (float(start_latlng[0]), float(start_latlng[1])),
                (float(end_latlng[0]), float(end_latlng[1])),
            ]
    except Exception:
        pass
    return []


def render_activity_map(act: pd.Series, container, *, map_key: str | None = None):
    with container:
        st.markdown("### 🗺️ Percurso da atividade")
        coords = _extract_activity_coords(act)
        if not coords:
            st.info("Esta atividade não possui dados de rota para exibição no mapa.")
            return

        center = coords[len(coords) // 2]
        fmap = folium.Map(location=[center[0], center[1]], tiles="OpenStreetMap", zoom_start=13)
        folium.PolyLine(coords, color="#fc4c02", weight=5, opacity=0.8).add_to(fmap)

        try:
            folium.Marker(coords[0], popup="Início", icon=folium.Icon(color="green")).add_to(fmap)
            folium.Marker(coords[-1], popup="Fim", icon=folium.Icon(color="red")).add_to(fmap)
        except Exception:
            pass

        try:
            lats = [c[0] for c in coords]
            lons = [c[1] for c in coords]
            fmap.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
        except Exception:
            pass

        map_component_key = map_key or f"activity-map-{act.get('ID') or act.get('UID') or 'unknown'}"
        st_folium(fmap, height=420, width=None, returned_objects=[], key=map_component_key)


def _apply_activity_to_training(user_id: str, planned_uid: str, activity_row: pd.Series):
    df = st.session_state.get("df", pd.DataFrame()).copy()
    idx = df[df["UID"] == planned_uid].index
    if idx.empty:
        return

    idx = idx[0]
    rpe_val = float(df.at[idx, "RPE"] or 0.0)
    duration_seconds = float(activity_row.get("MovingSeconds", 0.0) or 0.0)
    np_val = float(activity_row.get("NP", 0.0) or 0.0)
    ftp = None
    tss_val, intensity = _compute_tss(duration_seconds, np_val, ftp, rpe_val)

    distance_km = float(activity_row.get("Distância (km)", 0.0) or 0.0)
    duration_min = float(activity_row.get("Duração (min)", 0.0) or 0.0)
    strava_id = activity_row.get("ID")
    strava_url = f"https://www.strava.com/activities/{strava_id}" if strava_id else ""

    df.at[idx, "Status"] = "Realizado"
    df.at[idx, "TSS"] = round(tss_val, 2)
    df.at[idx, "IF"] = round(intensity or 0.0, 3)
    df.at[idx, "StravaID"] = str(strava_id or "")
    df.at[idx, "StravaURL"] = strava_url
    df.at[idx, "DuracaoRealMin"] = duration_min
    df.at[idx, "DistanciaReal"] = distance_km
    df.at[idx, "TempoEstimadoMin"] = duration_min
    if df.at[idx, "Unidade"] in ["km", "m"]:
        if df.at[idx, "Unidade"] == "m":
            df.at[idx, "Volume"] = distance_km * 1000.0
        else:
            df.at[idx, "Volume"] = distance_km

    df = _update_training_loads(user_id, df)
    st.session_state["df"] = df
    save_user_df(user_id, df)
    canonical_week_df.clear()
    st.success("Treino associado e métricas atualizadas!")


def get_saved_strava_token(user_id: str) -> dict | None:
    data = _load_strava_data(user_id)
    token = data.get("token") if isinstance(data, dict) else None
    return token if isinstance(token, dict) else None


def save_strava_token(user_id: str, token_data: dict, athlete: dict | None = None):
    data = _load_strava_data(user_id)
    if not isinstance(data, dict):
        data = {}
    data["token"] = token_data
    if athlete is not None:
        data["athlete"] = athlete
    _save_strava_data(user_id, data)


def exchange_strava_code_for_token(user_id: str, code: str) -> tuple[dict | None, str | None]:
    cfg = get_strava_config()
    if not cfg:
        return None, "Configuração do Strava ausente."
    try:
        response = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        token_data = {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
            "expires_at": payload.get("expires_at"),
            "token_type": payload.get("token_type"),
        }
        athlete_data = payload.get("athlete")
        save_strava_token(user_id, token_data, athlete=athlete_data)
        return token_data, None
    except Exception as exc:  # noqa: BLE001
        return None, f"Falha ao trocar o código: {exc}"


def refresh_strava_token(user_id: str, refresh_token: str) -> tuple[dict | None, str | None]:
    cfg = get_strava_config()
    if not cfg:
        return None, "Configuração do Strava ausente."
    try:
        response = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        token_data = {
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token", refresh_token),
            "expires_at": payload.get("expires_at"),
            "token_type": payload.get("token_type"),
        }
        athlete_data = payload.get("athlete")
        save_strava_token(user_id, token_data, athlete=athlete_data)
        return token_data, None
    except Exception as exc:  # noqa: BLE001
        return None, f"Falha ao renovar o token: {exc}"


def ensure_valid_strava_token(user_id: str) -> dict | None:
    token = get_saved_strava_token(user_id)
    if not token:
        return None

    expires_at = token.get("expires_at")
    now_ts = datetime.now(timezone.utc).timestamp()
    if expires_at and isinstance(expires_at, (int, float)) and now_ts >= float(expires_at) - 60:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return None
        refreshed, _ = refresh_strava_token(user_id, refresh_token)
        return refreshed
    return token


class StravaClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://www.strava.com/api/v3"

    def _get(self, path: str, params: dict | None = None):
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(
            f"{self.base_url}{path}", headers=headers, params=params or {}, timeout=15
        )
        response.raise_for_status()
        return response.json()

    def get_athlete(self):
        return self._get("/athlete")

    def get_athlete_activities(
        self,
        after: datetime | None = None,
        before: datetime | None = None,
        per_page: int = 50,
        page: int = 1,
    ):
        params: dict[str, int] = {"per_page": per_page, "page": page}
        if after:
            params["after"] = int(after.timestamp())
        if before:
            params["before"] = int(before.timestamp())
        return self._get("/athlete/activities", params=params)


def _normalize_strava_activities(activities: list[dict]) -> pd.DataFrame:
    rows = []
    for act in activities:
        start_local = pd.to_datetime(act.get("start_date_local"), errors="coerce")
        moving_seconds = act.get("moving_time") or 0
        distance_m = act.get("distance") or 0
        np_power = act.get("weighted_average_watts")
        elev_gain = act.get("total_elevation_gain") or 0.0
        map_data = act.get("map") or {}
        polyline_raw = (
            map_data.get("summary_polyline")
            or act.get("summary_polyline")
            or act.get("map.summary_polyline")
        )
        pace_min_per_km = 0.0
        if distance_m and moving_seconds:
            pace_min_per_km = round((float(moving_seconds) / 60.0) / (float(distance_m) / 1000), 2)
        rows.append(
            {
                "Nome": act.get("name"),
                "Tipo": act.get("type"),
                "Data": start_local.date() if isinstance(start_local, pd.Timestamp) else None,
                "Hora": start_local.strftime("%H:%M") if isinstance(start_local, pd.Timestamp) else "--:--",
                "Distância (km)": round(float(distance_m) / 1000, 2) if distance_m else 0.0,
                "Duração (min)": round(float(moving_seconds) / 60, 1) if moving_seconds else 0.0,
                "Velocidade média (km/h)": round(
                    (float(distance_m) / 1000) / (float(moving_seconds) / 3600), 2
                )
                if moving_seconds
                else 0.0,
                "Ritmo médio (min/km)": pace_min_per_km,
                "Ganho de elevação (m)": round(float(elev_gain), 1),
                "ID": act.get("id"),
                "MovingSeconds": moving_seconds,
                "DistanceMeters": distance_m,
                "TypeNormalized": str(act.get("type", "")),
                "NP": np_power or 0.0,
                "Polyline": polyline_raw,
                "StartLatLng": map_data.get("start_latlng") or act.get("start_latlng"),
                "EndLatLng": map_data.get("end_latlng") or act.get("end_latlng"),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df.sort_values(by=["Data", "Hora"], ascending=[False, False], inplace=True)
    return df


def _format_minutes_as_label(minutes_value: float | int) -> str:
    try:
        total_seconds = int(float(minutes_value) * 60)
    except Exception:
        return "0min"
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    if hours <= 0:
        return f"{minutes}min"
    return f"{hours:02d}h{minutes:02d}min"


def _plan_modality_from_strava(strava_type: str | None) -> str | None:
    if not strava_type:
        return None
    mapped = STRAVA_TO_PLAN_MODALITY.get(str(strava_type).strip().lower())
    if not mapped:
        return None
    if mapped == "corrida":
        return "Corrida"
    if mapped == "ciclismo":
        return "Ciclismo"
    if mapped == "natação":
        return "Natação"
    return None


def _load_training_loads(user_id: str) -> dict:
    row = db.fetch_one(
        "SELECT value FROM meta WHERE key = :key", {"key": f"load_metrics_{user_id}"}
    )
    if row and row.get("value"):
        try:
            return json.loads(row["value"])
        except Exception:
            return {}
    return {}


def _save_training_loads(user_id: str, payload: dict):
    db.execute(
        """
        INSERT INTO meta (key, value)
        VALUES (:key, :value)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        {"key": f"load_metrics_{user_id}", "value": json.dumps(payload)},
    )


def _compute_tss(duration_seconds: float, np_value: float | None, ftp: float | None, rpe: float | None) -> tuple[float, float]:
    ftp_val = ftp or 0.0
    np_val = np_value or 0.0
    if ftp_val > 0 and np_val > 0:
        intensity = np_val / ftp_val
        tss_val = (duration_seconds * (intensity**2) / 3600.0) * 100.0
        return tss_val, intensity
    duration_minutes = duration_seconds / 60.0
    rpe_val = rpe or 0.0
    if rpe_val <= 0:
        rpe_val = 5.0
    tss_val = (duration_minutes / 60.0) * rpe_val * 10.0
    return tss_val, 0.0


def _update_training_loads(user_id: str, user_df: pd.DataFrame) -> pd.DataFrame:
    df = user_df.copy()
    df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date
    df["TSS"] = pd.to_numeric(df.get("TSS", 0.0), errors="coerce").fillna(0.0)
    tss_per_day = (
        df.dropna(subset=["Data"])
        .groupby("Data")
        ["TSS"]
        .sum()
        .to_dict()
    )
    if not tss_per_day:
        _save_training_loads(user_id, {})
        return df

    start_date = min(tss_per_day.keys())
    end_date = max(tss_per_day.keys())
    atl = 0.0
    ctl = 0.0
    metrics: dict[str, dict[str, float]] = {}
    cursor = start_date
    while cursor <= end_date:
        tss_today = float(tss_per_day.get(cursor, 0.0) or 0.0)
        atl = atl + (tss_today - atl) / 7.0
        ctl = ctl + (tss_today - ctl) / 42.0
        tsb = ctl - atl
        metrics[cursor.isoformat()] = {"ATL": atl, "CTL": ctl, "TSB": tsb, "TSS": tss_today}
        cursor += timedelta(days=1)

    for idx, row in df.iterrows():
        d = row.get("Data")
        if isinstance(d, pd.Timestamp):
            d = d.date()
        if isinstance(d, date):
            key = d.isoformat()
            if key in metrics:
                df.at[idx, "ATL"] = metrics[key]["ATL"]
                df.at[idx, "CTL"] = metrics[key]["CTL"]
                df.at[idx, "TSB"] = metrics[key]["TSB"]

    _save_training_loads(user_id, metrics)
    return df


def render_strava_tab(user_id: str):
    st.header("🚴 Integração com Strava")

    params = _get_query_params()

    def _first(value):
        if isinstance(value, list):
            return value[0]
        return value

    code_param = _first(params.get("code")) if params else None
    state_param = _first(params.get("state")) if params else None
    error_param = _first(params.get("error")) if params else None

    if code_param:
        if state_param and state_param != user_id:
            st.error("O retorno do Strava não corresponde ao usuário atual.")
        else:
            with st.spinner("Finalizando conexão com o Strava..."):
                _, err = exchange_strava_code_for_token(user_id, str(code_param))
            _set_query_params()
            if err:
                st.error(err)
            else:
                st.success("Conta Strava conectada com sucesso!")
                safe_rerun()
    elif error_param:
        st.error(f"Erro retornado pelo Strava: {error_param}")
        _set_query_params()

    token_data = ensure_valid_strava_token(user_id)
    strava_data = _load_strava_data(user_id)
    athlete_data = strava_data.get("athlete") if isinstance(strava_data, dict) else None

    if not token_data or not token_data.get("access_token"):
        auth_url = build_strava_auth_url(user_id)
        st.info("Conecte sua conta do Strava para importar suas atividades recentes.")
        if auth_url:
            _render_strava_popup_button(auth_url)
        else:
            st.error(
                "Não foi possível iniciar a conexão com o Strava agora. Tente novamente em instantes ou contate o suporte."
            )
        return

    client = StravaClient(token_data["access_token"])

    if not athlete_data:
        try:
            athlete_data = client.get_athlete()
            strava_data = strava_data if isinstance(strava_data, dict) else {}
            strava_data["athlete"] = athlete_data
            _save_strava_data(user_id, strava_data)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Não foi possível carregar o perfil do atleta: {exc}")

    col_info, col_token = st.columns([3, 1])
    with col_info:
        if athlete_data:
            st.success(
                f"Conectado como **{athlete_data.get('firstname', '')} {athlete_data.get('lastname', '')}**"
            )
        else:
            st.success("Conta Strava conectada.")
    with col_token:
        exp_ts = token_data.get("expires_at")
        if exp_ts:
            exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
            st.caption(f"Token expira em {exp_dt.astimezone().strftime('%d/%m %H:%M')} (local)")

    today_dt = today()
    default_start = today_dt - timedelta(days=30)
    date_range = st.date_input(
        "Período", (default_start, today_dt), key="strava_date_range", help="Defina o intervalo desejado"
    )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date = default_start
        end_date = today_dt

    refresh_clicked = st.button("Atualizar atividades")

    after_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc) if start_date else None
    before_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc) if end_date else None

    activities_df: pd.DataFrame | None = None
    try:
        activities = client.get_athlete_activities(after=after_dt, before=before_dt)
        activities_df = _normalize_strava_activities(activities)
        strava_data = strava_data if isinstance(strava_data, dict) else {}
        strava_data["activities"] = activities
        _save_strava_data(user_id, strava_data)
        if refresh_clicked:
            st.success("Atividades atualizadas a partir do Strava.")
    except Exception as exc:  # noqa: BLE001
        st.error(f"Erro ao buscar atividades no Strava: {exc}")

    if activities_df is None or activities_df.empty:
        st.info("Nenhuma atividade encontrada para o período selecionado.")
        return

    activity_types = sorted([t for t in activities_df["Tipo"].dropna().unique()])
    selected_types = st.multiselect(
        "Filtrar por tipo de atividade",
        options=activity_types,
        default=activity_types,
        key="strava_type_filter",
    )

    filtered_df = activities_df[
        activities_df["Tipo"].isin(selected_types) if selected_types else [True] * len(activities_df)
    ]

    tab_acts, tab_match = st.tabs([
        "Atividades",
        "Match Treinos Planejados x Realizados",
    ])

    with tab_acts:
        st.subheader("Visão geral das atividades importadas")
        cols_to_hide = [
            "ID",
            "MovingSeconds",
            "DistanceMeters",
            "TypeNormalized",
            "NP",
            "Polyline",
            "StartLatLng",
            "EndLatLng",
        ]
        cols_to_drop = [c for c in cols_to_hide if c in filtered_df.columns]
        show_only_routes = st.toggle(
            "Mostrar somente atividades com percurso mapeado",
            value=False,
            key="strava_route_toggle",
            help="Filtra atividades que possuem trajeto disponível para o mapa",
        )

        activities_view = filtered_df.copy()

        if show_only_routes:
            activities_view = activities_view[activities_view["Polyline"].astype(str).str.strip() != ""].copy()

        total_distance = activities_view.get("Distância (km)", pd.Series(dtype=float)).sum()
        total_minutes = activities_view.get("Duração (min)", pd.Series(dtype=float)).sum()
        total_elev = activities_view.get("Ganho de elevação (m)", pd.Series(dtype=float)).sum()
        avg_speed = (
            activities_view.get("Velocidade média (km/h)", pd.Series(dtype=float))
            .replace([np.inf, -np.inf], 0)
            .fillna(0)
            .mean()
            if not activities_view.empty
            else 0.0
        )

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Atividades filtradas", f"{len(activities_view)}")
        m2.metric("Distância total", f"{total_distance:.1f} km")
        m3.metric("Tempo em movimento", _format_minutes_as_label(total_minutes))
        m4.metric("Ganho de elevação", f"{total_elev:.0f} m")
        m5.metric("Velocidade média", f"{avg_speed:.1f} km/h")

        if activities_view.empty:
            st.info("Nenhuma atividade após aplicar filtros e opções.")
        else:
            display_df = activities_view.drop(columns=cols_to_drop)
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Distância (km)": st.column_config.NumberColumn(format="%.2f km"),
                    "Duração (min)": st.column_config.NumberColumn(format="%.1f min"),
                    "Velocidade média (km/h)": st.column_config.NumberColumn(format="%.1f km/h"),
                    "Ritmo médio (min/km)": st.column_config.NumberColumn(format="%.2f min/km"),
                    "Ganho de elevação (m)": st.column_config.NumberColumn(format="%.0f m"),
                },
            )

            detail_container = st.container()
            map_container = st.container()
            map_options = {
                f"{row['Data']} - {row['Nome']} ({row['Tipo']})": row["ID"]
                for _, row in activities_view.iterrows()
            }

            if map_options:
                selected_map_label = st.selectbox(
                    "Selecionar atividade para visualizar o mapa",
                    options=list(map_options.keys()),
                    index=0,
                    key="strava_map_select_acts",
                )
                selected_map_id = map_options.get(selected_map_label)
                selected_row = activities_view[activities_view["ID"] == selected_map_id]
                if not selected_row.empty:
                    selected_act = selected_row.iloc[0]
                    with detail_container:
                        st.markdown("#### Destaque da atividade")
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Distância", f"{selected_act.get('Distância (km)', 0):.2f} km")
                        c2.metric("Duração", _format_minutes_as_label(selected_act.get("Duração (min)")))
                        c3.metric("Velocidade média", f"{selected_act.get('Velocidade média (km/h)', 0):.1f} km/h")
                        pace_val = selected_act.get("Ritmo médio (min/km)") or 0.0
                        pace_label = f"{pace_val:.2f} min/km" if pace_val else "--"
                        c4.metric("Ritmo médio", pace_label)
                        st.caption(
                            f"{selected_act.get('Data')} às {selected_act.get('Hora')} — {selected_act.get('Nome', '')}"
                        )

                    render_activity_map(
                        selected_act,
                        map_container,
                        map_key=f"strava-activities-map-{selected_map_id}",
                    )
            else:
                with map_container:
                    st.info("Nenhuma atividade disponível para exibir no mapa.")

    with tab_match:
        st.subheader("Match Treinos Planejados x Realizados")

        if "strava_match_map_id" not in st.session_state:
            st.session_state["strava_match_map_id"] = None

        planned_df = st.session_state.get("df", pd.DataFrame()).copy()
        if not planned_df.empty:
            planned_df["Data"] = pd.to_datetime(planned_df["Data"], errors="coerce").dt.date
        planned_candidates = planned_df[
            planned_df["Status"].astype(str).str.lower() != "realizado"
        ].copy()
        planned_candidates = planned_candidates[planned_candidates["Modalidade"] != "Descanso"]

        st.caption("Sugestões automáticas são feitas apenas quando data e modalidade são idênticas.")

        suggestions: list[tuple[pd.Series, pd.DataFrame]] = []
        for _, act in filtered_df.iterrows():
            plan_mod = _plan_modality_from_strava(act.get("Tipo"))
            if not plan_mod:
                continue
            same_day = planned_candidates[
                (planned_candidates["Data"] == act.get("Data"))
                & (planned_candidates["Modalidade"].str.lower() == plan_mod.lower())
                & (planned_candidates.get("StravaID", "").astype(str).str.strip() == "")
            ]
            if not same_day.empty:
                suggestions.append((act, same_day))

        if not suggestions:
            st.info("Nenhum match automático disponível para as atividades e filtros selecionados.")
        else:
            for act, same_day in suggestions:
                header = f"Sugestão: {act.get('Nome', '')} ({act.get('Data')})"
                with st.expander(header, expanded=False):
                    st.write(
                        "Encontramos treinos planejados com mesma data e modalidade: selecione ou confirme a associação."
                    )
                    if st.button("Ver no mapa", key=f"map_suggestion_{act.get('ID')}"):
                        st.session_state["strava_match_map_id"] = act.get("ID")
                    if len(same_day) == 1:
                        target = same_day.iloc[0]
                        st.markdown(
                            f"**Planejado:** {target.get('Tipo de Treino', '')} ({target.get('Modalidade')}) em {target.get('Data')}"
                        )
                        col_c, col_r = st.columns(2)
                        with col_c:
                            if st.button(
                                "Confirmar match",
                                key=f"confirm_auto_{act.get('ID')}_{target.get('UID')}",
                            ):
                                _apply_activity_to_training(user_id, target.get("UID"), act)
                                safe_rerun()
                        with col_r:
                            st.button("Recusar", key=f"reject_auto_{act.get('ID')}_{target.get('UID')}")
                    else:
                        plan_options = {
                            f"{row['Data']} - {row['Tipo de Treino']} ({row['Modalidade']})": row["UID"]
                            for _, row in same_day.iterrows()
                        }
                        chosen_plan = st.selectbox(
                            "Escolha o treino planejado para associar",
                            options=list(plan_options.keys()),
                            key=f"auto_plan_select_{act.get('ID')}",
                        )
                        col_c, col_r = st.columns(2)
                        with col_c:
                            if st.button(
                                "Confirmar match",
                                key=f"confirm_auto_{act.get('ID')}_multi",
                            ):
                                target_uid = plan_options.get(chosen_plan)
                                if target_uid:
                                    _apply_activity_to_training(user_id, target_uid, act)
                                    safe_rerun()
                        with col_r:
                            st.button("Recusar", key=f"reject_auto_{act.get('ID')}_multi")

        st.markdown("---")
        st.subheader("Match manual")

        today_local = today()
        planned_dates = sorted(
            {
                d
                for d in planned_candidates["Data"].tolist()
                if isinstance(d, (date, datetime, pd.Timestamp)) and not pd.isna(d)
            }
        )
        strava_dates = sorted(
            {
                d
                for d in filtered_df["Data"].tolist()
                if isinstance(d, (date, datetime, pd.Timestamp)) and not pd.isna(d)
            }
        )

        def _default_date(opts: list[date]):
            if not opts:
                return today_local
            normalized = [dt.date() if isinstance(dt, datetime) else dt for dt in opts]
            if today_local in normalized:
                return today_local
            return sorted(normalized, key=lambda d: abs(d - today_local))[0]

        col_plan_date, col_act_date = st.columns(2)
        planned_date_choice = col_plan_date.date_input(
            "Dia do treino planejado", value=_default_date(planned_dates), key="manual_plan_date"
        )
        act_date_choice = col_act_date.date_input(
            "Dia da atividade Strava", value=_default_date(strava_dates), key="manual_act_date"
        )

        planned_filtered = planned_candidates[planned_candidates["Data"] == planned_date_choice]
        strava_filtered = filtered_df[filtered_df["Data"] == act_date_choice]

        planned_options = {
            f"{row['Data']} - {row['Modalidade']} - {row['Tipo de Treino']}": row["UID"]
            for _, row in planned_filtered.iterrows()
        }
        strava_options = {
            f"{row['Data']} - {row['Tipo']} - {row['Nome']}": row["ID"]
            for _, row in strava_filtered.iterrows()
        }

        if not planned_options or not strava_options:
            st.info(
                "Nenhum treino planejado elegível ou nenhuma atividade do Strava disponível para as datas selecionadas."
            )
        else:
            col_p, col_s = st.columns(2)
            with col_p:
                selected_planned = st.selectbox(
                    "Treino planejado",
                    options=list(planned_options.keys()),
                    key="manual_planned_select",
                )
            with col_s:
                selected_strava = st.selectbox(
                    "Treino Strava",
                    options=list(strava_options.keys()),
                    key="manual_strava_select",
                )

            if selected_strava:
                st.session_state["strava_match_map_id"] = strava_options.get(selected_strava)

            if st.button("Associar manualmente"):
                planned_uid = planned_options.get(selected_planned)
                strava_id = strava_options.get(selected_strava)
                act_row = strava_filtered[strava_filtered["ID"] == strava_id]
                if not act_row.empty and planned_uid:
                    _apply_activity_to_training(user_id, planned_uid, act_row.iloc[0])
                    safe_rerun()
                else:
                    st.error("Seleção inválida para associação manual.")

        map_container = st.container()
        selected_map_id = st.session_state.get("strava_match_map_id")
        if selected_map_id:
            selected_row = filtered_df[filtered_df["ID"] == selected_map_id]
            if not selected_row.empty:
                render_activity_map(
                    selected_row.iloc[0],
                    map_container,
                    map_key=f"strava-match-map-{selected_map_id}",
                )
            else:
                with map_container:
                    st.info("Selecione uma atividade com percurso para exibição no mapa.")
        else:
            with map_container:
                st.info("Selecione uma atividade para visualizar o percurso no mapa.")

# ----------------------------------------------------------------------------
# Observações diárias
# ----------------------------------------------------------------------------


def init_daily_notes_if_needed():
    init_database()


@st.cache_data(show_spinner=False)
def load_all_daily_notes() -> pd.DataFrame:
    init_database()
    df = db.fetch_dataframe(
        "SELECT \"UserID\", \"Date\", \"Note\", \"UpdatedAt\" FROM daily_notes"
    )
    if df.empty:
        df = pd.DataFrame(columns=["UserID", "Date", "Note", "UpdatedAt"])
    if not df.empty:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.date
    return df


def load_daily_note_for_user(user_id: str, target_date: date) -> str:
    df = load_all_daily_notes()
    if df.empty:
        return ""
    row = df[(df["UserID"] == user_id) & (df["Date"] == target_date)]
    if row.empty:
        return ""
    return row.iloc[0]["Note"]


def save_daily_note_for_user(user_id: str, target_date: date, note: str):
    init_database()
    updated_at = datetime.now().isoformat(timespec="seconds")
    if isinstance(target_date, str):
        date_str = target_date
    elif isinstance(target_date, datetime):
        date_str = target_date.date().isoformat()
    else:
        date_str = target_date.isoformat()
    db.execute(
        "DELETE FROM daily_notes WHERE \"UserID\" = :user_id AND \"Date\" = :date",
        {"user_id": user_id, "date": date_str},
    )
    db.execute(
        "INSERT INTO daily_notes (\"UserID\", \"Date\", \"Note\", \"UpdatedAt\") VALUES (:user_id, :date, :note, :updated_at)",
        {"user_id": user_id, "date": date_str, "note": note, "updated_at": updated_at},
    )
    load_all_daily_notes.clear()


def _ensure_py_datetime(value):
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def extract_time_pattern_from_week(week_df: pd.DataFrame) -> dict:
    """Extrai slots de horários (start/dur) para cada dia da semana."""

    pattern = {i: [] for i in range(7)}
    if week_df.empty:
        return pattern

    def _normalize_tipo(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        value_str = str(value).strip()
        return value_str or None

    for _, r in week_df.iterrows():
        if r.get("Modalidade") == "Descanso":
            continue

        data = r.get("Data")
        if pd.isna(data):
            continue
        if isinstance(data, str):
            try:
                data = datetime.fromisoformat(data).date()
            except Exception:
                continue
        weekday = data.weekday()

        start = r.get("StartDT")
        end = r.get("EndDT")
        if start is None or end is None or pd.isna(start) or pd.isna(end):
            continue

        start = _ensure_py_datetime(start).replace(tzinfo=None)
        end = _ensure_py_datetime(end).replace(tzinfo=None)

        duration_min = int((end - start).total_seconds() / 60)
        if duration_min <= 0:
            duration_min = DEFAULT_TRAINING_DURATION_MIN

        tipo_treino = _normalize_tipo(r.get("Tipo de Treino"))
        pattern[weekday].append(
            {
                "start": start.time().strftime("%H:%M"),
                "dur": duration_min,
                "mod": r.get("Modalidade"),
                "tipo": tipo_treino,
            }
        )

    for wd in pattern:
        pattern[wd] = sorted(pattern[wd], key=lambda slot: slot["start"])

    return pattern


def _tipo_is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    value_str = str(value).strip()
    return value_str == ""


def _maybe_apply_slot_tipo(df: pd.DataFrame, idx: int, slot_tipo):
    if _tipo_is_blank(slot_tipo):
        return
    if "Tipo de Treino" not in df.columns:
        return
    if idx not in df.index:
        return
    current = df.at[idx, "Tipo de Treino"]
    if _tipo_is_blank(current):
        df.at[idx, "Tipo de Treino"] = slot_tipo


def apply_time_pattern_to_week(week_df: pd.DataFrame, pattern: dict) -> pd.DataFrame:
    """Aplica slots de horário por dia em um DataFrame de semana."""

    if not pattern or week_df.empty:
        return week_df

    df = week_df.copy()

    # Garante colunas necessárias para ordenação e aplicação de slots
    if "StartDT" not in df.columns:
        df["StartDT"] = pd.NaT
    if "EndDT" not in df.columns:
        df["EndDT"] = pd.NaT
    if "Start" not in df.columns:
        df["Start"] = pd.NaT
    if "End" not in df.columns:
        df["End"] = pd.NaT
    if "Tipo de Treino" not in df.columns:
        df["Tipo de Treino"] = None
    if "TempoEstimadoMin" not in df.columns:
        df["TempoEstimadoMin"] = 0.0

    if not np.issubdtype(df["Data"].dtype, np.datetime64):
        df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date

    for wd in range(7):
        slots = pattern.get(wd) or pattern.get(str(wd)) or []
        if not slots:
            continue

        day_mask = df["Data"].apply(lambda d: False if pd.isna(d) else d.weekday() == wd)
        if not day_mask.any():
            continue

        day_df = df[day_mask].copy()
        if "StartDT" in day_df.columns:
            day_df = day_df.sort_values("StartDT")
        else:
            day_df = day_df.sort_values("Data")

        # Reordena para respeitar E exigir a combinação modalidade + tipo salva no padrão
        def _norm_tipo(value):
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return None
            value_str = str(value).strip()
            return value_str.lower() if value_str else None

        def _slot_match_index(row_mod: str, row_tipo: str | None, available: list[dict] | list) -> int:
            row_tipo_norm = _norm_tipo(row_tipo)

            # Match estrito: mesma modalidade E mesmo tipo (incluindo ambos vazios/None)
            for idx, slot in enumerate(available):
                slot_tipo_norm = _norm_tipo(slot.get("tipo"))
                if slot.get("mod") == row_mod and slot_tipo_norm == row_tipo_norm:
                    return idx

            # 2) Modalidade com slot sem tipo definido (tanto padrão quanto semana atual sem tipo)
            for idx, slot in enumerate(available):
                slot_tipo_norm = _norm_tipo(slot.get("tipo"))
                if slot.get("mod") == row_mod and slot_tipo_norm is None and row_tipo_norm is None:
                    return idx

            # 3) Fallback leve: modalidade igual quando o padrão não especifica tipo
            for idx, slot in enumerate(available):
                if slot.get("mod") == row_mod:
                    return idx

            # Não encontrou: coloca no fim
            return len(available)

        day_df = day_df.sort_values(
            by=["Data"],
            key=lambda s: s.apply(lambda _: 0),
        )
        day_df = day_df.assign(
            _slot_pref=day_df.apply(
                lambda r: _slot_match_index(r.get("Modalidade"), r.get("Tipo de Treino"), slots), axis=1
            )
        ).sort_values(["_slot_pref", "StartDT", "Tipo de Treino"]).drop(columns=["_slot_pref"])

        slots_available = list(slots)
        for idx, row in day_df.iterrows():
            if row.get("Modalidade") == "Descanso":
                continue

            slot_tipo = None
            slot_tipo_raw = None
            duration_minutes = planned_duration_minutes(row)
            if duration_minutes <= 0:
                duration_minutes = DEFAULT_TRAINING_DURATION_MIN
            df.at[idx, "TempoEstimadoMin"] = duration_minutes
            if not slots_available:
                base_time = time(6, 0)
                duration = duration_minutes
            else:
                # Tenta casar o slot pelo par modalidade/tipo preservando ordem salva
                match_idx = _slot_match_index(
                    row.get("Modalidade"), row.get("Tipo de Treino"), slots_available
                )

                # Sem correspondência estrita de modalidade + tipo
                if match_idx >= len(slots_available):
                    continue

                slot = slots_available.pop(match_idx)
                slot_tipo_raw = slot.get("tipo")
                slot_tipo = _norm_tipo(slot_tipo_raw)
                try:
                    hour, minute = map(int, str(slot.get("start", "06:00")).split(":"))
                except Exception:
                    hour, minute = 6, 0
                base_time = time(hour, minute)
                duration = duration_minutes

            current_date = row["Data"]
            if pd.isna(current_date):
                continue

            start_dt = datetime.combine(current_date, base_time)
            end_dt = start_dt + timedelta(minutes=duration)

            df.at[idx, "Start"] = start_dt.isoformat()
            df.at[idx, "End"] = end_dt.isoformat()
            df.at[idx, "StartDT"] = start_dt
            df.at[idx, "EndDT"] = end_dt

            _maybe_apply_slot_tipo(df, idx, slot_tipo_raw)

    return df


def realign_week_types_with_pattern(
    week_df: pd.DataFrame, pattern: dict, week_start: date
) -> pd.DataFrame:
    """Realinha tipos/modalidades para os dias definidos no padrão.

    Quando o padrão salvo define que uma modalidade/tipo ocorre em um dia
    específico e o calendário atual está trocado (ex.: Corrida Força na
    quarta em vez de segunda), os treinos são reposicionados para o dia
    correto. Preserva volumes/detalhes e apenas ajusta Data/horários,
    deixando a aplicação de horários para `apply_time_pattern_to_week`.
    """

    if week_df.empty or not pattern or week_start is None:
        return week_df

    df = week_df.copy()

    if "Data" not in df.columns:
        return df

    if not np.issubdtype(df["Data"].dtype, np.datetime64):
        df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date

    def _norm_tipo(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        value_str = str(value).strip()
        return value_str.lower() if value_str else None

    df["Tipo de Treino"] = df.get("Tipo de Treino", pd.Series([None] * len(df)))

    candidates = list(df.index)
    used = set()

    for wd, slots in (pattern or {}).items():
        try:
            wd_int = int(wd)
        except Exception:
            continue

        if wd_int < 0 or wd_int > 6:
            continue

        day_slots = slots or []
        if not day_slots:
            continue

        target_date = week_start + timedelta(days=wd_int)

        for slot in day_slots:
            mod = slot.get("mod")
            tipo_norm = _norm_tipo(slot.get("tipo"))

            if not mod or mod == "Descanso":
                continue

            best_idx = None
            best_score = -1

            for idx in candidates:
                if idx in used:
                    continue

                row = df.loc[idx]
                if row.get("Modalidade") != mod:
                    continue

                row_tipo_norm = _norm_tipo(row.get("Tipo de Treino"))
                score = 0

                if row_tipo_norm == tipo_norm:
                    score += 2

                row_date = row.get("Data")
                if isinstance(row_date, date) and row_date.weekday() == wd_int:
                    score += 1

                if score > best_score:
                    best_idx = idx
                    best_score = score

            if best_idx is None:
                continue

            used.add(best_idx)

            df.at[best_idx, "Data"] = target_date
            df.at[best_idx, "Start"] = pd.NaT
            df.at[best_idx, "End"] = pd.NaT
            df.at[best_idx, "StartDT"] = pd.NaT
            df.at[best_idx, "EndDT"] = pd.NaT

            _maybe_apply_slot_tipo(df, best_idx, slot.get("tipo"))

    return df


def apply_time_pattern_to_cycle(cycle_df: pd.DataFrame, pattern: dict) -> pd.DataFrame:
    if cycle_df.empty or not pattern:
        return cycle_df

    df = cycle_df.copy()

    if "Data" in df.columns and not np.issubdtype(df["Data"].dtype, np.datetime64):
        df["Data"] = pd.to_datetime(df["Data"], errors="coerce").dt.date

    if "WeekStart" not in df.columns:
        return df

    if not np.issubdtype(df["WeekStart"].dtype, np.datetime64):
        df["WeekStart"] = pd.to_datetime(df["WeekStart"], errors="coerce").dt.date

    week_starts = sorted(pd.Series(df["WeekStart"]).dropna().unique())
    for ws in week_starts:
        week_mask = df["WeekStart"] == ws
        if not week_mask.any():
            continue

        week_chunk = df[week_mask].copy()
        week_chunk = realign_week_types_with_pattern(week_chunk, pattern, ws)
        week_chunk = apply_time_pattern_to_week(week_chunk, pattern)

        for col in ["Start", "End", "StartDT", "EndDT", "Data", "Tipo de Treino"]:
            if col in week_chunk.columns:
                df.loc[week_mask, col] = week_chunk[col].values

    return df

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
def _to_wall_naive(dt: datetime) -> datetime | None:
    """Remove tzinfo mantendo a HORA VISUAL (sem converter para UTC)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


def to_naive(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt

def parse_iso(dt_str: str):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", ""))  # pode vir com Z/+00:00
    except Exception:
        return None
    return _to_wall_naive(dt)

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


def apply_training_updates(user_id: str, uid: str, updates: dict) -> bool:
    df_current = st.session_state.get("df", pd.DataFrame()).copy()
    if df_current.empty:
        return False

    mask = (df_current["UserID"] == user_id) & (df_current["UID"] == uid)
    if not mask.any():
        return False

    idx = df_current[mask].index[0]
    old_row = df_current.loc[idx].copy()

    for key, value in updates.items():
        df_current.at[idx, key] = value

    df_current.at[idx, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
    df_current.at[idx, "ChangeLog"] = append_changelog(old_row, df_current.loc[idx])

    save_user_df(user_id, df_current)

    def _coerce_date(val):
        if isinstance(val, date):
            return val
        try:
            parsed = pd.to_datetime(val, errors="coerce")
        except Exception:
            return None
        if pd.isna(parsed):
            return None
        return parsed.date()

    if any(k in updates for k in ["Start", "End", "Data"]):
        old_date = _coerce_date(old_row.get("Data"))
        new_date = _coerce_date(df_current.loc[idx, "Data"])
        if old_date:
            update_availability_from_current_week(user_id, monday_of_week(old_date))
        if new_date and (not old_date or new_date != old_date):
            update_availability_from_current_week(user_id, monday_of_week(new_date))

    canonical_week_df.clear()
    return True

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


def _ensure_support_work(weekly_targets: dict, sessions_per_mod: dict) -> dict:
    targets = weekly_targets.copy()
    for mod, default_volume in SUPPORT_WORK_DEFAULTS.items():
        sessions = int(sessions_per_mod.get(mod, 0))
        current = float(targets.get(mod, 0.0) or 0.0)
        if sessions > 0 and current <= 0:
            targets[mod] = default_volume
    return targets

def _detail_from_planned_session(mod: str, session_spec: dict, unit: str) -> str | None:
    if not isinstance(session_spec, dict):
        return None
    meta = session_spec.get("meta") or {}
    if not isinstance(meta, dict):
        return None
    label = session_spec.get("label") or meta.get("tipo_nome") or meta.get("tipo")
    volume = float(session_spec.get("volume", 0) or 0)
    zone = meta.get("zona")
    duration = meta.get("duracao_estimada_min") or meta.get("tempo_estimado_min")
    descricao = meta.get("descricao")
    ritmo = meta.get("ritmo")
    tempo_txt = f" (~{int(round(duration))} min)" if duration else ""
    parts = [f"{label or 'Treino'} de {volume:g} {unit}{tempo_txt}."]
    if zone:
        parts.append(f"Zona-alvo: {zone}.")
    if ritmo:
        parts.append(f"Ritmo sugerido: {ritmo}.")
    if descricao:
        parts.append(str(descricao))
    return " ".join(parts)


def prescribe_detail(mod, tipo, volume, unit, paces, duration_override=None):
    vol = float(volume or 0)
    rp = paces.get("run_pace_min_per_km", 0)
    sp = paces.get("swim_sec_per_100m", 0)
    bk = paces.get("bike_kmh", 0)
    override_minutes = _coerce_duration_minutes(duration_override)

    if mod == "Corrida":
        tipo_norm = str(tipo or "").strip().lower()

        def _dur_txt(base_pace: float | None = None):
            if override_minutes:
                return f" (~{override_minutes} min)"
            pace_ref = base_pace if base_pace and base_pace > 0 else rp
            if unit == "km" and pace_ref > 0:
                return f" (~{math.ceil(vol * pace_ref)} min)"
            return ""

        if "prova" in tipo_norm:
            return (
                f"Prova alvo {vol:g} km{_dur_txt()}."
                " Faça aquecimento leve, mantenha o ritmo planejado e execute a estratégia de hidratação."
            )
        if "rodagem" in tipo_norm and "regener" in tipo_norm:
            return (
                f"Rodagem regenerativa Z1–Z2 {vol:g} km{_dur_txt()} para soltar as pernas."
                " Cadência leve, respiração fácil e foco em recuperar."
            )
        if "contínua" in tipo_norm and "leve" in tipo_norm:
            return (
                f"Corrida contínua leve Z2 {vol:g} km{_dur_txt()}."
                " Ritmo confortável, postura alta e respiração nasal sempre que possível."
            )
        if "contínua" in tipo_norm and "moderada" in tipo_norm:
            return (
                f"Corrida contínua moderada Z3 {vol:g} km{_dur_txt()}."
                " Segure perto do limiar inferior, mantendo ritmo firme mas controlado."
            )
        if "tempo" in tipo_norm:
            pace = paces.get("tempo_run", rp)
            return (
                f"Tempo Run em limiar {vol:g} km{_dur_txt(pace)}."
                " Faça blocos contínuos de 20–30 min sentindo esforço 7/10."
            )
        if "fartlek" in tipo_norm:
            return (
                f"Fartlek {vol:g} km{_dur_txt()} em Z3–Z4."
                " Alterne blocos forte/leve (ex.: 1' forte/1' leve) mantendo técnica sólida."
            )
        if "vo" in tipo_norm or "interval" in tipo_norm:
            reps = max(4, min(8, int(max(vol, 1))))
            return (
                f"Intervalado VO₂máx {vol:g} km."
                f" Execute ~{reps} repetições curtas em Z4–Z5 com recuperação igual ao esforço."
            )
        if "long" in tipo_norm:
            return (
                f"Longão contínuo {vol:g} km{_dur_txt()} em Z2 controlado."
                " Trabalhe hidratação/nutrição e conclua forte porém confortável."
            )
        if "educativo" in tipo_norm:
            return (
                f"Educativos técnicos por {vol:g} km (ou ~{max(10, int(vol * 5))} min)."
                " Inclua skipping, dribling, elevação de joelhos e saltitos para coordenação."
            )
        if tipo == "Regenerativo":
            return (
                f"Rodagem regenerativa Z1/Z2 {vol:g} km{_dur_txt()} para acelerar recuperação."
                " Respiração confortável e cadência solta."
            )
        if tipo == "Longão":
            return (
                f"Longão {vol:g} km (Z2/Z3){_dur_txt()}"
                " Objetivo: construir resistência aeróbia. Hidrate-se a cada 15–20min."
            )
        if tipo == "Tempo Run":
            bloco = max(20, min(40, int(vol * 6)))
            return (
                f"Tempo Run {bloco}min em Z3/Z4."
                " Objetivo: elevar limiar e tolerância ao ritmo de prova."
                " Divida em 2× metade se precisar, com transições curtas."
            )

    if mod == "Ciclismo":
        if tipo == "Endurance":
            vel = bk if bk > 0 else 28
            dur_h = vol / vel if vel > 0 else 0
            return (
                f"Endurance {vol:g} km (~{dur_h:.1f}h) em Z2."  # tempo estimado
                " Objetivo: base aeróbia e eficiência de cadência."  # objetivo
                " Pedale entre 85–95rpm e reforce nutrição a cada 30–40min."  # instrução
            )
        if tipo == "Intervalado":
            blocos = max(4, min(6, int(vol / 5)))
            return (
                f"{blocos}×(6min Z4) rec 3min."  # estrutura
                " Objetivo: aumentar VO2 e potência sustentada."  # objetivo
                " Foque em posição aerodinâmica estável nas séries."  # instrução
            )
        if tipo == "Cadência":
            return (
                "5×(3min 100–110rpm) rec 2min em Z2/Z3."  # estrutura
                " Objetivo: coordenação neuromuscular e suavidade de pedalada."  # objetivo
            )
        if tipo == "Força/Subida":
            return (
                "6×(4min 60–70rpm Z3/Z4) rec 3min."  # estrutura
                " Objetivo: torque e força específica para subida."  # objetivo
                " Mantenha tronco firme e cadência controlada."  # dica
            )

    if mod == "Natação":
        if tipo == "Técnica":
            return (
                "Drills técnicos + 8×50m educativos focando posição de corpo e pegada."  # detalhe
                " Objetivo: eficiência e coordenação na água."  # objetivo
            )
        if tipo == "Ritmo":
            reps = max(6, min(10, int(vol / 200)))
            return (
                f"{reps}×200m em ritmo de prova curta (Z3)."  # estrutura
                " Objetivo: consolidar ritmo constante e respiração bilateral."  # objetivo
            )
        if tipo == "Intervalado":
            reps = max(12, min(20, int(vol / 50)))
            alvo = f"{(sp and int(sp)) or '—'} s/100m"
            return (
                f"{reps}×50m forte (Z4/Z5). Alvo ~{alvo}."  # alvo
                " Objetivo: velocidade e sensação de braçada rápida."  # objetivo
            )
        if tipo == "Contínuo":
            km = vol / 1000.0
            return (
                f"{km:.1f} km contínuos Z2/Z3."  # volume
                " Objetivo: resistência e manter técnica sob fadiga."  # objetivo
            )

    if mod == "Força/Calistenia":
        if tipo == "Força máxima":
            return (
                "5×3 básicos pesados (agachamento/terra/empurrar)."  # estrutura
                " Objetivo: força neural e estabilidade."  # objetivo
                " Intervalos longos (2–3min) e técnica impecável."  # dica
            )
        if tipo == "Resistência muscular":
            return (
                "4×12–20 em circuito (empurrar, puxar, membros inferiores)."  # estrutura
                " Objetivo: resistência localizada e suporte para volumes maiores."  # objetivo
            )
        if tipo == "Core/Estabilidade":
            return (
                "Core 15–20min: pranchas, anti-rotação e glúteo médio."  # detalhe
                " Objetivo: proteção lombar e transferência de força."  # objetivo
            )
        if tipo == "Mobilidade/Recuperação":
            return (
                "Mobilidade 15–25min focando quadril, tornozelo e ombro."  # detalhe
                " Objetivo: soltar e acelerar recuperação pós-sessões chave."  # objetivo
            )

    if mod == "Mobilidade":
        if tipo == "Soltura":
            return (
                "Soltura dinâmica 15–25min (fluxos leves)."  # detalhe
                " Objetivo: melhorar amplitude e preparar para treinos seguintes."  # objetivo
            )
        if tipo == "Recuperação":
            return (
                "Alongamentos leves 10–20min + respiração nasal."  # detalhe
                " Objetivo: baixar tensão muscular e sistema nervoso."  # objetivo
            )
        if tipo == "Prevenção":
            return (
                "Mobilidade ombro/quadril 15–20min com foco em estabilidade/controle."  # detalhe
                " Objetivo: prevenir compensações e melhorar técnica."  # objetivo
            )

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
                "TempoEstimadoMin": 0.0,
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
    off_days: list[int] | None = None,
    planned_sessions: dict | None = None,
) -> pd.DataFrame:
    days = week_range(week_start)
    rows = []

    weekly_targets = _ensure_support_work(weekly_targets, sessions_per_mod)

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

    mod_sessions: dict[str, dict] = {}
    planned_sessions = planned_sessions or {}

    for mod, weekly_vol in weekly_targets.items():
        weekly_vol = float(weekly_vol or 0.0)
        planned_mod_sessions = planned_sessions.get(mod)
        n = len(planned_mod_sessions) if planned_mod_sessions else int(sessions_per_mod.get(mod, 0))
        if weekly_vol <= 0 or n <= 0:
            continue

        unit = UNITS_ALLOWED[mod]
        target_total = _round_to_step_sum(weekly_vol, unit)

        session_specs: list[dict] = []
        has_planned = bool(planned_mod_sessions)

        if planned_mod_sessions:
            for sess in planned_mod_sessions:
                if not isinstance(sess, dict):
                    continue
                sess_volume = _round_to_step_sum(float(sess.get("volume", 0.0) or 0.0), unit)
                label = sess.get("tipo_nome") or sess.get("tipo") or sess.get("tipo_slug") or "Treino"
                slug = sess.get("tipo_slug") or sess.get("tipo") or label
                session_specs.append(
                    {
                        "volume": sess_volume,
                        "label": label,
                        "slug": slug,
                        "meta": sess,
                    }
                )
        else:
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

            tipos_base = TIPOS_MODALIDADE.get(mod, ["Treino"])
            tipos = _expand_to_n(tipos_base, n)
            session_specs = [
                {
                    "volume": volumes[i],
                    "label": tipos[i],
                    "slug": tipos[i],
                    "meta": None,
                }
                for i in range(len(volumes))
            ]

        mod_sessions[mod] = {"sessions": session_specs, "has_planned": has_planned}

    session_assignments = {i: [] for i in range(7)}
    off_days_set = set(off_days or [])

    for mod, payload in mod_sessions.items():
        session_specs = payload.get("sessions", [])
        has_planned = payload.get("has_planned", False)
        n = len(session_specs)
        prefs = (user_preferred_days or {}).get(mod, default_days.get(mod, list(range(7))))
        prefs = [d for d in prefs if d in range(7)]

        base_order = []
        for candidate in prefs + list(range(7)):
            if candidate not in base_order and 0 <= candidate < 7:
                base_order.append(candidate)

        if off_days_set:
            preferred = [d for d in base_order if d not in off_days_set]
            fallback = [d for d in base_order if d in off_days_set]
            day_idx = preferred + fallback
        else:
            day_idx = base_order

        if not day_idx:
            day_idx = list(range(7))

        if len(day_idx) < n:
            extras = [i for i in range(7) if i not in day_idx]
            day_idx.extend(extras)

        if len(day_idx) < n:
            # Reuse the user-preferred order (or full week) cyclically when
            # more than seven sessions are requested for the modality.
            cycle = base_order if base_order else list(range(7))
            if not cycle:
                cycle = list(range(7))
            while len(day_idx) < n:
                day_idx.append(cycle[len(day_idx) % len(cycle)])

        day_idx = day_idx[:n]

        key_tipo = (key_sessions or {}).get(mod, "")
        if not has_planned and key_tipo:
            volumes_only = [spec.get("volume", 0.0) for spec in session_specs]
            if volumes_only:
                max_i = max(range(n), key=lambda i: volumes_only[i])
                session_specs[max_i]["label"] = key_tipo
                session_specs[max_i]["slug"] = key_tipo

        for i in range(n):
            session_assignments[day_idx[i]].append((mod, session_specs[i]))

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
                "TempoEstimadoMin": 0.0,
                "Observações": "",
                "Status": "Planejado",
                "adj": 0.0,
                "AdjAppliedAt": "",
                "ChangeLog": "[]",
                "LastEditedAt": "",
                "WeekStart": week_start,
            })
        else:
            for mod, spec in sessions:
                unit = UNITS_ALLOWED[mod]
                vol = float(spec.get("volume", 0.0))
                tipo_label = spec.get("label") or spec.get("slug") or "Treino"
                tempo_estimado = _duration_from_session_spec(
                    mod, spec, unit, tipo_label, paces
                )
                detail = _detail_from_planned_session(mod, spec, unit)
                if not detail:
                    detail = prescribe_detail(
                        mod,
                        tipo_label,
                        vol,
                        unit,
                        paces,
                        duration_override=tempo_estimado,
                    )
                rows.append({
                    "UserID": user_id,
                    "UID": generate_uid(user_id),
                    "Data": d,
                    "Start": "",
                    "End": "",
                    "Modalidade": mod,
                    "Tipo de Treino": tipo_label,
                    "Volume": vol,
                    "Unidade": unit,
                    "RPE": 0,
                    "Detalhamento": detail,
                    "TempoEstimadoMin": tempo_estimado or 0.0,
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

def _run_session_multiplier(tipo: str) -> float:
    tipo_low = str(tipo or "").lower()
    if "recup" in tipo_low:
        return 1.15
    if "long" in tipo_low:
        return 1.05
    if "prova" in tipo_low or "race" in tipo_low:
        return 0.92
    if "tempo" in tipo_low or "limiar" in tipo_low or "steady" in tipo_low:
        return 0.95
    if "tiro" in tipo_low or "interval" in tipo_low:
        return 0.85
    return 1.0


def _normalize_training_label(text: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(text or "")).encode("ASCII", "ignore").decode("ASCII")
    raw = raw.lower()
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_")


def _infer_running_tipo_slug(tipo: str | None) -> str | None:
    normalized = _normalize_training_label(tipo)
    if not normalized:
        return None
    info = getattr(triplanner_engine, "RUN_TRAINING_TYPE_INFO", {})
    for slug, meta in info.items():
        names = {slug, slug.replace("_", ""), _normalize_training_label(meta.get("nome"))}
        names = {n for n in names if n}
        if normalized in names:
            return slug
        for name in names:
            if name and (name in normalized or normalized in name):
                return slug
    if "long" in normalized:
        return "longao"
    if "tempo" in normalized:
        return "tempo_run"
    if "interval" in normalized or "vo2" in normalized:
        return "intervalado_vo2max"
    if "fartlek" in normalized:
        return "fartlek"
    if "regen" in normalized:
        return "rodagem_regenerativa"
    if "moderada" in normalized:
        return "corrida_continua_moderada"
    if "leve" in normalized:
        return "corrida_continua_leve"
    return None


def _run_zone_minutes_from_pace(base_minutes: float | None) -> dict[str, float]:
    try:
        pace_val = float(base_minutes)
    except (TypeError, ValueError):
        return {}
    if pace_val <= 0:
        return {}
    info = getattr(triplanner_engine, "RUN_TRAINING_TYPE_INFO", {})
    zone_map: dict[str, float] = {}
    for slug, meta in info.items():
        factor = meta.get("pace_factor")
        if not factor:
            continue
        try:
            zone_map[slug] = float(pace_val) * float(factor)
        except (TypeError, ValueError):
            continue
    return zone_map


def estimate_session_duration_minutes(
    row: pd.Series, pace_context: dict | None = None
) -> int:
    unit = row.get("Unidade")
    vol = row.get("Volume", 0)
    try:
        vol = float(vol)
    except (TypeError, ValueError):
        vol = 0.0
    pace_ctx = pace_context or {}
    mod = row.get("Modalidade", "")
    tipo = row.get("Tipo de Treino", "")

    if unit == "min" and vol > 0:
        return max(int(round(vol)), 10)
    if str(mod).lower().startswith("cor") and unit == "km" and vol > 0:
        zone_minutes = pace_ctx.get("run_zone_minutes")
        if not zone_minutes:
            zone_minutes = _run_zone_minutes_from_pace(pace_ctx.get("run_pace_min_per_km"))
            if zone_minutes:
                pace_ctx["run_zone_minutes"] = zone_minutes
        pace_minutes = None
        slug = _infer_running_tipo_slug(tipo)
        if zone_minutes and slug and slug in zone_minutes:
            pace_minutes = zone_minutes.get(slug)
        if pace_minutes is None:
            pace_base = zone_minutes.get("corrida_continua_leve") if zone_minutes else None
            if not pace_base:
                pace_base = pace_ctx.get("run_pace_min_per_km")
            if pace_base:
                pace_minutes = float(pace_base) * _run_session_multiplier(tipo)
        if pace_minutes:
            duration = vol * float(pace_minutes)
            return max(int(round(duration)), 15)
    if mod == "Ciclismo" and unit == "km" and vol > 0:
        speed = pace_ctx.get("bike_kmh")
        if speed:
            hours = vol / max(float(speed), 1e-3)
            return max(int(round(hours * 60)), 20)
    if mod == "Natação" and vol > 0:
        pace_swim = pace_ctx.get("swim_sec_per_100m")
        if pace_swim:
            minutes = (vol / 100.0) * (float(pace_swim) / 60.0)
            return max(int(round(minutes)), 10)
    return DEFAULT_TRAINING_DURATION_MIN


def _coerce_duration_minutes(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            value = float(value.replace(",", "."))
        except (TypeError, ValueError):
            return None
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(minutes) or minutes <= 0:
        return None
    return int(round(minutes))


def planned_duration_minutes(
    row: pd.Series | dict, pace_context: dict | None = None
) -> int:
    stored = None
    if isinstance(row, pd.Series):
        stored = row.get("TempoEstimadoMin")
    elif isinstance(row, dict):
        stored = row.get("TempoEstimadoMin")
    stored_minutes = _coerce_duration_minutes(stored)
    if stored_minutes:
        return stored_minutes
    return estimate_session_duration_minutes(row, pace_context)


def _duration_from_session_spec(
    mod: str,
    spec: dict,
    unit: str,
    tipo_label: str,
    paces: dict | None,
) -> int:
    meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else None
    if meta:
        duration = _coerce_duration_minutes(
            meta.get("duracao_estimada_min") or meta.get("tempo_estimado_min")
        )
        if duration:
            return duration
    payload = {
        "Modalidade": mod,
        "Tipo de Treino": tipo_label,
        "Volume": spec.get("volume", 0.0),
        "Unidade": unit,
        "TempoEstimadoMin": spec.get("duracao_estimada_min"),
    }
    return estimate_session_duration_minutes(payload, paces)


def _preferred_time_for_modality(modality: str, preferences: dict | None) -> time:
    pref_map = (preferences or {}).get("time_preferences", {}) or {}
    label = pref_map.get(modality)
    if label in TIME_OF_DAY_WINDOWS:
        return TIME_OF_DAY_WINDOWS[label]
    return TIME_OF_DAY_WINDOWS["Indiferente"]


def _collect_daily_limit_warnings(df: pd.DataFrame, limit_minutes: int | None) -> list[str]:
    if not limit_minutes:
        return []

    if df.empty:
        return []

    tmp = df.copy()
    tmp["StartDT"] = tmp["Start"].apply(parse_iso)
    tmp["EndDT"] = tmp["End"].apply(parse_iso)

    warnings = []
    for day, chunk in tmp.groupby("Data"):
        total = 0
        for _, row in chunk.iterrows():
            if row["Modalidade"] == "Descanso":
                continue
            s = row.get("StartDT")
            e = row.get("EndDT")
            if s and e and e > s:
                total += int((e - s).total_seconds() // 60)
            else:
                total += DEFAULT_TRAINING_DURATION_MIN
        if total > limit_minutes:
            warnings.append(
                f"Dia {day.strftime('%d/%m')}: {total} min planejados (limite {limit_minutes} min)"
            )
    return warnings


def assign_times_to_week(
    week_df: pd.DataFrame,
    slots,
    use_availability: bool,
    preferences: dict | None = None,
    pace_context: dict | None = None,
):
    df = week_df.copy()
    if "Start" not in df.columns:
        df["Start"] = ""
    if "End" not in df.columns:
        df["End"] = ""
    if "TempoEstimadoMin" not in df.columns:
        df["TempoEstimadoMin"] = 0.0

    raw_limit = (preferences or {}).get("daily_limit_minutes") if preferences else None
    daily_limit = None
    if raw_limit not in (None, ""):
        try:
            daily_limit = int(float(raw_limit))
            if daily_limit <= 0:
                daily_limit = None
        except (TypeError, ValueError):
            daily_limit = None

    free = normalize_slots(slots) if use_availability else slots
    warnings = []

    if use_availability:
        for idx, row in df.iterrows():
            if row["Modalidade"] == "Descanso":
                df.at[idx, "Start"] = ""
                df.at[idx, "End"] = ""
                continue

            planned_minutes = planned_duration_minutes(row, pace_context)
            df.at[idx, "TempoEstimadoMin"] = planned_minutes
            duration = timedelta(minutes=planned_minutes)
            assigned = False
            for si, slot in enumerate(free):
                if slot["start"].date() != row["Data"]:
                    continue
                if slot["end"] - slot["start"] >= duration:
                    start_dt = slot["start"]
                    end_dt = start_dt + duration
                    df.at[idx, "Start"] = start_dt.isoformat()
                    df.at[idx, "End"] = end_dt.isoformat()
                    if slot["end"] == end_dt:
                        free.pop(si)
                    else:
                        free[si]["start"] = end_dt
                    assigned = True
                    break
            if not assigned:
                pref_time = _preferred_time_for_modality(row["Modalidade"], preferences)
                start_dt = datetime.combine(row["Data"], pref_time)
                df.at[idx, "Start"] = start_dt.isoformat()
                df.at[idx, "End"] = (start_dt + duration).isoformat()
        warnings.extend(_collect_daily_limit_warnings(df, daily_limit))
        return df, (free if use_availability else slots), warnings

    # Sem disponibilidade: atribui horários respeitando preferências
    df.loc[df["Modalidade"] == "Descanso", ["Start", "End"]] = ""

    training_mask = df["Modalidade"] != "Descanso"
    if training_mask.any():
        grouped = df[training_mask].groupby("Data")
        for day, idxs in grouped.groups.items():
            if isinstance(idxs, (list, tuple)):
                indices = list(idxs)
            else:
                indices = list(idxs.tolist())
            indices.sort(
                key=lambda i: (
                    _preferred_time_for_modality(df.at[i, "Modalidade"], preferences).hour,
                    _preferred_time_for_modality(df.at[i, "Modalidade"], preferences).minute,
                    i,
                )
            )

            current_dt = None
            total_minutes = 0
            for idx in indices:
                row = df.loc[idx]
                pref_time = _preferred_time_for_modality(row["Modalidade"], preferences)
                start_dt = datetime.combine(day, pref_time)
                if current_dt and start_dt < current_dt:
                    start_dt = current_dt
                duration_min = planned_duration_minutes(row, pace_context)
                df.at[idx, "TempoEstimadoMin"] = duration_min
                end_dt = start_dt + timedelta(minutes=duration_min)
                df.at[idx, "Start"] = start_dt.isoformat()
                df.at[idx, "End"] = end_dt.isoformat()
                current_dt = end_dt + timedelta(minutes=5)
                total_minutes += duration_min

            if daily_limit and total_minutes > daily_limit:
                warnings.append(
                    f"Dia {day.strftime('%d/%m')}: {total_minutes} min planejados (limite {daily_limit} min)"
                )

    return df, slots, warnings

def subtract_trainings_from_slots(week_df: pd.DataFrame, slots):
    trainings = []
    for _, r in week_df.iterrows():
        if r["Modalidade"] == "Descanso":
            continue
        s = to_naive(parse_iso(r.get("Start", "")))
        e = to_naive(parse_iso(r.get("End", "")))
        if s and e and e > s:
            trainings.append({"start": s, "end": e})

    # slots -> garantir naive também
    norm_slots = []
    for sl in (slots or []):
        s = to_naive(sl.get("start"))
        e = to_naive(sl.get("end"))
        if s and e and e > s:
            norm_slots.append({"start": s, "end": e})

    if not trainings or not norm_slots:
        return normalize_slots(norm_slots)

    trainings = sorted(trainings, key=lambda x: x["start"])
    new_slots = []
    for slot in normalize_slots(norm_slots):
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
                        tmp.append({"start": to_naive(te), "end": e})
                    elif s < ts < e <= te:
                        tmp.append({"start": s, "end": to_naive(ts)})
                    elif s < ts and te < e:
                        tmp.append({"start": s, "end": to_naive(ts)})
                        tmp.append({"start": to_naive(te), "end": e})
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
        mod_display = modality_label(row.get("Modalidade"))
        summary = f"{mod_display} - {row['Tipo de Treino']}"
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

def _render_week_into_pdf(pdf: PDF, df: pd.DataFrame, week_start: date):
    if df.empty:
        pdf.add_page(orientation="L")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 10, pdf_safe("Sem treinos para esta semana."), 0, 1, "L")
        return

    df = df.copy()
    df = df.sort_values(["Data", "StartDT"]).reset_index(drop=True)

    pdf.add_page(orientation="L")
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

    # Página 1: tabela com horários (AGORA EM PAISAGEM) + coluna de Notas do Atleta
    # Ajustei levemente as larguras para caber em A4 paisagem
    # Mais compacto para sempre caber em uma página A4 paisagem
    col_widths = [22, 16, 16, 28, 32, 16, 12, 64, 42]
    headers = [
        "Data",
        "Início",
        "Fim",
        "Modalidade",
        "Tipo",
        "Volume",
        "Unid.",
        "Detalhamento",
        "Notas do Atleta",
    ]

    pdf.set_font("Arial", "B", 8)
    pdf.set_fill_color(220, 220, 220)
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, pdf_safe(h), 1, 0, "C", 1)
    pdf.ln()

    pdf.set_font("Arial", "", 7.5)
    for _, row in df.iterrows():
        vol_val = float(row["Volume"]) if str(row["Volume"]).strip() != "" else 0.0
        mod = row["Modalidade"]
        mod_display = modality_label(mod)
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
        line_h = 4.5

        # 7 primeiras colunas (dados “fixos”)
        pdf.set_fill_color(*color)
        pdf.set_text_color(*text_color)
        pdf.cell(col_widths[0], line_h, pdf_safe(data_str), 1, 0, "L", 1)
        pdf.cell(col_widths[1], line_h, pdf_safe(ini_str), 1, 0, "C", 1)
        pdf.cell(col_widths[2], line_h, pdf_safe(fim_str), 1, 0, "C", 1)
        pdf.cell(col_widths[3], line_h, pdf_safe(mod_display), 1, 0, "L", 1)
        pdf.cell(col_widths[4], line_h, pdf_safe(tipo), 1, 0, "L", 1)
        pdf.cell(col_widths[5], line_h, pdf_safe(vol), 1, 0, "R", 1)
        pdf.cell(col_widths[6], line_h, pdf_safe(unit), 1, 0, "C", 1)

        # Agora vamos desenhar duas células multi-linha lado a lado:
        # - Detalhamento (texto do plano)
        # - Notas do Atleta (em branco para ele escrever)

        pdf.set_text_color(0, 0, 0)
        pdf.set_fill_color(255, 255, 255)

        # Ponto de início da célula de Detalhamento
        x_detail = pdf.get_x()
        y_detail = pdf.get_y()

        # Célula de Detalhamento (multi_cell com borda)
        pdf.multi_cell(col_widths[7], line_h, pdf_safe(detail), 1, "L")

        # Altura efetiva ocupada por esse multi_cell
        used_height = pdf.get_y() - y_detail
        if used_height <= 0:
            used_height = line_h

        # Célula de Notas do Atleta, com MESMA altura da célula de Detalhamento
        pdf.set_xy(x_detail + col_widths[7], y_detail)
        pdf.multi_cell(col_widths[8], used_height, "", 1, "L")

        # Vai para o início da próxima linha (margem esquerda padrão = 10)
        pdf.set_xy(10, y_detail + used_height)

    # Página 2: calendário visual alinhado ao timeGridWeek (já era paisagem)
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


def generate_pdf(df: pd.DataFrame, week_start: date) -> bytes:
    pdf = PDF(orientation="L")  # já em paisagem
    pdf.alias_nb_pages()
    _render_week_into_pdf(pdf, df, week_start)
    return pdf.output(dest="S").encode("latin-1")


def generate_cycle_pdf(user_id: str, week_starts: list[date]) -> bytes:
    pdf = PDF(orientation="L")
    pdf.alias_nb_pages()

    if not week_starts:
        pdf.add_page(orientation="L")
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 10, pdf_safe("Nenhuma semana definida para este ciclo."), 0, 1, "L")
        return pdf.output(dest="S").encode("latin-1")

    for week_start in week_starts:
        week_df = canonical_week_df(user_id, week_start)
        _render_week_into_pdf(pdf, week_df, week_start)

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


def _normalize_status_flags(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    if "Status" not in tmp.columns:
        tmp["Status"] = ""
    status_norm = tmp["Status"].astype(str).str.strip().str.lower()
    tmp["status_norm"] = status_norm
    tmp["is_planned"] = status_norm != "cancelado"
    tmp["is_realized"] = status_norm == "realizado"
    tmp["is_partial"] = status_norm.isin(["adiado", "parcial"])
    return tmp


def compute_weekly_adherence(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    tmp = df.copy()
    tmp["WeekStart"] = pd.to_datetime(tmp["WeekStart"], errors="coerce").dt.date
    tmp["Data"] = pd.to_datetime(tmp["Data"], errors="coerce").dt.date
    tmp["Volume"] = pd.to_numeric(tmp["Volume"], errors="coerce").fillna(0.0)
    tmp = tmp[tmp["Modalidade"] != "Descanso"]
    if tmp.empty:
        return pd.DataFrame()

    tmp = _normalize_status_flags(tmp)

    planned_mask = tmp["is_planned"]
    realized_mask = tmp["is_realized"]

    planned_sessions = (
        tmp[planned_mask]
        .groupby(["WeekStart", "Modalidade"])
        .size()
        .rename("planned_sessions")
    )
    realized_sessions = (
        tmp[realized_mask]
        .groupby(["WeekStart", "Modalidade"])
        .size()
        .rename("realized_sessions")
    )
    planned_volume = (
        tmp[planned_mask]
        .groupby(["WeekStart", "Modalidade"])["Volume"]
        .sum()
        .rename("planned_volume")
    )
    realized_volume = (
        tmp[realized_mask]
        .groupby(["WeekStart", "Modalidade"])["Volume"]
        .sum()
        .rename("realized_volume")
    )

    planned_sessions_dict = planned_sessions.to_dict()
    realized_sessions_dict = realized_sessions.to_dict()
    planned_volume_dict = planned_volume.to_dict()
    realized_volume_dict = realized_volume.to_dict()

    weeks = sorted(w for w in tmp["WeekStart"].dropna().unique())
    modalities = [m for m in ["Corrida", "Ciclismo", "Natação", "Força/Calistenia"] if m in tmp["Modalidade"].unique()]

    rows = []
    for week in weeks:
        row = {"_week": week, "Semana": week.strftime("%d/%m/%Y")}
        total_planned_sessions = 0
        total_realized_sessions = 0
        total_planned_volume = 0.0
        total_realized_volume = 0.0

        for mod in modalities:
            key = (week, mod)
            psess = planned_sessions_dict.get(key, 0)
            rsess = realized_sessions_dict.get(key, 0)
            pvol = planned_volume_dict.get(key, 0.0)
            rvol = realized_volume_dict.get(key, 0.0)

            total_planned_sessions += psess
            total_realized_sessions += rsess
            total_planned_volume += pvol
            total_realized_volume += rvol

            parts = []
            if psess > 0:
                parts.append(f"S:{rsess / psess * 100:.0f}%")
            if pvol > 0:
                parts.append(f"V:{rvol / pvol * 100:.0f}%")
            row[mod] = " / ".join(parts) if parts else "-"

        if total_planned_sessions > 0:
            row["Total"] = f"{total_realized_sessions / total_planned_sessions * 100:.0f}%"
        else:
            row["Total"] = "-"

        if total_planned_volume > 0:
            row["Aderência (%)"] = f"{total_realized_volume / total_planned_volume * 100:.0f}%"
        else:
            row["Aderência (%)"] = "-"

        rows.append(row)

    if not rows:
        return pd.DataFrame()

    result = pd.DataFrame(rows)
    result = result.sort_values("_week", ascending=False).drop(columns=["_week"])
    return result.reset_index(drop=True)


def build_daily_adherence_heatmap(df: pd.DataFrame, month_start: date):
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    tmp = df.copy()
    tmp["Data"] = pd.to_datetime(tmp["Data"], errors="coerce").dt.date
    tmp["WeekStart"] = pd.to_datetime(tmp["WeekStart"], errors="coerce").dt.date
    tmp["Volume"] = pd.to_numeric(tmp["Volume"], errors="coerce").fillna(0.0)
    tmp = tmp[tmp["Modalidade"] != "Descanso"]
    if tmp.empty:
        return pd.DataFrame(), pd.DataFrame()

    tmp = _normalize_status_flags(tmp)

    tmp["planned_volume"] = tmp.apply(
        lambda r: r["Volume"] if r["is_planned"] else 0.0,
        axis=1,
    )
    tmp["realized_volume"] = tmp.apply(
        lambda r: r["Volume"] if r["is_realized"] else 0.0,
        axis=1,
    )

    daily_stats = tmp.groupby("Data").agg(
        planned_sessions=("is_planned", "sum"),
        realized_sessions=("is_realized", "sum"),
        planned_volume=("planned_volume", "sum"),
        realized_volume=("realized_volume", "sum"),
    )

    daily_stats_dict = daily_stats.to_dict("index")

    cal = py_calendar.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(month_start.year, month_start.month)

    columns = OFF_DAY_LABELS
    display_df = pd.DataFrame("", index=[f"Sem {i+1}" for i in range(len(weeks))], columns=columns)
    ratio_df = pd.DataFrame(np.nan, index=display_df.index, columns=columns)

    for w_idx, week_days in enumerate(weeks):
        for d_idx, day_dt in enumerate(week_days):
            if day_dt.month != month_start.month:
                display_df.iat[w_idx, d_idx] = ""
                ratio_df.iat[w_idx, d_idx] = np.nan
                continue

            stats = daily_stats_dict.get(day_dt)
            if not stats:
                display_df.iat[w_idx, d_idx] = ""
                ratio_df.iat[w_idx, d_idx] = np.nan
                continue

            planned = stats.get("planned_sessions", 0)
            realized = stats.get("realized_sessions", 0)

            if planned <= 0:
                ratio = 1.0 if realized > 0 else np.nan
            else:
                ratio = realized / planned

            ratio_df.iat[w_idx, d_idx] = ratio

            if planned <= 0:
                display_df.iat[w_idx, d_idx] = ""
            else:
                percent = ratio * 100 if ratio == ratio else 0.0
                display_df.iat[w_idx, d_idx] = f"{percent:.0f}% ({int(realized)}/{int(planned)})"

    return display_df, ratio_df


def make_heatmap_style(ratio_df: pd.DataFrame):
    def _style(data):
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        for r in data.index:
            for c in data.columns:
                ratio = ratio_df.loc[r, c]
                if pd.isna(ratio):
                    color = "#f1f3f5"
                elif ratio >= 0.99:
                    color = "#69db7c"
                elif ratio > 0:
                    color = "#ffd43b"
                else:
                    color = "#ff6b6b"
                styles.loc[r, c] = f"background-color: {color}; color: #1f1f1f; font-weight: 600;"
        return styles

    return _style


def extract_training_changelog(row: pd.Series) -> list[dict]:
    log_raw = row.get("ChangeLog", "[]")
    try:
        entries = json.loads(log_raw or "[]")
    except Exception:
        entries = []

    parsed = []
    for entry in entries:
        ts_str = entry.get("at", "")
        ts = None
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
            except Exception:
                ts = None
        changes = entry.get("changes", {}) or {}
        change_list = []
        for field, values in changes.items():
            old = values.get("old", "")
            new = values.get("new", "")
            change_list.append(f"{field}: {old} → {new}")
        parsed.append(
            {
                "timestamp": ts,
                "timestamp_str": ts.strftime("%d/%m %H:%M") if ts else ts_str,
                "changes": change_list,
            }
        )

    parsed.sort(key=lambda x: x["timestamp"] or datetime.min, reverse=True)
    return parsed


def build_week_changelog(df: pd.DataFrame, week_start: date) -> list[dict]:
    if df.empty:
        return []

    chunk = week_slice(df, week_start)
    if chunk.empty:
        return []

    events = []
    for _, row in chunk.iterrows():
        mod_display = modality_label(row.get("Modalidade"))
        training_desc = f"{mod_display} - {row['Tipo de Treino']} ({row['Data']})"
        for entry in extract_training_changelog(row):
            events.append(
                {
                    "timestamp": entry["timestamp"],
                    "timestamp_str": entry["timestamp_str"],
                    "training": training_desc,
                    "changes": entry["changes"],
                }
            )

    events.sort(key=lambda x: x["timestamp"] or datetime.min, reverse=True)
    return events

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
    user_preferences: dict | None = None,
) -> pd.DataFrame:
    all_weeks = []
    for w in range(num_weeks):
        ws = cycle_start_week + timedelta(days=7 * w)
        phase = PHASES[w % 4]

        weekly_targets = {}
        for mod in MODALIDADES:
            prop = phase_proportions.get(mod, {}).get(phase, 0.0)
            weekly_targets[mod] = base_load * float(prop)

        weekly_targets = _ensure_support_work(weekly_targets, sessions_per_mod)

        week_df = distribute_week_by_targets(
            ws,
            weekly_targets,
            sessions_per_mod,
            key_sessions,
            paces,
            user_preferred_days,
            user_id,
            off_days=(user_preferences or {}).get("off_days"),
        )
        week_df, _, _ = assign_times_to_week(
            week_df,
            [],
            use_availability=False,
            preferences=user_preferences,
            pace_context=paces,
        )
        all_weeks.append(week_df)

    if not all_weeks:
        return pd.DataFrame(columns=SCHEMA_COLS)
    return pd.concat(all_weeks, ignore_index=True)[SCHEMA_COLS]


def _pace_defaults_from_state() -> dict:
    run_pace = float(st.session_state.get("run_pace_min_per_km", 5.0))
    paces = {
        "run_pace_min_per_km": run_pace,
        "swim_sec_per_100m": float(st.session_state.get("swim_sec_per_100m", 110)),
        "bike_kmh": float(st.session_state.get("bike_kmh", 32.0)),
    }
    zone_minutes = _run_zone_minutes_from_pace(run_pace)
    if zone_minutes:
        paces["run_zone_minutes"] = zone_minutes
        for slug, minutes in zone_minutes.items():
            paces[slug] = minutes
    return paces


def _pace_minutes_to_str(minutes: float | None) -> str | None:
    try:
        pace_val = float(minutes)
    except (TypeError, ValueError):
        return None
    if pace_val <= 0:
        return None
    total_seconds = int(round(pace_val * 60))
    total_seconds = max(total_seconds, 1)
    mins, secs = divmod(total_seconds, 60)
    return f"{int(mins):02d}:{int(secs):02d}/km"


def _preferred_days_from_state(off_days: set[int]) -> dict:
    dias_map = {"Seg": 0, "Ter": 1, "Qua": 2, "Qui": 3, "Sex": 4, "Sáb": 5, "Dom": 6}
    preferred = {}
    for mod in MODALIDADES:
        raw_selection = [
            dias_map[d]
            for d in st.session_state.get(f"pref_days_{mod}", [])
            if d in dias_map
        ]
        filtered_sel = [d for d in raw_selection if d not in off_days]
        if not filtered_sel:
            filtered_sel = [idx for idx in dias_map.values() if idx not in off_days]
        preferred[mod] = filtered_sel
    return preferred


def _sessions_per_mod_from_state() -> dict:
    return {mod: int(st.session_state.get(f"sess_{mod}", 2)) for mod in MODALIDADES}


def _key_sessions_from_state() -> dict:
    return {mod: st.session_state.get(f"key_sess_{mod}", "") for mod in MODALIDADES}


def cycle_plan_to_trainings(
    plan: dict,
    sessions_per_mod: dict,
    key_sessions: dict,
    preferred_days: dict,
    paces: dict,
    user_id: str,
    user_preferences: dict | None,
) -> pd.DataFrame:
    weeks_payload = plan.get("semanas", []) if isinstance(plan, dict) else []
    if not weeks_payload:
        return pd.DataFrame(columns=SCHEMA_COLS)

    all_weeks = []
    off_days = (user_preferences or {}).get("off_days")

    for week_data in weeks_payload:
        start_raw = week_data.get("inicio") if isinstance(week_data, dict) else None
        try:
            ws = date.fromisoformat(start_raw) if start_raw else None
        except Exception:
            ws = None
        if not ws:
            continue

        volume_targets = week_data.get("volume_por_modalidade") or {}
        weekly_targets = {
            mod: float(vol or 0.0)
            for mod, vol in volume_targets.items()
            if mod in UNITS_ALLOWED
        }

        weekly_targets = _ensure_support_work(weekly_targets, sessions_per_mod)

        planned_sessions_by_mod = {}
        treinos = week_data.get("treinos") if isinstance(week_data, dict) else None
        if treinos and isinstance(treinos, list):
            planned = []
            for sess in treinos:
                if not isinstance(sess, dict):
                    continue
                volume = sess.get("volume_km")
                if volume is None:
                    volume = sess.get("volume")
                try:
                    volume = float(volume)
                except (TypeError, ValueError):
                    volume = None
                if volume is None or volume <= 0:
                    continue
                tipo_slug = sess.get("tipo") or sess.get("slug")
                tipo_nome = sess.get("tipo_nome") or sess.get("nome") or tipo_slug or "Treino"
                planned.append(
                    {
                        "volume": round(volume, 1),
                        "tipo_nome": tipo_nome,
                        "tipo_slug": tipo_slug or tipo_nome,
                        "zona": sess.get("zona"),
                        "descricao": sess.get("descricao"),
                        "duracao_estimada_min": sess.get("duracao_estimada_min"),
                        "ritmo": sess.get("ritmo"),
                    }
                )
            if planned:
                planned_sessions_by_mod["Corrida"] = planned

        week_df = distribute_week_by_targets(
            ws,
            weekly_targets,
            sessions_per_mod,
            key_sessions,
            paces,
            preferred_days,
            user_id,
            off_days=off_days,
            planned_sessions=planned_sessions_by_mod,
        )
        week_df, _, _ = assign_times_to_week(
            week_df,
            [],
            use_availability=False,
            preferences=user_preferences,
            pace_context=paces,
        )
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

        # Atualiza sessão + banco para que os handlers (eventDrop/eventClick) enxerguem os mesmos UIDs do calendário
        save_user_df(user_id, base_df)

    # StartDT / EndDT canônicos
    if "TempoEstimadoMin" not in week_df.columns:
        week_df["TempoEstimadoMin"] = 0.0

    week_df["StartDT"] = week_df["Start"].apply(parse_iso)
    week_df["StartDT"] = week_df.apply(
        lambda r: r["StartDT"] or datetime.combine(r["Data"], time(6, 0)),
        axis=1,
    )

    week_df["EndDT"] = week_df["End"].apply(parse_iso)
    week_df["EndDT"] = week_df.apply(
        lambda r: r["EndDT"]
        or (
            r["StartDT"]
            + timedelta(minutes=planned_duration_minutes(r))
        ),
        axis=1,
    )

    # Remove Descanso puro (como combinado para calendário/PDF/ICS)
    mask_valid = ~((week_df["Modalidade"] == "Descanso") & (week_df["Volume"] <= 0))
    week_df = week_df[mask_valid]

    # Ordena
    week_df = week_df.sort_values(["Data", "StartDT"]).reset_index(drop=True)

    return week_df


def render_cycle_planning_tab(user_id: str, user_preferences: dict | None = None):
    st.subheader("Planejamento semanal do ciclo")
    st.markdown(
        "Monte um esqueleto semanal do ciclo inteiro antes de preencher os treinos. "
        "Escolha modalidade, distância e duração e o motor gera a carga semanal com focos e intensidades."
    )

    user_preferences = user_preferences or {}

    modality_labels = {
        "triathlon": "Triathlon",
        "corrida": "Corrida",
        "bike": "Ciclismo",
        "natação": "Natação",
    }

    modality = st.selectbox(
        "Modalidade",
        list(modality_labels.keys()),
        format_func=lambda k: modality_labels.get(k, k).title(),
    )

    distance_options = {
        "triathlon": ["Sprint", "Olímpico", "70.3", "Ironman"],
        "corrida": ["5k", "10k", "21k", "42k"],
        "bike": ["100k", "200k", "Longo"],
        "natação": ["1.5k", "3k", "5k"],
    }
    distance = st.selectbox(
        "Distância/Prova",
        distance_options.get(modality, ["Livre"]),
        key="cycle_distance_select",
    )

    goal = st.radio("Objetivo", ["Completar", "Performar"], horizontal=True)

    level_options = {
        "iniciante": "Iniciante",
        "intermediario": "Intermediário",
        "avancado": "Avançado",
    }
    level_keys = list(level_options.keys())
    nivel = st.selectbox(
        "Nível do atleta",
        level_keys,
        format_func=lambda key: level_options.get(key, key.title()),
        index=0,
        key="cycle_level_select",
    )

    start_date_default = monday_of_week(today())
    start_date = st.date_input("Início do ciclo", value=start_date_default, key="cycle_start_date")

    duration_mode = st.radio(
        "Como prefere informar a duração?",
        ["Número de semanas", "Data da prova"],
        horizontal=True,
        key="cycle_duration_mode",
    )

    cycle_weeks: int
    if duration_mode == "Número de semanas":
        cycle_weeks = int(st.number_input("Semanas de preparação", min_value=4, max_value=52, value=12, step=1))
    else:
        event_date = st.date_input("Data da prova", value=start_date + timedelta(weeks=12), key="cycle_event_date")
        cycle_weeks = triplanner_engine.compute_weeks_from_date(event_date, start_date)
        st.caption(f"Serão necessárias cerca de **{cycle_weeks} semanas** até a prova.")

    notes = st.text_area("Observações", value="", key="cycle_notes")

    use_time_pattern_cycle_plan = st.checkbox(
        "Aplicar padrão de horários salvo", value=True, key="apply_time_pattern_cycle_plan"
    )

    if st.button("Gerar plano semanal do ciclo", key="cycle_generate_btn"):
        paces = _pace_defaults_from_state()
        pace_hint = _pace_minutes_to_str(paces.get("run_pace_min_per_km"))
        plan = triplanner_engine.build_triplanner_plan(
            modality=modality,
            distance=distance,
            goal=goal,
            cycle_weeks=cycle_weeks,
            start_date=start_date,
            pace_medio=pace_hint,
            nivel=nivel,
            notes=notes,
        )

        off_days_cycle = set(user_preferences.get("off_days", []))
        pref_days = _preferred_days_from_state(off_days_cycle)
        sess_per_mod = _sessions_per_mod_from_state()
        key_sess = _key_sessions_from_state()

        new_cycle_df = cycle_plan_to_trainings(
            plan,
            sess_per_mod,
            key_sess,
            pref_days,
            paces,
            user_id,
            user_preferences,
        )

        pattern = load_timepattern_for_user(user_id) if use_time_pattern_cycle_plan else None
        if pattern:
            new_cycle_df = apply_time_pattern_to_cycle(new_cycle_df, pattern)

        cycle_end = start_date + timedelta(weeks=cycle_weeks)
        existing_df = st.session_state["df"].copy()
        if not existing_df.empty and not np.issubdtype(existing_df["WeekStart"].dtype, np.datetime64):
            existing_df["WeekStart"] = pd.to_datetime(
                existing_df["WeekStart"], errors="coerce"
            ).dt.date

        df_outside_cycle = existing_df[
            (existing_df["WeekStart"] < start_date)
            | (existing_df["WeekStart"] >= cycle_end)
        ]

        final_df = pd.concat([df_outside_cycle, new_cycle_df], ignore_index=True)
        save_user_df(user_id, final_df)
        canonical_week_df.clear()

        st.success(
            f"{cycle_weeks} semanas de ciclo geradas e enviadas para o calendário!"
        )

    st.markdown("---")
    st.subheader("Exportar ciclo em PDF")
    st.caption(
        "O PDF reúne cada semana do ciclo usando os mesmos treinos exibidos no calendário."
    )

    week_starts = [start_date + timedelta(weeks=i) for i in range(cycle_weeks)]
    cycle_pdf = generate_cycle_pdf(user_id, week_starts)

    st.download_button(
        "📕 Exportar PDF do ciclo",
        data=cycle_pdf,
        file_name=f"ciclo_{start_date.strftime('%Y%m%d')}.pdf",
        mime="application/pdf",
        key="cycle_export_pdf",
    )


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
    if "calendar_snapshot" not in st.session_state:
        st.session_state["calendar_snapshot"] = []
    if "calendar_forcar_snapshot" not in st.session_state:
        st.session_state["calendar_forcar_snapshot"] = False
    if "pending_clear_week" not in st.session_state:
        st.session_state["pending_clear_week"] = None

    df = st.session_state["df"]

    if (
        "user_preferences_cache" not in st.session_state
        or st.session_state.get("user_preferences_cache_user") != user_id
    ):
        prefs_loaded = load_preferences_for_user(user_id)
        st.session_state["user_preferences_cache"] = prefs_loaded
        st.session_state["user_preferences_cache_user"] = user_id

    user_preferences = st.session_state.get("user_preferences_cache", load_preferences_for_user(user_id))

    # SIDEBAR
    st.sidebar.title("TriPlano 🌀")
    st.sidebar.markdown(f"👤 **{user_name}**  \n`{user_id}`")
    if st.sidebar.button("Sair"):
        logout()

    menu = st.sidebar.radio(
        "Navegação",
        ["📅 Planejamento Semanal", "🗓️ Resumo do Dia", "📈 Dashboard", "🚴 Strava"],
        index=0,
    )
    st.sidebar.markdown("---")
    st.sidebar.markdown("Desenvolvido por **Matheus Vianna**")

    # ---------------- PLANEJAMENTO SEMANAL ----------------
    if menu == "📅 Planejamento Semanal":
        st.header("📅 Planejamento Semanal")
        tab_semana, tab_ciclo = st.tabs(["Planeje sua semana", "Plano semanal do ciclo"])
        with tab_semana:

            off_days_set = set(user_preferences.get("off_days", []))
            opcoes_agendamento = [
                "Padrão do app (ignorar horários livres)",
                "Usar padrão de horários salvo",
            ]
            modo_agendamento_default = st.session_state.get(
                "modo_agendamento_choice", opcoes_agendamento[0]
            )

            generate_week_clicked = False
            with st.popover(
                "⚙️ Parâmetros de prescrição e metas semanais", use_container_width=True
            ):
                st.markdown(
                    "Defina ritmos de referência, sessões e dias preferidos para gerar a semana e o ciclo já corrigidos."
                )

                st.markdown("**Parâmetros de prescrição**")
                col_p1, col_p2, col_p3 = st.columns(3)
                paces = {
                    "run_pace_min_per_km": col_p1.number_input(
                        "Corrida Z2 (min/km)",
                        value=float(st.session_state.get("run_pace_min_per_km", 5.0)),
                        min_value=3.0,
                        max_value=10.0,
                        step=0.1,
                        format="%.1f",
                        key="run_pace_min_per_km",
                        help="Informe o pace confortável/Z2 (ex.: 6.0 = 6:00/km)",
                    ),
                    "swim_sec_per_100m": col_p2.number_input(
                        "Natação (seg/100m)",
                        value=int(st.session_state.get("swim_sec_per_100m", 110)),
                        min_value=60,
                        max_value=200,
                        step=5,
                        key="swim_sec_per_100m",
                    ),
                    "bike_kmh": col_p3.number_input(
                        "Ciclismo (km/h)",
                        value=float(st.session_state.get("bike_kmh", 32.0)),
                        min_value=15.0,
                        max_value=50.0,
                        step=0.5,
                        format="%.1f",
                        key="bike_kmh",
                    ),
                }

                st.markdown("**Metas semanais (volume, sessões e dias preferidos)**")
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
                    default_volume = SUPPORT_WORK_DEFAULTS.get(mod, 0.0)

                    weekly_targets[mod] = cols_mod[i].number_input(
                        f"{mod} ({unit})/sem",
                        value=float(st.session_state.get(f"target_{mod}", default_volume)),
                        min_value=0.0,
                        step=_unit_step(unit),
                        format="%.1f" if unit == "km" else "%g",
                        key=f"target_{mod}",
                    )

                    default_selected = [
                        abrev
                        for abrev, idx in dias_semana_options.items()
                        if idx in default_days.get(mod, []) and idx not in off_days_set
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

                st.caption("Essas metas também alimentam a geração de ciclo direto no calendário.")

                st.markdown("**Como encaixar os treinos no horário?**")
                modo_agendamento = st.radio(
                    "Opção de agendamento",
                    opcoes_agendamento,
                    index=opcoes_agendamento.index(modo_agendamento_default)
                    if modo_agendamento_default in opcoes_agendamento
                    else 0,
                    horizontal=True,
                    key="modo_agendamento_radio",
                )
                st.session_state["modo_agendamento_choice"] = modo_agendamento

                generate_week_clicked = st.button(
                    "📆 Gerar Semana Automática",
                    use_container_width=True,
                    key="btn_generate_week",
                )

            st.markdown("---")

            # 3. Semana atual
            col1, col2, col3 = st.columns([1, 2, 1])
            if col1.button("⬅️ Semana anterior"):
                st.session_state["current_week_start"] -= timedelta(days=7)
                st.session_state["calendar_snapshot"] = []
                st.session_state["calendar_forcar_snapshot"] = False
                canonical_week_df.clear()
                safe_rerun()
            week_start = st.session_state["current_week_start"]
            col2.subheader(f"Semana de {week_start.strftime('%d/%m/%Y')}")
            if col3.button("Semana seguinte ➡️"):
                st.session_state["current_week_start"] += timedelta(days=7)
                st.session_state["calendar_snapshot"] = []
                st.session_state["calendar_forcar_snapshot"] = False
                canonical_week_df.clear()
                safe_rerun()

            if st.session_state.get("pending_clear_week") not in (None, week_start):
                st.session_state["pending_clear_week"] = None

            week_df_raw = week_slice(df, week_start)
            if week_df_raw.empty:
                week_df_raw = default_week_df(week_start, user_id)
    
            week_slots = get_week_availability(user_id, week_start)
    
            # Gerar semana automática
            if generate_week_clicked:
                dias_map = dias_semana_options
                off_days_set = set(user_preferences.get("off_days", []))
                current_preferred_days = {}
                for mod in MODALIDADES:
                    selected_labels = st.session_state.get(f"pref_days_{mod}", [])
                    selected = [dias_map[d] for d in selected_labels if d in dias_map]
                    filtered = [d for d in selected if d not in off_days_set]
                    if not filtered:
                        filtered = [idx for idx in dias_map.values() if idx not in off_days_set]
                    current_preferred_days[mod] = filtered
                key_sessions = {mod: st.session_state.get(f"key_sess_{mod}", "") for mod in MODALIDADES}

                weekly_targets = _ensure_support_work(weekly_targets, sessions_per_mod)

                new_week_df = distribute_week_by_targets(
                    week_start,
                    weekly_targets,
                    sessions_per_mod,
                    key_sessions,
                    paces,
                    current_preferred_days,
                    user_id,
                    off_days=user_preferences.get("off_days"),
                )
    
                use_saved_pattern = modo_agendamento == opcoes_agendamento[1]
                pattern = load_timepattern_for_user(user_id) if use_saved_pattern else None
                warnings = []

                if use_saved_pattern and not pattern:
                    st.warning(
                        "Nenhum padrão de horários salvo ainda. Usando lógica padrão do app."
                    )

                if pattern:
                    new_week_df = apply_time_pattern_to_week(new_week_df, pattern)
                else:
                    new_week_df, _updated_slots, warnings = assign_times_to_week(
                        new_week_df,
                        week_slots,
                        use_availability=False,
                        preferences=user_preferences,
                        pace_context=paces,
                    )

                for warn in warnings:
                    st.warning(warn)
    
                user_df = st.session_state["df"]
                others = user_df[user_df["WeekStart"] != week_start]
                user_df_new = pd.concat([others, new_week_df], ignore_index=True)
                save_user_df(user_id, user_df_new)
                st.success("Semana gerada e salva!")
                canonical_week_df.clear()
                safe_rerun()
    
            # Recarrega df do usuário após geração
            df = st.session_state["df"]

            # Calendário: usa df canônico (MESMO dataset do PDF/ICS)
            st.subheader("Calendário da Semana")
    
            week_df_can = canonical_week_df(user_id, week_start)
    

            events = []
    
            # Treinos
            for _, row in week_df_can.iterrows():
                uid = row["UID"]
                vol_val = float(row["Volume"]) if str(row["Volume"]).strip() != "" else 0.0
    
                mod_display = modality_label(row.get("Modalidade"))
                title = f"{mod_display} - {row['Tipo de Treino']}"
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
            options["initialDate"] = week_start.isoformat()
    
            cal_state = st_calendar(
                events=events,
                options=options,
                key=f"cal_semana_{get_week_key(week_start)}",
            )
            if cal_state and "eventsSet" in cal_state:
                eventos_visuais = cal_state["eventsSet"]["events"]
                st.session_state["calendar_snapshot"] = eventos_visuais
    
            if st.session_state.get("calendar_forcar_snapshot", False):
                eventos = []
                if isinstance(cal_state, dict):
                    eventos = cal_state.get("events") or []
                    if not eventos:
                        eventos = cal_state.get("eventsSet", {}).get("events", [])
                if not eventos:
                    eventos = st.session_state.get("calendar_snapshot", [])
    
                if eventos:
                    df_current = st.session_state["df"].copy()
    
                    for ev in eventos:
                        ext = ev.get("extendedProps", {})
                        if ext.get("type") != "treino":
                            continue
    
                        uid = ext.get("uid") or ev.get("id")
                        if not uid:
                            continue
    
                        mask = (df_current["UserID"] == user_id) & (df_current["UID"] == uid)
                        if not mask.any():
                            continue
    
                        idx = df_current[mask].index[0]
                        old_row = df_current.loc[idx].copy()
                        start = parse_iso(ev.get("start"))
                        end = parse_iso(ev.get("end"))
                        if not start or not end or end <= start:
                            continue
    
                        df_current.at[idx, "Start"] = start.isoformat()
                        df_current.at[idx, "End"] = end.isoformat()
                        df_current.at[idx, "Data"] = start.date()
                        df_current.at[idx, "WeekStart"] = monday_of_week(start.date())
                        df_current.at[idx, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
                        df_current.at[idx, "ChangeLog"] = append_changelog(old_row, df_current.loc[idx])
    
                    save_user_df(user_id, df_current)
    
                    df_from_csv = load_all()
                    st.session_state["df"] = df_from_csv[df_from_csv["UserID"] == user_id].copy()
                    st.session_state["all_df"] = df_from_csv
                    st.session_state["calendar_snapshot"] = eventos
                    canonical_week_df.clear()
    
                    st.success("✅ Semana salva com os horários visuais do calendário.")
                else:
                    st.warning("⚠️ Nenhum evento encontrado para salvar.")
    
                st.session_state["calendar_forcar_snapshot"] = False
    
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
    
        def _persist_calendar_update(uid: str, start: datetime, end: datetime) -> Optional[int]:
            if not uid or not start or not end or end <= start:
                st.toast("ERRO: Dados inválidos ao persistir o evento.", icon="🚨")
                return None

            df_current = st.session_state["df"].copy()
            mask = (df_current["UserID"] == user_id) & (df_current["UID"] == uid)
            if not mask.any():
                st.toast(f"ERRO: Treino {uid} não encontrado no DataFrame.", icon="🚨")
                return None

            idx = df_current[mask].index[0]
            old_row = df_current.loc[idx].copy()

            df_current.loc[idx, "Start"] = start.isoformat()
            df_current.loc[idx, "End"] = end.isoformat()
            duration_min = max(int((end - start).total_seconds() // 60), 1)
            df_current.loc[idx, "TempoEstimadoMin"] = duration_min
            df_current.loc[idx, "Data"] = start.date()
            df_current.loc[idx, "WeekStart"] = monday_of_week(start.date())
            df_current.loc[idx, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
            df_current.loc[idx, "ChangeLog"] = append_changelog(old_row, df_current.loc[idx])

            save_user_df(user_id, df_current)
            st.session_state["df"] = df_current

            ws_old = monday_of_week(old_row["Data"]) if not isinstance(old_row["Data"], str) else monday_of_week(datetime.fromisoformat(old_row["Data"]).date())
            ws_new = monday_of_week(start.date())
            update_availability_from_current_week(user_id, ws_old)
            update_availability_from_current_week(user_id, ws_new)

            canonical_week_df.clear()
            return idx


        def render_training_detail(uid: str):
            df_current = st.session_state.get("df", pd.DataFrame())
            if df_current.empty or "UserID" not in df_current or "UID" not in df_current:
                st.error("Treino não encontrado para detalhamento.")
                return

            mask = (df_current["UserID"] == user_id) & (df_current["UID"] == uid)
            if not mask.any():
                st.error("Treino não encontrado para detalhamento.")
                return

            idx = df_current[mask].index[0]
            r = df_current.loc[idx]

            st.markdown("---")
            with st.container(border=True):
                st.markdown("### 📝 Detalhes do treino")

                start_dt = parse_iso(r.get("Start", "")) or datetime.combine(r["Data"], time(6, 0))
                end_dt = parse_iso(r.get("End", "")) or (start_dt + timedelta(minutes=DEFAULT_TRAINING_DURATION_MIN))
                dur_min = int((end_dt - start_dt).total_seconds() / 60)
                stored_duration = _coerce_duration_minutes(r.get("TempoEstimadoMin"))
                if stored_duration:
                    dur_min = stored_duration

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
                new_date = col_dt1.date_input("Data do treino", value=start_dt.date(), key=f"dt_{uid}")
                new_time = col_dt2.time_input("Horário de início", value=start_dt.time(), key=f"tm_{uid}")
                new_dur = st.number_input("Duração (min)", min_value=15, max_value=300, value=dur_min, step=5, key=f"dur_{uid}")

                new_start = datetime.combine(new_date, new_time)
                new_end = new_start + timedelta(minutes=int(new_dur))

                new_rpe = st.slider("RPE (esforço percebido)", 0, 10, int(r.get("RPE", 0) or 0), key=f"rpe_{uid}")
                new_obs = st.text_area("Comentário rápido", value=str(r.get("Observações", "")), key=f"obs_{uid}")

                col_feito, col_nao, col_salvar = st.columns(3)

                def apply_update(status_override=None):
                    df_upd = st.session_state["df"]
                    mask2 = (df_upd["UserID"] == user_id) & (df_upd["UID"] == uid)
                    if not mask2.any():
                        return
                    i2 = df_upd[mask2].index[0]
                    old_row2 = df_upd.loc[i2].copy()

                    df_upd.loc[i2, "Modalidade"] = new_mod
                    df_upd.loc[i2, "Tipo de Treino"] = new_tipo
                    df_upd.loc[i2, "Volume"] = new_vol
                    df_upd.loc[i2, "Unidade"] = UNITS_ALLOWED.get(new_mod, old_row2.get("Unidade", ""))

                    df_upd.loc[i2, "Start"] = new_start.isoformat()
                    df_upd.loc[i2, "End"] = new_end.isoformat()
                    df_upd.loc[i2, "TempoEstimadoMin"] = int(new_dur)
                    df_upd.loc[i2, "Data"] = new_start.date()
                    df_upd.loc[i2, "WeekStart"] = monday_of_week(new_start.date())

                    df_upd.loc[i2, "RPE"] = new_rpe
                    df_upd.loc[i2, "Observações"] = new_obs

                    if status_override is not None:
                        df_upd.loc[i2, "Status"] = status_override

                    df_upd.loc[i2, "LastEditedAt"] = datetime.now().isoformat(timespec="seconds")
                    df_upd.loc[i2, "ChangeLog"] = append_changelog(old_row2, df_upd.loc[i2])

                    save_user_df(user_id, df_upd)

                    ws_old2 = monday_of_week(old_row2["Data"]) if not isinstance(old_row2["Data"], str) else monday_of_week(datetime.fromisoformat(old_row2["Data"]).date())
                    ws_new2 = monday_of_week(new_start.date())
                    update_availability_from_current_week(user_id, ws_old2)
                    update_availability_from_current_week(user_id, ws_new2)

                    canonical_week_df.clear()
                    safe_rerun()

                if col_feito.button("✅ FEITO", key=f"feito_{uid}"):
                    apply_update("Realizado")
                if col_nao.button("❌ NÃO FEITO", key=f"naofeito_{uid}"):
                    apply_update("Cancelado")
                if col_salvar.button("💾 Salvar", key=f"save_{uid}"):
                    apply_update(None)

        # 5.2 Drag/resize treinos -> atualiza df base (logo afeta canonical e PDF/ICS)
        def handle_move_or_resize(ev_dict, action_label):
            ev = ev_dict.get("event", {}) if ev_dict else {}
            ext = ev.get("extendedProps", {}) or {}
            if ext.get("type") != "treino":
                return

            uid = ext.get("uid")
            start = parse_iso(ev.get("start"))
            end = parse_iso(ev.get("end"))

            idx = _persist_calendar_update(uid, start, end)
            if idx is not None:
                st.toast(f"Treino {uid} {action_label} e salvo.", icon="💾")
                render_training_detail(uid)


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
                new_slots = [sl for sl in week_slots if not (to_naive(sl["start"]) == s and to_naive(sl["end"]) == e)]
                set_week_availability(user_id, week_start, new_slots)
                canonical_week_df.clear()
                safe_rerun()

            # Clique em treino -> SALVA horário do calendário no banco e abre o popup
            if etype == "treino":
                uid = ext.get("uid") or ev.get("id")
                cal_start = parse_iso(ev.get("start"))
                cal_end = parse_iso(ev.get("end"))

                idx = _persist_calendar_update(uid, cal_start, cal_end)
                if idx is None:
                    st.error("Evento inválido.")
                else:
                    render_training_detail(uid)
    
        # Botões de persistência da semana
        col_save_week, col_clear_week = st.columns([1, 1])
        if col_save_week.button("💾 Salvar Semana Atual"):
            st.session_state["calendar_forcar_snapshot"] = True
            if "calendar_snapshot" not in st.session_state:
                st.session_state["calendar_snapshot"] = []
            safe_rerun()

        if col_clear_week.button("🧹 Limpar semana", key=f"clear_week_bottom_{week_start}"):
            st.session_state["pending_clear_week"] = week_start

        if st.session_state.get("pending_clear_week") == week_start:
            with st.container(border=True):
                st.warning(
                    "Tem certeza de que deseja remover todos os treinos desta semana?"
                )
                col_confirma, col_cancela = st.columns(2)

                if col_confirma.button(
                    "Sim, limpar semana", key=f"confirm_clear_{week_start}"
                ):
                    df_current = st.session_state.get("df", pd.DataFrame()).copy()
                    if not df_current.empty and not np.issubdtype(
                        df_current["WeekStart"].dtype, np.datetime64
                    ):
                        df_current["WeekStart"] = pd.to_datetime(
                            df_current["WeekStart"], errors="coerce"
                        ).dt.date

                    mask = (df_current["UserID"] == user_id) & (
                        df_current["WeekStart"] == week_start
                    )
                    df_current = df_current[~mask].copy()
                    save_user_df(user_id, df_current)
                    set_week_availability(user_id, week_start, [])
                    canonical_week_df.clear()
                    st.session_state["pending_clear_week"] = None
                    st.success("Semana limpa com sucesso.")
                    safe_rerun()

                if col_cancela.button("Cancelar", key=f"cancel_clear_{week_start}"):
                    st.session_state["pending_clear_week"] = None

                st.warning("Esta ação irá remover TODAS as semanas e horários livres do atleta.")
                col_confirm_all, col_cancel_all = st.columns(2)

                if col_confirm_all.button(
                    "Confirmar limpeza total", key="confirm_clear_all"
                ):
                    empty_df = pd.DataFrame(columns=SCHEMA_COLS)
                    save_user_df(user_id, empty_df)
                    clear_all_availability_for_user(user_id)
                    canonical_week_df.clear()
                    st.session_state["pending_clear_week"] = None
                    st.success("Todas as semanas foram removidas para este atleta.")
                    safe_rerun()

                if col_cancel_all.button("Cancelar", key="cancel_clear_all"):
                    st.session_state["pending_clear_week"] = None

        col_pat1, col_pat2 = st.columns(2)
        if col_pat1.button("📌 Capturar padrão de horários desta semana"):
            pattern = extract_time_pattern_from_week(week_df_can)
            save_timepattern_for_user(user_id, pattern)
            st.success("Padrão de horários salvo para este usuário.")

        if col_pat2.button("↩️ Aplicar padrão salvo nesta semana"):
            pattern = load_timepattern_for_user(user_id)
            if not pattern:
                st.warning("Nenhum padrão de horários salvo ainda.")
            else:
                df_current = st.session_state["df"].copy()
                week_start_series = pd.to_datetime(
                    df_current.get("WeekStart"), errors="coerce"
                ).dt.date
                week_mask = (
                    (df_current["UserID"] == user_id)
                    & (week_start_series == week_start)
                )
                week_chunk = df_current[week_mask].copy()

                if week_chunk.empty:
                    st.warning("Nenhum treino encontrado nesta semana para aplicar o padrão.")
                else:
                    week_chunk = realign_week_types_with_pattern(
                        week_chunk, pattern, week_start
                    )
                    week_chunk = apply_time_pattern_to_week(week_chunk, pattern)
                    df_current.loc[week_mask, "Start"] = week_chunk["Start"].values
                    df_current.loc[week_mask, "End"] = week_chunk["End"].values
                    df_current.loc[week_mask, "Data"] = week_chunk["Data"].values
                    df_current.loc[week_mask, "Tipo de Treino"] = week_chunk[
                        "Tipo de Treino"
                    ].values

                    save_user_df(user_id, df_current)
                    canonical_week_df.clear()
                    st.success("Padrão aplicado nesta semana.")
                    safe_rerun()


        # 6. Exportações — usam SEMPRE o df canônico (mesmo do calendário)
        st.subheader("Exportar Semana Atual")

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

        with tab_ciclo:
            render_cycle_planning_tab(user_id, user_preferences=user_preferences)

    # ---------------- RESUMO DO DIA ----------------
    elif menu == "🗓️ Resumo do Dia":
        st.header("🗓️ Resumo do Dia")
        hoje = today()
        st.subheader(hoje.strftime("%A, %d/%m/%Y").title())

        week_start_today = monday_of_week(hoje)
        day_week_df = canonical_week_df(user_id, week_start_today)
        day_df = day_week_df[day_week_df["Data"] == hoje].copy()

        if day_df.empty:
            st.info("Nenhum treino planejado para hoje.")
        else:
            day_flags = _normalize_status_flags(day_df)
            planned_today = int(day_flags["is_planned"].sum())
            realized_today = int(day_flags["is_realized"].sum())
            partial_today = int(day_flags["is_partial"].sum())

            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Sessões planejadas", planned_today)
            col_m2.metric("Concluídas", realized_today)
            col_m3.metric("Parciais", partial_today)

            if "editing_uid" not in st.session_state:
                st.session_state["editing_uid"] = None

            for _, row in day_df.iterrows():
                uid = row["UID"]
                mod = row["Modalidade"]
                tipo = row["Tipo de Treino"]
                status = row.get("Status", "Planejado")
                volume_raw = row.get("Volume", 0)
                try:
                    volume_val = float(volume_raw or 0.0)
                except (TypeError, ValueError):
                    volume_val = 0.0
                unidade = row.get("Unidade", "")
                start_dt = row.get("StartDT")
                end_dt = row.get("EndDT")
                start_str = start_dt.strftime("%H:%M") if isinstance(start_dt, datetime) else "--:--"

                with st.container():
                    st.markdown(f"### {start_str} — {mod} ({tipo})")
                    st.markdown(f"**Status atual:** {status}")
                    if volume_val:
                        st.caption(f"Volume: {volume_val:g} {unidade}")
                    if row.get("Detalhamento"):
                        st.caption(f"Plano: {row['Detalhamento']}")
                    if row.get("Observações"):
                        st.caption(f"Notas: {row['Observações']}")

                    col_feito, col_nao, col_edit = st.columns(3)

                    if col_feito.button("✅ FEITO", key=f"daily_done_{uid}"):
                        if apply_training_updates(user_id, uid, {"Status": "Realizado"}):
                            st.session_state["editing_uid"] = None
                            safe_rerun()

                    if col_nao.button("❌ NÃO FEITO", key=f"daily_cancel_{uid}"):
                        if apply_training_updates(user_id, uid, {"Status": "Cancelado"}):
                            st.session_state["editing_uid"] = None
                            safe_rerun()

                    if col_edit.button("✏️ EDITAR", key=f"daily_edit_{uid}"):
                        st.session_state["editing_uid"] = uid

                    if st.session_state.get("editing_uid") == uid:
                        with st.form(f"daily_edit_form_{uid}"):
                            status_options = STATUS_CHOICES
                            status_clean = status if status in status_options else status_options[0]
                            status_index = status_options.index(status_clean)
                            status_value = st.selectbox(
                                "Status",
                                options=status_options,
                                index=status_index,
                                key=f"daily_status_{uid}",
                            )

                            volume_input = st.number_input(
                                "Volume",
                                min_value=0.0,
                                value=float(volume_val),
                                step=_unit_step(unidade),
                                key=f"daily_volume_{uid}",
                            )

                            obs_input = st.text_area(
                                "Observações",
                                value=row.get("Observações", ""),
                                key=f"daily_obs_{uid}",
                            )

                            start_default = start_dt.time() if isinstance(start_dt, datetime) else time(6, 0)
                            start_time_input = st.time_input(
                                "Horário de início",
                                value=start_default,
                                key=f"daily_start_{uid}",
                            )

                            if isinstance(start_dt, datetime) and isinstance(end_dt, datetime) and end_dt > start_dt:
                                duration_guess = int((end_dt - start_dt).total_seconds() // 60)
                            else:
                                duration_guess = planned_duration_minutes(row)
                            if duration_guess < 15:
                                duration_guess = 15

                            duration_input = st.number_input(
                                "Duração (min)",
                                min_value=15,
                                max_value=600,
                                value=duration_guess,
                                step=5,
                                key=f"daily_duration_{uid}",
                            )

                            submitted = st.form_submit_button("Salvar alterações")
                            if submitted:
                                start_combined = datetime.combine(row["Data"], start_time_input)
                                end_combined = start_combined + timedelta(minutes=int(duration_input))
                                updates = {
                                    "Status": status_value,
                                    "Volume": float(volume_input),
                                    "Observações": obs_input,
                                    "Start": start_combined.isoformat(),
                                    "End": end_combined.isoformat(),
                                    "TempoEstimadoMin": int(duration_input),
                                }
                                if apply_training_updates(user_id, uid, updates):
                                    st.session_state["editing_uid"] = None
                                    safe_rerun()

        st.markdown("---")

        note_key = f"daily_note_{hoje.isoformat()}"
        existing_note = load_daily_note_for_user(user_id, hoje)
        if note_key not in st.session_state:
            st.session_state[note_key] = existing_note
        note_value = st.text_area(
            "Observações gerais do dia",
            value=st.session_state.get(note_key, existing_note),
            key=note_key,
            height=150,
        )
        if st.button("Salvar observações do dia"):
            save_daily_note_for_user(user_id, hoje, note_value)
            st.success("Observações salvas!")
            st.session_state[note_key] = note_value

    # ---------------- DASHBOARD ----------------
    elif menu == "📈 Dashboard":
        st.header("📈 Dashboard de Performance")
        weekly_metrics, df_with_load = calculate_metrics(df)
        metrics_memory = _load_training_loads(user_id)

        df_dashboard = df.copy()
        if not df_dashboard.empty:
            df_dashboard["Data"] = pd.to_datetime(df_dashboard["Data"], errors="coerce").dt.date
            df_dashboard["WeekStart"] = pd.to_datetime(df_dashboard["WeekStart"], errors="coerce").dt.date

        tab_aderencia, tab_carga, tab_historico = st.tabs([
            "Aderência", "Carga", "Histórico de Edição"
        ])

        with tab_aderencia:
            st.subheader("Aderência diária")
            if df_dashboard.empty:
                st.info("Cadastre treinos para visualizar a aderência diária.")
            else:
                available_dates = pd.to_datetime(df_dashboard["Data"], errors="coerce").dropna()
                month_keys = sorted({date(d.year, d.month, 1) for d in available_dates.dt.date}, reverse=True)
                if month_keys:
                    month_labels = [m.strftime("%m/%Y") for m in month_keys]
                    month_map = dict(zip(month_labels, month_keys))
                    current_month = date.today().replace(day=1)
                    default_index = 0
                    if current_month in month_keys:
                        try:
                            default_index = month_labels.index(current_month.strftime("%m/%Y"))
                        except ValueError:
                            default_index = 0
                    selected_label = st.selectbox(
                        "Selecione o mês",
                        month_labels,
                        index=default_index,
                        key="adherence_month_select",
                    )
                    selected_month = month_map[selected_label]
                    heatmap_df, ratio_df = build_daily_adherence_heatmap(df_dashboard, selected_month)
                    if heatmap_df.empty:
                        st.info("Sem treinos planejados para o mês selecionado.")
                    else:
                        styled = heatmap_df.style.apply(make_heatmap_style(ratio_df), axis=None)
                        styled = styled.set_properties(**{"text-align": "center", "white-space": "pre"})
                        st.write(styled)
                        st.caption(
                            "Verde = 100% das sessões concluídas; Amarelo = parcial; Vermelho = não feito."
                        )
                else:
                    st.info("Cadastre treinos para visualizar a aderência diária.")

            if metrics_memory:
                st.markdown("---")
                st.subheader("Carga do atleta (ATL/CTL/TSB)")
                memory_rows = []
                for day_str, vals in metrics_memory.items():
                    memory_rows.append(
                        {
                            "Data": day_str,
                            "TSS": round(float(vals.get("TSS", 0.0) or 0.0), 2),
                            "ATL": round(float(vals.get("ATL", 0.0) or 0.0), 2),
                            "CTL": round(float(vals.get("CTL", 0.0) or 0.0), 2),
                            "TSB": round(float(vals.get("TSB", 0.0) or 0.0), 2),
                        }
                    )

                memory_df = pd.DataFrame(memory_rows)
                memory_df["Data"] = pd.to_datetime(memory_df["Data"], errors="coerce").dt.date
                memory_df = memory_df.dropna(subset=["Data"]).sort_values("Data")

                latest = memory_df.iloc[-1]
                prev = memory_df.iloc[-2] if len(memory_df) > 1 else None
                col_atl, col_ctl, col_tsb = st.columns(3)
                col_atl.metric(
                    "ATL", f"{latest['ATL']:.1f}",
                    delta=(latest["ATL"] - prev["ATL"]) if prev is not None else None,
                    help=f"Última atualização em {latest['Data'].strftime('%d/%m/%Y')}"
                )
                col_ctl.metric(
                    "CTL", f"{latest['CTL']:.1f}",
                    delta=(latest["CTL"] - prev["CTL"]) if prev is not None else None,
                    help=f"Última atualização em {latest['Data'].strftime('%d/%m/%Y')}"
                )
                col_tsb.metric(
                    "TSB", f"{latest['TSB']:.1f}",
                    delta=(latest["TSB"] - prev["TSB"]) if prev is not None else None,
                    help=f"Última atualização em {latest['Data'].strftime('%d/%m/%Y')}"
                )

                with st.expander("Memória de cálculo ATL/CTL/TSB (diário)", expanded=False):
                    st.dataframe(memory_df.sort_values("Data", ascending=False), use_container_width=True)

            st.markdown("---")
            st.subheader("Planilha de aderência semanal")
            adherence_df = compute_weekly_adherence(df_dashboard)
            if adherence_df.empty:
                st.info("Sem dados suficientes para calcular aderência semanal.")
            else:
                st.dataframe(adherence_df, use_container_width=True)
                st.caption("S:% = aderência em sessões. V:% = aderência em volume.")

        with tab_carga:
            plot_load_chart(weekly_metrics)
            st.dataframe(df_with_load)

        with tab_historico:
            if df_dashboard.empty:
                st.info("Sem treinos cadastrados ainda.")
            else:
                week_candidates = pd.to_datetime(df_dashboard["WeekStart"], errors="coerce").dropna().dt.date
                if week_candidates.empty:
                    date_candidates = pd.to_datetime(df_dashboard["Data"], errors="coerce").dropna().dt.date
                    week_options = sorted({monday_of_week(d) for d in date_candidates}, reverse=True)
                else:
                    week_options = sorted(set(week_candidates), reverse=True)

                if not week_options:
                    st.info("Sem semanas com alterações registradas.")
                else:
                    week_labels = [ws.strftime("%d/%m/%Y") for ws in week_options]
                    week_map = dict(zip(week_labels, week_options))
                    selected_week_label = st.selectbox(
                        "Semana",
                        week_labels,
                        index=0,
                        key="history_week_select",
                    )
                    selected_week = week_map[selected_week_label]

                    events = build_week_changelog(df_dashboard, selected_week)
                    if not events:
                        st.info("Nenhuma alteração registrada para a semana selecionada.")
                    else:
                        for event in events:
                            title = event["training"]
                            if event["timestamp_str"]:
                                title = f"{event['timestamp_str']} — {title}"
                            with st.expander(title, expanded=False):
                                if event["changes"]:
                                    for change in event["changes"]:
                                        st.markdown(f"- {change}")
                                else:
                                    st.caption("Alteração registrada sem detalhes adicionais.")

                    st.markdown("---")
                    week_df = week_slice(df_dashboard, selected_week)
                    if week_df.empty:
                        st.info("Nenhum treino encontrado na semana selecionada.")
                    else:
                        training_options = [
                            f"{r['Data'].strftime('%d/%m')} — {r['Modalidade']} ({r['Tipo de Treino']})"
                            for _, r in week_df.iterrows()
                        ]
                        training_map = dict(zip(training_options, week_df.index))
                        selected_training_label = st.selectbox(
                            "Treino",
                            training_options,
                            key="history_training_select",
                        )
                        selected_training = week_df.loc[training_map[selected_training_label]]
                        training_log = extract_training_changelog(selected_training)
                        if not training_log:
                            st.info("Este treino ainda não possui alterações registradas.")
                        else:
                            for entry in reversed(training_log):
                                st.markdown(f"**{entry['timestamp_str'] or 'Sem horário'}**")
                                if entry["changes"]:
                                    for change in entry["changes"]:
                                        st.markdown(f"- {change}")
                                else:
                                    st.caption("Alteração sem detalhes adicionais.")

    elif menu == "🚴 Strava":
        render_strava_tab(user_id)

    
if __name__ == "__main__":
    main()
