# Copyright @oVo-HxBots

from flask import Flask, request, jsonify, redirect, render_template, send_file
import mysql.connector
import time, os, datetime, requests
from bs4 import BeautifulSoup
from guessit import guessit
import hashlib, time, secrets
from urllib.parse import quote
import requests
import re


active_streams = {}     # token -> {user, start}
bandwidth = {}          # user -> bytes
MAX_TOKEN_LIFETIME = 3600  # 1 hour
tmdb_cache = {}

app = Flask(__name__)

# CONFIG
BASE_URL = os.getenv("BASE_URL")
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
TMDB_KEY = os.getenv("TMDB_KEY")
ALIST_API = os.getenv("ALIST_API")
ALIST_PATH = "/Movies"

db_cache = {
    "movies": [],
    "series": [],
    "anime": []
}

# ---------------- DB ----------------

def db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS tokens (
  id INT AUTO_INCREMENT PRIMARY KEY,
  token VARCHAR(64) UNIQUE,
  username VARCHAR(50),
  created BIGINT,
  expires BIGINT,
  active INT DEFAULT 1
);
    """)
    conn.commit()
    conn.close()

init_db()

# ---------------- TMDB ----------------

def detect_category(path):
    p = path.lower()

    if "anime" in p:
        return "anime"

    if "season" in p or "s01" in p:
        return "series"

    return "movies"


def tmdb_cached(title, is_series=False):
    key = f"{title}_{is_series}"

    if key in tmdb_cache:
        return tmdb_cache[key]

    data = tmdb(title, is_series=is_series)
    tmdb_cache[key] = data
    return data


def get_categories():
    root = alist_list("/Movies")
    return [f["name"] for f in root if f.get("is_dir")]

def list_dir(path):
    try:
        r = requests.post(ALIST_API, json={
            "path": path,
            "password": ""
        }, timeout=10).json()

        if r.get("code") != 200:
            return []

        return r.get("data", {}).get("content", []) or []

    except:
        return []



def tmdb(title, is_series=False):
    url = f"https://api.themoviedb.org/3/search/{'tv' if is_series else 'movie'}"
    params = {"api_key": TMDB_KEY, "query": title}

    try:
        r = requests.get(url, params=params).json()
        if not r.get("results"):
            return {"title": title, "poster": "", "overview": "", "genres": []}

        data = r["results"][0]

        genres = data.get("genre_ids", [])

        # 🔥 map genre IDs to names (simple map)
        GENRE_MAP = {
            28:"Action", 35:"Comedy", 18:"Drama", 27:"Horror",
            878:"Sci-Fi", 10749:"Romance", 80:"Crime"
        }

        genre_names = [GENRE_MAP.get(g, "Other") for g in genres]

        return {
            "title": data.get("title") or data.get("name"),
            "poster": "https://image.tmdb.org/t/p/w500" + data.get("poster_path",""),
            "overview": data.get("overview",""),
            "genres": genre_names
        }

    except:
        return {"title": title, "poster": "", "overview": "", "genres": []}

# ---------------- SCAN ----------------

def is_series(name):
    return re.search(r"S\d+E\d+", name, re.IGNORECASE)

def scan_category(category):
    base = f"/Movies/{category}"
    items = []

    def walk(path):
        files = alist_list(path) or []

        for f in files:
            name = f["name"]
            full = f"{path}/{name}"

            if f["is_dir"]:
                walk(full)
            else:
                if not name.lower().endswith((".mp4", ".mkv")):
                    continue

                folder = path.split("/")[-1]

                series = is_series(name)

                items.append({
                    "title": folder,
                    "file": name,
                    "path": full,
                    "category": category,
                    "type": "series" if series else "movie"
                })

    walk(base)
    return items


def scan():
    global db_cache

    db_cache = {
        "movies": {},
        "series": {}
    }

    categories = get_categories()

    for cat in categories:
        items = scan_category(cat)

        db_cache["movies"][cat] = []
        db_cache["series"][cat] = []

        for item in items:
            meta = tmdb_cached(item["title"], item["type"] == "series")

            entry = {
                "title": meta["title"],
                "poster": meta["poster"],
                "overview": meta["overview"],
                "genres": meta["genres"],
                "url": f"/stream{quote(path, safe='/')}",
                "type": item["type"]  # movie / series
            }

            if item["type"] == "series":
                db_cache["series"][cat].append(entry)
            else:
                db_cache["movies"][cat].append(entry)

    print("Scan complete")



def scan_folder(path, category):
    items = list_dir(path)

    for item in items:
        if not isinstance(item, dict):
            continue

        name = item.get("name")

        if item.get("is_dir"):
            # 🔁 go deeper
            scan_folder(f"{path}/{name}", category)

        else:
            if not name.lower().endswith((".mp4", ".mkv")):
                continue

            rel = f"{path}/{name}"
            url = f"/stream{quote(rel, safe='/')}"

            # 🧠 detect title from parent folder
            folder_name = path.split("/")[-1]

            meta = tmdb_cached(folder_name)

            entry = {
                "title": meta["title"],
                "url": url,
                "poster": meta["poster"],
                "overview": meta["overview"]
            }

            with lock:
                db_cache[category].append(entry)


def generate_m3u():
    lines = ["#EXTM3U"]

    for group, items in db_cache.items():
        for item in items:
            lines.append(
    f'#EXTINF:-1 tvg-logo="{item["poster"]}" group-title="{group}",{item["title"]}'
)
            lines.append(BASE_URL + item["url"])

    with open("playlist.m3u", "w") as f:
        f.write("\n".join(lines))


def generate_genre_playlists():
    BASE = os.getenv("BASE_URL")

    base_dir = "/app/playlists"
    movies_dir = f"{base_dir}/Movies"
    series_dir = f"{base_dir}/Series"

    os.makedirs(movies_dir, exist_ok=True)
    os.makedirs(series_dir, exist_ok=True)

    movie_genres = {}
    series_genres = {}

    # 🔁 group by genre
    for content_type in ["movies", "series"]:
        for category, items in db_cache[content_type].items():
            for item in items:

                genres = item.get("genres", ["Other"])

                for g in genres:
                    target = movie_genres if content_type == "movies" else series_genres

                    if g not in target:
                        target[g] = []

                    target[g].append(item)

    # 🎬 create movie playlists
    for genre, items in movie_genres.items():
        lines = ["#EXTM3U"]

        for item in items:
            url = BASE + item["url"]

            lines.append(
                f'#EXTINF:-1 tvg-id="{item["title"]}" tvg-logo="{item["poster"]}" group-title="{genre}",{item["title"]}'
            )
            lines.append(url)

        with open(f"{movies_dir}/{genre}.m3u", "w") as f:
            f.write("\n".join(lines))

    # 📺 create series playlists
    for genre, items in series_genres.items():
        lines = ["#EXTM3U"]

        for item in items:
            url = BASE + item["url"]

            lines.append(
                f'#EXTINF:-1 tvg-id="{item["title"]}" tvg-logo="{item["poster"]}" group-title="{genre}",{item["title"]}'
            )
            lines.append(url)

        with open(f"{series_dir}/{genre}.m3u", "w") as f:
            f.write("\n".join(lines))

    print("Genre playlists generated")


# ---------------- EPG ----------------

def generate_epg():
    now = datetime.datetime.utcnow()
    xml = ['<?xml version="1.0" encoding="UTF-8"?><tv>']

    i = 0
    for group, items in db_cache.items():
        for item in items:
            start = now.strftime("%Y%m%d%H%M%S +0000")
            end = (now + datetime.timedelta(hours=2)).strftime("%Y%m%d%H%M%S +0000")

            xml.append(f"""
            <programme start="{start}" stop="{end}" channel="ch{i}">
                <title>{item["title"]}</title>
                <desc>{item["overview"]}</desc>
                <icon src="{item["poster"]}"/>
            </programme>
            """)
            i += 1

    xml.append("</tv>")

    with open("epg.xml", "w") as f:
        f.write("\n".join(xml))

# ---------------- ROUTES ----------------

@app.route("/playlist/movies/<genre>.m3u")
def movie_genre(genre):
    return send_file(f"/app/playlists/Movies/{genre}.m3u")


@app.route("/playlist/series/<genre>.m3u")
def series_genre(genre):
    return send_file(f"/app/playlists/Series/{genre}.m3u")

@app.route("/scan")
def rescan():
    scan()
    generate_epg()
    generate_genre_playlists()
    return {"status": "updated", "items": sum(len(v) for v in db_cache.values())}

@app.route("/playlist.m3u")
def playlist():
    return send_file("playlist.m3u")

@app.route("/epg.xml")
def epg():
    return send_file("epg.xml")

@app.route("/api")
def api():
    return jsonify(db_cache)

# ---------------- USERS ----------------

@app.route("/admin/user/add", methods=["POST"])
def add_user():
    data = request.json
    conn = db()
    c = conn.cursor()

    c.execute(
        "INSERT INTO users (username,password,max_conn,expires) VALUES (%s,%s,%s,%s)",
        (data["username"], data["password"], data["max_conn"], data["expires"])
    )

    conn.commit()
    conn.close()
    return {"status": "added"}

@app.route("/auth")
def auth():
    u = request.args.get("username")
    p = request.args.get("password")

    conn = db()
    c = conn.cursor()

    c.execute("SELECT password,expires FROM users WHERE username=%s", (u,))
    user = c.fetchone()

    conn.close()

    if not user or user[0] != p or user[1] < int(time.time()):
        return {"auth": 0}

    return {"auth": 1}

# ---------------- UI ----------------

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/admin")
def admin():
    return render_template("admin.html")

@app.route("/play")
def play():
    u = request.args.get("username")
    p = request.args.get("password")
    path = request.args.get("path")  # e.g. movies/movie.mkv

    conn = db()
    c = conn.cursor()

    c.execute("SELECT password,expires,max_conn,status FROM users WHERE username=%s", (u,))
    user = c.fetchone()
    conn.close()

    if not user or user[0] != p or user[3] == 0 or user[1] < int(time.time()):
        return {"error": "auth failed"}, 403

    # connection limit (per user, REAL)
    current = sum(1 for t in active_streams.values() if t["user"] == u)
    if current >= user[2]:
        return {"error": "max connections reached"}, 403

    token = secrets.token_hex(16)

    conn = db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO tokens (token,username,created,expires) VALUES (%s,%s,%s,%s)",
        (token, u, int(time.time()), int(time.time()) + MAX_TOKEN_LIFETIME)
    )
    conn.commit()
    conn.close()

    active_streams[token] = {"user": u, "start": time.time()}

    return {
        "url": f"/stream/{path}?token={token}"
    }


@app.route("/validate_token")
def validate_token():
    token = request.args.get("token")

    conn = db()
    c = conn.cursor()
    c.execute("SELECT username,expires FROM tokens WHERE token=%s AND active=1", (token,))
    row = c.fetchone()
    conn.close()

    if not row:
        return "", 403

    if row[1] < int(time.time()):
        return "", 403

    return "", 200


@app.route("/stream_end")
def stream_end():
    token = request.args.get("token")

    if token in active_streams:
        user = active_streams[token]["user"]
        active_streams.pop(token)

    conn = db()
    c = conn.cursor()
    c.execute("UPDATE tokens SET active=0 WHERE token=%s", (token,))
    conn.commit()
    conn.close()

    return {"status": "ended"}

@app.route("/admin/streams")
def streams():
    return {
        "active": len(active_streams),
        "users": active_streams
    }

@app.route("/admin/bandwidth")
def bw():
    return bandwidth




@app.route("/stream/<path:filepath>")
def stream(filepath):

    # convert URL path → Alist path
    alist_path = "/" + filepath

    try:
        r = requests.post("http://vod.stableserver.one/api/fs/get", json={
            "path": alist_path,
            "password": ""
        }, timeout=10).json()

        if r.get("code") != 200:
            return {"error": "file not found"}, 404

        url = r.get("data", {}).get("raw_url")

        if not url:
            return {"error": "no link"}, 404

        # 🔥 redirect to real stream
        return redirect(url)

    except Exception as e:
        return {"error": str(e)}, 500
    
# ---------------- START ----------------

if __name__ == "__main__":
    scan()
    generate_m3u()
    generate_epg()
    app.run(host="0.0.0.0", port=5000)
