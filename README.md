# Game Tracker

Track every game you've played, are playing, or want to play, with the platform you played it on.

The frontend and backend run in a single Docker container. The database is a SQLite file stored on the host via a bind mount, so it lives outside the container and survives rebuilds.

## Run with Docker

```sh
docker compose up -d --build
```

Open http://localhost:8000. The database file lands in `./data/games.db` on the host.

To back it up, copy that one file. To start fresh, delete it.

## Run locally without Docker

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
```

The local database defaults to `./data/games.db` relative to where you run it. Override with the `DATABASE_URL` env var if you want it somewhere else.

## Features

- Status tracking: Want to Play, Playing, Completed, Dropped (editable in the admin page)
- Status tabs to view each list, plus an All view
- Platform per game: Steam, PlayStation, Xbox, Nintendo (also editable), with a platform filter
- Optional 1-10 rating and free-form notes
- Change a game's status inline from its card
- Admin page at `/admin.html` to add, rename, and delete platforms and statuses
- Title autocomplete with box art via IGDB (optional, see below)
- Wishlist at `/calendar.html`: search any game on IGDB, out or not, add what you want, and see it in date order with upcoming releases first

## Wishlist

`/calendar.html` is a wishlist. Search by title, add what you want, and the page shows your list: upcoming games grouped by month, then `Out now` newest-first, then anything with no announced date.

Search goes straight to IGDB. Nothing is stored until you actually add a game, so the database holds your wishlist and nothing else.

An earlier version cached every upcoming release (~3,800 rows, resynced every 12 hours) so search could hit a local table first. That was the wrong shape: the search called IGDB on the same request anyway, so the cache never saved a round trip. It only added rows to maintain, a sync window to fall outside of, and a pile of reconciliation logic for releases nobody had asked about.

Caching the back catalogue instead was never an option either. Two years *forward* is 5,802 IGDB rows; two years *backward* is 57,645, and twenty years back is 122,055 while still missing anything older.

### Keeping dates current

A background job re-reads every wishlisted game from IGDB every 12 hours, so a delayed game corrects itself. It's one query per 50 games and takes well under a second for a normal list, versus about six seconds for the old bulk sync.

The date shown is not simply a game's earliest. A game with an upcoming port or re-release has both past and future dates, and the one you're waiting for is the next future one. Only when everything has shipped does the original release date become the useful answer. Elden Ring shows its Switch 2 date, not February 2022; Ocarina of Time shows 1998.

Dates IGDB only knows roughly ("Q4 2026", "2027") keep that wording rather than being pinned to a day, and year-only entries get their own `2027 · date TBA` heading instead of being filed under December.

### Row ownership

| source | Created by | Refreshed from IGDB? | Editable |
| --- | --- | --- | --- |
| `igdb` | Adding from search | Title, date, cover, platforms | Notes only |
| `manual` | `+ Manual entry` | No | Everything |

Notes are yours on either kind and survive a refresh. Everything else on an `igdb` row gets rewritten by the next refresh, so the API rejects editing it rather than pretending the change stuck. Removing a row deletes it.

### Manual entries

IGDB has no physical/digital distinction, so a boxed edition of a game that already shipped digitally will not appear anywhere in its data. Add those by hand with `+ Manual entry`. The date is optional, for a game that's wanted but unannounced.

## API

The UI talks to a JSON API you can also hit directly:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/games?status=&platform=` | List games, both filters optional |
| POST | `/api/games` | Add a game |
| PATCH | `/api/games/{id}` | Update any field, partial body |
| DELETE | `/api/games/{id}` | Remove a game |
| GET | `/api/releases/search?q=` | Search IGDB for something to add |
| GET | `/api/releases?platform=` | Your wishlist, soonest first |
| POST | `/api/releases/wishlist` | Add an IGDB game by id |
| POST | `/api/releases` | Add a manual entry |
| PATCH | `/api/releases/{id}` | Edit a manual entry, or the notes on any row |
| DELETE | `/api/releases/{id}` | Remove from the wishlist |
| POST | `/api/releases/{id}/add-to-library` | Create a game from a wishlist row, with its cover |
| POST | `/api/releases/refresh` | Force a date refresh

Interactive docs at `/docs`.

Example:

```sh
curl -X POST localhost:8000/api/games \
  -H 'Content-Type: application/json' \
  -d '{"title": "Hades II", "platform": "steam", "status": "playing"}'
```
