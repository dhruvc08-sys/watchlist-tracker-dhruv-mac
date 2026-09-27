from fastapi import FastAPI, Request, Form, Cookie, HTTPException, Depends
from fastapi.templating import Jinja2Templates
import httpx
from supabase_client import supabase, new_client, get_user_client
from urllib.parse import quote_plus
from fastapi.responses import RedirectResponse, HTMLResponse
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from gemini_client import gemini, MODELS, TasteProfile
from google.genai import types
import asyncio
from uuid import UUID
from datetime import datetime
################################################

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
        response.set_cookie(
            key="session_token",
            value=result.session.access_token,
            httponly=True,
        )
        response.set_cookie(
            key="user_email",
            value=result.user.email,
            httponly=True,
        )
        return response
    except Exception as error_message:
        return templates.TemplateResponse(
            request=request, name="signin.html", context={"error": str(error_message)}
        )

def get_current_user(session_token: str | None = Cookie(default=None)):
    if session_token is None:
        raise HTTPException(status_code=303, headers={"Location": "/signin"})
    try:
        result = supabase.auth.get_user(session_token)
        return result.user
    except Exception:
        raise HTTPException(status_code=303, headers={"Location": "/signin"})


def get_db(session_token: str | None = Cookie(default=None)):
    if session_token is None:
        raise HTTPException(status_code=303, headers={"Location": "/signin"})
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
                headers={"Authorization": f"Bearer {os.environ['TMDB_READ_TOKEN']}"},
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
                headers={"Authorization": f"Bearer {os.environ['TMDB_READ_TOKEN']}"},
            )

            details_response.raise_for_status()
            details = details_response.json()


        insert_result = db.table("watchlist_entries").insert({
            "user_id": user.id,
            "tmdb_id": tmdb_id,
            "title": title,
            "poster_path": poster_path,
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
async def remove_entry(entry_id: str, user = Depends(get_current_user), db = Depends(get_db)):
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

@app.get("/taste")
async def taste_page(request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    watched = get_watched_with_genres(db, user.id)
    return templates.TemplateResponse(
        request=request, name="taste.html",
        context={
            "total_watched": len(watched),
            "taste": None,
        },
    )

def build_taste_prompt(watched):
    summary = []
    for movie in watched:
        rating = movie["rating"]
        genres_text = ", ".join(movie["genres"])
        if rating is not None:
            summary.append(f"- {movie["title"]}; rated {rating}/10; genres: {genres_text}")
        else:
            summary.append(f"- {movie["title"]}; not rated; genres: {genres_text}")

    movie_list = "\n".join(summary)

    return f"""You are an expert in all movies, what they're about, their genre and taste.
    You are responsible, as an advisor, to read and understand someone's watch history and generate 
    a taste profile which will involve curating what genres they lean towards and what movies you personally
    reccomend for them depending on their taste profile. 

    Their watch history will include the title of each movie, what they rated the movie out of 10 (so you
    can understand the person's own enjoyment of the movie) and a list of genres that movie covers.
    
    **Rules**
    
    - What to write for the profile / what the output should look like: a short one-line headline, plus a paragraph
    on the genres, themes, or kinds of story they lean towards, and what their rating suggests about what they love

    - When providing movie reccomendations, provide 3

    - Grounding: every reason must name at least one specific movie from their watch history and explain the connection
    in a couple of words.

    - What not to reccomend: do not recommend anything already in their watch history

    - What to do with unrated movies: if a movie is "not rated", it means it they have watched it,
    but are they yet to rate it. It doesn't mean they didn't like the movie. 
    
    Here is their watch history:
    {movie_list}
    """
    



@app.post("/taste")
async def generate_taste(request: Request, user = Depends(get_current_user), db = Depends(get_db)):
    watched = get_watched_with_genres(db, user.id)
    taste = None
    error = None

    if watched:
        prompt = build_taste_prompt(watched)
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
                print("TASTE OK with", model, type(taste))
                break                                  # got an answer, stop trying
            except Exception as error_message:
                print(f"TASTE FAILED with {model}:", error_message)
                await asyncio.sleep(1)                 # brief pause before the next model

        if taste is None:
            error = "Our AI model is busy right now. Please try again in a minute."

    return templates.TemplateResponse(
        request=request, name="taste.html",
        context={"total_watched": len(watched), "taste": taste, "error": error},
    )