#!/usr/bin/env python3
"""Enrich games.json with poster/metacritic/playtime data from RAWG.io.

Reads the API key from the RAWG_API_KEY environment variable. Resumable:
every game slug that has been attempted (matched or not) is recorded in
rawg_progress.json, so re-running the script (with the same or a new API
key) only fetches games that haven't been looked up yet. Stops early and
saves progress if the key's quota/rate limit is hit, so a fresh key can
pick up where this one left off.
"""
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")
GAMES_PATH = os.path.join(ROOT, "games.json")
PROGRESS_PATH = os.path.join(os.path.dirname(__file__), "rawg_progress.json")

API_KEY = os.environ["RAWG_API_KEY"]
SAVE_EVERY = 200
REQUEST_DELAY = float(os.environ.get("RAWG_REQUEST_DELAY", "0.25"))
MAX_REQUESTS = int(os.environ.get("RAWG_MAX_REQUESTS", "19000"))


def normalize(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def fetch_candidates(title):
    q = urllib.parse.urlencode({"key": API_KEY, "search": title, "page_size": 5})
    url = f"https://api.rawg.io/api/games?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "game-roulette-enrich/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp).get("results", [])


def best_match(title, results):
    norm_title = normalize(title)
    if not norm_title:
        return None
    best, best_score = None, 0
    for r in results:
        norm_name = normalize(r.get("name") or "")
        if not norm_name:
            continue
        if norm_name == norm_title:
            score = 100
        elif norm_title in norm_name or norm_name in norm_title:
            score = 80
        else:
            score = len(set(norm_title) & set(norm_name))
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 60 else None


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_games(games):
    with open(GAMES_PATH, "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False, separators=(",", ":"))


def save_progress(progress):
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, separators=(",", ":"))


def main():
    games = load_json(GAMES_PATH, [])
    progress = load_json(PROGRESS_PATH, {})

    todo = [g for g in games if g["slug"] not in progress]
    print(f"Total games: {len(games)}, already processed: {len(progress)}, remaining: {len(todo)}")

    requests_made = 0
    found = 0
    stop_reason = "processed all remaining"

    for g in todo:
        if requests_made >= MAX_REQUESTS:
            stop_reason = f"reached request budget ({MAX_REQUESTS})"
            break

        try:
            results = fetch_candidates(g["title"])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                stop_reason = f"HTTP {e.code} from RAWG (quota/rate exceeded)"
                break
            print(f"  ! HTTP {e.code} for {g['title']!r}, marking as error")
            progress[g["slug"]] = "err"
            requests_made += 1
            time.sleep(REQUEST_DELAY)
            continue
        except Exception as e:
            print(f"  ! transient error for {g['title']!r}: {e}; retrying once")
            time.sleep(2)
            try:
                results = fetch_candidates(g["title"])
            except Exception as e2:
                print(f"  ! retry failed: {e2}; marking as error")
                progress[g["slug"]] = "err"
                requests_made += 1
                time.sleep(REQUEST_DELAY)
                continue

        requests_made += 1
        match = best_match(g["title"], results)
        if match:
            if match.get("background_image"):
                g["poster"] = match["background_image"]
            if match.get("metacritic"):
                g["metacritic"] = match["metacritic"]
            if match.get("playtime"):
                g["playtime"] = match["playtime"]
            found += 1
            progress[g["slug"]] = "ok"
        else:
            progress[g["slug"]] = "miss"

        if requests_made % SAVE_EVERY == 0:
            save_games(games)
            save_progress(progress)
            print(f"  ... {requests_made} requests, {found} matched, checkpoint saved")

        time.sleep(REQUEST_DELAY)

    save_games(games)
    save_progress(progress)

    print(f"Done. requests={requests_made} found={found} stop_reason={stop_reason}")
    print(f"Remaining unprocessed: {len(games) - len(progress)}")


if __name__ == "__main__":
    main()
