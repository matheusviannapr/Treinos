"""Streamlit app to manage gym training sheets with editing and PDF export."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
from fpdf import FPDF

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "fichas.json")
EXPORT_DIR = os.path.join(BASE_DIR, "exports")


EXERCICIOS_CLASSICOS: Dict[str, List[str]] = {
    "Peito": [
        "Supino reto com barra",
        "Supino reto com halteres",
        "Supino inclinado com barra",
        "Supino inclinado com halteres",
        "Crucifixo reto com halteres",
        "Crucifixo inclinado com halteres",
        "Peck deck",
        "Flexão de braço no solo",
    ],
    "Costas": [
        "Puxada frente na barra fixa (pegada pronada)",
        "Puxada frente na barra fixa (pegada supinada)",
        "Puxada frente na polia",
        "Remada curvada com barra",
        "Remada unilateral com halter",
        "Remada baixa na polia",
        "Levantamento terra clássico",
    ],
    "Ombros": [
        "Desenvolvimento militar com barra",
        "Desenvolvimento com halteres",
        "Elevação lateral com halteres",
        "Elevação frontal com halteres",
        "Remada alta com barra",
        "Crucifixo invertido (voador inverso)",
    ],
    "Bíceps": [
        "Rosca direta com barra",
        "Rosca alternada com halteres",
        "Rosca martelo com halteres",
        "Rosca concentrada",
        "Rosca na barra fixa (pegada supinada)",
    ],
    "Tríceps": [
        "Tríceps testa com barra",
        "Tríceps na polia (barra ou corda)",
        "Mergulho em paralelas",
        "Tríceps banco",
        "Tríceps francês com halter",
    ],
    "Pernas": [
        "Agachamento livre com barra",
        "Agachamento no smith",
        "Leg press 45°",
        "Cadeira extensora",
        "Mesa flexora",
        "Cadeira flexora",
        "Afundo com halteres",
        "Passada (lunge) com halteres",
    ],
    "Glúteos": [
        "Levantamento terra romeno",
        "Elevação pélvica com barra (hip thrust)",
        "Avanço (lunge) para trás",
        "Agachamento búlgaro",
        "Subida no banco com halteres",
    ],
    "Core": [
        "Prancha isométrica",
        "Prancha lateral",
        "Abdominal crunch no solo",
        "Elevação de pernas pendurado",
        "Abdominal infra no banco",
        "Abdominal na máquina",
    ],
    "Aeróbico": [
        "Esteira",
        "Bicicleta ergométrica",
        "Elíptico",
        "Escada",
        "Corda de pular",
    ],
}


SUGGESTED_TREINOS: List[Dict[str, Any]] = [
    {
        "nome": "Treino 1 – Peito e Costas (Superséries)",
        "exercicios": [
            {"grupo": "Peito", "exercicio": "Supino Reto com Barra", "series": 4, "reps": "8–10"},
            {"grupo": "Costas", "exercicio": "Remada Curvada Barra", "series": 4, "reps": "8–10"},
            {
                "grupo": "Peito",
                "exercicio": "Supino Inclinado com Halteres",
                "series": 3,
                "reps": "10–12",
            },
            {"grupo": "Costas", "exercicio": "Puxada Frente Aberta", "series": 3, "reps": "10–12"},
            {"grupo": "Peito", "exercicio": "Crossover Polia Alta", "series": 3, "reps": "12–15"},
            {"grupo": "Costas", "exercicio": "Pulldown Polia", "series": 3, "reps": "12–15"},
        ],
    },
    {
        "nome": "Treino 2 – Pernas Completo",
        "exercicios": [
            {"grupo": "Pernas", "exercicio": "Agachamento Livre", "series": 4, "reps": "6–8"},
            {"grupo": "Pernas", "exercicio": "Leg Press", "series": 4, "reps": "10–12"},
            {"grupo": "Pernas", "exercicio": "Cadeira Extensora", "series": 3, "reps": "12–15"},
            {"grupo": "Pernas", "exercicio": "Mesa Flexora", "series": 3, "reps": "10–12"},
            {"grupo": "Pernas", "exercicio": "Panturrilha em Pé", "series": 4, "reps": "15–20"},
            {"grupo": "Core", "exercicio": "Abdominal Infra", "series": 3, "reps": "20–30"},
        ],
    },
    {
        "nome": "Treino 3 – Push (Peito, Ombro, Tríceps)",
        "exercicios": [
            {
                "grupo": "Peito",
                "exercicio": "Supino Reto com Halteres",
                "series": 4,
                "reps": "8–12",
            },
            {"grupo": "Peito", "exercicio": "Crucifixo no Banco", "series": 3, "reps": "10–12"},
            {
                "grupo": "Ombros",
                "exercicio": "Desenvolvimento com Halteres",
                "series": 4,
                "reps": "8–12",
            },
            {"grupo": "Ombros", "exercicio": "Elevação Lateral", "series": 3, "reps": "12–15"},
            {"grupo": "Tríceps", "exercicio": "Tríceps Polia Alta", "series": 3, "reps": "10–12"},
            {"grupo": "Tríceps", "exercicio": "Mergulho Paralelas", "series": 3, "reps": "Falha controlada"},
        ],
    },
    {
        "nome": "Treino 4 – Pull (Costas e Bíceps)",
        "exercicios": [
            {"grupo": "Costas", "exercicio": "Puxada Neutra", "series": 4, "reps": "8–12"},
            {"grupo": "Costas", "exercicio": "Remada Unilateral Halter", "series": 3, "reps": "10–12"},
            {"grupo": "Costas", "exercicio": "Remada Baixa Máquina", "series": 3, "reps": "12–15"},
            {"grupo": "Bíceps", "exercicio": "Rosca Direta Barra", "series": 4, "reps": "8–12"},
            {"grupo": "Bíceps", "exercicio": "Rosca Alternada", "series": 3, "reps": "10–12"},
            {"grupo": "Bíceps", "exercicio": "Rosca Concentrada", "series": 3, "reps": "12–15"},
        ],
    },
    {
        "nome": "Treino 5 – Full Body (três vezes por semana)",
        "exercicios": [
            {"grupo": "Pernas", "exercicio": "Agachamento Livre", "series": 4, "reps": "6–10"},
            {"grupo": "Peito", "exercicio": "Supino Inclinado com Barra", "series": 4, "reps": "8–12"},
            {"grupo": "Costas", "exercicio": "Remada Curvada Barra", "series": 4, "reps": "8–10"},
            {"grupo": "Ombros", "exercicio": "Elevação Frontal", "series": 3, "reps": "12–15"},
            {"grupo": "Bíceps", "exercicio": "Rosca Martelo", "series": 3, "reps": "10–12"},
            {"grupo": "Tríceps", "exercicio": "Tríceps Testa", "series": 3, "reps": "10–12"},
            {"grupo": "Core", "exercicio": "Prancha", "series": 3, "reps": "45–60s"},
        ],
    },
    {
        "nome": "Treino 6 – Pernas (ênfase em posterior)",
        "exercicios": [
            {"grupo": "Pernas", "exercicio": "Stiff", "series": 4, "reps": "8–12"},
            {"grupo": "Pernas", "exercicio": "Agachamento Barra", "series": 4, "reps": "5–8"},
            {"grupo": "Pernas", "exercicio": "Mesa Flexora", "series": 3, "reps": "10–12"},
            {"grupo": "Pernas", "exercicio": "Hack Machine", "series": 3, "reps": "10–12"},
            {"grupo": "Pernas", "exercicio": "Panturrilha Sentado", "series": 4, "reps": "12–20"},
            {"grupo": "Core", "exercicio": "Abdominal Máquina", "series": 3, "reps": "15–20"},
        ],
    },
    {
        "nome": "Treino 7 – Ombro e Braços",
        "exercicios": [
            {"grupo": "Ombros", "exercicio": "Desenvolvimento com Barra", "series": 4, "reps": "6–10"},
            {"grupo": "Ombros", "exercicio": "Elevação Lateral", "series": 3, "reps": "12–15"},
            {"grupo": "Ombros", "exercicio": "Crucifixo Inverso", "series": 3, "reps": "12–15"},
            {"grupo": "Bíceps", "exercicio": "Rosca Scott", "series": 4, "reps": "8–12"},
            {"grupo": "Tríceps", "exercicio": "Tríceps Francês", "series": 4, "reps": "8–12"},
            {"grupo": "Bíceps", "exercicio": "Rosca Concentrada", "series": 3, "reps": "12–15"},
        ],
    },
    {
        "nome": "Treino 8 – Peito com ênfase em halteres",
        "exercicios": [
            {"grupo": "Peito", "exercicio": "Supino Reto com Halteres", "series": 4, "reps": "8–12"},
            {"grupo": "Peito", "exercicio": "Supino Inclinado com Halteres", "series": 4, "reps": "10–12"},
            {
                "grupo": "Peito",
                "exercicio": "Crucifixo Máquina (Peck Deck)",
                "series": 3,
                "reps": "12–15",
            },
            {"grupo": "Ombros", "exercicio": "Remada Alta", "series": 3, "reps": "10–12"},
            {"grupo": "Tríceps", "exercicio": "Tríceps Corda", "series": 3, "reps": "12–15"},
            {"grupo": "Core", "exercicio": "Elevação de Pernas", "series": 3, "reps": "15–20"},
        ],
    },
    {
        "nome": "Treino 9 – Corpo inteiro com peso corporal (funcional)",
        "exercicios": [
            {"grupo": "Peito", "exercicio": "Flexão de Braço", "series": 4, "reps": "15–20"},
            {"grupo": "Costas", "exercicio": "Barra Fixa", "series": 3, "reps": "6–10"},
            {"grupo": "Pernas", "exercicio": "Agachamento Livre", "series": 4, "reps": "12–15"},
            {"grupo": "Pernas", "exercicio": "Avanço (lunge)", "series": 3, "reps": "10–12 por perna"},
            {"grupo": "Ombros", "exercicio": "Elevação Lateral com elástico", "series": 3, "reps": "15–20"},
            {"grupo": "Core", "exercicio": "Prancha", "series": 3, "reps": "60s"},
        ],
    },
    {
        "nome": "Treino 10 – Core e Estabilidade",
        "exercicios": [
            {"grupo": "Core", "exercicio": "Prancha", "series": 4, "reps": "60s"},
            {"grupo": "Core", "exercicio": "Elevação de Pernas", "series": 3, "reps": "15–20"},
            {"grupo": "Core", "exercicio": "Abdominal Solo", "series": 4, "reps": "20–30"},
            {"grupo": "Core", "exercicio": "Abdominal Infra", "series": 3, "reps": "15–20"},
            {"grupo": "Core", "exercicio": "Prancha Lateral", "series": 3, "reps": "45s cada lado"},
            {"grupo": "Core", "exercicio": "Abdominal Máquina", "series": 3, "reps": "15–20"},
        ],
    },
]


def load_fichas(path: str = DATA_PATH) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            return data.get("fichas", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_fichas(fichas: List[Dict[str, Any]], path: str = DATA_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"fichas": fichas, "last_saved_at": datetime.utcnow().isoformat()}
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def get_ficha_by_nome(fichas: List[Dict[str, Any]], nome_ficha: str) -> Optional[Dict[str, Any]]:
    for ficha in fichas:
        if ficha.get("nome_ficha") == nome_ficha:
            return ficha
    return None


def ficha_to_dataframe(ficha: Dict[str, Any]) -> pd.DataFrame:
    exercicios = ficha.get("exercicios", [])
    if not exercicios:
        return pd.DataFrame(
            [
                {
                    "ordem": 1,
                    "grupo_muscular": "",
                    "exercicio": "",
                    "series": 3,
                    "repeticoes": "8-10",
                    "carga_observacao": "",
                    "descanso_s": 60,
                }
            ]
        )
    df = pd.DataFrame(exercicios)
    return df.sort_values(by=["ordem", "grupo_muscular", "exercicio"], na_position="last").reset_index(drop=True)


def dataframe_to_exercicios(df: pd.DataFrame) -> List[Dict[str, Any]]:
    clean_df = df.fillna("")
    clean_df["ordem"] = pd.to_numeric(clean_df.get("ordem", 0), errors="coerce").fillna(0).astype(int)
    clean_df["series"] = pd.to_numeric(clean_df.get("series", 0), errors="coerce").fillna(0).astype(int)
    clean_df["descanso_s"] = (
        pd.to_numeric(clean_df.get("descanso_s", 0), errors="coerce").fillna(0).astype(int)
    )
    clean_df = clean_df.sort_values(by="ordem").reset_index(drop=True)
    return clean_df.to_dict(orient="records")


def ensure_exports_dir() -> None:
    os.makedirs(EXPORT_DIR, exist_ok=True)


def _pdf_safe(text: Any) -> str:
    return str(text or "").encode("latin-1", "ignore").decode("latin-1")


def _render_ficha_table(pdf: FPDF, exercicios: List[Dict[str, Any]]) -> None:
    headers = [
        ("Ordem", 12),
        ("Grupo muscular", 38),
        ("Exercício", 52),
        ("Séries", 15),
        ("Repetições", 22),
        ("Carga/Obs", 38),
        ("Descanso (s)", 23),
    ]
    pdf.set_font("Helvetica", "B", 10)
    for title, width in headers:
        pdf.cell(width, 8, _pdf_safe(title), 1, 0, "C")
    pdf.ln()

    pdf.set_font("Helvetica", size=9)
    for ex in exercicios:
        row = [
            ex.get("ordem", ""),
            ex.get("grupo_muscular", ""),
            ex.get("exercicio", ""),
            ex.get("series", ""),
            ex.get("repeticoes", ""),
            ex.get("carga_observacao", ""),
            ex.get("descanso_s", ""),
        ]
        widths = [w for _, w in headers]
        for value, width in zip(row, widths):
            pdf.cell(width, 8, _pdf_safe(value), 1, 0, "C")
        pdf.ln()


def export_ficha_pdf(ficha: Dict[str, Any]) -> str:
    ensure_exports_dir()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _pdf_safe(f"Ficha de Treino – {ficha.get('nome_ficha', '')}"), ln=True)
    pdf.set_font("Helvetica", size=11)
    identificador = ficha.get("identificador", "")
    if identificador:
        pdf.cell(0, 8, _pdf_safe(f"Identificador: {identificador}"), ln=True)
    pdf.ln(4)
    _render_ficha_table(pdf, ficha.get("exercicios", []))

    filename = f"ficha_{ficha.get('nome_ficha', 'treino')}.pdf"
    path = os.path.join(EXPORT_DIR, filename)
    pdf.output(path)
    return path


def export_ciclo_pdf(fichas: List[Dict[str, Any]]) -> str:
    ensure_exports_dir()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _pdf_safe("Ciclo de Treino – Fichas A, B, C"), ln=True)
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, _pdf_safe("Resumo do ciclo com fichas combinadas"), ln=True)
    pdf.ln(6)

    for ficha in fichas:
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 9, _pdf_safe(f"Ficha: {ficha.get('nome_ficha', '')}"), ln=True)
        pdf.set_font("Helvetica", size=11)
        ident = ficha.get("identificador")
        if ident:
            pdf.cell(0, 7, _pdf_safe(f"Identificador: {ident}"), ln=True)
        pdf.ln(2)
        _render_ficha_table(pdf, ficha.get("exercicios", []))
        pdf.ln(8)

    filename = "ciclo_fichas_ABC.pdf"
    path = os.path.join(EXPORT_DIR, filename)
    pdf.output(path)
    return path


def add_empty_row(df: pd.DataFrame) -> pd.DataFrame:
    new_row = {
        "ordem": (df["ordem"].max() + 1) if not df.empty else 1,
        "grupo_muscular": "",
        "exercicio": "",
        "series": 3,
        "repeticoes": "8-10",
        "carga_observacao": "",
        "descanso_s": 60,
    }
    return pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)


def _normalize_grupo(grupo: str) -> str:
    mapping = {"Ombro": "Ombros", "Ombros": "Ombros"}
    return mapping.get(grupo, grupo)


def suggestion_to_exercicios(exercicios_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    exercicios: List[Dict[str, Any]] = []
    for idx, ex in enumerate(exercicios_raw, start=1):
        exercicios.append(
            {
                "ordem": idx,
                "grupo_muscular": _normalize_grupo(ex.get("grupo", "")),
                "exercicio": ex.get("exercicio", ""),
                "series": ex.get("series", 0),
                "repeticoes": ex.get("reps", ""),
                "carga_observacao": "",
                "descanso_s": 60,
            }
        )
    return exercicios


def apply_suggestion_to_block(
    fichas: List[Dict[str, Any]], bloco: str, exercicios: List[Dict[str, Any]]
) -> tuple[Dict[str, Any], bool]:
    identificador = str(bloco).upper()
    target = next(
        (f for f in fichas if str(f.get("identificador", "")).upper() == identificador), None
    )
    created = False
    if not target:
        target = {
            "id": str(uuid.uuid4()),
            "nome_ficha": f"Ficha {identificador}",
            "identificador": identificador,
            "exercicios": [],
        }
        fichas.append(target)
        created = True

    target["exercicios"] = exercicios
    save_fichas(fichas)
    return target, created


def sidebar_selection(fichas: List[Dict[str, Any]]) -> tuple[str, Optional[str], Optional[str]]:
    st.sidebar.header("Fichas de treino")
    existing_names = [f.get("nome_ficha") for f in fichas]
    selection = st.sidebar.selectbox(
        "Selecione uma ficha de treino", existing_names + ["Criar nova ficha"]
    )

    new_name: Optional[str] = None
    new_identificador: Optional[str] = None
    if selection == "Criar nova ficha":
        new_name = st.sidebar.text_input("Nome da nova ficha (ex.: Ficha A, Ficha B, Full Body)")
        identificadores = ["A", "B", "C", "D", "Outro"]
        ident_choice = st.sidebar.selectbox("Letra/identificador da ficha", identificadores)
        new_identificador = ident_choice if ident_choice != "Outro" else st.sidebar.text_input("Identificador personalizado")
        if st.sidebar.button("Confirmar criação da ficha"):
            if not new_name:
                st.sidebar.error("Dê um nome para a nova ficha.")
            else:
                ficha = {
                    "id": str(uuid.uuid4()),
                    "nome_ficha": new_name,
                    "identificador": new_identificador,
                    "exercicios": [],
                }
                fichas.append(ficha)
                save_fichas(fichas)
                st.sidebar.success("Ficha criada! Selecione-a na lista.")
    return selection, new_name, new_identificador


def main() -> None:
    st.set_page_config(page_title="Gerador de Fichas de Treino", layout="wide")
    st.title("Gerador de Fichas de Treino")
    st.write("Crie, edite, visualize e exporte suas fichas de treino de forma simples.")

    fichas = load_fichas()
    selection, _, _ = sidebar_selection(fichas)

    if selection == "Criar nova ficha":
        st.info("Crie a ficha na barra lateral e selecione-a para começar a editar.")
        return

    ficha = get_ficha_by_nome(fichas, selection) if selection else None
    if not ficha:
        st.warning("Selecione ou crie uma ficha para começar.")
        return

    st.subheader(f"Ficha: {ficha.get('nome_ficha', '')}")

    df = ficha_to_dataframe(ficha)
    table_key = f"table_{ficha['id']}"
    if "tables" not in st.session_state:
        st.session_state["tables"] = {}
    if table_key not in st.session_state["tables"]:
        st.session_state["tables"][table_key] = df

    st.markdown("### Treinos sugeridos para blocos A, B ou C")
    st.caption("Escolha um dos modelos prontos e envie diretamente para a ficha do bloco escolhido.")
    sugestoes_nomes = [s.get("nome") for s in SUGGESTED_TREINOS]
    sugestao_selecionada = st.selectbox(
        "Veja os treinos sugeridos", sugestoes_nomes, key=f"sugestoes_select_{ficha['id']}"
    )
    sugestao = next((s for s in SUGGESTED_TREINOS if s.get("nome") == sugestao_selecionada), None)
    sugestao_exercicios = suggestion_to_exercicios(sugestao.get("exercicios", [])) if sugestao else []
    sugestao_df = pd.DataFrame(sugestao_exercicios)
    if not sugestao_df.empty:
        st.dataframe(
            sugestao_df.drop(columns=["carga_observacao", "descanso_s"]), use_container_width=True
        )

    col_sug_1, col_sug_2 = st.columns(2)
    with col_sug_1:
        bloco_destino = st.selectbox(
            "Enviar para qual bloco?", ["A", "B", "C"], key=f"bloco_destino_{ficha['id']}"
        )
    with col_sug_2:
        if st.button("Enviar treino sugerido para o bloco", key=f"btn_enviar_sug_{ficha['id']}"):
            if not sugestao_exercicios:
                st.error("Escolha uma sugestão válida para enviar.")
            else:
                destino_ficha, created = apply_suggestion_to_block(
                    fichas, bloco_destino, sugestao_exercicios
                )
                if destino_ficha.get("id") == ficha.get("id"):
                    st.session_state["tables"][table_key] = ficha_to_dataframe(destino_ficha)
                msg_extra = " (ficha criada automaticamente)" if created else ""
                st.success(
                    f"{sugestao_selecionada} adicionada à ficha do bloco {bloco_destino}{msg_extra}."
                )
                if destino_ficha.get("id") != ficha.get("id"):
                    st.info(
                        f"Selecione a ficha {destino_ficha.get('nome_ficha', f'Ficha {bloco_destino}')} na barra lateral para editar ou visualizar."
                    )

    if st.button("Adicionar exercício"):
        st.session_state["tables"][table_key] = add_empty_row(st.session_state["tables"][table_key])

    edited_df = st.data_editor(
        st.session_state["tables"][table_key],
        key=table_key,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "grupo_muscular": st.column_config.SelectboxColumn(
                "Grupo muscular",
                options=list(EXERCICIOS_CLASSICOS.keys()),
                help="Escolha um grupo muscular clássico.",
            ),
            "exercicio": st.column_config.TextColumn(
                "Exercício",
                help="Escolha um exercício clássico ou digite outro manualmente.",
            ),
            "series": st.column_config.NumberColumn("Séries", min_value=0, step=1),
            "repeticoes": st.column_config.TextColumn("Repetições"),
            "carga_observacao": st.column_config.TextColumn("Carga/Observação"),
            "descanso_s": st.column_config.NumberColumn("Descanso (s)", min_value=0, step=5),
            "ordem": st.column_config.NumberColumn("Ordem", min_value=1, step=1),
        },
        hide_index=True,
    )
    st.session_state["tables"][table_key] = edited_df

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Salvar ficha"):
            ficha["exercicios"] = dataframe_to_exercicios(edited_df)
            save_fichas(fichas)
            st.success("Ficha salva com sucesso!")
    with col2:
        if st.button("Exportar ficha em PDF"):
            exercicios = dataframe_to_exercicios(edited_df)
            if not exercicios:
                st.error("Adicione exercícios antes de exportar.")
            else:
                path = export_ficha_pdf({**ficha, "exercicios": exercicios})
                st.success(f"PDF gerado: {os.path.basename(path)}")
                st.download_button("Baixar PDF da ficha", open(path, "rb"), file_name=os.path.basename(path))
    with col3:
        if st.button("Exportar ciclo (fichas A, B, C) em PDF"):
            ciclo = [f for f in fichas if str(f.get("identificador", "")).upper() in {"A", "B", "C"}]
            if not ciclo:
                st.error("Cadastre as fichas A, B e C para gerar o ciclo.")
            else:
                path = export_ciclo_pdf(ciclo)
                st.success(f"PDF do ciclo gerado: {os.path.basename(path)}")
                st.download_button("Baixar PDF do ciclo", open(path, "rb"), file_name=os.path.basename(path))

    st.markdown("---")
    st.markdown("### Visualização rápida")
    st.dataframe(edited_df.sort_values("ordem"), use_container_width=True)


if __name__ == "__main__":
    main()
