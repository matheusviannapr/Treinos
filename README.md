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

## UI palette (dark mode) guidance

TriPlanner must run in dark mode with a consistent palette. When instructing a
copilot or adjusting styles, avoid raw whites (`#FFFFFF`, `#F0F0F0`) or blacks
(`#000000`) and instead map everything to the tokens below.

### Surfaces and borders
* **Background:** `#020617` (page body, behind the calendar and cards)
* **Surface:** `#0B1120` (primary cards and panels that might currently be white)
* **Surface-soft:** `#111827` (inner boxes and inputs that need gentle contrast)
* **Border:** `#1F2937` (card outlines, input borders, subtle dividers)

### Text
* **Primary text:** `#F9FAFB` (titles, key labels)
* **Secondary text:** `#9CA3AF` (subtitles, descriptions, placeholders)
* **Muted/disabled text:** `#6B7280` (disabled states or low-emphasis notes)

### Primary green (logo/buttons/highlights)
* **Primary:** `#A3E635` (solid fill for main buttons and selected chips)
* **Hover:** `#84CC16` (same elements on hover)
* **Active:** `#4D7C0F` (pressed/active state)
* **Soft accent:** `rgba(163, 230, 53, 0.12)` (badges or light highlights)
* **Text on primary:** `#020617` (preferred for maximum contrast)

### Error/attention accents (optional)
* **Error:** `#F97373` (base) and `#FCA5A5` (lighter)
* **Warning:** `#FACC15`

### Conceptual guidance for the copilot
* Cards should be dark (`surface`) over a darker background. Avoid light cards
  over light backgrounds.
* Inputs must follow the dark palette: background `#111827`, text `#F9FAFB`,
  placeholder `#9CA3AF`, borders `#1F2937`.
* Replace any light borders (`#dddddd`, `#e0e0e0`) with the border token.
* Primary buttons use the solid green: normal `#A3E635`, hover `#84CC16`, active
  `#4D7C0F`, text `#020617`. Prefer solid fills with subtle shadows over heavy
  gradients. Disable by lowering opacity when needed.
* The app should never render pure white components on a light background; keep
  all surfaces within the dark palette.
