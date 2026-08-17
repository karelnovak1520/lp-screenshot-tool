"""Shared configuration and helper functions for lp_tool.py."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

PLATFORMS: dict[str, dict[str, str]] = {
    "daoofleads": {
        "label": "DaoOfLeads",
        "admin_base": "https://affiliates.daoofleads.com",
        "storage_state_filename": "storage_state_daoofleads.json",
    },
    "imaxcash": {
        "label": "ImaxCash",
        "admin_base": "https://affiliates.imaxcash.com",
        "storage_state_filename": "storage_state_imaxcash.json",
    },
    "onlinedatingkings": {
        "label": "OnlineDatingKings",
        "admin_base": "https://affiliates.onlinedatingkings.com",
        "storage_state_filename": "storage_state_onlinedatingkings.json",
    },
}
DEFAULT_PLATFORM = "daoofleads"

DEFAULT_AFID = "2792"
DEFAULT_VIEWPORT = {"width": 1250, "height": 825}

# Valid LP numbers and niche_id for each niche.
NICHE_LP_NUMBERS: dict[str, list[int]] = {
    "ADULT": [1, 2, 3, 4, 5, 10, 15, 17, 20],
    "FLIRT": [1, 2, 4, 5, 9, 10, 15, 17, 20],
    "BDSM": [1, 2, 3, 4, 5, 9, 10, 15, 17],
    "MILF": [1, 2, 3, 4, 5, 9, 10, 17, 20],
    "SENIOR": [1, 2, 3, 5, 9, 10, 15, 17, 20],
    "TRANS": [10, 17],
}

NICHE_IDS: dict[str, str | None] = {
    "ADULT": "1",
    "FLIRT": "2",
    "BDSM": "3",
    "MILF": "4",
    "SENIOR": "6",
    "TRANS": "10",  # confirmed from real data (hledasetrans.cz, /lp/10/4/10/ and /lp/17/4/10/)
}

# The second path segment is always fixed to "4" for newly written/fixed
# records, regardless of niche (older records sometimes have "0" - those get
# rewritten to "4").
LP_FORM_SEGMENT = "4"

# Button texts for dismissing the cookie banner / age gate (self-declaration),
# in every language we've run into so far. Tried in this order, every match
# gets clicked (best effort - if a text isn't found, it just moves on).
DISMISS_BUTTON_TEXTS = [
    # cookie - "reject all" variants
    "Alle ablehnen",
    "Rifiuta tutto",
    "Odmítnout vše",
    "Reject all",
    "Zamietnuť všetko",
    # cookie - two-step variants ("Let me choose" -> "Reject all")
    "Let me choose",
    "Manage preferences",
    "Déjame elegir",
    "Rechazar todas",
    # age gate - self-declaration "I'm 18+"
    "Ho 18 anni o più",
    "Tengo 18 años o más",
    "Je mi 18 let nebo více",
    "Som starší ako 18 rokov",
    "I am 18 or older",
    "Ich bin 18 oder älter",
]

# Fallback for when none of the exact texts above match - looks for a
# keyword in the text of clickable elements (case-insensitive). Used only as
# a last resort, and clicks at most the first match found.
DISMISS_FALLBACK_KEYWORDS = ["18", "adult", "yes", "entrar", "confirmo", "tengo"]


_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE)
_LP_NUMBER_RE = re.compile(r"/lp/(\d+)/")
_TITLE_LP_NUMBER_RE = re.compile(r"^LP0*(\d+)", re.IGNORECASE)


def looks_like_domain(value: str) -> bool:
    """No spaces/slashes, shaped like something.something (e.g. sexkontakt.com)."""
    return bool(_DOMAIN_RE.match(value.strip()))


def normalize_domain(domain: str) -> str:
    """"Bare" domains (just name.tld, e.g. erotickykontakt.cz) need a "www."
    prefix on the actually working LP - verified manually on potrestajma.sk,
    flirteouruguayo.com, mojemilfka.cz, zralalaska.cz, hledasetrans.cz and
    erotickykontakt.cz. Domains that already have their own subdomain (e.g.
    de.dateefy.com, m.seitensprung.ag) are left unchanged.
    """
    domain = domain.lower()
    if domain.startswith("www."):
        return domain
    if domain.count(".") == 1:
        return f"www.{domain}"
    return domain


def domain_from_offer_title(title: str) -> str | None:
    """The domain is whichever segment of the offer title (split on " - ")
    looks like a domain, e.g. "sexkontakt.com - GERMANY - ADULT - REV" ->
    "www.sexkontakt.com". Usually that's the first segment, but test/cloned
    offers sometimes have an extra descriptive prefix before the domain
    (e.g. "TEST (lp PREVIEW TOOL) - encuentrosamorosos.cl - ADULT - ...") -
    so every segment is checked and the first one that looks like a domain
    is used, instead of hardcoding the first segment.

    Not every offer has a domain in its title - older ones ("... RevShare
    old System") sometimes only have a human brand name (e.g. "Sexkontakt
    DE"). In that case this returns None, so the caller doesn't mistake it
    for a valid domain.
    """
    # Some titles in offers.json have a non-breaking space (\xa0) before the
    # dash instead of a normal one - without normalizing this, split(" - ")
    # would fail.
    for segment in title.replace("\xa0", " ").split(" - "):
        candidate = re.sub(r"^https?://", "", segment.strip())
        if looks_like_domain(candidate):
            return normalize_domain(candidate)
    return None


def path_from_url(url: str) -> str:
    """Extracts just the path from a URL (no domain, no query string - that
    gets appended separately, whether it's ?afid=... for the screenshot, or
    the placeholder template written back into the admin).
    """
    return urlsplit(url.strip()).path


def parse_lp_number(url_or_path: str) -> int | None:
    """Extracts the LP number from a path shaped like
    /lp/{N}/{form}/{niche_id}/. Returns None when the pattern isn't present
    (older/foreign path format - the row then isn't assigned to any LP
    number and has to be skipped with a warning).

    Not used to assign a row to an LP slot (that's parse_lp_number_from_title)
    - cloned rows carry over a foreign domain and possibly a foreign LP
    number from the source offer in their URL, so matching by URL would be
    unreliable."""
    match = _LP_NUMBER_RE.search(url_or_path)
    return int(match.group(1)) if match else None


def parse_lp_number_from_title(title: str) -> int | None:
    """Extracts the LP number from the Title field (shaped like "LP{N} -
    {NICHE}", e.g. "LP01 - ADULT" or "LP11 - Adult" -> 11). Title is a more
    reliable source of truth for "which LP slot this row belongs to" than
    the URL/URL preview, because on cloned offers the URL points at a
    foreign domain and possibly a foreign LP number inherited from the
    source offer, while Title stays correct for the target slot. Returns
    None when the title doesn't match the "LP<number>..." pattern (a
    foreign/unknown title - the row is then skipped with a warning for
    manual review)."""
    match = _TITLE_LP_NUMBER_RE.match(title.strip())
    return int(match.group(1)) if match else None


def build_lp_path(lp_number: int, niche_id: str) -> str:
    return f"/lp/{lp_number}/{LP_FORM_SEGMENT}/{niche_id}/"


def build_title(lp_number: int, niche: str) -> str:
    """LP numbers below 10 get zero-padded to two digits in the title
    (LP01, LP02, ...), 10 and up stay unchanged (LP10, LP17, LP20...). LP20
    gets an extra "- Christmas" suffix, across every niche."""
    number = f"{lp_number:02d}" if lp_number < 10 else str(lp_number)
    suffix = " - Christmas" if lp_number == 20 else ""
    return f"LP{number} - {niche.upper()}{suffix}"


def build_preview_url(domain: str, path: str) -> str:
    return f"https://{domain}{path}"


def build_full_url_template(domain: str, path: str) -> str:
    return (
        f"https://{domain}{path}"
        "?afid={affiliate_id}&ofid={offer_id}&trid={transaction_id}&source={source}&{params}"
    )
