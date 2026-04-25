# copyright @oVo-HxBots


import os, datetime
from flask import Flask, jsonify, send_file
from flask import render_template
import requests
from guessit import guessit
from flask import render_template, request

TMDB_KEY = os.getenv("TMDB_KEY")
BASE_URL = "161.118.182.88:8001"
MEDIA_ROOT = os.getenv("MEDIA_ROOT", "/mnt")
RCLONE_URL = "161.118.182.88:8001"

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

    for drive in os.listdir(MEDIA_ROOT):
        drive_path = os.path.join(MEDIA_ROOT, drive)

        if not os.path.isdir(drive_path):
            continue

        media_path = os.path.join(drive_path, "media")

        if not os.path.exists(media_path):
            continue

        for root, _, files in os.walk(media_path):
            for f in files:
                if not f.lower().endswith((".mkv", ".mp4", ".ts")):
                    continue

                full = os.path.join(root, f)

                # Extract path after /media
                rel = full.split("media", 1)[-1]

                url = f"{BASE_URL}" + rel.replace(" ", "%20")

                info = guessit(f)
                folder = os.path.basename(os.path.dirname(full))
                title = folder

                # Determine category (movies / series / etc.)
                parts = rel.strip("/").split("/")
                category = parts[0] if len(parts) > 0 else "other"

                if category not in db:
                    db[category] = []

                item = {
                    "title": title,
                    "url": url,
                    "poster": "",
                    "overview": ""
                }

                db[category].append(item)
                
def generate_m3u():
    lines = ["#EXTM3U"]

    for group, items in db.items():
        for item in items:
            name = item["title"]

            if item.get("season") and item.get("episode"):
                name = f'{name} S{item["season"]}E{item["episode"]}'

            lines.append(
                f'#EXTINF:-1 tvg-logo="{item["poster"]}" group-title="{group.upper()}",{name}'
            )
            lines.append(item["url"])

    with open("/app/playlist.m3u", "w") as f:
        f.write("\n".join(lines))

def generate_epg():
    now = datetime.datetime.utcnow()
    xml = ['<?xml version="1.0" encoding="UTF-8"?><tv>']

    i = 0
    for group, items in db.items():
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

    with open("/app/epg.xml", "w") as f:
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
