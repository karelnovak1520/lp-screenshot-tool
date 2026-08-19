"""
Shared logic for pulling the offer grid's CSV export out of an admin
platform. Used by both offer_cache.py (the local cache that powers the
Tracking Link Generator's search, refreshed automatically on login) and
sync_offers.py (the manual, on-demand push into the TL app's
data/offers.json).
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from playwright.sync_api import sync_playwright

from lp_tool import ToolError

# Same filters as the admin's default Offer list view (active/paused/passive/
# test) - a plain "active only" export would silently drop offers the moment
# they're paused, which is exactly the kind of change we want to see.
EXPORT_PATH = (
    "/en/admin/offer/list?offerGrid-id=1"
    "&offerGrid-filter%5Bstatus%5D%5B0%5D=active"
    "&offerGrid-filter%5Bstatus%5D%5B1%5D=paused"
    "&offerGrid-filter%5Bstatus%5D%5B2%5D=passive"
    "&offerGrid-filter%5Bstatus%5D%5B3%5D=test"
    "&do=offerGrid-export"
)

# Country name (as the admin's CSV export spells it, upper-cased) -> ISO
# 3166-1 alpha-2 code, used to build the flag emoji. Not exhaustive - an
# unrecognized country just gets no flag (see country_flag() below), never
# an error, since this is cosmetic.
COUNTRY_ISO = {
    "UNITED STATES": "US", "USA": "US", "UNITED STATES OF AMERICA": "US",
    "UNITED KINGDOM": "GB", "UK": "GB", "GREAT BRITAIN": "GB",
    "GERMANY": "DE", "AUSTRIA": "AT", "SWITZERLAND": "CH",
    "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ", "SLOVAKIA": "SK",
    "POLAND": "PL", "HUNGARY": "HU", "ROMANIA": "RO", "BULGARIA": "BG",
    "SERBIA": "RS", "CROATIA": "HR", "SLOVENIA": "SI",
    "BOSNIA AND HERZEGOVINA": "BA", "GREECE": "GR",
    "ITALY": "IT", "SPAIN": "ES", "PORTUGAL": "PT", "FRANCE": "FR",
    "NETHERLANDS": "NL", "BELGIUM": "BE", "DENMARK": "DK", "SWEDEN": "SE",
    "NORWAY": "NO", "FINLAND": "FI", "ICELAND": "IS", "IRELAND": "IE",
    "RUSSIA": "RU", "UKRAINE": "UA", "TURKEY": "TR", "BELARUS": "BY",
    "COLOMBIA": "CO", "URUGUAY": "UY", "ARGENTINA": "AR", "MEXICO": "MX",
    "BRAZIL": "BR", "CHILE": "CL", "PERU": "PE", "ECUADOR": "EC",
    "VENEZUELA": "VE",
    "INDIA": "IN", "PAKISTAN": "PK", "BANGLADESH": "BD",
    "CANADA": "CA", "AUSTRALIA": "AU", "NEW ZEALAND": "NZ",
    "SOUTH AFRICA": "ZA", "JAPAN": "JP", "SOUTH KOREA": "KR", "CHINA": "CN",
    "ESTONIA": "EE", "LATVIA": "LV", "LITHUANIA": "LT",
    "MALTA": "MT", "CYPRUS": "CY", "LUXEMBOURG": "LU", "ALBANIA": "AL",
    "NORTH MACEDONIA": "MK", "MONTENEGRO": "ME", "MOLDOVA": "MD",
    "ISRAEL": "IL", "UNITED ARAB EMIRATES": "AE", "UAE": "AE",
    "SAUDI ARABIA": "SA", "EGYPT": "EG", "MOROCCO": "MA", "NIGERIA": "NG",
    "KENYA": "KE", "THAILAND": "TH", "VIETNAM": "VN", "PHILIPPINES": "PH",
    "INDONESIA": "ID", "MALAYSIA": "MY", "SINGAPORE": "SG",
}


def country_flag(country: str) -> str | None:
    """Converts a country name to its flag emoji, via the ISO code lookup
    above. Returns None for an unrecognized country - a missing flag is a
    cosmetic gap, not something worth failing over."""
    code = COUNTRY_ISO.get((country or "").strip().upper())
    if not code:
        return None
    return "".join(chr(127397 + ord(c)) for c in code)


def fetch_offers_csv(admin_base: str, state_path: Path) -> bytes:
    """Downloads the offer grid's CSV export using the saved login session.
    Uses an authenticated in-browser request rather than page.goto(), since
    the response is application/octet-stream and would otherwise trigger a
    download instead of returning the bytes directly."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        resp = page.request.get(admin_base + EXPORT_PATH)
        if resp.status != 200:
            browser.close()
            raise ToolError(f"CSV export failed (HTTP {resp.status}) for {admin_base}")
        body = resp.body()
        browser.close()
        return body


def _clean_status(raw: str) -> str:
    """The admin's Status column comes back as e.g. " Paused private" or
    " Active private" - just the first word is the actual status."""
    return raw.strip().split(" ")[0].lower() if raw.strip() else ""


def parse_offers_csv(body: bytes, source: str) -> list[dict]:
    """Parses the admin's ';'-delimited CSV export into
    {ofid, title, offerType, status, country, flag, source} dicts."""
    text = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    offers = []
    for row in reader:
        ofid = (row.get("ID") or "").strip()
        if not ofid:
            continue
        country = (row.get("Country") or "").strip()
        offers.append({
            "ofid": ofid,
            "title": (row.get("Title") or "").strip(),
            "offerType": (row.get("Offer type") or "").strip(),
            "status": _clean_status(row.get("Status") or ""),
            "country": country,
            "flag": country_flag(country),
            "source": source,
        })
    return offers
