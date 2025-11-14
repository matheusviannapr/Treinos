"""Migrate existing SQLite data into the Neon Postgres database."""

import os
import sqlite3

import pandas as pd

import db

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "treinos.sqlite")


def load_sqlite_dataframe(path: str, table: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"SQLite database not found at {path}")
    with sqlite3.connect(path) as conn:
        return pd.read_sql(f"SELECT * FROM {table}", conn)


def migrate_table(df: pd.DataFrame, table: str) -> None:
    if df.empty:
        return
    records = df.to_dict(orient="records")
    if table == "users":
        db.execute_many(
            """
            INSERT INTO users (user_id, nome, created_at)
            VALUES (:user_id, :nome, :created_at)
            ON CONFLICT (user_id) DO UPDATE SET
                nome = EXCLUDED.nome,
                created_at = EXCLUDED.created_at
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
    elif table == "treinos":
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
                    "user_id": rec.get("UserID", ""),
                    "uid": rec.get("UID", ""),
                    "data": rec.get("Data"),
                    "start": rec.get("Start"),
                    "end": rec.get("End"),
                    "modalidade": rec.get("Modalidade", ""),
                    "tipo_treino": rec.get("Tipo de Treino", ""),
                    "volume": rec.get("Volume"),
                    "unidade": rec.get("Unidade", ""),
                    "rpe": rec.get("RPE"),
                    "detalhamento": rec.get("Detalhamento", ""),
                    "observacoes": rec.get("Observações", ""),
                    "status": rec.get("Status", ""),
                    "adj": rec.get("adj"),
                    "adj_applied_at": rec.get("AdjAppliedAt", ""),
                    "changelog": rec.get("ChangeLog", ""),
                    "last_edited_at": rec.get("LastEditedAt", ""),
                    "week_start": rec.get("WeekStart"),
                }
                for rec in records
            ],
        )
    else:
        raise ValueError(f"Unsupported table: {table}")


def main():
    db.init_db()
    for table in ["users", "treinos"]:
        df = load_sqlite_dataframe(SQLITE_PATH, table)
        migrate_table(df, table)
    print("Migration completed successfully.")


if __name__ == "__main__":
    main()
