import os
import json
import random
import string

import httpx
import psycopg2
from psycopg2.extras import Json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# App
app = FastAPI(title="Pokemon Ability API")

# Config (read from docker-compose)
DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     os.getenv("POSTGRES_PORT", "5432"),
    "dbname":   os.getenv("POSTGRES_DB",   "pokemon_db"),
    "user":     os.getenv("POSTGRES_USER", "pokemon_user"),
    "password": os.getenv("POSTGRES_PASSWORD", "pokemon_pass"),
}

# Schemas
class AbilityRequest(BaseModel):
    pokemon_ability_id: int

# Functions
def get_conn():
    """Open and return a new psycopg2 connection."""
    return psycopg2.connect(**DB_CONFIG)

def make_raw_id() -> str:
    """Random 13-char alphanumeric string."""
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=13))

def make_user_id() -> str:
    """Random 7-digit number string (no leading zero)."""
    return str(random.randint(1_000_000, 9_999_999))

# Startup — create table
@app.on_event("startup")
def startup():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stg_ability_raw (
            load_id      BIGSERIAL PRIMARY KEY,
            ability_id   INTEGER NOT NULL,
            raw_payload  JSONB NOT NULL,
            loaded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ods_abilities (
            raw_id      TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            ability_id  INTEGER PRIMARY KEY,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ods_effect_entries (
            id                  BIGSERIAL PRIMARY KEY,
            raw_id              TEXT NOT NULL,
            user_id             TEXT NOT NULL,
            pokemon_ability_id  INTEGER NOT NULL,
            effect              TEXT,
            language            JSONB,
            short_effect        TEXT,
            pokemon_names       JSONB,
            is_current          BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("[DB] Table ready.")

# Entry point
@app.post("/ability")
def get_ability(payload: AbilityRequest):
    ability_id = payload.pokemon_ability_id

    # 1. Hit PokeAPI
    url      = f"https://pokeapi.co/api/v2/ability/{ability_id}"
    response = httpx.get(url, timeout=10.0)

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Ability {ability_id} not found.")
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="PokeAPI error.")

    data = response.json()

    # 2. Extract pokemon
    pokemon_names = [p["pokemon"]["name"] for p in data.get("pokemon", [])]

    # 3. Normalize effect_entries and insert each row
    effect_entries = data.get("effect_entries", [])
    if not effect_entries:
        raise HTTPException(status_code=404, detail="No effect_entries found.")

    conn = get_conn()
    cur  = conn.cursor()
    raw_id = make_raw_id()
    user_id = make_user_id()

    # 3a. STG (immutable raw JSONB)
    cur.execute("""
        INSERT INTO stg_ability_raw (ability_id, raw_payload)
        VALUES (%s, %s)
        RETURNING load_id
    """, (ability_id, Json(data)))
    _ = cur.fetchone()[0]

    # 3b. ODS ability snapshot (1 current row per ability_id)
    cur.execute("""
        INSERT INTO ods_abilities (raw_id, user_id, ability_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (ability_id)
        DO UPDATE SET
            raw_id = EXCLUDED.raw_id,
            user_id = EXCLUDED.user_id,
            updated_at = NOW()
    """, (raw_id, user_id, ability_id))

    # 3c. Idempotent load: replace rows for this ability_id
    cur.execute("""
        DELETE FROM ods_effect_entries
        WHERE pokemon_ability_id = %s
    """, (ability_id,))

    # 3d. Insert fresh current ODS effect entries
    for entry in effect_entries:
        cur.execute("""
            INSERT INTO ods_effect_entries
                (raw_id, user_id, pokemon_ability_id, effect, language, short_effect, pokemon_names, is_current)
            VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
        """, (
            raw_id,
            user_id,
            ability_id,
            entry.get("effect", ""),
            Json(entry.get("language", {})),
            entry.get("short_effect", ""),
            Json(pokemon_names),
        ))

    conn.commit()

    # 4. Fetch and return current ODS rows
    cur.execute(
        """
        SELECT *
        FROM ods_effect_entries
        WHERE pokemon_ability_id = %s
          AND is_current = TRUE
        """,
        (ability_id,)
    )
    cols = [desc[0] for desc in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()

    # Deserialize JSON fields back to Python objects
    for row in rows:
        if isinstance(row.get("language"), str):
            row["language"] = json.loads(row["language"])
        if isinstance(row.get("pokemon_names"), str):
            row["pokemon_names"] = json.loads(row["pokemon_names"])

    return rows


@app.get("/health")
def health():
    return {"status": "ok"}
