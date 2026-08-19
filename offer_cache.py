"""
Local offer cache for the Tracking Link Generator's search - refreshed
automatically every time a login succeeds for a platform it covers (DAO,
69cash/ImaxCash), not on any timer. Lives only on this computer
(offers_cache.json, gitignored) - never pushed anywhere, never touched by
anything other than sync_platform() below.

This is intentionally separate from sync_offers.py, which pushes into the
TL app's data/offers.json on Vercel - that's a manual, deliberate action
(deploy a public app), while this cache is just a local search index for
convenience.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import PLATFORMS
from lp_tool import ToolError, storage_state_path
from offer_export import fetch_offers_csv, parse_offers_csv

CACHE_PATH = Path(__file__).parent / "offers_cache.json"

# Maps our internal platform key (config.py's PLATFORMS) to the "source"
# label used in the cache and shown in the UI. Only these two platforms are
# relevant to tracking links - OnlineDatingKings isn't part of this at all.
PLATFORM_TO_SOURCE = {
    "daoofleads": "dao",
    "imaxcash": "69cash",
}


def load_cache() -> dict:
    """Returns the current cache, or an empty shell if it doesn't exist yet."""
    if not CACHE_PATH.exists():
        return {"offers": [], "syncedAt": {}}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"offers": [], "syncedAt": {}}


def sync_platform(platform: str, log=print) -> dict:
    """Fetches a fresh offer CSV for `platform` and merges it into the local
    cache, replacing only that platform's own entries (other platforms'
    cached data is left untouched). Returns a summary dict with what changed
    - new offer IDs, and offers that just flipped to paused/passive/etc.
    since the last sync - so a refresh is a reported event, not a silent
    overwrite.

    Raises ToolError if the platform isn't logged in or isn't one this
    cache covers."""
    if platform not in PLATFORM_TO_SOURCE:
        raise ToolError(f"{platform} isn't used by the Tracking Link Generator - nothing to sync.")
    source = PLATFORM_TO_SOURCE[platform]

    state_path = storage_state_path(platform)
    if not state_path.exists():
        raise ToolError(f"Not logged in for {platform} yet.")

    admin_base = PLATFORMS[platform]["admin_base"]
    log(f"Syncing offer list for {platform}...")
    body = fetch_offers_csv(admin_base, state_path)
    fresh_offers = parse_offers_csv(body, source)

    cache = load_cache()
    old_offers = [o for o in cache.get("offers", []) if o.get("source") == source]
    old_by_id = {o["ofid"]: o for o in old_offers}
    new_by_id = {o["ofid"]: o for o in fresh_offers}

    added = sorted(set(new_by_id) - set(old_by_id), key=int)
    newly_paused = sorted(
        (
            ofid for ofid, offer in new_by_id.items()
            if offer["status"] in ("paused", "passive")
            and ofid in old_by_id
            and old_by_id[ofid]["status"] not in ("paused", "passive")
        ),
        key=int,
    )

    other_sources_offers = [o for o in cache.get("offers", []) if o.get("source") != source]
    cache["offers"] = other_sources_offers + fresh_offers
    cache.setdefault("syncedAt", {})[source] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")

    log(f"  {len(fresh_offers)} offers ({len(added)} new, {len(newly_paused)} newly paused/passive)")

    return {
        "source": source,
        "total": len(fresh_offers),
        "added": added,
        "newly_paused": newly_paused,
    }
