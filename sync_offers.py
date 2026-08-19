"""
Pulls the full offer list LIVE from each admin platform's CSV export and
regenerates the TL tracking-links app's data/offers.json - replacing the
manual "paste a CSV in the conversation, ask Claude to update it" workflow
with something that can just be re-run whenever new offers are created.

This is a separate, manual/on-demand action from offer_cache.py's local
cache (which refreshes automatically on login and only feeds this repo's
own Tracking Link Generator) - this script instead writes into the OTHER
project's repo (LP-tool) and is meant to be run deliberately, since its
output is what gets deployed publicly via `vercel --prod`.

Only DaoOfLeads ("dao") and ImaxCash ("69cash") feed the TL app - it has no
concept of a third source, so OnlineDatingKings is not included here even
though lp_tool.py supports it as an admin platform.

A platform that isn't logged in yet is skipped, and its existing entries in
the output file (from the last successful sync) are kept as-is rather than
being dropped - so running this with only one platform logged in doesn't
wipe out the other's data.

Usage:
    python login.py --platform daoofleads      # once, or whenever the session expires
    python login.py --platform imaxcash
    python sync_offers.py --out ../LP-tool/data/offers.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from config import PLATFORMS
from lp_tool import ToolError, storage_state_path
from offer_cache import PLATFORM_TO_SOURCE
from offer_export import fetch_offers_csv, parse_offers_csv


_TL_APP_FIELDS = ("ofid", "title", "offerType", "source")


def _to_tl_app_shape(offer: dict) -> dict:
    """The TL app's data/offers.json shape only has ofid/title/offerType/
    source - status/country/flag are extras offer_export.py added for our
    own local cache and don't belong in that file."""
    return {k: offer[k] for k in _TL_APP_FIELDS}


def sync_offers(*, platforms: list[str], out_path: Path, log=print) -> dict:
    """Fetches a fresh offer CSV for each given platform that's currently
    logged in, keeps the existing data/offers.json entries for any platform
    that's skipped (not logged in), and writes the merged result to
    `out_path`. Returns a summary dict."""
    existing_by_source: dict[str, list[dict]] = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            for offer in existing.get("offers", []):
                existing_by_source.setdefault(offer.get("source"), []).append(offer)
        except Exception:
            pass

    fresh_by_source: dict[str, list[dict]] = {}
    skipped = []
    for platform in platforms:
        if platform not in PLATFORM_TO_SOURCE:
            log(f"Skipping {platform} - the TL app has no offer source for it.")
            continue
        source = PLATFORM_TO_SOURCE[platform]
        state_path = storage_state_path(platform)
        if not state_path.exists():
            log(f"Skipping {platform} - not logged in (python login.py --platform {platform}). Keeping existing '{source}' data.")
            skipped.append(platform)
            continue
        admin_base = PLATFORMS[platform]["admin_base"]
        log(f"Fetching offers for {platform} ({admin_base})...")
        body = fetch_offers_csv(admin_base, state_path)
        offers = [_to_tl_app_shape(o) for o in parse_offers_csv(body, source)]
        log(f"  {len(offers)} offers")
        fresh_by_source[source] = offers

    all_sources = set(existing_by_source) | set(fresh_by_source)
    all_offers: list[dict] = []
    for source in all_sources:
        all_offers.extend(fresh_by_source.get(source, existing_by_source.get(source, [])))

    if not all_offers:
        raise ToolError("No offers to write - nothing was fetched and no existing data was found.")

    now = datetime.now(timezone.utc)
    payload = {
        "offers": all_offers,
        "updatedAt": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    note = f" (kept existing data for: {', '.join(skipped)})" if skipped else ""
    log(f"\nWrote {len(all_offers)} offers to {out_path}{note}")
    return {"count": len(all_offers), "skipped": skipped, "out_path": str(out_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--platform", action="append", choices=sorted(PLATFORM_TO_SOURCE.keys()),
        help="Platform to sync (repeatable). Default: both daoofleads and imaxcash.",
    )
    parser.add_argument(
        "--out", default="../LP-tool/data/offers.json",
        help="Path to write the merged offers.json to (default: ../LP-tool/data/offers.json)",
    )
    args = parser.parse_args()

    platforms = args.platform or sorted(PLATFORM_TO_SOURCE.keys())
    out_path = Path(args.out).expanduser().resolve()

    try:
        sync_offers(platforms=platforms, out_path=out_path, log=print)
    except ToolError as exc:
        print(str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
