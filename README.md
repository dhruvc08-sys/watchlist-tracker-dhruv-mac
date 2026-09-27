# Watchlist Tracker

A Letterboxd-style movie tracker. Search for movies, add them to your watchlist, mark them as watched, rate them out of 10, see your stats, and get an AI-generated taste profile with recommendations.

- **Name:** Dhruv Chadha
- **Email:** dhruvchadha08@gmail.com
- **Deployed app:** https://watchlist-tracker-one.vercel.app
- **Repo:** https://github.com/dhruvc08-sys/watchlist-tracker-dhruv-mac

## Stack

- **Backend:** Python, FastAPI
- **Frontend:** Jinja2 templates, Tailwind CSS (CDN), HTMX
- **Auth + database:** Supabase (Auth + Postgres with Row Level Security)
- **Movie data:** TMDB API
- **AI:** Google Gemini (`google-genai`)
- **Hosting:** Vercel

## Features

- **Search** TMDB by title and view results as a poster grid
- **Watchlist:** add, mark as watched, rate 1–10, clear a rating, remove
- Adding a movie uses HTMX, so the button changes to "Added" without reloading the page
- **Stats:** total watched, average rating, and genre breakdown
- **Taste profile:** Gemini reads your watch history and ratings, writes a short profile, and recommends 5 movies, each with a poster, a reason tied to a movie you've watched, and an 'Add to Watchlist' button
- **Auth:** sign up, sign in, sign out; every user only sees their own data
- Responsive layout (mobile to desktop), plus loading states on search, add and generate

## Run locally

```bash
git clone git clone https://github.com/dhruvc08-sys/watchlist-tracker-dhruv-mac.git
cd cd watchlist-tracker-dhruv-mac
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in the four keys below
uvicorn main:app --reload
```

Open http://127.0.0.1:8000

`.env` needs:

```
SUPABASE_URL=
SUPABASE_ANON_KEY=
TMDB_READ_TOKEN=
GEMINI_API_KEY=
```

## Data model

- `profiles`: one row per user, `id` = Supabase auth user id
- `watchlist_entries`: `id` (uuid PK), `user_id`, `tmdb_id`, `title`, `poster_path`, `added_at`, `watched_at`, `rating`
- `genres`: TMDB genre ids and names
- `entry_genres`: join table (entry ↔ genre), so one movie can have several genres

## Design decisions

- **Server-rendered with FastAPI + Jinja + HTMX instead of React.** I'm much stronger in Python than JavaScript, so I kept the logic in Python and used Jinja templates with HTMX for the interactive elements. This
also means that there's no build step and it's one deployable.
- **Security is enforced in the database, not just the app.** RLS policy `auth.uid() = user_id` on the user tables. Each request creates its own Supabase client authenticated with that user's access token, so Postgres itself decides what rows they can see or change. A shared client would leak one user's session to everyone.
- **Ids from the URL are typed as `UUID`**, so malformed ids are rejected with a 422 before touching the database. Updates and deletes also filter by `user_id`, so a valid id belonging to someone else matches nothing (and RLS blocks it anyway).
- **Session token is stored in an `httponly` cookie**, so page JavaScript can't read it. The cookie expires when the Supabase token does.
- **API keys stay on the server.** TMDB and Gemini are only called from FastAPI, never from the browser.
- **Gemini returns structured JSON**, validated against a Pydantic schema (`TasteProfile`), so the page never has to parse free text. If one model is overloaded, it falls back to the next.
- **Recommendations are checked against TMDB**, looked up by title + year in parallel with `asyncio.gather`, to get a real poster and id. Anything already on the user's list, or not found, is dropped, and the page shows the first 5.

## Assumptions

- A movie can only be on a user's list once. Search shows "Added to watchlist" for movies already on it.
- Ratings are whole numbers from 1 to 10 and can only be given once a movie is marked watched. A rating can be cleared.
- Stats only count **watched** movies. The average rating ignores watched-but-unrated movies. A movie with several genres counts towards each of them.
- Genres are saved from TMDB at the time the movie is added.
- Search results without a poster are hidden, to keep the grid clean.
- The taste profile needs at least one watched movie and is generated on demand (button press), not saved.
- Sessions last as long as the Supabase access token (about 1 hour). After that you sign in again. There's no refresh-token handling.

## Use of Artificial Intelligence

I used Claude by Anthropic as a tutor and pair-programmer throughout to explain unfamiliar concepts (e.g. FastAPI dependencies, RLS, HTMX, Jinja, Tailwind styling), draft some of the code and assist me with debugging.

Within the watchlist-tracker, Gemini is used for generating the taste profile.