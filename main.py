from fastapi import FastAPI, Request, Form, Cookie, HTTPException, Depends
from fastapi.templating import Jinja2Templates
import httpx
from supabase_client import supabase, new_client, get_user_client
from urllib.parse import quote_plus
from fastapi.responses import RedirectResponse, HTMLResponse
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from gemini_client import gemini, MODELS, TasteProfile
from google.genai import types
import asyncio
from uuid import UUID
################################################

TMDB_READ_TOKEN = os.environ["TMDB_READ_TOKEN"].strip()
app=FastAPI()
templates = Jinja2Templates(directory="templates")

# Signing Up 

@app.get("/signup")
async def signup_page(request: Request):
    return templates.TemplateResponse(request=request, name="signup.html", context={})

@app.post("/signup")
async def signup(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        auth_client = new_client()
        result = auth_client.auth.sign_up({"email": email, "password": password})
        auth_client.table("profiles").insert({"id": result.user.id}).execute()
        return RedirectResponse(url="/signin", status_code=303) #303 - "see other"
    except Exception as error_message:
        return templates.TemplateResponse(request=request, name="signup.html", context={"error": str(error_message)})

# Signing In

@app.get("/signin")
async def signin_page(request: Request):
    return templates.TemplateResponse(request=request, name="signin.html", context={})


@app.post("/signin")
async def signin(request: Request, email: str = Form(...), password: str = Form(...)):
    try:
        result = new_client().auth.sign_in_with_password({"email": email, "password": password})
        response = RedirectResponse(url="/", status_code=303)
        # Supabase access tokens expire (usually after 1 hour). Make the cookies expire at the
        # same time, so the app never holds a dead token that still "looks" signed in.
        max_age = result.session.expires_in
        response.set_cookie(
            key="session_token",
            value=result.session.access_token,
            httponly=True,
            max_age=max_age,
        )
        response.set_cookie(
            key="user_email",
            value=result.user.email,
            httponly=True,
            max_age=max_age,
        )
        return response
    except Exception as error_message:
        return templates.TemplateResponse(
            request=request, name="signin.html", context={"error": str(error_message)}
        )

def signin_redirect(request: Request):
    # HTMX requests (e.g. "Add to Watchlist") would follow a normal redirect in the background
    # and paste the whole sign-in page into the movie card. The HX-Redirect header tells
    # HTMX to navigate the full page to /signin instead.
    if request.headers.get("HX-Request") == "true":
        return HTTPException(status_code=401, headers={"HX-Redirect": "/signin"})
    return HTTPException(status_code=303, headers={"Location": "/signin"})


def get_current_user(request: Request, session_token: str | None = Cookie(default=None)):
    if session_token is None:
        raise signin_redirect(request)
    try:
        result = supabase.auth.get_user(session_token)
        return result.user
    except Exception:
        raise signin_redirect(request)


def get_db(request: Request, session_token: str | None = Cookie(default=None)):
    if session_token is None:
        raise signin_redirect(request)
    return get_user_client(session_token)


def get_watched_with_genres(db, user_id):
    # One query: watched entries + their genre names, following entry_genres -> genres
    rows = (
        db.table("watchlist_entries")
        .select("title, rating, watched_at, entry_genres(genres(name))")
        .eq("user_id", user_id)
        .not_.is_("watched_at", "null")
        .execute()
        .data
    )
    watched = []
    for row in rows:
        genres = [eg["genres"]["name"] for eg in row.get("entry_genres", []) if eg.get("genres")]
        watched.append({"title": row["title"], "rating": row["rating"], "genres": genres})
    return watched


@app.post("/signout")
async def signout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_token")
    response.delete_cookie("user_email")
    return response

@app.get("/watchlist")
async def watchlist_page(request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    entries = (
        db.table("watchlist_entries").select("*")
        .eq("user_id", user.id)
        .order("added_at", desc=True)
        .execute().data
    )
    # Split into the two sections: not watched yet vs watched
    to_watch = [e for e in entries if not e["watched_at"]]
    watched = [e for e in entries if e["watched_at"]]
    return templates.TemplateResponse(
        request=request, name="watchlist.html",
        context={"to_watch": to_watch, "watched": watched},
    )


@app.get("/")
async def homepage(request: Request, query: str = "", session_token: str | None = Cookie(default=None)):
    movies = []
    if query:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.themoviedb.org/3/search/movie",
                headers={"Authorization": f"Bearer {TMDB_READ_TOKEN}"},
                params={"query": query},
            )
            movies = response.json()["results"]

    # Which of these movies has the signed-in user already added?
    added_ids = set()
    if session_token:
        try:
            user = supabase.auth.get_user(session_token).user
            rows = get_user_client(session_token).table("watchlist_entries").select("tmdb_id").eq("user_id", user.id).execute().data
            added_ids = {row["tmdb_id"] for row in rows}
        except Exception:
            pass  # not signed in properly - treat nothing as added

    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"movies": movies, "query": query, "added_ids": added_ids}
    )


