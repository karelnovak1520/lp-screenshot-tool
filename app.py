"""
Local web app for lp_tool.py - a form in the browser instead of typing
commands into the terminal. Runs only on your own computer (127.0.0.1),
nothing is exposed outward.

Always processes just one offer at a time (deliberately, for control).

Usage:
    python app.py
    (then open http://127.0.0.1:5001 in a browser)
"""

from __future__ import annotations

import json
import threading
import time
import uuid

from flask import Flask, Response, jsonify, render_template, request, send_file

from config import DEFAULT_AFID, DEFAULT_PLATFORM, DEFAULT_VIEWPORT, NICHE_LP_NUMBERS, PLATFORMS
from lp_tool import ToolError, run_tool, storage_state_path
from link_tool import LINK_FOOTER_NOTE, TRACKING_LINK_SUFFIX, build_new_affiliate_document, generate_tracking_links
from offer_cache import PLATFORM_TO_SOURCE, load_cache, sync_platform
from login import login_and_save

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# Only these two platforms feed the Tracking Link Generator - the TL app
# has no concept of a third source, so OnlineDatingKings is left out of its
# login widget entirely (it's still fully available on the LP tool page).
LINK_PLATFORMS = {k: v for k, v in PLATFORMS.items() if k in PLATFORM_TO_SOURCE}

_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}

_login_lock = threading.Lock()
_logins: dict[str, dict] = {}


@app.route("/")
def home_page():
    return render_template("home.html")


@app.route("/login")
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


@app.route("/links")
def links_page():
    return render_template("links_hub.html")


@app.route("/links/existing")
def links_existing_page():
    # link_sources drives the "Source" search dropdown, keyed by the same
    # "dao" / "69cash" strings used in offers_cache.json - NOT the platform
    # keys (daoofleads / imaxcash) that the login widget below it uses.
    link_sources = [
        {"source": source, "label": PLATFORMS[platform]["label"]}
        for platform, source in PLATFORM_TO_SOURCE.items()
    ]
    return render_template(
        "links_existing.html",
        platforms=LINK_PLATFORMS,
        link_sources=link_sources,
        default_afid=DEFAULT_AFID,
        tracking_link_suffix=TRACKING_LINK_SUFFIX,
        link_footer_note=LINK_FOOTER_NOTE,
    )


@app.route("/links/new")
def links_new_page():
    return render_template(
        "links_new.html",
        platforms={"daoofleads": PLATFORMS["daoofleads"]},
        default_afid=DEFAULT_AFID,
    )


@app.route("/links/new/run", methods=["POST"])
def links_new_run():
    # DaoOfLeads only for now - that's the only platform this onboarding
    # document exists for.
    data = request.json
    aff_id = (data.get("aff_id") or "").strip()
    postback = (data.get("postback") or "").strip()
    fraud_email = (data.get("fraud_email") or "").strip()
    template = (data.get("template") or "").strip()
    offer_ids_raw = (data.get("offer_ids") or "").strip()

    if not aff_id:
        return jsonify({"error": "Missing affiliate ID."}), 400
    if not postback:
        return jsonify({"error": "Missing postback."}), 400
    if not fraud_email:
        return jsonify({"error": "Missing fraud-report email."}), 400
    if not template:
        return jsonify({"error": "Missing URL template."}), 400
    offer_ids = [o.strip() for o in offer_ids_raw.replace(",", "\n").splitlines() if o.strip()]
    if not offer_ids:
        return jsonify({"error": "Missing offer ID(s)."}), 400

    try:
        results = generate_tracking_links(
            platform="daoofleads",
            offer_ids=offer_ids,
            aff_id=aff_id,
            template=template,
            headless=True,
            log=lambda line: None,
        )
    except ToolError as exc:
        return jsonify({"error": str(exc)}), 400

    document = build_new_affiliate_document(results, postback, fraud_email)
    errors = [{"offer_id": r["offer_id"], "error": r["error"]} for r in results if r["error"]]
    return jsonify({"document": document, "errors": errors})


@app.route("/links/offers")
def links_offers():
    """Serves the local offer cache for client-side search - refreshed
    automatically on login (see offer_cache.py), or on demand via
    /links/sync below."""
    cache = load_cache()
    return jsonify(cache)


@app.route("/links/sync", methods=["POST"])
def links_sync():
    """Manual refresh, in case the user knows new offers exist but doesn't
    want to fully re-log-in just to see them."""
    platform = request.json.get("platform")
    if platform not in LINK_PLATFORMS:
        return jsonify({"error": f"Unknown platform {platform!r}"}), 400
    try:
        summary = sync_platform(platform, log=lambda line: None)
    except ToolError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(summary)


@app.route("/links/run", methods=["POST"])
def links_run():
    data = request.json
    platform = data.get("platform")
    aff_id = (data.get("aff_id") or "").strip()
    template = (data.get("template") or "").strip()
    offer_ids_raw = (data.get("offer_ids") or "").strip()

    if platform not in PLATFORMS:
        return jsonify({"error": f"Unknown platform {platform!r}"}), 400
    if not aff_id:
        return jsonify({"error": "Missing affiliate ID."}), 400
    if not template:
        return jsonify({"error": "Missing URL template."}), 400
    offer_ids = [o.strip() for o in offer_ids_raw.replace(",", "\n").splitlines() if o.strip()]
    if not offer_ids:
        return jsonify({"error": "Missing offer ID(s)."}), 400

    try:
        results = generate_tracking_links(
            platform=platform,
            offer_ids=offer_ids,
            aff_id=aff_id,
            template=template,
            headless=True,
            log=lambda line: None,
        )
    except ToolError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"results": results})


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
    niche_raw = (data.get("niche") or "").strip()
    niche = niche_raw.upper() if niche_raw else None

    if platform not in PLATFORMS:
        return jsonify({"error": f"Unknown platform {platform!r}"}), 400
    if not offer_id:
        return jsonify({"error": "Missing offer ID."}), 400
    if niche is not None and niche not in NICHE_LP_NUMBERS:
        return jsonify({"error": f"Unknown niche {niche_raw!r}"}), 400

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
    # Not 5000 - on macOS that's squatted by the AirPlay Receiver
    # (ControlCenter), which silently answers instead of this app and
    # returns a 403 "Access to localhost was denied" page in the browser.
    app.run(host="127.0.0.1", port=5001, debug=False, threaded=True)
