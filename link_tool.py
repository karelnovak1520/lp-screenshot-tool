"""
Tracking link generator - looks up each offer ID in the local offer cache
first (instant, refreshed automatically on every login - see
offer_cache.py), and falls back to a live admin lookup only for an ID that
isn't in the cache yet (e.g. an offer created after the last login).

The template can be pasted as either a literal {aff_id}/{offer_id}
placeholder template, or a real, already-filled-in example link (e.g. one
copied from a previous generation) - normalize_template() finds whichever
query params are literally named aff_id/offer_id and rewrites just their
value to the placeholder form, so a real example works without editing.
The fixed tracking suffix (&ext_id={click_ID}&source={traffic_source_id})
is then appended automatically - see TRACKING_LINK_SUFFIX.

Usage:
    python link_tool.py --platform daoofleads --offer-id 16793 \\
        --aff-id 2792 --template "https://hubaffillink.eu/?aff_id=22513&offer_id=13923"

    # multiple offers in one run (reuses the same browser/session for any
    # that need a live lookup):
    python link_tool.py --platform daoofleads --offer-id 16793,16813 \\
        --aff-id 2792 --template "..."
"""

from __future__ import annotations

import argparse
import re
import sys

from playwright.sync_api import sync_playwright

from config import DEFAULT_PLATFORM, PLATFORMS, domain_from_offer_title
from lp_tool import ToolError, fetch_offer_title_from_admin, storage_state_path
from offer_cache import PLATFORM_TO_SOURCE, load_cache

# Required by the affiliate network on every tracking link (see the
# generator's own footer note) - {click_ID} and {traffic_source_id} are
# filled in by the network at click time, not by us. This is fixed, not
# user-editable data: a template only ever needs {aff_id} and {offer_id},
# this gets appended automatically and is never stored in a file a sync
# script could touch.
TRACKING_LINK_SUFFIX = "&ext_id={click_ID}&source={traffic_source_id}"

# Shown under every batch of generated links (not just in a tooltip) -
# explains what the network expects the affiliate to do with the two
# placeholders in TRACKING_LINK_SUFFIX. Fixed text, not user-editable.
LINK_FOOTER_NOTE = (
    "IMPORTANT - Please use the following parameters to pass your clickID and sourceID\n"
    "&source={your parameter for source/subid}\n"
    "&ext_id={your parameter for click ID}"
)

# Countries whose tracking link format is completely different from the
# normal {aff_id}/{offer_id} template - generating a normal-looking link for
# them would be wrong, not just imprecise. Only enforced for offers found in
# the local cache (country comes from the admin's CSV export) - a brand new,
# not-yet-cached offer looked up live has no country data to check against.
ATYPICAL_COUNTRIES = {"INDIA"}


_PARAM_PATTERNS = {
    "aff_id": re.compile(r"(?i)\baff_id=[^&]*"),
    "offer_id": re.compile(r"(?i)\boffer_id=[^&]*"),
}


def normalize_template(template: str) -> str:
    """A pasted example is usually a REAL, already-filled-in link (e.g.
    copied from a previous generation or from documentation), not a
    template with literal {aff_id}/{offer_id} placeholders typed in by
    hand. This finds the query parameters literally named `aff_id` and
    `offer_id` - whatever VALUE they currently hold, including an already-
    correct {aff_id}/{offer_id} placeholder - and rewrites just that value
    to the placeholder form. Everything else in the link (domain, path,
    other params, casing) is left exactly as given, so this is safe to
    call unconditionally before every generation."""
    template = _PARAM_PATTERNS["aff_id"].sub("aff_id={aff_id}", template)
    template = _PARAM_PATTERNS["offer_id"].sub("offer_id={offer_id}", template)
    return template


def validate_template(template: str) -> None:
    """After normalize_template(), a template still missing {aff_id} or
    {offer_id} means neither a literal placeholder NOR a query param
    literally named aff_id/offer_id could be found anywhere in it - not
    fixable automatically, since we wouldn't know which value to touch.
    Checked upfront so this fails loudly instead of quietly generating a
    batch of links that all share the same wrong, hardcoded value."""
    missing = [ph for ph in ("{aff_id}", "{offer_id}") if ph not in template]
    if missing:
        raise ToolError(
            f"Couldn't find {' and '.join(missing)} in the link - make sure it has query parameters "
            "literally named aff_id and offer_id somewhere in it (e.g. "
            "https://hubaffillink.eu/?aff_id=22513&offer_id=13923, real numbers are fine)."
        )


def build_tracking_link(template: str, aff_id: str, offer_id: str) -> str:
    """Substitutes {aff_id} and {offer_id} in `template`, then appends the
    fixed tracking suffix (unless the template already ends with it, so
    re-running this on an already-built link doesn't double it up)."""
    link = template.replace("{aff_id}", aff_id).replace("{offer_id}", offer_id)
    if not link.endswith(TRACKING_LINK_SUFFIX):
        link += TRACKING_LINK_SUFFIX
    return link


