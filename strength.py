"""Utilities to manage strength training splits, workouts and exercises."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

import pandas as pd

import db

DEFAULT_MUSCLE_GROUPS = [
    "Peito",
    "Costas",
    "Pernas",
    "Ombro",
    "Bíceps",
    "Tríceps",
    "Glúteos",
    "Core",
    "Full Body",
    "Cardio",
]


def list_splits(user_id: str) -> pd.DataFrame:
    sql = (
        "SELECT id, user_id, nome_split, descricao, data_criacao, data_atualizacao, ativo "
        "FROM strength_splits WHERE user_id = :user_id "
        "ORDER BY ativo DESC, data_atualizacao DESC, data_criacao DESC"
    )
    df = db.fetch_dataframe(sql, {"user_id": user_id})
    return df.fillna("") if not df.empty else pd.DataFrame(
        columns=["id", "user_id", "nome_split", "descricao", "data_criacao", "data_atualizacao", "ativo"]
    )


def create_split(user_id: str, nome: str, descricao: str = "", set_active: bool = True) -> Optional[int]:
    params = {
        "user_id": user_id,
        "nome_split": nome,
        "descricao": descricao,
        "data_atualizacao": datetime.now(),
    }
    row = db.fetch_one(
        """
        INSERT INTO strength_splits (user_id, nome_split, descricao, data_atualizacao)
        VALUES (:user_id, :nome_split, :descricao, :data_atualizacao)
        RETURNING id
        """,
        params,
    )
    if not row:
        return None
    split_id = row.get("id")
    if set_active and split_id:
        set_active_split(user_id, split_id)
    return split_id


def update_split(user_id: str, split_id: int, nome: str, descricao: str) -> None:
    db.execute(
        """
        UPDATE strength_splits
        SET nome_split = :nome_split,
            descricao = :descricao,
            data_atualizacao = :data_atualizacao
        WHERE id = :split_id AND user_id = :user_id
        """,
        {
            "nome_split": nome,
            "descricao": descricao,
            "data_atualizacao": datetime.now(),
            "split_id": split_id,
            "user_id": user_id,
        },
    )


def set_active_split(user_id: str, split_id: int) -> None:
    db.execute(
        "UPDATE strength_splits SET ativo = FALSE WHERE user_id = :user_id",
        {"user_id": user_id},
    )
    db.execute(
        """
        UPDATE strength_splits
        SET ativo = TRUE, data_atualizacao = :data_atualizacao
        WHERE id = :split_id AND user_id = :user_id
        """,
        {
            "split_id": split_id,
            "user_id": user_id,
            "data_atualizacao": datetime.now(),
        },
    )


def delete_split(user_id: str, split_id: int) -> None:
    db.execute(
        "DELETE FROM strength_splits WHERE id = :split_id AND user_id = :user_id",
        {"split_id": split_id, "user_id": user_id},
    )


def get_active_split(user_id: str) -> Optional[dict]:
    row = db.fetch_one(
        "SELECT * FROM strength_splits WHERE user_id = :user_id AND ativo = TRUE LIMIT 1",
        {"user_id": user_id},
    )
    return row


def list_workouts(user_id: str, split_id: int) -> pd.DataFrame:
    sql = (
        "SELECT w.id, w.split_id, w.nome_treino_letra, w.ordem "
        "FROM strength_workouts w "
        "JOIN strength_splits s ON s.id = w.split_id "
        "WHERE s.user_id = :user_id AND s.id = :split_id "
        "ORDER BY w.ordem, w.id"
    )
    df = db.fetch_dataframe(sql, {"user_id": user_id, "split_id": split_id})
    if df.empty:
        return pd.DataFrame(columns=["id", "split_id", "nome_treino_letra", "ordem"])
    return df


def list_exercises(user_id: str, workout_id: int) -> pd.DataFrame:
    sql = (
        "SELECT e.id, e.workout_id, e.grupo_muscular, e.nome_exercicio, e.series, e.repeticoes, "
        "e.carga, e.intervalo, e.observacoes, e.ordem "
        "FROM strength_exercises e "
        "JOIN strength_workouts w ON w.id = e.workout_id "
        "JOIN strength_splits s ON s.id = w.split_id "
        "WHERE s.user_id = :user_id AND w.id = :workout_id "
        "ORDER BY e.ordem, e.id"
    )
    df = db.fetch_dataframe(sql, {"user_id": user_id, "workout_id": workout_id})
    if df.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "workout_id",
                "grupo_muscular",
                "nome_exercicio",
                "series",
                "repeticoes",
                "carga",
                "intervalo",
                "observacoes",
                "ordem",
            ]
        )
    return df


def _clean_id(value) -> Optional[int]:
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def save_workouts(user_id: str, split_id: int, workouts: Iterable[dict]) -> list[int]:
    split_row = db.fetch_one(
        "SELECT id FROM strength_splits WHERE id = :split_id AND user_id = :user_id",
        {"split_id": split_id, "user_id": user_id},
    )
    if not split_row:
        return []

    existing_df = list_workouts(user_id, split_id)
    existing_ids = set(existing_df["id"].tolist()) if not existing_df.empty else set()
    saved_ids: list[int] = []

    for order, w in enumerate(workouts):
        nome = (w.get("nome_treino_letra") or "").strip()
        if not nome:
            continue
        workout_id = _clean_id(w.get("id"))
        params = {
            "split_id": split_id,
            "nome_treino_letra": nome,
            "ordem": order,
        }
        if workout_id:
            db.execute(
                """
                UPDATE strength_workouts
                SET nome_treino_letra = :nome_treino_letra, ordem = :ordem
                WHERE id = :workout_id AND split_id = :split_id
                """,
                {**params, "workout_id": workout_id},
            )
            saved_ids.append(workout_id)
        else:
            row = db.fetch_one(
                """
                INSERT INTO strength_workouts (split_id, nome_treino_letra, ordem)
                VALUES (:split_id, :nome_treino_letra, :ordem)
                RETURNING id
                """,
                params,
            )
            if row and row.get("id"):
                saved_ids.append(int(row["id"]))

    to_delete = existing_ids - set(saved_ids)
    if to_delete:
        db.execute_many(
            "DELETE FROM strength_workouts WHERE id = :id",
            [{"id": del_id} for del_id in to_delete],
        )

    db.execute(
        "UPDATE strength_splits SET data_atualizacao = :dt WHERE id = :split_id",
        {"dt": datetime.now(), "split_id": split_id},
    )

    return saved_ids


def save_exercises(user_id: str, workout_id: int, exercises: Iterable[dict]) -> list[int]:
    workout_row = db.fetch_one(
        """
        SELECT w.id
        FROM strength_workouts w
        JOIN strength_splits s ON s.id = w.split_id
        WHERE w.id = :workout_id AND s.user_id = :user_id
        LIMIT 1
        """,
        {"workout_id": workout_id, "user_id": user_id},
    )
    if not workout_row:
        return []

    existing_df = list_exercises(user_id, workout_id)
    existing_ids = set(existing_df["id"].tolist()) if not existing_df.empty else set()
    saved_ids: list[int] = []

    for order, ex in enumerate(exercises):
        nome = (ex.get("nome_exercicio") or "").strip()
        grupo = (ex.get("grupo_muscular") or "").strip()
        if not nome and not grupo:
            continue
        exercise_id = _clean_id(ex.get("id"))
        params = {
            "workout_id": workout_id,
            "grupo_muscular": grupo,
            "nome_exercicio": nome,
            "series": (ex.get("series") or "").strip(),
            "repeticoes": (ex.get("repeticoes") or "").strip(),
            "carga": (ex.get("carga") or "").strip(),
            "intervalo": str(ex.get("intervalo") or "").strip(),
            "observacoes": (ex.get("observacoes") or "").strip(),
            "ordem": order,
        }
        if exercise_id:
            db.execute(
                """
                UPDATE strength_exercises
                SET grupo_muscular = :grupo_muscular,
                    nome_exercicio = :nome_exercicio,
                    series = :series,
                    repeticoes = :repeticoes,
                    carga = :carga,
                    intervalo = :intervalo,
                    observacoes = :observacoes,
                    ordem = :ordem
                WHERE id = :exercise_id AND workout_id = :workout_id
                """,
                {**params, "exercise_id": exercise_id},
            )
            saved_ids.append(exercise_id)
        else:
            row = db.fetch_one(
                """
                INSERT INTO strength_exercises (
                    workout_id, grupo_muscular, nome_exercicio, series, repeticoes, carga,
                    intervalo, observacoes, ordem
                )
                VALUES (
                    :workout_id, :grupo_muscular, :nome_exercicio, :series, :repeticoes,
                    :carga, :intervalo, :observacoes, :ordem
                )
                RETURNING id
                """,
                params,
            )
            if row and row.get("id"):
                saved_ids.append(int(row["id"]))

    to_delete = existing_ids - set(saved_ids)
    if to_delete:
        db.execute_many(
            "DELETE FROM strength_exercises WHERE id = :id",
            [{"id": del_id} for del_id in to_delete],
        )

    db.execute(
        """
        UPDATE strength_workouts
        SET ordem = ordem
        WHERE id = :workout_id
        """,
        {"workout_id": workout_id},
    )

    return saved_ids
