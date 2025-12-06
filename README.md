# TriPlanner – Neon Postgres Setup

This project is a Streamlit application for planning triathlon training sessions. The
app now persists data in a Neon-hosted PostgreSQL database via SQLAlchemy and
Psycopg.

## Configuration

### Production (Streamlit Cloud)
1. Open the app settings and add the connection string to **Secrets**:
   ```toml
   [db]
   url = "postgresql://USUARIO:SENHA@ep-xxxxx.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
   ```
2. Redeploy the app. The database connection is read from `st.secrets["db"]["url"]`.

### Local development
1. Create a `.env` file in the project root:
   ```env
   DATABASE_URL=postgresql://USUARIO:SENHA@ep-xxxxx.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the app:
   ```bash
   streamlit run app.py
   ```

The `.env` file is ignored by Git and is only required locally. The app loads it
via `python-dotenv` when no Streamlit secret is available.

## Database initialization

The module `db.py` centralizes the SQLAlchemy engine creation, table creation,
and CRUD helpers. The database schema is created automatically on startup via
`init_db()`, and CSV migrations remain idempotent by tracking state in the
`meta` table.

## Migrating historical SQLite data (optional)

If you still have the old `data/treinos.sqlite` file and want to move its
contents to Neon, run:

```bash
python scripts/migrate_sqlite_to_postgres.py
```

The script copies users and training sessions into Postgres, using UPSERT
statements so you can rerun it safely.

## Persistence

Because all state now lives in Neon, redeploying or rebooting Streamlit Cloud no
longer wipes user data. The local SQLite file is no longer used by the app.

## UI palette (light mode) guidance

TriPlanner now runs in a light, warm palette to avoid dark mode entirely. The
cream tone `#F9F3BF` replaces pure white across the experience.

### Surfaces and borders
* **Background:** `#F9F3BF` (page body and general background)
* **Surface:** `#FFF9DA` (cards and main panels)
* **Surface-soft:** `#FFF3C4` (inputs and inset areas)
* **Border:** `#E2D7A8` (subtle outlines and separators)

### Text
* **Primary text:** `#1F2933`
* **Secondary text:** `#3E4C59`
* **Muted/disabled text:** `#52606D`

### Primary green (logo/buttons/highlights)
* **Primary:** `#3B5228`
* **Hover:** `#4D7C0F`
* **Active:** `#2F3E1F`
* **Soft accent:** `rgba(59, 82, 40, 0.12)`
* **Text on primary:** `#F9F3BF`

### Conceptual guidance for the copilot
* Cards should be light, layered above the cream background.
* Inputs follow the light palette with soft borders for clarity.
* Avoid introducing any dark mode toggles or styling; stick to the warm scheme.
* Use subtle shadows to keep the light look airy rather than heavy.
