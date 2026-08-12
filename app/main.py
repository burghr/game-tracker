import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    String,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    literal,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/games.db")
TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")

# Ensure the directory for a SQLite file exists (e.g. the /data bind mount)
default_covers_dir = Path("./data/covers")
if DATABASE_URL.startswith("sqlite:///"):
    db_path = Path(DATABASE_URL.removeprefix("sqlite:///"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    default_covers_dir = db_path.parent / "covers"

COVERS_DIR = Path(os.environ.get("COVERS_DIR", str(default_covers_dir)))
COVERS_DIR.mkdir(parents=True, exist_ok=True)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50))
    notes: Mapped[str] = mapped_column(String(2000), default="")
    rating: Mapped[int | None] = mapped_column(default=None)
    cover: Mapped[str] = mapped_column(String(300), default="")
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Platform(Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(String(50), unique=True)
    label: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(default=0)


class Status(Base):
    __tablename__ = "statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(String(50), unique=True)
    label: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(default=0)


class Release(Base):
    """A game on the wishlist. One row per game, not per release date.

    Every row here is something you want; there's no browsable catalogue to
    filter out of. Search goes straight to IGDB, so nothing is stored until you
    actually add it.

    `source` decides who owns the row's facts:
      igdb   - looked up from IGDB; date, title, cover and platforms are
               refreshed on a schedule, your notes are not
      manual - typed in by hand, for things IGDB doesn't model at all
    """

    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("igdb_game_id", name="uq_release_game"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL for manual entries; SQLite allows duplicate NULLs so they don't collide
    igdb_game_id: Mapped[int | None] = mapped_column(default=None, index=True)
    title: Mapped[str] = mapped_column(String(200))
    # NULL when a wanted game has no announced date at all
    release_date: Mapped[int | None] = mapped_column(default=None, index=True)
    # IGDB date precision: 0 exact day, 1 month, 2 year, 3-6 quarters, 7 TBD
    date_format: Mapped[int] = mapped_column(default=0)
    human: Mapped[str] = mapped_column(String(50), default="")
    platforms: Mapped[str] = mapped_column(String(300), default="")  # "PC, PS5, Switch"
    platform_values: Mapped[str] = mapped_column(String(300), default="")  # local filter values
    cover_image_id: Mapped[str] = mapped_column(String(50), default="")
    source: Mapped[str] = mapped_column(String(10), default="igdb")
    notes: Mapped[str] = mapped_column(String(500), default="")


Base.metadata.create_all(engine)


def migrate():
    # create_all never alters existing tables, so bring older DBs up to date.
    columns = [c["name"] for c in inspect(engine).get_columns("games")]
    if "cover" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE games ADD COLUMN cover VARCHAR(300) NOT NULL DEFAULT ''")
            )
    if "sort_order" not in columns:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE games ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            )
        # Seed positions from the previous display order (newest activity first).
        # Raw SQL so the updated_at onupdate hook doesn't rewrite timestamps.
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT id FROM games ORDER BY updated_at DESC, id DESC")
            ).fetchall()
            for i, row in enumerate(rows):
                conn.execute(
                    text("UPDATE games SET sort_order = :pos WHERE id = :id"),
                    {"pos": i, "id": row[0]},
                )

    # The releases table used to cache every upcoming release and flag the ones
    # you cared about. It now holds only the wishlist, so drop `tracked` and
    # `popularity`, keep just the rows that were starred or hand-entered, and
    # fold the old 'wishlist' source into 'igdb'. SQLite can't drop columns
    # in place here, so rebuild.
    inspector = inspect(engine)
    if inspector.has_table("releases"):
        old_cols = {c["name"] for c in inspector.get_columns("releases")}
        if "tracked" in old_cols:
            kept = [c.name for c in Base.metadata.tables["releases"].columns if c.name in old_cols]
            copied = ", ".join(kept)
            # One transaction: a failure part-way through must not leave the
            # table renamed out from under the app.
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE releases RENAME TO releases_old"))
                # SQLite carries index names across a rename, so they would
                # collide with the ones the new table wants to create.
                stale = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                        " AND tbl_name='releases_old' AND sql IS NOT NULL"
                    )
                ).fetchall()
                for (name,) in stale:
                    conn.execute(text(f'DROP INDEX "{name}"'))
                Base.metadata.tables["releases"].create(conn)
                conn.execute(
                    text(
                        f"INSERT INTO releases ({copied}) SELECT {copied} FROM releases_old"
                        " WHERE tracked = 1 OR source IN ('manual', 'wishlist')"
                    )
                )
                conn.execute(text("UPDATE releases SET source = 'igdb' WHERE source = 'wishlist'"))
                conn.execute(text("DROP TABLE releases_old"))


