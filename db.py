import os
from collections.abc import Mapping
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv()

DATABASE_URL = st.secrets["db"]["url"]

def _extract_url_from_mapping(mapping: Mapping[str, Any]) -> str | None:
    """Extract a database URL from common keys in the provided mapping."""

    # Support nested [db] section like the one used by Streamlit secrets.
    db_section = mapping.get("db")
    if isinstance(db_section, Mapping):
        url = db_section.get("url") or db_section.get("DATABASE_URL")
        if isinstance(url, str) and url:
            return url

    # Fall back to top-level keys for flexibility.
    for key in ("DATABASE_URL", "url"):
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value

    return None


def _get_database_url() -> str:
    """Return the database URL from Streamlit secrets or environment variables."""

    secrets_url = None
    try:
        secrets = getattr(st, "secrets", None)  # type: ignore[attr-defined]
        if isinstance(secrets, Mapping):
            secrets_url = _extract_url_from_mapping(secrets)
    except Exception:
        # st.secrets may not be available outside Streamlit runtime
        secrets_url = None

    env_url = os.getenv("DATABASE_URL")

    url = secrets_url or env_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Set it in .env for local "
            "development or in Streamlit secrets under [db]."
        )
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return a cached SQLAlchemy engine."""
    url = _get_database_url()
    return create_engine(url, pool_pre_ping=True, future=True)


@contextmanager
def get_connection():
    """Yield a transactional connection."""
    engine = get_engine()
    with engine.begin() as conn:
        yield conn


def init_db() -> None:
    """Ensure all required tables exist."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    nome TEXT,
                    created_at TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS treinos (
                    "UserID" TEXT NOT NULL,
                    "UID" TEXT PRIMARY KEY,
                    "Data" DATE,
                    "Start" TIMESTAMP,
                    "End" TIMESTAMP,
                    "Modalidade" TEXT,
                    "Tipo de Treino" TEXT,
                    "Volume" NUMERIC,
                    "Unidade" TEXT,
                    "RPE" NUMERIC,
                    "Detalhamento" TEXT,
                    "Observações" TEXT,
                    "Status" TEXT,
                    "adj" NUMERIC,
                    "AdjAppliedAt" TEXT,
                    "ChangeLog" TEXT,
                    "LastEditedAt" TEXT,
                    "WeekStart" DATE
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS availability (
                    "UserID" TEXT NOT NULL,
                    "WeekStart" DATE NOT NULL,
                    "Start" TIMESTAMP,
                    "End" TIMESTAMP,
                    PRIMARY KEY ("UserID", "WeekStart", "Start", "End")
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS time_patterns (
                    "UserID" TEXT PRIMARY KEY,
                    "PatternJSON" TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    "UserID" TEXT PRIMARY KEY,
                    "PreferencesJSON" TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS daily_notes (
                    "UserID" TEXT NOT NULL,
                    "Date" DATE NOT NULL,
                    "Note" TEXT,
                    "UpdatedAt" TEXT,
                    PRIMARY KEY ("UserID", "Date")
                )
                """
            )
        )


def execute(sql: str, params: dict | None = None) -> None:
    statement = text(sql)
    with get_connection() as conn:
        conn.execute(statement, params or {})


def execute_many(sql: str, params_seq: list[dict]) -> None:
    if not params_seq:
        return
    statement = text(sql)
    with get_connection() as conn:
        conn.execute(statement, params_seq)


def fetch_one(sql: str, params: dict | None = None) -> dict | None:
    engine = get_engine()
    statement = text(sql)
    with engine.connect() as conn:
        result = conn.execute(statement, params or {})
        row = result.mappings().first()
        return dict(row) if row else None


def fetch_all(sql: str, params: dict | None = None) -> list[dict]:
    engine = get_engine()
    statement = text(sql)
    with engine.connect() as conn:
        result = conn.execute(statement, params or {})
        return [dict(row) for row in result.mappings().all()]


def fetch_dataframe(sql: str, params: dict | None = None) -> pd.DataFrame:
    engine = get_engine()
    statement = text(sql)
    with engine.connect() as conn:
        df = pd.read_sql_query(statement, conn, params=params)
    return df


def salvar_treino(data, modalidade, volume, unidade, observacoes=""):
    execute(
        """
        INSERT INTO treinos
            ("UserID", "UID", "Data", "Modalidade", "Volume", "Unidade", "Observações", "Status", "ChangeLog", "WeekStart")
        VALUES
            (:user_id, :uid, :data, :modalidade, :volume, :unidade, :observacoes, :status, :changelog, :week_start)
        """,
        {
            "user_id": "default",
            "uid": f"manual-{os.urandom(8).hex()}",
            "data": data,
            "modalidade": modalidade,
            "volume": volume,
            "unidade": unidade,
            "observacoes": observacoes,
            "status": "Planejado",
            "changelog": "[]",
            "week_start": data,
        },
    )


def carregar_treinos() -> pd.DataFrame:
    return fetch_dataframe(
        "SELECT * FROM treinos ORDER BY \"Data\" DESC, \"UID\" DESC"
    )
