from fastapi import FastAPI, Request, Form, Cookie, HTTPException, Depends
from fastapi.templating import Jinja2Templates
import httpx
from supabase_client import supabase, new_client, get_user_client
from urllib.parse import quote_plus
from fastapi.responses import RedirectResponse
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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
    entries = db.table("watchlist_entries").select("*").eq("user_id", user.id).execute().data
    return templates.TemplateResponse(
        request=request, name="watchlist.html", context={"entries": entries}
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
    title: str = Form(...),
    poster_path: str = Form(...),
    query: str = Form(""),
    user = Depends(get_current_user),
    db = Depends(get_db),
):
    back_to_search = f"/?query={quote_plus(query)}"
    try:
        async with httpx.AsyncClient() as client:
            details_response = await client.get(
                f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                headers={"Authorization": f"Bearer {os.environ['TMDB_READ_TOKEN']}"},
            )
            details = details_response.json()

        genre_ids = [g["id"] for g in details.get("genres", [])]

        insert_result = db.table("watchlist_entries").insert({
            "user_id": user.id,
            "tmdb_id": tmdb_id,
            "title": title,
            "poster_path": poster_path,
        }).execute()

        new_entry_id = insert_result.data[0]["id"]

        if genre_ids:
            db.table("entry_genres").insert(
                [{"entry_id": new_entry_id, "genre_id": g_id} for g_id in genre_ids]
            ).execute()

        return RedirectResponse(url=back_to_search, status_code=303)
    except Exception as error_message:
        print("ADD FAILED:", error_message)
        return RedirectResponse(url=back_to_search, status_code=303)


# watch / rate / remove routes

@app.post("/watchlist/{entry_id}/watch")
async def mark_watched(entry_id: str, user = Depends(get_current_user), db = Depends(get_db)):
    db.table("watchlist_entries").update(
        {"watched_at": datetime.now(ZoneInfo("Australia/Melbourne")).isoformat()}
    ).eq("id", entry_id).eq("user_id", user.id).execute()
    return RedirectResponse(url="/watchlist", status_code=303)


@app.post("/watchlist/{entry_id}/rate")
async def rate_movie(entry_id: str, rating: int = Form(...), user = Depends(get_current_user), db = Depends(get_db)):
    if 1 <= rating <= 10:
        db.table("watchlist_entries").update(
            {"rating": rating}
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