migrate()

DEFAULT_PLATFORMS = [
    ("steam", "Steam"),
    ("playstation", "PlayStation"),
    ("xbox", "Xbox"),
    ("nintendo", "Nintendo"),
]
DEFAULT_STATUSES = [
    ("playing", "Playing"),
    ("want_to_play", "Want to Play"),
    ("completed", "Completed"),
    ("dropped", "Dropped"),
]


def seed_lookups():
    with SessionLocal() as db:
        for model, defaults in ((Platform, DEFAULT_PLATFORMS), (Status, DEFAULT_STATUSES)):
            if db.scalar(select(func.count()).select_from(model)) == 0:
                for i, (value, label) in enumerate(defaults):
                    db.add(model(value=value, label=label, sort_order=i))
        db.commit()


seed_lookups()


class GameIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    platform: str
    status: str | None = None
    notes: str = Field(default="", max_length=2000)
    rating: int | None = Field(default=None, ge=1, le=10)
    cover_image_id: str | None = None


class GameUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    platform: str | None = None
    status: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
    rating: int | None = Field(default=None, ge=1, le=10)
    cover_image_id: str | None = None


class GameOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    platform: str
    status: str
    notes: str
    rating: int | None
    cover: str
    created_at: datetime
    updated_at: datetime


class LookupIn(BaseModel):
    label: str = Field(min_length=1, max_length=50)


class LookupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    value: str
    label: str
    sort_order: int


class ReleaseIn(BaseModel):
    """A hand-entered wishlist row, for things IGDB doesn't model.

    IGDB has no physical/digital axis, so a boxed edition of an already-released
    game has to be added here by hand.
    """

    title: str = Field(min_length=1, max_length=200)
    release_date: datetime | None = None
    platforms: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=500)
    cover_image_id: str | None = None


class ReleaseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    release_date: datetime | None = None
    platforms: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)


class ReleaseOut(BaseModel):
    id: int
    igdb_game_id: int | None
    title: str
    date: str | None  # ISO date, YYYY-MM-DD; None when no date is known
    human: str
    precise: bool  # False for "Q4 2026" style dates, which shouldn't pin to a day
    platforms: str
    platform_values: list[str]
    thumb: str | None
    cover_image_id: str
    source: str
    notes: str
    in_library: bool
    library_status: str | None  # the status label, e.g. "Want to Play"


class SearchResult(BaseModel):
    """An IGDB search hit, flagged if it's already on the wishlist."""

    release_id: int | None  # set when it's already on the list
    igdb_game_id: int
    title: str
    date: str | None
    human: str
    platforms: str
    thumb: str | None
    on_list: bool


class WishlistIn(BaseModel):
    igdb_game_id: int


class AddToLibraryIn(BaseModel):
    platform: str
    status: str | None = None


def thumb_url(image_id: str) -> str | None:
    if not image_id:
        return None
    return f"https://images.igdb.com/igdb/image/upload/t_cover_small/{image_id}.jpg"


