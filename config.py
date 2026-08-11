"""Sdílená konfigurace a pomocné funkce pro lp_tool.py."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

ADMIN_BASE = "https://affiliates.daoofleads.com"
OFFERS_JSON = Path(__file__).parent.parent.parent / "data" / "offers.json"

DEFAULT_AFID = "2792"
DEFAULT_VIEWPORT = {"width": 1250, "height": 825}

# Platná LP čísla a niche_id pro každou niche (zadání sekce 2). Trans nemá
# potvrzené niche_id - dokud ho uživatel neurčí, tool na tuto niche odmítne
# běžet bez explicitního --niche-id.
NICHE_LP_NUMBERS: dict[str, list[int]] = {
    "ADULT": [1, 2, 3, 4, 5, 10, 15, 17, 20],
    "FLIRT": [1, 2, 4, 5, 9, 10, 15, 17, 20],
    "BDSM": [1, 2, 3, 4, 5, 9, 10, 15, 17],
    "MILF": [1, 2, 3, 4, 5, 9, 10, 17, 20],
    "SENIOR": [1, 2, 3, 5, 9, 10, 15, 17, 20],
    "TRANS": [10, 20],
}

NICHE_IDS: dict[str, str | None] = {
    "ADULT": "1",
    "FLIRT": "2",
    "BDSM": "3",
    "MILF": "4",
    "SENIOR": "6",
    "TRANS": None,  # neznámé/nepotvrzené - viz zadání sekce 2
}

# Druhý segment cesty LP je u nově zapisovaných/opravovaných záznamů vždy
# fixně "4", bez ohledu na niche (starší záznamy mívají "0" - ty se přepisem
# opraví na "4").
LP_FORM_SEGMENT = "4"

# Texty tlačítek pro odbavení cookie lišty / věkové brány (self-declaration),
# ve všech jazycích, na které jsme dosud narazili. Zkouší se v tomto pořadí,
# každý match se klikne (best effort - když text nenajde, jde dál).
DISMISS_BUTTON_TEXTS = [
    # cookie - "odmítnout vše" varianty
    "Alle ablehnen",
    "Rifiuta tutto",
    "Odmítnout vše",
    "Reject all",
    "Zamietnuť všetko",
    # cookie - dvoukrokové varianty ("Let me choose" -> "Reject all")
    "Let me choose",
    "Manage preferences",
    "Déjame elegir",
    "Rechazar todas",
    # age gate - self-declaration "jsem 18+"
    "Ho 18 anni o più",
    "Tengo 18 años o más",
    "Je mi 18 let nebo více",
    "Som starší ako 18 rokov",
    "I am 18 or older",
    "Ich bin 18 oder älter",
]

# Fallback, když se nenajde žádný z přesných textů výše (zadání sekce 10) -
# hledá se klíčové slovo v textu klikatelných prvků (case-insensitive).
# Použije se jen jako záloha a klikne se maximálně na první nalezenou shodu.
DISMISS_FALLBACK_KEYWORDS = ["18", "adult", "yes", "entrar", "confirmo", "tengo"]


_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", re.IGNORECASE)
_LP_NUMBER_RE = re.compile(r"/lp/(\d+)/")


def looks_like_domain(value: str) -> bool:
    """Nejsou tam mezery/lomítka a má to tvar něco.něco (např. sexkontakt.com)."""
    return bool(_DOMAIN_RE.match(value.strip()))


def normalize_domain(domain: str) -> str:
    """"Holé" domény (jen název.tld, např. erotickykontakt.cz) na reálně
    fungujících LP potřebují "www." prefix - ověřeno ručně na potrestajma.sk,
    flirteouruguayo.com, mojemilfka.cz, zralalaska.cz, hledasetrans.cz a
    erotickykontakt.cz. Domény, co už mají vlastní subdomenu (např.
    de.dateefy.com, m.seitensprung.ag), zůstávají beze změny.
    """
    domain = domain.lower()
    if domain.startswith("www."):
        return domain
    if domain.count(".") == 1:
        return f"www.{domain}"
    return domain


def domain_from_offer_title(title: str) -> str | None:
    """Doména je první segment v popisku offeru, např.
    "sexkontakt.com - GERMANY - ADULT - REV" -> "www.sexkontakt.com"

    Ne všechny offery mají v titulku doménu - starší ("... RevShare old
    System") mívají jen lidský název brandu (např. "Sexkontakt DE"). V tom
    případě vrací None, aby to volající nezaměnil za platnou doménu.
    """
    # Některé tituly v offers.json mají před pomlčkou nedělitelnou mezeru
    # (\xa0) místo normální - bez normalizace by split(" - ") selhal.
    domain = title.replace("\xa0", " ").split(" - ")[0].strip()
    domain = re.sub(r"^https?://", "", domain)
    if not looks_like_domain(domain):
        return None
    return normalize_domain(domain)


def get_offer_title(offer_id: str) -> str | None:
    """Zkusí najít titulek offeru v data/offers.json (lokální cache appky).
    Vrací None, pokud tam offer není - pak se doména musí dohledat v adminu.
    """
    if not OFFERS_JSON.exists():
        return None
    data = json.loads(OFFERS_JSON.read_text(encoding="utf-8"))
    for offer in data.get("offers", []):
        if str(offer.get("ofid")) == str(offer_id):
            return offer.get("title")
    return None


def path_from_url(url: str) -> str:
    """Vytáhne jen cestu z URL (bez domény, bez query stringu - ten se pak
    doplňuje samostatně, ať už jde o ?afid=... na screenshot, nebo o šablonu
    s placeholdery při zápisu zpět do administrace).
    """
    return urlsplit(url.strip()).path


def parse_lp_number(url_or_path: str) -> int | None:
    """Vytáhne číslo LP z cesty tvaru /lp/{N}/{form}/{niche_id}/. Vrací None,
    když vzor v hodnotě není (starší/cizí formát cesty - řádek se pak
    nepřiřazuje k žádnému LP číslu a musí se přeskočit s varováním)."""
    match = _LP_NUMBER_RE.search(url_or_path)
    return int(match.group(1)) if match else None


def build_lp_path(lp_number: int, niche_id: str) -> str:
    return f"/lp/{lp_number}/{LP_FORM_SEGMENT}/{niche_id}/"


def build_title(lp_number: int, niche: str) -> str:
    """LP čísla do 9 se do title dávají zarovnaná na dvě číslice (LP01, LP02,
    ...), od 10 výš beze změny (LP10, LP17, LP20...). LP20 má napříč niche
    navíc příponu "- Christmas"."""
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
