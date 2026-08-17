"""
Nástroj pro hromadné vytváření/opravu landing page záznamů a jejich náhledů
v administraci affiliates.daoofleads.com, podle platné sady LP čísel dané niche.

Postup pro daný offer_id + niche:
  1. Doména se vezme z popisku (title) offeru v adminu, NIKDY ze statické
     mapy niche -> domain a NIKDY z existující URL v gridu (ta může být
     špatně/zastaralá).
  2. Podle niche se zjistí platná sada čísel LP a niche_id (config.py).
  3. Načte se grid "Landing page" offeru a řádky se přiřadí k LP číslům
     podle vzoru /lp/{N}/4/{niche_id}/ v jejich URL preview.
  4. Pro každé platné LP číslo:
       - řádek neexistuje -> Add (screenshot + nový řádek, status active)
       - řádek existuje, ale URL preview neodpovídá očekávané doméně/cestě
         -> Edit (nový screenshot, přepsané URL/URL preview, title)
       - řádek existuje a je v pořádku -> nic se nedělá
  5. Řádky, jejichž LP číslo NENÍ v platné sadě dané niche, se ponechají
     obsahově beze změny a jen se jim nastaví status Paused.
  6. Řádky, u kterých se nepodaří rozpoznat LP číslo, se přeskočí s varováním
     (nikdy se nehádá, co s nimi - jen se zaloguje k ruční kontrole).

Nástroj nikdy nemaže řádky (do=deleteLanding) - to je vždy ruční akce.

Použití:
    python login.py                                     # jednou, ručně se přihlásit
    python lp_tool.py --offer-id 16689 --niche ADULT --dry-run
    python lp_tool.py --offer-id 16689 --niche ADULT --only-lp 10
    python lp_tool.py --offer-id 16689 --niche ADULT
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import uuid
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

from config import (
    ADMIN_BASE,
    DEFAULT_AFID,
    DEFAULT_VIEWPORT,
    DISMISS_BUTTON_TEXTS,
    DISMISS_FALLBACK_KEYWORDS,
    NICHE_IDS,
    NICHE_LP_NUMBERS,
    build_full_url_template,
    build_lp_path,
    build_preview_url,
    build_title,
    domain_from_offer_title,
    get_offer_title,
    parse_lp_number_from_title,
)

STORAGE_STATE_FILE = Path(__file__).parent / "storage_state.json"
SUCCESS_TEXT_RE = re.compile(r"Successfully (added|edited|created|inserted)", re.IGNORECASE)


def dismiss_overlays(page: Page) -> None:
    """Best-effort odbavení cookie lišty / age gate. Neselže, když nic nenajde.

    Dva průchody seznamem textů, protože dvoukrokové cookie bannery ("Let me
    choose"/"Déjame elegir" -> teprve pak se objeví "Reject all"/"Rechazar
    todas") by v jediném průchodu mohly být odbavené jen zpola, pokud tlačítko
    druhého kroku vyjde v DISMISS_BUTTON_TEXTS dřív než tlačítko prvního kroku.
    """
    dismissed_any = False
    for _ in range(2):
        for text in DISMISS_BUTTON_TEXTS:
            try:
                btn = page.get_by_text(text, exact=False)
                if btn.count() > 0:
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(300)
                    dismissed_any = True
            except Exception:
                continue

    if dismissed_any:
        return

    # Fallback dle zadání sekce 10 - hledání klíčových slov v klikatelných
    # prvcích, klikne se nejvýš na první nalezenou shodu (opatrnost proti
    # náhodnému kliknutí na nesouvisející prvek).
    for keyword in DISMISS_FALLBACK_KEYWORDS:
        try:
            candidate = page.locator(f"button:has-text('{keyword}'), a:has-text('{keyword}'), input[type=submit][value*='{keyword}' i]").first
            if candidate.count() > 0:
                candidate.click(timeout=2000)
                page.wait_for_timeout(300)
                break
        except Exception:
            continue


def screenshot_lp(browser, domain: str, path: str, afid: str, viewport: dict) -> Path:
    url = f"https://{domain}{path}?afid={afid}"

    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        # networkidle nemusí nastat na strankach s trackery co běží pořád - zkusíme dál i tak
        pass
    dismiss_overlays(page)
    page.wait_for_timeout(500)

    out_path = Path(tempfile.gettempdir()) / f"lp-screenshot-{uuid.uuid4().hex}.png"
    page.screenshot(path=str(out_path))
    context.close()
    return out_path


def fetch_offer_title_from_admin(admin_page: Page, offer_id: str) -> str:
    """Fallback, když offer není v lokálním data/offers.json - zkusí vytáhnout
    popisek offeru přímo z hlavní edit stránky offeru v adminu."""
    admin_page.goto(f"{ADMIN_BASE}/en/admin/offer/edit/{offer_id}?locale=en", wait_until="networkidle")
    return admin_page.locator("h1").first.inner_text()


def get_landing_rows(admin_page: Page, offer_id: str) -> list[dict]:
    grid_url = f"{ADMIN_BASE}/en/admin/offer/edit/{offer_id}/landing?locale=en&landingGrid-perPage=200"
    admin_page.goto(grid_url, wait_until="networkidle")

    table = admin_page.locator("table").first

    # thead má víc <tr> (samostatný řádek se zaškrtávátkem, řádek s názvy
    # sloupců, řádek s filtry) - je potřeba najít konkrétně ten s názvy
    # sloupců podle OBSAHU, ne podle pozice, jinak se sloupce posunou oproti
    # reálným <td> v řádcích (viz README - "Známá omezení").
    required = ["id", "title", "url", "url preview", "status"]
    thead_rows = table.locator("thead tr")
    headers = None
    for r in range(thead_rows.count()):
        candidate = [h.strip().lower() for h in thead_rows.nth(r).locator("th").all_inner_texts()]
        if set(required).issubset(set(candidate)):
            headers = candidate
            break

    if headers is None:
        all_rows_preview = [
            [h.strip().lower() for h in thead_rows.nth(r).locator("th").all_inner_texts()]
            for r in range(thead_rows.count())
        ]
        raise RuntimeError(
            f"Řádek hlavičky obsahující {required} nenalezen (thead řádky: {all_rows_preview}). "
            "Struktura admin gridu se zřejmě liší - je potřeba upravit get_landing_rows() v lp_tool.py."
        )
    col = {name: i for i, name in enumerate(headers)}

    rows = []
    body_rows = table.locator("tbody tr")
    for i in range(body_rows.count()):
        tds = body_rows.nth(i).locator("td").all_inner_texts()
        if len(tds) <= max(col.values()):
            continue
        rows.append(
            {
                "id": tds[col["id"]].strip(),
                "title": tds[col["title"]].strip(),
                "url": tds[col["url"]].strip(),
                "url_preview": tds[col["url preview"]].strip(),
                "status": tds[col["status"]].strip(),
            }
        )
    return rows


def open_inline_add(admin_page: Page) -> Locator:
    add_link = admin_page.locator('a[href*="do=landingGrid-showInlineAdd"]')
    if add_link.count() == 0:
        raise RuntimeError("Tlačítko Add (do=landingGrid-showInlineAdd) nenalezeno v gridu.")
    add_link.first.click()
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_selector('[name="inline_add[title]"]', timeout=10000)
    return admin_page.locator("tr").filter(has=admin_page.locator('[name="inline_add[title]"]')).first


def open_inline_edit(admin_page: Page, row_id: str) -> Locator:
    edit_link = admin_page.locator(
        f'a[href*="do=landingGrid-inlineEdit"][href*="landingGrid-id={row_id}"]'
    )
    if edit_link.count() == 0:
        raise RuntimeError(f"Edit link pro řádek {row_id} nenalezen.")
    edit_link.first.click()
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_selector('[name="inline_edit[title]"]', timeout=10000)
    return admin_page.locator("tr").filter(has=admin_page.locator('[name="inline_edit[title]"]')).first


def fill_inline_form(row: Locator, prefix: str, *, title: str | None = None, preview_path: str | None = None,
                      url: str | None = None, url_preview: str | None = None, status: str | None = None) -> None:
    if title is not None:
        row.locator(f'[name="{prefix}[title]"]').fill(title)
    if preview_path is not None:
        row.locator(f'[name="{prefix}[preview]"]').set_input_files(preview_path)
    if url is not None:
        row.locator(f'[name="{prefix}[url]"]').fill(url)
    if url_preview is not None:
        row.locator(f'[name="{prefix}[url_preview]"]').fill(url_preview)
    if status is not None:
        row.locator(f'[name="{prefix}[status]"]').select_option(status)


def submit_inline_form(admin_page: Page, row: Locator, prefix: str) -> None:
    row.locator(f'[name="{prefix}[submit]"]').click()
    admin_page.wait_for_selector(f"text=/{SUCCESS_TEXT_RE.pattern}/i", timeout=10000)


def row_matches_expected(row: dict, expected_preview_url: str) -> bool:
    return row["url_preview"].rstrip("/") == expected_preview_url.rstrip("/")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offer-id", required=True, help="ID offeru v administraci")
    parser.add_argument(
        "--niche",
        required=True,
        choices=sorted(NICHE_LP_NUMBERS.keys()),
        type=str.upper,
        help="Niche offeru - určuje platnou sadu LP čísel a niche_id",
    )
    parser.add_argument(
        "--niche-id",
        help="Vynutit niche_id ručně (povinné pro TRANS, dokud nebude potvrzeno v zadání)",
    )
    parser.add_argument("--afid", default=DEFAULT_AFID, help=f"affiliate ID pro screenshoty (default {DEFAULT_AFID})")
    parser.add_argument("--width", type=int, default=DEFAULT_VIEWPORT["width"])
    parser.add_argument("--height", type=int, default=DEFAULT_VIEWPORT["height"])
    parser.add_argument("--domain", help="vynutit doménu manuálně (přeskočí odvozování z popisku offeru)")
    parser.add_argument("--only-lp", type=int, help="zpracovat jen jedno konkrétní LP číslo, pro testování")
    parser.add_argument("--dry-run", action="store_true", help="jen udělat screenshoty a vypsat plán, nic neuploadovat/neukládat do administrace")
    parser.add_argument("--headless", action="store_true", help="spustit prohlížeč bez viditelného okna")
    args = parser.parse_args()

    if not STORAGE_STATE_FILE.exists():
        print("Nejdřív se musíš přihlásit: python login.py")
        sys.exit(1)

    niche_id = args.niche_id or NICHE_IDS[args.niche]
    if not niche_id:
        print(
            f"niche_id pro niche {args.niche} není v zadání potvrzené (chybí referenční příklad). "
            "Potvrď ho ručně a spusť znovu s --niche-id <hodnota>."
        )
        sys.exit(1)

    valid_lp_numbers = NICHE_LP_NUMBERS[args.niche]
    if args.only_lp is not None:
        valid_lp_numbers = [n for n in valid_lp_numbers if n == args.only_lp]
        if not valid_lp_numbers:
            print(f"LP {args.only_lp} není v platné sadě pro niche {args.niche} ({NICHE_LP_NUMBERS[args.niche]}).")
            sys.exit(1)

    viewport = {"width": args.width, "height": args.height}

    domain = args.domain
    source_title = None
    if not domain:
        source_title = get_offer_title(args.offer_id)
        if source_title:
            domain = domain_from_offer_title(source_title)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        admin_context = browser.new_context(storage_state=str(STORAGE_STATE_FILE))
        admin_page = admin_context.new_page()

        if not domain:
            source_title = fetch_offer_title_from_admin(admin_page, args.offer_id)
            domain = domain_from_offer_title(source_title)

        if not domain:
            print(
                f"Z popisku offeru '{source_title}' se nepodařilo rozpoznat doménu "
                "(zřejmě starší offer bez domény v titulku, jen s lidským názvem brandu). "
                "Zadej doménu manuálně přes --domain <doména>."
            )
            browser.close()
            sys.exit(1)

        print(f"Doména (z popisku '{source_title}'): {domain}")
        print(f"Niche: {args.niche} (niche_id={niche_id}), platná LP čísla: {valid_lp_numbers}\n")

        all_rows = get_landing_rows(admin_page, args.offer_id)
        rows_by_lp: dict[int, dict] = {}
        unparsed_rows: list[dict] = []
        for row in all_rows:
            lp_number = parse_lp_number_from_title(row["title"])
            if lp_number is None:
                unparsed_rows.append(row)
            else:
                rows_by_lp[lp_number] = row

        if unparsed_rows and args.only_lp is None:
            print(f"Přeskakuji {len(unparsed_rows)} řádků bez rozpoznatelného LP čísla (ruční kontrola):")
            for row in unparsed_rows:
                print(f"  [{row['id']}] {row['title']} - {row['url_preview'] or row['url']}")
            print()

        created, updated, paused, skipped, failed = [], [], [], [], []

        for lp_number in valid_lp_numbers:
            path = build_lp_path(lp_number, niche_id)
            preview_url = build_preview_url(domain, path)
            full_url = build_full_url_template(domain, path)
            title = build_title(lp_number, args.niche)
            existing = rows_by_lp.get(lp_number)

            label = f"LP{lp_number}"
            screenshot_path = None
            try:
                if existing and row_matches_expected(existing, preview_url):
                    print(f"[{label}] už odpovídá ({preview_url}) - přeskakuji")
                    skipped.append(lp_number)
                    continue

                action = "edit" if existing else "add"
                print(f"[{label}] {action} -> {preview_url}")
                screenshot_path = screenshot_lp(browser, domain, path, args.afid, viewport)
                print(f"  screenshot: {screenshot_path}")

                if args.dry_run:
                    (updated if action == "edit" else created).append(lp_number)
                    continue

                if action == "add":
                    row = open_inline_add(admin_page)
                    fill_inline_form(
                        row, "inline_add",
                        title=title, preview_path=str(screenshot_path),
                        url=full_url, url_preview=preview_url, status="active",
                    )
                    submit_inline_form(admin_page, row, "inline_add")
                    created.append(lp_number)
                else:
                    row = open_inline_edit(admin_page, existing["id"])
                    fill_inline_form(
                        row, "inline_edit",
                        title=title, preview_path=str(screenshot_path),
                        url=full_url, url_preview=preview_url,
                    )
                    submit_inline_form(admin_page, row, "inline_edit")
                    updated.append(lp_number)
                print("  uloženo do administrace")
            except Exception as exc:
                print(f"  CHYBA: {exc}")
                failed.append(lp_number)
            finally:
                if screenshot_path and not args.dry_run:
                    screenshot_path.unlink(missing_ok=True)

        if args.only_lp is None:
            for lp_number, row in rows_by_lp.items():
                if lp_number in NICHE_LP_NUMBERS[args.niche]:
                    continue
                if "paus" in row["status"].lower():
                    continue
                label = f"LP{lp_number} (mimo platnou sadu {args.niche})"
                print(f"[{label}] [{row['id']}] {row['title']} -> pause")
                if args.dry_run:
                    paused.append(lp_number)
                    continue
                try:
                    row_locator = open_inline_edit(admin_page, row["id"])
                    fill_inline_form(row_locator, "inline_edit", status="paused")
                    submit_inline_form(admin_page, row_locator, "inline_edit")
                    print("  status nastaven na paused")
                    paused.append(lp_number)
                except Exception as exc:
                    print(f"  CHYBA: {exc}")
                    failed.append(lp_number)

        browser.close()

    print(
        f"\nHotovo. Vytvořeno: {len(created)}, upraveno: {len(updated)}, "
        f"pozastaveno: {len(paused)}, beze změny: {len(skipped)}, chyby: {len(failed)}"
    )
    if failed:
        print("LP s chybou:", ", ".join(str(n) for n in failed))


if __name__ == "__main__":
    main()