def iso_day(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def library_index(db: Session) -> dict[str, str]:
    """Lowercased library title -> its status label, for the "In library" badge.

    Matching on title is crude, but the wishlist has no link to a library row.
    A title held on two platforms takes the first by the library's own order.
    """
    labels = {s.value: s.label for s in db.scalars(select(Status))}
    index: dict[str, str] = {}
    for game in db.scalars(select(Game).order_by(Game.sort_order, Game.id)):
        index.setdefault(game.title.lower(), labels.get(game.status, game.status))
    return index


def release_out(row: Release, library: dict[str, str]) -> ReleaseOut:
    if row.human:
        human = row.human
    elif row.release_date is not None:
        human = datetime.fromtimestamp(row.release_date, tz=timezone.utc).strftime("%b %d, %Y")
    else:
        human = "No date yet"
    return ReleaseOut(
        id=row.id,
        igdb_game_id=row.igdb_game_id,
        title=row.title,
        date=iso_day(row.release_date),
        human=human,
        precise=row.date_format == 0 and row.release_date is not None,
        platforms=row.platforms,
        platform_values=[v for v in row.platform_values.split(",") if v],
        thumb=thumb_url(row.cover_image_id),
        cover_image_id=row.cover_image_id,
        source=row.source,
        notes=row.notes,
        in_library=row.title.lower() in library,
        library_status=library.get(row.title.lower()),
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not slug:
        raise HTTPException(status_code=422, detail="Name must contain letters or numbers")
    return slug


def require_lookup_value(db: Session, model, value: str, kind: str):
    if db.scalar(select(model).where(model.value == value)) is None:
        raise HTTPException(status_code=422, detail=f"Unknown {kind}: {value}")


_igdb_token = {"value": "", "expires": 0.0}


def igdb_token() -> str:
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Game search is not configured (set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET)",
        )
    if time.time() < _igdb_token["expires"] - 60:
        return _igdb_token["value"]
    resp = httpx.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Twitch authentication failed")
    data = resp.json()
    _igdb_token["value"] = data["access_token"]
    _igdb_token["expires"] = time.time() + data["expires_in"]
    return _igdb_token["value"]


def platform_labels(game: dict, cap: int = 6) -> str:
    """Display string for a game's platforms, straight from IGDB's abbreviations.

    The IGDB_PLATFORMS map below only covers current hardware, so it can't name
    an N64 or a Dreamcast. Wishlisted back-catalogue games need those, and
    IGDB's own abbreviation field already has them.
    """
    labels = []
    for p in game.get("platforms") or []:
        if not isinstance(p, dict):
            continue
        label = p.get("abbreviation") or p.get("name")
        if label and label not in labels:
            labels.append(label)
    if len(labels) > cap:
        labels = labels[:cap] + [f"+{len(labels) - cap} more"]
    return ", ".join(labels)[:300]


def igdb_query(endpoint: str, body: str) -> list:
    resp = httpx.post(
        f"https://api.igdb.com/v4/{endpoint}",
        headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {igdb_token()}"},
        content=body,
        timeout=20,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"IGDB {endpoint} query failed")
    return resp.json()


# IGDB platform ids we care about, mapped to the default platform lookup values.
# Users can rename or delete platforms in /admin, so a missing value just means
# the calendar row won't match that platform filter.
IGDB_PLATFORMS = {
    6: ("PC", "steam"),
    14: ("Mac", "steam"),
    3: ("Linux", "steam"),
    48: ("PS4", "playstation"),
    167: ("PS5", "playstation"),
    49: ("Xbox One", "xbox"),
    169: ("Xbox Series", "xbox"),
    130: ("Switch", "nintendo"),
    508: ("Switch 2", "nintendo"),
}

# Wishlist rows are refreshed on this cadence so a delayed game updates itself.
REFRESH_INTERVAL = 12 * 3600
# IGDB caps a query at 500 rows, and nested release_dates multiply that fast
REFRESH_CHUNK = 50

_sync_state = {"at": 0.0, "lock": threading.Lock()}

GAME_FIELDS = (
    "fields name, first_release_date, cover.image_id, platforms.abbreviation,"
    " platforms.name, release_dates.date, release_dates.human, release_dates.date_format;"
)


def wishlist_date(game: dict) -> tuple[int | None, int, str]:
    """The (timestamp, precision, wording) a wishlist cares about for a game.

    Not simply the earliest date. A game with an upcoming port or re-release has
    both past and future dates, and the one you're waiting for is the next
    future one, not the original launch. Only when everything has shipped does
    the original release date become the useful answer.

    `first_release_date` alone loses IGDB's precision, so a game it only dates
    to "Q4 2026" would render as a specific day. The nested release_dates carry
    date_format and the human string, so prefer those.
    """
    dated = [d for d in (game.get("release_dates") or []) if d.get("date")]
    if dated:
        today = int(time.time())
        upcoming = [d for d in dated if d["date"] >= today]
        best = min(upcoming or dated, key=lambda d: d["date"])
        stamp = best["date"]
        return stamp - (stamp % 86400), best.get("date_format", 0), (best.get("human") or "")[:50]
    stamp = game.get("first_release_date")
    if stamp:
        return stamp - (stamp % 86400), 0, ""
    return None, 7, ""


def apply_igdb_game(row: Release, game: dict):
    """Copy IGDB's facts onto a wishlist row, leaving the user's notes alone."""
    row.title = (game.get("name") or row.title or "Untitled")[:200]
    row.release_date, row.date_format, row.human = wishlist_date(game)
    row.platforms = platform_labels(game)
    row.platform_values = ",".join(
        sorted(
            IGDB_PLATFORMS[p["id"]][1]
            for p in (game.get("platforms") or [])
            if isinstance(p, dict) and p.get("id") in IGDB_PLATFORMS
        )
    )[:300]
    row.cover_image_id = ((game.get("cover") or {}).get("image_id") or "")[:50]


def refresh_wishlist() -> int:
    """Re-read every wishlisted game from IGDB so slipped dates correct themselves.

    This replaced a bulk sync of every upcoming release. Search goes straight to
    IGDB now, so there is nothing to warehouse: the only rows worth keeping
    current are the ones actually on the list.
    """
    with SessionLocal() as db:
        ids = [
            r.igdb_game_id
            for r in db.scalars(select(Release).where(Release.igdb_game_id.isnot(None)))
        ]
    if not ids:
        _sync_state["at"] = time.time()
        return 0

    games: dict[int, dict] = {}
    for i in range(0, len(ids), REFRESH_CHUNK):
        chunk = ids[i : i + REFRESH_CHUNK]
        listed = ",".join(str(g) for g in chunk)
        for game in igdb_query("games", f"{GAME_FIELDS} where id = ({listed}); limit 500;"):
            games[game["id"]] = game

    updated = 0
    with SessionLocal() as db:
        for row in db.scalars(select(Release).where(Release.igdb_game_id.isnot(None))):
            game = games.get(row.igdb_game_id)
            # A game IGDB has since removed keeps its last known values rather
            # than being wiped or dropped off the list.
            if game is None:
                continue
            apply_igdb_game(row, game)
            updated += 1
        db.commit()

    _sync_state["at"] = time.time()
    return updated


def refresh_if_stale():
    """Kick off a background refresh when the last one is older than the interval."""
    if time.time() - _sync_state["at"] < REFRESH_INTERVAL:
        return
    if not _sync_state["lock"].acquire(blocking=False):
        return  # already running

    def run():
        try:
            refresh_wishlist()
        except Exception:
            # Reading the wishlist should never 500 because IGDB is having a bad day
            _sync_state["at"] = time.time() - REFRESH_INTERVAL + 300  # retry in 5 min
        finally:
            _sync_state["lock"].release()

    threading.Thread(target=run, daemon=True).start()


def fetch_cover(image_id: str) -> str:
    """Download a cover from IGDB once and return its local URL path."""
    if not re.fullmatch(r"[A-Za-z0-9]+", image_id):
        raise HTTPException(status_code=422, detail="Invalid cover image id")
    dest = COVERS_DIR / f"{image_id}.jpg"
    if not dest.exists():
        try:
            resp = httpx.get(
                f"https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg",
                timeout=15,
            )
        except httpx.HTTPError:
            return ""
        if resp.status_code != 200:
            return ""
        dest.write_bytes(resp.content)
    return f"/covers/{image_id}.jpg"


app = FastAPI(title="Game Tracker")


@app.middleware("http")
async def cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".html", ".js", ".css")):
        # Revalidate every load so deploys show up without hard refreshes
        response.headers["Cache-Control"] = "no-cache"
    elif path.startswith("/covers/"):
        # Cover filenames are unique IGDB image ids; safe to cache forever
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.get("/api/search-external")
def search_external(q: str):
    q = q.strip()
    if len(q) < 2:
        return []
    token = igdb_token()
    escaped = q.replace("\\", "").replace('"', '\\"')
    resp = httpx.post(
        "https://api.igdb.com/v4/games",
        headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"},
        content=f'search "{escaped}"; fields name, first_release_date, cover.image_id; limit 8;',
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="IGDB search failed")
    results = []
    for g in resp.json():
        image_id = (g.get("cover") or {}).get("image_id")
        year = None
        if g.get("first_release_date"):
            year = datetime.fromtimestamp(g["first_release_date"], tz=timezone.utc).year
        results.append(
            {
                "name": g.get("name", ""),
                "year": year,
                "cover_image_id": image_id,
                "thumb": (
                    f"https://images.igdb.com/igdb/image/upload/t_cover_small/{image_id}.jpg"
                    if image_id
                    else None
                ),
            }
        )
    return results


