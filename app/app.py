# copyright @oVo-HxBots


import os, datetime
from flask import Flask, jsonify, send_file
from flask import render_template
import requests
from guessit import guessit
from flask import render_template, request

TMDB_KEY = os.getenv("TMDB_KEY")
BASE_URL = os.getenv("BASE_URL")
MEDIA_ROOT = os.getenv("MEDIA_ROOT", "/mnt")

app = Flask(__name__)
db = {}


def tmdb_search(title, year=None, is_series=False):
    url = f"https://api.themoviedb.org/3/search/{'tv' if is_series else 'movie'}"
    params = {"api_key": TMDB_KEY, "query": title}
    if year:
        params["year"] = year
    try:
        r = requests.get(url, params=params, timeout=10).json()
        return r["results"][0] if r.get("results") else None
    except:
        return None
def scan():
    global db
    db = {}

    media_paths = [
        os.path.join(MEDIA_ROOT, d)
        for d in os.listdir(MEDIA_ROOT)
        if os.path.isdir(os.path.join(MEDIA_ROOT, d))
    ]

    for base_path in media_paths:
        folder_name = os.path.basename(base_path)

        # create group if not exists
        if folder_name not in db:
            db[folder_name] = []

        for root, _, files in os.walk(base_path):
            for f in files:
                if not f.lower().endswith((".mkv", ".mp4", ".ts")):
                    continue

                full = os.path.join(root, f)
                rel = full.replace(base_path, "")
                url = f"{BASE_URL}/{folder_name}" + rel.replace(" ", "%20")

                info = guessit(f)
                title = info.get("title", f)
                year = info.get("year")
                season = info.get("season")
                episode = info.get("episode")

                is_series = season is not None
                tmdb = tmdb_search(title, year, is_series)

                poster = ""
                overview = ""
                name = title

                if tmdb:
                    name = tmdb.get("name") if is_series else tmdb.get("title")
                    overview = tmdb.get("overview", "")
                    if tmdb.get("poster_path"):
                        poster = "https://image.tmdb.org/t/p/w500" + tmdb["poster_path"]

                item = {
                    "title": name,
                    "url": url,
                    "poster": poster,
                    "overview": overview,
                    "season": season,
                    "episode": episode
                }

                db[folder_name].append(item)

def generate_m3u():
    lines = ["#EXTM3U"]

    for m in db["movies"]:
        lines.append(
            f'#EXTINF:-1 tvg-logo="{m["poster"]}" group-title="{m["group"]}",{m["title"]}'
        )
        lines.append(m["url"])

    for s in db["series"]:
        name = f'{s["title"]} S{s["season"]}E{s["episode"]}'
        lines.append(
            f'#EXTINF:-1 tvg-logo="{s["poster"]}" group-title="{s["group"]}",{name}'
        )
        lines.append(s["url"])

    with open("/app/playlist.m3u","w") as f:
        f.write("\n".join(lines))

def generate_epg():
    now = datetime.datetime.utcnow()
    xml = ['<?xml version="1.0" encoding="UTF-8"?><tv>']

    i = 0
    for item in db["movies"] + db["series"]:
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

    with open("/app/epg.xml","w") as f:
        f.write("\n".join(xml))

@app.route("/scan")
def rescan():
    scan()
    generate_m3u()
    generate_epg()
    return jsonify({"status":"updated"})

@app.route("/playlist.m3u")
def playlist():
    return send_file("/app/playlist.m3u")

@app.route("/epg.xml")
def epg():
    return send_file("/app/epg.xml")

@app.route("/api")
def api():
    return jsonify(db)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/player")
def player():
    return render_template("player.html")
    
if __name__ == "__main__":
    scan()
    generate_m3u()
    generate_epg()
    app.run(host="0.0.0.0", port=5000)
