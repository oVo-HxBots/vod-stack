# copyright @oVo-HxBots

import requests
from bs4 import BeautifulSoup
import os, datetime
from flask import Flask, jsonify, send_file
from flask import render_template
import requests
from guessit import guessit
from flask import render_template, request
import subprocess

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

    base_url = BASE_URL  # http://161.118.182.88:8001

    categories = ["movies"]  # add "series", "4k" later

    for category in categories:
        url = f"{base_url}/{category}/"

        try:
            r = requests.get(url)
        except:
            continue

        soup = BeautifulSoup(r.text, "html.parser")

        db[category] = []

        for link in soup.find_all("a"):
            href = link.get("href")

            if not href or href == "../":
                continue

            # folder inside movies
            movie_folder = href.strip("/")

            movie_url = f"{url}{movie_folder}/"

            try:
                r2 = requests.get(movie_url)
                soup2 = BeautifulSoup(r2.text, "html.parser")

                for file_link in soup2.find_all("a"):
                    fhref = file_link.get("href")

                    if fhref.endswith(".mkv") or fhref.endswith(".mp4"):
                        file_url = f"{movie_url}{fhref}"

                        db[category].append({
                            "title": movie_folder,
                            "url": file_url,
                            "poster": "",
                            "overview": ""
                        })

            except:
                continue

    print("DB:", db)


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

@app.route("/admin")
def admin():
    return render_template("admin.html")

@app.route("/admin/scan")
def admin_scan():
    scan()
    return {"status": "scan complete", "items": sum(len(v) for v in db.values())}


@app.route("/admin/cache/clear")
def clear_cache():
    cache.clear()
    return {"status": "cache cleared"}


@app.route("/admin/stats")
def stats():
    return {
        "categories": {k: len(v) for k, v in db.items()},
        "total": sum(len(v) for v in db.values())
    }


@app.route("/admin/rclone/start")
def rclone_start():
    try:
        subprocess.Popen([
            "rclone", "serve", "http", "union:",
            "--addr", ":8001",
            "--read-only",
            "--buffer-size", "0"
        ])
        return {"status": "rclone started"}
    except Exception as e:
        return {"error": str(e)}


@app.route("/admin/rclone/stop")
def rclone_stop():
    try:
        subprocess.run(["pkill", "-f", "rclone serve"])
        return {"status": "rclone stopped"}
    except Exception as e:
        return {"error": str(e)}


@app.route("/admin/rclone/status")
def rclone_status():
    try:
        result = subprocess.check_output(["pgrep", "-f", "rclone serve"])
        return {"running": True}
    except:
        return {"running": False}
    
if __name__ == "__main__":
    scan()
    generate_m3u()
    generate_epg()
    app.run(host="0.0.0.0", port=5000)
