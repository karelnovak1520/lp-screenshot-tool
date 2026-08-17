"""
Local web app for lp_tool.py - a form in the browser instead of typing
commands into the terminal. Runs only on your own computer (127.0.0.1),
nothing is exposed outward.

Always processes just one offer at a time (deliberately, for control).

Usage:
    python app.py
    (then open http://127.0.0.1:5000 in a browser)
"""

from __future__ import annotations

import json
import threading
import time
import uuid

from flask import Flask, Response, jsonify, render_template, request, send_file

from config import DEFAULT_AFID, DEFAULT_PLATFORM, DEFAULT_VIEWPORT, NICHE_LP_NUMBERS, PLATFORMS
from lp_tool import ToolError, run_tool, storage_state_path
from login import login_and_save

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}

_login_lock = threading.Lock()
_logins: dict[str, dict] = {}


@app.route("/")
def login_page():
    return render_template("login.html", platforms=PLATFORMS)


@app.route("/tool")
def tool_page():
    return render_template(
        "tool.html",
        platforms=PLATFORMS,
        default_platform=DEFAULT_PLATFORM,
        niches=sorted(NICHE_LP_NUMBERS.keys()),
        default_afid=DEFAULT_AFID,
        default_width=DEFAULT_VIEWPORT["width"],
        default_height=DEFAULT_VIEWPORT["height"],
    )


@app.route("/login-status")
def login_status():
    result = {}
    for key in PLATFORMS:
        with _login_lock:
            in_progress = _logins.get(key, {}).get("in_progress", False)
        result[key] = {
            "logged_in": storage_state_path(key).exists(),
            "in_progress": in_progress,
        }
    return jsonify(result)


@app.route("/login/start", methods=["POST"])
def login_start():
    platform = request.json.get("platform")
    if platform not in PLATFORMS:
        return jsonify({"error": f"Unknown platform {platform!r}"}), 400

    with _login_lock:
        if _logins.get(platform, {}).get("in_progress"):
            return jsonify({"error": "A login for this platform is already in progress."}), 409
        confirm_event = threading.Event()
        _logins[platform] = {"in_progress": True, "confirm_event": confirm_event, "error": None}

    def worker():
        try:
            login_and_save(platform, wait_for_confirm=confirm_event.wait)
        except Exception as exc:
            with _login_lock:
                _logins[platform]["error"] = str(exc)
        finally:
            with _login_lock:
                _logins[platform]["in_progress"] = False

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/login/confirm", methods=["POST"])
def login_confirm():
    platform = request.json.get("platform")
    with _login_lock:
        entry = _logins.get(platform)
        if not entry or not entry["in_progress"]:
            return jsonify({"error": "No login is in progress for this platform."}), 409
        entry["confirm_event"].set()
    return jsonify({"ok": True})


@app.route("/run", methods=["POST"])
def run():
    data = request.json
    platform = data.get("platform")
    offer_id = (data.get("offer_id") or "").strip()
    niche = (data.get("niche") or "").strip()

    if platform not in PLATFORMS:
        return jsonify({"error": f"Unknown platform {platform!r}"}), 400
    if not offer_id:
        return jsonify({"error": "Missing offer ID."}), 400
    if niche.upper() not in NICHE_LP_NUMBERS:
        return jsonify({"error": f"Unknown niche {niche!r}"}), 400

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        if any(not j["done"] for j in _jobs.values()):
            return jsonify({"error": "Another job is already running - wait for it to finish (deliberately only one at a time)."}), 409
        _jobs[job_id] = {
            "lines": [], "screenshots": {}, "done": False, "result": None, "error": None,
            "event": threading.Event(),
        }

    def log(line: str) -> None:
        with _jobs_lock:
            job = _jobs[job_id]
            job["lines"].append(line)
            job["event"].set()

    def on_screenshot(lp_number: int, path) -> None:
        with _jobs_lock:
            job = _jobs[job_id]
            job["screenshots"][lp_number] = str(path)
            job["event"].set()

    def worker():
        try:
            result = run_tool(
                platform=platform,
                offer_id=offer_id,
                niche=niche,
                niche_id=(data.get("niche_id") or "").strip() or None,
                afid=(data.get("afid") or "").strip() or DEFAULT_AFID,
                width=int(data.get("width") or DEFAULT_VIEWPORT["width"]),
                height=int(data.get("height") or DEFAULT_VIEWPORT["height"]),
                domain=(data.get("domain") or "").strip() or None,
                only_lp=int(data["only_lp"]) if data.get("only_lp") else None,
                dry_run=bool(data.get("dry_run")),
                headless=bool(data.get("headless")),
                log=log,
                on_screenshot=on_screenshot,
            )
            with _jobs_lock:
                _jobs[job_id]["result"] = result
        except ToolError as exc:
            log(f"ERROR: {exc}")
            with _jobs_lock:
                _jobs[job_id]["error"] = str(exc)
        except Exception as exc:
            log(f"UNEXPECTED ERROR: {exc}")
            with _jobs_lock:
                _jobs[job_id]["error"] = str(exc)
        finally:
            with _jobs_lock:
                _jobs[job_id]["done"] = True
                _jobs[job_id]["event"].set()

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id: str):
    def generate():
        sent = 0
        sent_screenshots = set()
        while True:
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None:
                    yield "event: error\ndata: unknown job\n\n"
                    return
                new_lines = job["lines"][sent:]
                sent = len(job["lines"])
                new_screenshot_lps = [lp for lp in job["screenshots"] if lp not in sent_screenshots]
                sent_screenshots.update(new_screenshot_lps)
                done = job["done"]
                result = job["result"]
                error = job["error"]
                job["event"].clear()

            for line in new_lines:
                yield f"data: {json.dumps(line)}\n\n"
            for lp in new_screenshot_lps:
                yield f"event: screenshot\ndata: {json.dumps({'lp': lp})}\n\n"

            if done:
                payload = {"result": result, "error": error}
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                return

            job["event"].wait(timeout=30)
            time.sleep(0.05)

    return Response(generate(), mimetype="text/event-stream")


@app.route("/screenshot/<job_id>/<int:lp_number>")
def screenshot(job_id: str, lp_number: int):
    with _jobs_lock:
        job = _jobs.get(job_id)
        path = job["screenshots"].get(lp_number) if job else None
    if not path:
        return "", 404
    return send_file(path, mimetype="image/png")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
