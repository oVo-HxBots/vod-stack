# Copyright @oVo-HxBots

from flask import Flask, request, jsonify, render_template, send_file
import mysql.connector
import time, os, datetime, requests
from bs4 import BeautifulSoup
from guessit import guessit
import hashlib, time, secrets

active_streams = {}     # token -> {user, start}
bandwidth = {}          # user -> bytes
MAX_TOKEN_LIFETIME = 3600  # 1 hour

app = Flask(__name__)

# CONFIG
BASE_URL = "os.getenv("BASE_URL")
DB_HOST = "os.getenv("DB_HOST")
DB_USER = "os.getenv("DB_USER")
DB_PASS = "os.getenv("DB_PASS")
DB_NAME = "os.getenv("DB_NAME")
TMDB_KEY = os.getenv("TMDB_KEY")

db_cache = {}

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

def tmdb(title):
    try:
        r = requests.get(
            "https://api.themoviedb.org/3/search/multi",
            params={"api_key": TMDB_KEY, "query": title}
        ).json()

        if r.get("results"):
            d = r["results"][0]
            poster = ""
            if d.get("poster_path"):
                poster = "https://image.tmdb.org/t/p/w500" + d["poster_path"]

            return {
                "title": d.get("title") or d.get("name"),
                "overview": d.get("overview", ""),
                "poster": poster
            }
    except:
        pass

    return {"title": title, "overview": "", "poster": ""}

# ---------------- SCAN ----------------

def scan():
    global db_cache
    db_cache = {}

    r = requests.get(BASE_URL)
    soup = BeautifulSoup(r.text, "html.parser")

    for link in soup.find_all("a"):
        href = link.get("href")

        if not href or href == "../":
            continue

        category = href.strip("/")
        db_cache[category] = []

        cat_url = f"{BASE_URL}/{category}/"

        r2 = requests.get(cat_url)
        soup2 = BeautifulSoup(r2.text, "html.parser")

        for item in soup2.find_all("a"):
            sub = item.get("href")
            if not sub or sub == "../":
                continue

            folder = sub.strip("/")
            folder_url = f"{cat_url}{folder}/"

            try:
                r3 = requests.get(folder_url)
                soup3 = BeautifulSoup(r3.text, "html.parser")

                for f in soup3.find_all("a"):
                    file = f.get("href")

                    if file.endswith((".mkv", ".mp4")):
                        file_url = f"{folder_url}{file}"

                        meta = tmdb(folder)

                        db_cache[category].append({
                            "title": meta["title"],
                            "url": file_url,
                            "poster": meta["poster"],
                            "overview": meta["overview"]
                        })
            except:
                continue

    print("SCAN DONE:", len(db_cache))

# ---------------- PLAYLIST ----------------

def generate_m3u():
    lines = ["#EXTM3U"]

    for group, items in db_cache.items():
        for item in items:
            lines.append(
                f'#EXTINF:-1 tvg-logo="{item["poster"]}" group-title="{group}",{item["title"]}'
            )
            lines.append(item["url"])

    with open("playlist.m3u", "w") as f:
        f.write("\n".join(lines))

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

@app.route("/scan")
def rescan():
    scan()
    generate_m3u()
    generate_epg()
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

    
    
# ---------------- START ----------------

if __name__ == "__main__":
    scan()
    generate_m3u()
    generate_epg()
    app.run(host="0.0.0.0", port=5000)