@app.post("/watchlist/add")
async def add_to_watchlist(
    request: Request,
    tmdb_id: int = Form(...),
    query: str = Form(""),
    user = Depends(get_current_user),
    db = Depends(get_db),
):

    # HTMX sends this header when the button is clicked
    # We reply with just the new button 
    is_htmx = request.headers.get("HX-Request") == "true"
    back_to_search = f"/?query={quote_plus(query)}"

    try:
        async with httpx.AsyncClient() as client:
            details_response = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                headers={"Authorization": f"Bearer {TMDB_READ_TOKEN}"},
            )

            details_response.raise_for_status()
            details = details_response.json()


        insert_result = db.table("watchlist_entries").insert({
            "user_id": user.id,
            "tmdb_id": tmdb_id,
            "title": details["title"],
            "poster_path": details.get("poster_path"),
        }).execute()

        new_entry_id = insert_result.data[0]["id"]
        genre_ids = [g["id"] for g in details.get("genres", [])]

        if genre_ids:
            db.table("entry_genres").insert(
                [{"entry_id": new_entry_id, "genre_id": g_id} for g_id in genre_ids]
            ).execute()

        if is_htmx:
            return templates.TemplateResponse(request=request, name="partials/added_button.html", context={})
        return RedirectResponse(url=back_to_search, status_code=303)
    
    except Exception as error_message:
        print("ADD FAILED:", error_message)
        if is_htmx:
            return HTMLResponse('<p class="mt-auto text-sm text-red-700"> Could not add this movie to the watchlist. Refresh and try again.</p>')
        return RedirectResponse(url=back_to_search, status_code=303)
        

# watch / rate / remove routes

@app.post("/watchlist/{entry_id}/watch")
async def mark_watched(entry_id: UUID, user = Depends(get_current_user), db = Depends(get_db)):
    db.table("watchlist_entries").update(
        {"watched_at": datetime.now(ZoneInfo("Australia/Melbourne")).isoformat()}
    ).eq("id", entry_id).eq("user_id", user.id).execute()
    return RedirectResponse(url="/watchlist", status_code=303)


@app.post("/watchlist/{entry_id}/rate")
async def rate_movie(entry_id: UUID, rating: int = Form(...), user = Depends(get_current_user), db = Depends(get_db)):
    if 1 <= rating <= 10:
        db.table("watchlist_entries").update(
            {"rating": rating}
        ).eq("id", entry_id).eq("user_id", user.id).execute()
    return RedirectResponse(url="/watchlist", status_code=303)


@app.post("/watchlist/{entry_id}/unrate")
async def clear_rating(entry_id: UUID, user = Depends(get_current_user), db = Depends(get_db)):
    db.table("watchlist_entries").update(
        {"rating": None}
    ).eq("id", entry_id).eq("user_id", user.id).execute()
    return RedirectResponse(url="/watchlist", status_code=303)


@app.post("/watchlist/{entry_id}/remove")
async def remove_entry(entry_id: UUID, user = Depends(get_current_user), db = Depends(get_db)):
    db.table("watchlist_entries").delete().eq("id", entry_id).eq("user_id", user.id).execute()
    return RedirectResponse(url="/watchlist", status_code=303)

# stats