def _cache_lookup(offer_id: str, source: str, cache: dict) -> dict | None:
    for offer in cache.get("offers", []):
        if offer.get("ofid") == offer_id and offer.get("source") == source:
            return offer
    return None


def generate_tracking_links(
    *,
    platform: str,
    offer_ids: list[str],
    aff_id: str,
    template: str,
    headless: bool = True,
    log=print,
) -> list[dict]:
    """Resolves each offer ID (cache first, live admin lookup as a fallback
    for anything not cached) and builds its tracking link. Returns a list of
    dicts (one per offer_id, in the given order): {"offer_id", "title",
    "domain", "link", "from_cache", "error"} - `error` is None on success,
    so a single bad ID in a batch doesn't stop the rest.

    Raises ToolError for a fatal, run-wide problem (missing login, needed
    only for IDs not already in the cache, or a template missing a required
    placeholder) - as opposed to a single offer's error, which is captured
    per-item instead."""
    template = normalize_template(template)
    validate_template(template)

    if platform not in PLATFORMS:
        raise ToolError(f"Unknown platform {platform!r}. Valid: {sorted(PLATFORMS)}")
    admin_base = PLATFORMS[platform]["admin_base"]
    source = PLATFORM_TO_SOURCE.get(platform)

    cache = load_cache()
    results: dict[str, dict] = {}
    need_live: list[str] = []

    for offer_id in offer_ids:
        cached = _cache_lookup(offer_id, source, cache) if source else None
        if cached:
            country = (cached.get("country") or "").strip().upper()
            if country in ATYPICAL_COUNTRIES:
                results[offer_id] = {
                    "offer_id": offer_id, "title": cached["title"],
                    "domain": domain_from_offer_title(cached["title"]),
                    "country": cached.get("country"), "flag": cached.get("flag"),
                    "link": None, "from_cache": True,
                    "error": (
                        f"{country.title()} offer - its tracking link format is completely different. "
                        "Pull it manually from the affiliate account instead of using this generator."
                    ),
                }
                log(f"[{offer_id}] BLOCKED - {country.title()} offer, needs a manual link")
                continue
            results[offer_id] = {
                "offer_id": offer_id, "title": cached["title"],
                "domain": domain_from_offer_title(cached["title"]),
                "country": cached.get("country"), "flag": cached.get("flag"),
                "link": build_tracking_link(template, aff_id, offer_id),
                "from_cache": True, "error": None,
            }
            log(f"[{offer_id}] (cache) {cached['title']}")
        else:
            need_live.append(offer_id)

    if need_live:
        state_path = storage_state_path(platform)
        if not state_path.exists():
            for offer_id in need_live:
                results[offer_id] = {
                    "offer_id": offer_id, "title": None, "domain": None,
                    "country": None, "flag": None, "link": None, "from_cache": False,
                    "error": f"Not in the local cache, and not logged in for {platform} to look it up live.",
                }
        else:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                admin_context = browser.new_context(storage_state=str(state_path))
                admin_page = admin_context.new_page()
                for offer_id in need_live:
                    try:
                        title = fetch_offer_title_from_admin(admin_page, admin_base, offer_id, platform)
                        domain = domain_from_offer_title(title)
                        link = build_tracking_link(template, aff_id, offer_id)
                        log(f"[{offer_id}] (live) {title} -> {link}")
                        results[offer_id] = {
                            "offer_id": offer_id, "title": title, "domain": domain,
                            "country": None, "flag": None,
                            "link": link, "from_cache": False, "error": None,
                        }
                    except Exception as exc:
                        log(f"[{offer_id}] ERROR: {exc}")
                        results[offer_id] = {
                            "offer_id": offer_id, "title": None, "domain": None,
                            "country": None, "flag": None,
                            "link": None, "from_cache": False, "error": str(exc),
                        }
                browser.close()

    return [results[offer_id] for offer_id in offer_ids]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, choices=sorted(PLATFORMS.keys()), help="Which admin to use (default daoofleads)")
    parser.add_argument("--offer-id", required=True, help="One offer ID, or several separated by commas")
    parser.add_argument("--aff-id", required=True, help="Affiliate ID to substitute into the template")
    parser.add_argument("--template", required=True, help="Any real example link with aff_id=.../offer_id=... query params (real numbers are fine, they get replaced) - the tracking suffix is appended automatically")
    parser.add_argument("--headless", action="store_true", help="run the browser without a visible window")
    args = parser.parse_args()

    offer_ids = [o.strip() for o in args.offer_id.split(",") if o.strip()]

    try:
        results = generate_tracking_links(
            platform=args.platform,
            offer_ids=offer_ids,
            aff_id=args.aff_id,
            template=args.template,
            headless=args.headless,
            log=print,
        )
    except ToolError as exc:
        print(str(exc))
        sys.exit(1)

    errors = [r for r in results if r["error"]]
    if errors:
        print(f"\n{len(errors)} of {len(results)} offer(s) failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
