# Pokemon Ability API — Simple Single-File Version

A minimal FastAPI app in **one file** (`app/main.py`) that:
1. Receives a `pokemon_ability_id` via POST request
2. Fetches ability data from [PokeAPI](https://pokeapi.co)
3. Stores immutable raw JSON in STG
4. Builds idempotent ODS tables by `ability_id`
5. Returns current ODS rows as JSON

---

## Project Structure

```
pokemon_api/
├── app/main.py          ← entire application logic
├── requirements.txt     ← Python dependencies
├── Dockerfile           ← builds the API container
├── docker-compose.yml   ← spins up API + PostgreSQL
└── README.md
```

---

## How to Run

### Prerequisites
- [Docker](https://www.docker.com/products/docker-desktop/) installed and running

### Start everything

```bash
docker compose up --build
```

### Stop

```bash
docker compose down
```

To also remove DB volume:

```bash
docker compose down -v
```

---

## API Reference

### `POST /ability`

Fetches a Pokemon ability, loads STG + ODS, and returns current ODS rows (`is_current = true`) for that `ability_id`.

**Request body:**
```json
{ "pokemon_ability_id": 150 }
```

**Example:**
```bash
curl -X POST http://localhost:8000/ability \
  -H "Content-Type: application/json" \
  -d '{"pokemon_ability_id": 150}'
```

**Example response:**
```json
[
  {
    "id": 11,
    "raw_id": "a3Kx9mZ2pQr7w",
    "user_id": "5199434",
    "pokemon_ability_id": 150,
    "effect": "Transforms itself into the Pokémon it is facing...",
    "language": {"name": "en", "url": "https://pokeapi.co/api/v2/language/9/"},
    "short_effect": "Transforms into the opposing Pokémon.",
    "pokemon_names": ["ditto", "mew"],
    "is_current": true,
    "updated_at": "2026-04-20T07:12:32.581245+00:00"
  }
]
```

**Error responses:**

| Status | Reason |
|--------|--------|
| `404`  | Ability ID not found on PokeAPI, or no `effect_entries` exist |
| `502`  | PokeAPI returned an unexpected error |

### `GET /health`

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### Swagger UI

Open:

```
http://localhost:8000/docs
```

---

## Data Layers

### STG (raw, immutable)

#### `stg_ability_raw`

```sql
CREATE TABLE IF NOT EXISTS stg_ability_raw (
    load_id      BIGSERIAL PRIMARY KEY,
    ability_id   INTEGER NOT NULL,
    raw_payload  JSONB NOT NULL,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- Contains immutable raw PokeAPI payload (`JSONB`)
- New row is appended every API hit

### ODS (serving layer)

#### `ods_abilities`

```sql
CREATE TABLE IF NOT EXISTS ods_abilities (
    raw_id      TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    ability_id  INTEGER PRIMARY KEY,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `ods_effect_entries`

```sql
CREATE TABLE IF NOT EXISTS ods_effect_entries (
    id                  BIGSERIAL PRIMARY KEY,
    raw_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    pokemon_ability_id  INTEGER NOT NULL,
    effect              TEXT,
    language            JSONB,
    short_effect        TEXT,
    pokemon_names       TEXT,
    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## ID Rules

- `raw_id`: random alphanumeric string, exactly **13 chars**
- `user_id`: random numeric string, exactly **7 digits**

---

## Idempotency Rules

Idempotency is enforced in ODS by `ability_id`:

1. Insert immutable raw payload to `stg_ability_raw`
2. Upsert one row in `ods_abilities` for that `ability_id`
3. Delete old rows in `ods_effect_entries` for that `ability_id`
4. Insert current effect rows from latest payload

Result:
- If `ability_id = 1` has 3 languages/effect rows, repeated API hits still keep ODS row count at **3** for `pokemon_ability_id = 1`.
- STG still grows by design (append-only raw history).

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_HOST` | `localhost` | DB hostname (`postgres` in Docker) |
| `POSTGRES_PORT` | `5432` | DB port |
| `POSTGRES_DB` | `pokemon_db` | Database name |
| `POSTGRES_USER` | `pokemon_user` | Database user |
| `POSTGRES_PASSWORD` | `pokemon_pass` | Database password |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | Web framework |
| `uvicorn` | ASGI server |
| `httpx` | HTTP client for PokeAPI |
| `pydantic` | Request validation |
| `psycopg2-binary` | PostgreSQL driver |