@app.get("/stats")
async def stats_page(request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    watched = get_watched_with_genres(db, user.id)

    # Average only over movies that actually have a rating (ignore None)
    ratings = [w["rating"] for w in watched if w["rating"] is not None]
    average = round(sum(ratings) / len(ratings), 1) if ratings else None

    # Genre breakdown: count how many watched movies fall in each genre
    genre_counts = {}
    for w in watched:
        for genre in w["genres"]:
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
    genre_counts = sorted(genre_counts.items(), key=lambda pair: pair[1], reverse=True)
    top_count = genre_counts[0][1] if genre_counts else 1

    return templates.TemplateResponse(
        request=request, name="stats.html",
        context={
            "total_watched": len(watched),
            "rated_count": len(ratings),
            "average": average,
            "genre_counts": genre_counts,
            "top_count": top_count,
        },
    )

# taste

def build_taste_prompt(watched, to_watch_titles):
    summary = []
    for movie in watched:
        rating = movie["rating"]
        genres_text = ", ".join(movie["genres"])
        if rating is not None:
            summary.append(f"- {movie['title']}; rated {rating}/10; genres: {genres_text}")
        else:
            summary.append(f"- {movie['title']}; not rated; genres: {genres_text}")

    movie_list = "\n".join(summary)
    to_watch_list = "\n".join(f"- {title}" for title in to_watch_titles) or "- (none)"

    return f"""You are an expert in all movies, what they're about, their genre and taste.
    You are responsible, as an advisor, to read and understand someone's watch history and generate 
    a taste profile which will involve curating what genres they lean towards and what movies you personally
    recommend for them depending on their taste profile. 

    Their watch history will include the title of each movie, what they rated the movie out of 10 (so you
    can understand the person's own enjoyment of the movie) and a list of genres that movie covers.
    
    **Rules**
    
    - What to write for the profile: a short one-line headline, plus 2-3 sentences on the genres, themes,
    or kinds of story they lean towards, and what their ratings suggest about what they love versus merely like

    - When providing movie recommendations, provide exactly 8

    - Grounding: every reason must name at least one specific movie from their watch history and explain the connection
    in a couple of words.

    - What not to recommend: do not recommend anything in their watch history OR on their watchlist below

    - What to do with unrated movies: if a movie is "not rated", it means they have watched it
    but have not rated it yet. It doesn't mean they didn't like the movie. 
    
    Here is their watch history:
    {movie_list}

    Already on their watchlist (not watched yet), so do not recommend these either:
    {to_watch_list}
    """

async def find_on_tmdb(client, title, year):
    # Gemini only gives us a title and year, so look the movie up on TMDB to get its id and poster.
    # Returns the best match, or None if TMDB has nothing (or the request fails).
    try:
        response = await client.get(
            "https://api.themoviedb.org/3/search/movie",
            headers={"Authorization": f"Bearer {TMDB_READ_TOKEN}"},
            params={"query": title, "primary_release_year": year},
        )
        results = response.json().get("results", [])
        return results[0] if results else None
    except Exception:
        return None


async def find_on_tmdb(client, title, year):
    # Gemini only gives us a title and year, so look the movie up on TMDB to get its id and poster.
    # Returns the best match, or None if TMDB has nothing (or the request fails).
    try:
        response = await client.get(
            "https://api.themoviedb.org/3/search/movie",
            headers={"Authorization": f"Bearer {TMDB_READ_TOKEN}"},
            params={"query": title, "primary_release_year": year},
        )
        results = response.json().get("results", [])
        return results[0] if results else None
    except Exception:
        return None


@app.post("/taste")
async def generate_taste(request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    watched = get_watched_with_genres(db, user.id)
    taste = None
    error = None
    recommendations = []
    added_ids = set()

    if watched:
        # Everything already on the user's list (watched or not), so we never recommend it again
        rows = db.table("watchlist_entries").select("tmdb_id, title, watched_at").eq("user_id", user.id).execute().data
        added_ids = {row["tmdb_id"] for row in rows}
        to_watch_titles = [row["title"] for row in rows if not row["watched_at"]]

        prompt = build_taste_prompt(watched, to_watch_titles)
        for model in MODELS:
            try:
                response = await gemini.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=TasteProfile,
                    ),
                )
                taste = response.parsed
                print("TASTE OK with", model)
                break                                  # got an answer, stop trying
            except Exception as error_message:
                print(f"TASTE FAILED with {model}:", error_message)
                await asyncio.sleep(1)                 # brief pause before the next model

        if taste is None:
            error = "Our AI model is busy right now. Please try again in a minute."
        else:
            # Look up all recommendations on TMDB 
            async with httpx.AsyncClient() as client:
                matches = await asyncio.gather(
                    *[find_on_tmdb(client, rec.movie_name, rec.movie_year) for rec in taste.recommendations]
                )
            for rec, match in zip(taste.recommendations, matches):
                # Skip anything TMDB couldn't find, anything already on the list, and duplicates.
                if not match or match["id"] in added_ids or any(r["id"] == match["id"] for r in recommendations):
                    continue
                recommendations.append({
                    "id": match["id"],
                    "title": match["title"],
                    "year": (match.get("release_date") or "")[:4],
                    "poster_path": match.get("poster_path"),
                    "reason": rec.why_it_fits,
                })
                if len(recommendations) == 5:
                    break

    return templates.TemplateResponse(
        request=request, name="taste.html",
        context={
            "total_watched": len(watched),
            "taste": taste,
            "recommendations": recommendations,
            "added_ids": added_ids,
            "error": error,
        },
    )
