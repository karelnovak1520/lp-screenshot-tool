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

# Valid LP numbers and niche_id for each niche. Usually whole numbers, but a
# niche can also have decimal variants of a slot (e.g. TRANS's 10.2 is a
# variant of LP10, 27.1 a variant of the new LP27) - kept as float in that
# case, plain int otherwise; parse_lp_number_from_title() and
# parse_lp_number_arg() below both follow the same int-vs-float rule so a
# row's title and a --only-lp/web "Single LP number" value compare equal to
# these.
NICHE_LP_NUMBERS: dict[str, list[int | float]] = {
    "ADULT": [1, 2, 3, 4, 5, 10, 15, 17, 20],
    "FLIRT": [1, 2, 4, 5, 9, 10, 15, 17, 20],
    "BDSM": [1, 2, 3, 4, 5, 9, 10, 15, 17],
    "MILF": [1, 2, 3, 4, 5, 9, 10, 17, 20],
    "SENIOR": [1, 2, 3, 5, 9, 10, 15, 17, 20],
    # 10.2, 27, 27.1 confirmed live on tsseeker.com (new LPs added 2026-08-25).
    "TRANS": [10, 10.2, 17, 27, 27.1],
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

# Button texts for dismissing the age gate (self-declaration "I'm 18+"), in
# every language we've run into so far. Tried in this order, every match
# gets clicked (best effort - if a text isn't found, it just moves on).
#
# The cookie banner is NOT handled through a text list like this one - it's
# dismissed generically based on the word "cookie" appearing in a dialog
# (see dismiss_overlays() in lp_tool.py), because accept-vs-reject doesn't
# matter there and maintaining translations for dozens of languages would be
# wasted effort. For the age gate, picking wrong means leaving to an "I'm
# underage" page instead of just a differently-worded banner, so an exact
# list stays here.
AGE_GATE_BUTTON_TEXTS = [
    "Ho 18 anni o più",  # IT
    "Tengo 18 años o más",  # ES
    "Je mi 18 let nebo více",  # CS
    "Som starší ako 18 rokov",  # SK
    "Mám 18 rokov alebo viac",  # SK (different LP template's own wording - webmaster_disclaimer_overlay)
    "I am 18 or older",  # EN
    "Ich bin 18 oder älter",  # DE
    "J'ai 18 ans ou plus",  # FR
    "Tenho 18 anos ou mais",  # PT/BR
    "Ik ben 18 jaar of ouder",  # NL
    "Mam 18 lat lub więcej",  # PL
    "Am 18 ani sau mai mult",  # RO
    "18 éves vagy idősebb vagyok",  # HU
    "Jag är 18 år eller äldre",  # SV
    "18 yaşında veya daha büyüğüm",  # TR
    "Мне 18 лет или больше",  # RU
]

# Fallback for when none of the exact texts above match - looks for a
# keyword in the text of clickable elements (case-insensitive). Used only as
# a last resort, and clicks at most the first match found.
DISMISS_FALLBACK_KEYWORDS = ["18", "adult", "yes", "entrar", "confirmo", "tengo"]


_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE)
_LP_NUMBER_RE = re.compile(r"/lp/(\d+(?:\.\d+)?)/")
_TITLE_LP_NUMBER_RE = re.compile(r"^LP0*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _to_lp_number(raw: str) -> int | float:
    """Whole LP numbers stay int (matches the plain int entries already in
    NICHE_LP_NUMBERS); a decimal variant (e.g. "10.2") becomes float. Shared
    by every place an LP number gets parsed from text, so a row's title, a
    URL, and a --only-lp/web "Single LP number" value all compare equal to
    the values in NICHE_LP_NUMBERS regardless of which one produced them."""
    return float(raw) if "." in raw else int(raw)


def parse_lp_number_arg(value: str) -> int | float:
    """Parses a user-supplied LP number (CLI --only-lp, or the web app's
    "Single LP number" field) - same int-vs-float rule as
    parse_lp_number_from_title() below, so it matches entries in
    NICHE_LP_NUMBERS for niches with decimal LP variants (e.g. TRANS's
    10.2, 27.1)."""
    return _to_lp_number(str(value).strip())


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
    (e.g. "TEST - minasdivinas.com - ADULT - ...") - so every segment is
    checked and the first one that looks like a domain is used, instead of
    hardcoding the first segment.

    Not every offer has a domain in its title - older ones ("... RevShare
    old System") sometimes only have a human brand name (e.g. "Sexkontakt
    DE"). In that case this returns None, so the caller doesn't mistake it
    for a valid domain.
    """
    # Some titles have a non-breaking space (\xa0) before the dash instead
    # of a normal one - without normalizing this, split(" - ") would fail.
    for segment in title.replace("\xa0", " ").split(" - "):
        candidate = re.sub(r"^https?://", "", segment.strip())
        if looks_like_domain(candidate):
            return normalize_domain(candidate)
    return None


def niche_from_offer_title(title: str) -> str | None:
    """The niche is the first NICHE_LP_NUMBERS key that appears (case-
    insensitive) as a substring of one of the title's " - "-separated
    segments, e.g. "TEST - minasdivinas.com - ADULT - CPL(DOI) / UY
    [Desktop/Mobile]" -> "ADULT", or "... - SENIORS 50+ - ..." -> "SENIOR"
    (matches as a substring even though the segment has an extra "S 50+").
    Returns None when nothing matches - the niche then has to be given
    explicitly via --niche.

    The domain segment is skipped (same segment domain_from_offer_title()
    would pick) - domain names are often branded with a niche-like
    substring that has nothing to do with the offer's actual niche (e.g.
    "tsflirtdate.com" contains "FLIRT" even on a TRANS offer), so searching
    the raw, undivided title would find a false match there before ever
    reaching the real niche segment."""
    for segment in title.replace("\xa0", " ").split(" - "):
        candidate = re.sub(r"^https?://", "", segment.strip())
        if looks_like_domain(candidate):
            continue
        upper_segment = segment.upper()
        for niche in NICHE_LP_NUMBERS:
            if niche in upper_segment:
                return niche
    return None


def path_from_url(url: str) -> str:
    """Extracts just the path from a URL (no domain, no query string - that
    gets appended separately, whether it's ?afid=... for the screenshot, or
    the placeholder template written back into the admin).
    """
    return urlsplit(url.strip()).path


def parse_lp_number(url_or_path: str) -> int | float | None:
    """Extracts the LP number from a path shaped like
    /lp/{N}/{form}/{niche_id}/. Returns None when the pattern isn't present.

    Not used to assign a row to an LP slot (that's parse_lp_number_from_title
    below) - cloned rows carry over a foreign domain and possibly a foreign
    LP number from the source offer in their URL, so matching by URL would
    be unreliable. Kept as a building block."""
    match = _LP_NUMBER_RE.search(url_or_path)
    return _to_lp_number(match.group(1)) if match else None


def parse_lp_number_from_title(title: str) -> int | float | None:
    """Extracts the LP number from the Title field (shaped like "LP{N} -
    {NICHE}", e.g. "LP01 - ADULT" or "LP11 - Adult" -> 11, or "LP10.2 -
    TRANS" -> 10.2 for a decimal variant). Title is a more reliable source
    of truth for "which LP slot this row belongs to" than the URL/URL
    preview, because on cloned offers the URL points at a foreign domain
    and possibly a foreign LP number inherited from the source offer, while
    Title stays correct for the target slot. Returns None when the title
    doesn't match the "LP<number>..." pattern (a foreign/unknown title -
    the row is then skipped with a warning for manual review)."""
    match = _TITLE_LP_NUMBER_RE.match(title.strip())
    return _to_lp_number(match.group(1)) if match else None


def build_lp_path(lp_number: int | float, niche_id: str) -> str:
    return f"/lp/{lp_number}/{LP_FORM_SEGMENT}/{niche_id}/"


def build_title(lp_number: int | float, niche: str) -> str:
    """LP numbers below 10 get zero-padded to two digits in the title
    (LP01, LP02, ...), 10 and up stay unchanged (LP10, LP17, LP20...). LP20
    gets an extra "- Christmas" suffix, across every niche. A decimal
    variant (e.g. 10.2) only pads its whole-number part (e.g. "01.5" for a
    hypothetical 1.5, unchanged for anything already >= 10)."""
    if lp_number < 10:
        whole, _, frac = str(lp_number).partition(".")
        number = f"{int(whole):02d}" + (f".{frac}" if frac else "")
    else:
        number = str(lp_number)
    suffix = " - Christmas" if lp_number == 20 else ""
    return f"LP{number} - {niche.upper()}{suffix}"


def build_preview_url(domain: str, path: str) -> str:
    return f"https://{domain}{path}"


def build_full_url_template(domain: str, path: str) -> str:
    return (
        f"https://{domain}{path}"
        "?afid={affiliate_id}&ofid={offer_id}&trid={transaction_id}&source={source}&{params}"
    )