@app.get("/api/games", response_model=list[GameOut])
def list_games(
    status: str | None = None,
    platform: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    query = select(Game).order_by(Game.sort_order, Game.id)
    if status is not None:
        query = query.where(Game.status == status)
    if platform is not None:
        query = query.where(Game.platform == platform)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Game.title.ilike(f"%{escaped}%", escape="\\"))
    return db.scalars(query).all()


@app.post("/api/games", response_model=GameOut, status_code=201)
def create_game(payload: GameIn, db: Session = Depends(get_db)):
    require_lookup_value(db, Platform, payload.platform, "platform")
    status = payload.status
    if status is None:
        first = db.scalars(
            select(Status).order_by(Status.sort_order, Status.id)
        ).first()
        if first is None:
            raise HTTPException(status_code=422, detail="No statuses defined")
        status = first.value
    require_lookup_value(db, Status, status, "status")
    game = Game(
        title=payload.title.strip(),
        platform=payload.platform,
        status=status,
        notes=payload.notes,
        rating=payload.rating,
        cover=fetch_cover(payload.cover_image_id) if payload.cover_image_id else "",
        sort_order=(db.scalar(select(func.min(Game.sort_order))) or 0) - 1,
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


@app.patch("/api/games/{game_id}", response_model=GameOut)
def update_game(game_id: int, payload: GameUpdate, db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    changes = payload.model_dump(exclude_unset=True)
    if "platform" in changes and changes["platform"] is not None:
        require_lookup_value(db, Platform, changes["platform"], "platform")
    if "status" in changes and changes["status"] is not None:
        require_lookup_value(db, Status, changes["status"], "status")
    if "cover_image_id" in changes:
        image_id = changes.pop("cover_image_id")
        game.cover = fetch_cover(image_id) if image_id else ""
    for field, value in changes.items():
        if field == "title" and value is not None:
            value = value.strip()
        setattr(game, field, value)
    db.commit()
    db.refresh(game)
    return game


class ReorderIn(BaseModel):
    ids: list[int] = Field(min_length=1)


@app.post("/api/games/reorder", status_code=204)
def reorder_games(payload: ReorderIn, db: Session = Depends(get_db)):
    """Reassign the positions held by these games to match the given order.

    Only the listed games move relative to each other; games outside the
    list (other tabs/filters) keep their positions.
    """
    if len(payload.ids) != len(set(payload.ids)):
        raise HTTPException(status_code=422, detail="Duplicate game id in reorder list")
    games = db.scalars(select(Game).where(Game.id.in_(payload.ids))).all()
    if len(games) != len(payload.ids):
        raise HTTPException(status_code=422, detail="Unknown game id in reorder list")
    slots = sorted(g.sort_order for g in games)
    with engine.begin() as conn:
        for slot, game_id in zip(slots, payload.ids):
            conn.execute(
                text("UPDATE games SET sort_order = :pos WHERE id = :id"),
                {"pos": slot, "id": game_id},
            )


@app.delete("/api/games/{game_id}", status_code=204)
def delete_game(game_id: int, db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    db.delete(game)
    db.commit()


@app.get("/api/releases", response_model=list[ReleaseOut])
def list_releases(platform: str | None = None, db: Session = Depends(get_db)):
    """The wishlist, soonest first, with undated wants at the end."""
    refresh_if_stale()

    query = select(Release).order_by(
        # SQLite sorts NULLs first; undated wants belong at the end of the list
        Release.release_date.is_(None),
        Release.release_date,
        Release.title,
    )
    if platform:
        # Stored as a comma-joined list; bracket both sides so "xbox" can't match "xboxone"
        bracketed = literal(",").concat(Release.platform_values).concat(",")
        query = query.where(func.instr(bracketed, f",{platform},") > 0)

    rows = db.scalars(query).all()
    library = library_index(db)
    return [release_out(r, library) for r in rows]


@app.get("/api/releases/search", response_model=list[SearchResult])
def search_for_wishlist(q: str, db: Session = Depends(get_db)):
    """Search IGDB for something to add, upcoming or long since released.

    This goes straight to IGDB rather than through a local cache. An earlier
    version kept every upcoming release in the database to search first, but it
    still called IGDB on the same request, so the cache never saved a round trip.
    """
    term = q.strip()
    if len(term) < 2:
        return []

    quoted = term.replace("\\", "").replace('"', '\\"')
    games = igdb_query("games", f'search "{quoted}"; {GAME_FIELDS} limit 20;')

    # Flag the ones already on the list so the UI can disable adding them twice
    on_list = {
        row.igdb_game_id: row.id
        for row in db.scalars(select(Release).where(Release.igdb_game_id.isnot(None)))
    }

    results = []
    for game in games:
        if not game.get("name"):
            continue
        stamp, date_format, human = wishlist_date(game)
        results.append(
            SearchResult(
                release_id=on_list.get(game["id"]),
                igdb_game_id=game["id"],
                title=game["name"],
                date=iso_day(stamp),
                human=human or iso_day(stamp) or "No date yet",
                platforms=platform_labels(game),
                thumb=thumb_url((game.get("cover") or {}).get("image_id") or ""),
                on_list=game["id"] in on_list,
            )
        )
    return results


@app.post("/api/releases/wishlist", response_model=ReleaseOut, status_code=201)
def add_to_wishlist(payload: WishlistIn, db: Session = Depends(get_db)):
    """Put an IGDB game on the wishlist. Adding one already on it is a no-op."""
    existing = db.scalars(
        select(Release).where(Release.igdb_game_id == payload.igdb_game_id)
    ).first()
    if existing is not None:
        return release_out(existing, library_index(db))

    games = igdb_query(
        "games", f"{GAME_FIELDS} where id = {payload.igdb_game_id}; limit 1;"
    )
    if not games:
        raise HTTPException(status_code=404, detail="Game not found on IGDB")

    row = Release(igdb_game_id=payload.igdb_game_id, source="igdb")
    apply_igdb_game(row, games[0])
    db.add(row)
    db.commit()
    db.refresh(row)
    return release_out(row, library_index(db))


@app.post("/api/releases/refresh")
def trigger_refresh():
    with _sync_state["lock"]:
        count = refresh_wishlist()
    return {"refreshed": count}


@app.post("/api/releases", response_model=ReleaseOut, status_code=201)
def create_release(payload: ReleaseIn, db: Session = Depends(get_db)):
    day = None
    if payload.release_date is not None:
        stamp = int(payload.release_date.replace(tzinfo=timezone.utc).timestamp())
        day = stamp - (stamp % 86400)
    row = Release(
        igdb_game_id=None,
        title=payload.title.strip(),
        release_date=day,
        date_format=0,
        human="",
        platforms=payload.platforms.strip(),
        platform_values="",
        cover_image_id=payload.cover_image_id or "",
        source="manual",
        notes=payload.notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return release_out(row, library_index(db))


@app.patch("/api/releases/{release_id}", response_model=ReleaseOut)
def update_release(release_id: int, payload: ReleaseUpdate, db: Session = Depends(get_db)):
    row = db.get(Release, release_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Release not found")
    changes = payload.model_dump(exclude_unset=True)
    # Notes are yours on any row. Everything else on an IGDB row gets rewritten
    # by the next refresh, so editing it would only look like it worked.
    if row.source == "igdb" and set(changes) - {"notes"}:
        raise HTTPException(
            status_code=422,
            detail="IGDB rows refresh from upstream; only 'notes' can be edited",
        )
    if "release_date" in changes and changes["release_date"] is not None:
        day = int(changes.pop("release_date").replace(tzinfo=timezone.utc).timestamp())
        row.release_date = day - (day % 86400)
    for field, value in changes.items():
        if field == "title" and value is not None:
            value = value.strip()
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return release_out(row, library_index(db))


@app.delete("/api/releases/{release_id}", status_code=204)
def delete_release(release_id: int, db: Session = Depends(get_db)):
    row = db.get(Release, release_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Release not found")
    db.delete(row)
    db.commit()


@app.post("/api/releases/{release_id}/add-to-library", response_model=GameOut, status_code=201)
def add_release_to_library(
    release_id: int, payload: AddToLibraryIn, db: Session = Depends(get_db)
):
    row = db.get(Release, release_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return create_game(
        GameIn(
            title=row.title,
            platform=payload.platform,
            status=payload.status,
            notes=row.notes,
            cover_image_id=row.cover_image_id or None,
        ),
        db,
    )


def make_lookup_router(model, name: str, game_column, kind: str) -> APIRouter:
    router = APIRouter(prefix=f"/api/{name}", tags=[name])

    def list_items(db: Session = Depends(get_db)):
        return db.scalars(select(model).order_by(model.sort_order, model.id)).all()

    def create_item(payload: LookupIn, db: Session = Depends(get_db)):
        value = slugify(payload.label)
        if db.scalar(select(model).where(model.value == value)):
            raise HTTPException(status_code=409, detail=f"A {kind} named '{payload.label.strip()}' already exists")
        max_sort = db.scalar(select(func.max(model.sort_order)))
        item = model(
            value=value,
            label=payload.label.strip(),
            sort_order=(max_sort if max_sort is not None else -1) + 1,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def rename_item(item_id: int, payload: LookupIn, db: Session = Depends(get_db)):
        item = db.get(model, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"{kind.capitalize()} not found")
        item.label = payload.label.strip()
        db.commit()
        db.refresh(item)
        return item

    def delete_item(item_id: int, db: Session = Depends(get_db)):
        item = db.get(model, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"{kind.capitalize()} not found")
        in_use = db.scalar(select(func.count()).select_from(Game).where(game_column == item.value))
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete '{item.label}': {in_use} game(s) still use it",
            )
        db.delete(item)
        db.commit()

    # Unique function names so OpenAPI operation ids don't collide between routers
    list_items.__name__ = f"list_{name}"
    create_item.__name__ = f"create_{kind}"
    rename_item.__name__ = f"rename_{kind}"
    delete_item.__name__ = f"delete_{kind}"

    router.get("", response_model=list[LookupOut])(list_items)
    router.post("", response_model=LookupOut, status_code=201)(create_item)
    router.patch("/{item_id}", response_model=LookupOut)(rename_item)
    router.delete("/{item_id}", status_code=204)(delete_item)
    return router


app.include_router(make_lookup_router(Platform, "platforms", Game.platform, "platform"))
app.include_router(make_lookup_router(Status, "statuses", Game.status, "status"))

app.mount("/covers", StaticFiles(directory=COVERS_DIR))
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True))
