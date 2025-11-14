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
