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
A bare platform URL with no query string at all (e.g.
"https://hubaffillink.eu") also works - aff_id={aff_id}&offer_id={offer_id}
gets appended automatically, since those are the network's standard
param names and don't need a real example to be spelled out.
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

from config import DEFAULT_PLATFORM, PLATFORMS, domain_from_offer_title, looks_like_domain
from lp_tool import ToolError, fetch_offer_title_from_admin, storage_state_path
from offer_cache import PLATFORM_TO_SOURCE, load_cache
from offer_export import COUNTRY_ISO, country_flag

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

# Fixed boilerplate for the DaoOfLeads new-affiliate onboarding document
# (sections 2-6) - the parts that vary per affiliate are the postback value
# (section 2) and the fraud-report email (section 4), both substituted in
# via .format() below. DaoOfLeads-only for now; other platforms would need
# their own version of this if/when that document exists for them.
NEW_AFFILIATE_BOILERPLATE_DAO = """\
------------------------------------------------------
2) POSTBACK
We set up the following global postback for all offers, the postback will fire only for the approved lead that will be paid.

{postback}

In our DAOofLEADS platform, we work with conversion(lead) statuses (Pending, Approved, Rejected)
When the lead is created, it has status "PENDING", then our antifraud tool decides if the lead is fraud and mark it as "REJECTED", or if the lead is valid and mark it as "APPROVED".

------------------------------------------------------
3) CAPS
The CAPs per offer is provided in the offer list you have received, please confirm with your affiliate manager the Monthly Budget that has been set for you.
Notice that the detailed information about current CAP/Budget limits can be seen directly in your account:
https://affiliate.daoofleads.com/en/offer/capping

------------------------------------------------------
4) FRAUD REPORTS
As default, you will receive the fraud reports daily to the following email address:
{fraud_email}
You can see here all the details: https://affiliate.daoofleads.com/en/reports/fraud-list

------------------------------------------------------
5) BILLING SETTINGS
Please fill up your missing billing information here: https://affiliate.daoofleads.com/en/account/edit

------------------------------------------------------
6) TEST LINK
Please provide us with a test link to verify the conversions are properly set.
"""


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
    call unconditionally before every generation.

    If either one is still missing afterward (e.g. a bare platform URL with
    no query string at all, like "https://hubaffillink.eu") it's appended
    directly, using the network's standard param names - the affiliate ID
    and offer ID are already known from elsewhere in the flow, so a real
    example link isn't required just to name them."""
    template = _PARAM_PATTERNS["aff_id"].sub("aff_id={aff_id}", template)
    template = _PARAM_PATTERNS["offer_id"].sub("offer_id={offer_id}", template)

    missing = [ph for ph in ("aff_id={aff_id}", "offer_id={offer_id}") if f"{{{ph.split('=')[0]}}}" not in template]
    if missing:
        separator = "&" if "?" in template else "?"
        template = template.rstrip("&?") + separator + "&".join(missing)
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


def _country_from_offer_title(title: str) -> str | None:
    """A live-looked-up offer (not yet in the local cache, which is where
    country normally comes from - see offer_export.py) can still be
    checked against ATYPICAL_COUNTRIES without any extra admin scraping:
    the title already carries the country as one of its " - "-separated
    segments (e.g. "hledasetrans.cz - CZECHIA - TRANS - REV"), the same
    segment shape niche_from_offer_title() in config.py already relies on.
    Returns None when no segment matches a known country name - the India
    check then can't confirm safety, and treats that the same as actually
    being India (see generate_tracking_links())."""
    for segment in title.replace("\xa0", " ").split(" - "):
        candidate = re.sub(r"^https?://", "", segment.strip())
        if looks_like_domain(candidate):
            continue
        if segment.strip().upper() in COUNTRY_ISO:
            return segment.strip().upper()
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
                        country = _country_from_offer_title(title)
                        if country is None or country in ATYPICAL_COUNTRIES:
                            # Same fail-safe as the cached path's ATYPICAL_COUNTRIES
                            # check, but stricter: an unresolvable country (title
                            # didn't have a segment matching a known country name)
                            # is treated the same as India rather than generated
                            # anyway - a wrong link going out silently is worse
                            # than one extra manual lookup.
                            reason = country.title() if country else "unrecognized-country"
                            results[offer_id] = {
                                "offer_id": offer_id, "title": title, "domain": domain,
                                "country": country, "flag": country_flag(country) if country else None,
                                "link": None, "from_cache": False,
                                "error": (
                                    f"{reason} offer (live lookup) - can't confirm its tracking link format is "
                                    "the standard one. Pull it manually from the affiliate account instead."
                                    if country else
                                    "Couldn't recognize this offer's country from its title (live lookup) - "
                                    "can't confirm it isn't India (different tracking format). Pull it manually instead."
                                ),
                            }
                            log(f"[{offer_id}] BLOCKED (live) - {reason}, needs a manual link")
                            continue
                        link = build_tracking_link(template, aff_id, offer_id)
                        log(f"[{offer_id}] (live) {title} -> {link}")
                        results[offer_id] = {
                            "offer_id": offer_id, "title": title, "domain": domain,
                            "country": country, "flag": country_flag(country),
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


def build_new_affiliate_document(results: list[dict], postback: str, fraud_email: str) -> str:
    """Assembles the full DaoOfLeads new-affiliate onboarding document: a
    "1) TRACKING LINKS" section built from already-generated tracking-link
    results (same shape generate_tracking_links() returns), followed by the
    fixed boilerplate sections - identical every time except the postback
    value and the fraud-report email, which both differ per affiliate.

    This document gets copied and sent to the affiliate as-is, so a failed
    offer (wrong platform, not found, ...) is left out entirely rather than
    written in as an error line - the caller is expected to surface those
    separately (see links_new_run() in app.py) instead of forwarding them."""
    lines = ["1)TRACKING LINKS", "TRACKING LINKS HERE:", ""]
    for r in results:
        if r["error"]:
            continue
        country_tag = f"{r['flag'] + ' ' if r.get('flag') else ''}{r['country'].upper()} - " if r.get("country") else ""
        lines.append(f"{r['offer_id']} - {country_tag}{r['title']}")
        lines.append(r["link"])
        lines.append("")
    lines.append("------------------------------------------------------")
    lines.append(LINK_FOOTER_NOTE)
    lines.append("")
    return "\n".join(lines) + NEW_AFFILIATE_BOILERPLATE_DAO.format(postback=postback, fraud_email=fraud_email)


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
