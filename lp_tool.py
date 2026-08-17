"""
Tool for bulk creating/fixing landing page records and their previews in the
affiliates.{platform}.com admin, according to the niche's valid set of LP
numbers.

Steps for a given offer_id + niche:
  1. The domain is taken from the offer's title in the admin, NEVER from a
     static niche -> domain map and NEVER from the existing URL in the grid
     (that one can be wrong/stale).
  2. The niche determines the valid set of LP numbers and the niche_id
     (config.py).
  3. The offer's "Landing page" grid is loaded and rows are matched to LP
     numbers by Title (shaped like "LP{N} - {NICHE}") - not by URL, because
     cloned rows carry over a foreign domain and a foreign LP number in
     their URL.
  4. For every valid LP number:
       - the row doesn't exist -> Add (screenshot + new row, status active)
       - the row exists but its URL preview doesn't match the expected
         domain/path -> Edit (new screenshot, rewritten URL/URL preview,
         title)
       - the row exists and is fine -> nothing happens
  5. Rows whose LP number is NOT in the niche's valid set are left
     untouched content-wise and just get their status set to Paused
     (unless already Paused or Deleted).
  6. Rows whose LP number can't be recognized are skipped with a warning
     (never guessed at - just logged for manual review).

The tool never deletes rows (do=deleteLanding) - that's always a manual
action.

Usage:
    python login.py --platform daoofleads               # once, log in manually
    python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT --dry-run
    python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT --only-lp 10
    python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT
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
    DEFAULT_AFID,
    DEFAULT_PLATFORM,
    DEFAULT_VIEWPORT,
    DISMISS_BUTTON_TEXTS,
    DISMISS_FALLBACK_KEYWORDS,
    NICHE_IDS,
    NICHE_LP_NUMBERS,
    PLATFORMS,
    build_full_url_template,
    build_lp_path,
    build_preview_url,
    build_title,
    domain_from_offer_title,
    parse_lp_number_from_title,
)

STORAGE_STATE_DIR = Path(__file__).parent
SUCCESS_TEXT_RE = re.compile(r"Successfully (added|edited|created|inserted)", re.IGNORECASE)


class ToolError(Exception):
    """An error that stops the whole run (missing login, unrecognized
    domain, invalid niche) - as opposed to a single LP's error, which just
    gets logged and the run continues."""


def storage_state_path(platform: str) -> Path:
    return STORAGE_STATE_DIR / PLATFORMS[platform]["storage_state_filename"]


def dismiss_overlays(page: Page) -> None:
    """Best-effort dismissal of the cookie banner / age gate. Doesn't fail
    if it finds nothing.

    Two passes over the text list, because two-step cookie banners ("Let me
    choose"/"Déjame elegir" -> only then does "Reject all"/"Rechazar todas"
    appear) could otherwise be only half-handled in a single pass, if the
    second step's button comes before the first step's in
    DISMISS_BUTTON_TEXTS.
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

    # Fallback for when none of the exact texts matched - looks for a
    # keyword among clickable elements, clicks at most the first match
    # found (caution against clicking something unrelated by mistake).
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
        # networkidle might never fire on pages with trackers that keep
        # running - try to continue anyway
        pass
    dismiss_overlays(page)
    page.wait_for_timeout(500)

    out_path = Path(tempfile.gettempdir()) / f"lp-screenshot-{uuid.uuid4().hex}.png"
    page.screenshot(path=str(out_path))
    context.close()
    return out_path


def fetch_offer_title_from_admin(admin_page: Page, admin_base: str, offer_id: str) -> str:
    """Pulls the offer's title from its main edit page in the admin. It's
    not the <h1> - that's just a generic "Offer offer edit" heading, the
    same on every offer - the real title is in the navbar-brand link in the
    top-left corner of the page."""
    admin_page.goto(f"{admin_base}/en/admin/offer/edit/{offer_id}?locale=en", wait_until="networkidle")
    return admin_page.locator("a.navbar-brand strong").first.inner_text()


def get_landing_rows(admin_page: Page, admin_base: str, offer_id: str) -> list[dict]:
    grid_url = f"{admin_base}/en/admin/offer/edit/{offer_id}/landing?locale=en&landingGrid-perPage=200"
    admin_page.goto(grid_url, wait_until="networkidle")

    table = admin_page.locator("table").first

    # thead has more than one <tr> (a standalone row with just the
    # select-all checkbox, a row with the column labels, a row with the
    # filter inputs) - the label row needs to be found by CONTENT, not by
    # position, otherwise the columns shift relative to the real <td> in
    # each row (see README - "Known limitations").
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
            f"Couldn't find a header row containing {required} (thead rows: {all_rows_preview}). "
            "The admin grid structure has apparently changed - get_landing_rows() in lp_tool.py needs updating."
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
        raise RuntimeError("Add button (do=landingGrid-showInlineAdd) not found in the grid.")
    add_link.first.click()
    admin_page.wait_for_load_state("networkidle")
    admin_page.wait_for_selector('[name="inline_add[title]"]', timeout=10000)
    return admin_page.locator("tr").filter(has=admin_page.locator('[name="inline_add[title]"]')).first


def open_inline_edit(admin_page: Page, row_id: str) -> Locator:
    edit_link = admin_page.locator(
        f'a[href*="do=landingGrid-inlineEdit"][href*="landingGrid-id={row_id}"]'
    )
    if edit_link.count() == 0:
        raise RuntimeError(f"Edit link for row {row_id} not found.")
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


def row_is_untouchable(status: str) -> bool:
    """Paused and Deleted rows outside the valid set are left untouched -
    Deleted is a more final state than Paused, and should never be
    "revived" back to Paused."""
    status = status.lower()
    return "paus" in status or "delet" in status


def run_tool(
    *,
    platform: str,
    offer_id: str,
    niche: str,
    niche_id: str | None = None,
    afid: str = DEFAULT_AFID,
    width: int = DEFAULT_VIEWPORT["width"],
    height: int = DEFAULT_VIEWPORT["height"],
    domain: str | None = None,
    only_lp: int | None = None,
    dry_run: bool = False,
    headless: bool = False,
    log=print,
) -> dict:
    """The tool's main logic, callable both from the CLI (main()) and from
    the web app. Logs progress through the `log(text)` callback and returns
    a result summary. Raises ToolError for fatal errors (missing login,
    invalid niche, unrecognized domain) - that should stop the whole run,
    as opposed to a single LP's error, which just gets logged and the run
    continues."""
    if platform not in PLATFORMS:
        raise ToolError(f"Unknown platform {platform!r}. Valid: {sorted(PLATFORMS)}")
    admin_base = PLATFORMS[platform]["admin_base"]

    state_path = storage_state_path(platform)
    if not state_path.exists():
        raise ToolError(
            f"You need to log in for platform {platform} first: "
            f"python login.py --platform {platform}"
        )

    niche = niche.upper()
    if niche not in NICHE_LP_NUMBERS:
        raise ToolError(f"Unknown niche {niche!r}. Valid: {sorted(NICHE_LP_NUMBERS)}")

    resolved_niche_id = niche_id or NICHE_IDS[niche]
    if not resolved_niche_id:
        raise ToolError(
            f"niche_id for niche {niche} isn't confirmed (no reference example). "
            "Confirm it manually and run again with niche_id filled in."
        )

    valid_lp_numbers = NICHE_LP_NUMBERS[niche]
    if only_lp is not None:
        valid_lp_numbers = [n for n in valid_lp_numbers if n == only_lp]
        if not valid_lp_numbers:
            raise ToolError(f"LP {only_lp} isn't in the valid set for niche {niche} ({NICHE_LP_NUMBERS[niche]}).")

    viewport = {"width": width, "height": height}
    source_title = None

    created, updated, paused, skipped, failed = [], [], [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        admin_context = browser.new_context(storage_state=str(state_path))
        admin_page = admin_context.new_page()

        if not domain:
            source_title = fetch_offer_title_from_admin(admin_page, admin_base, offer_id)
            domain = domain_from_offer_title(source_title)

        if not domain:
            browser.close()
            raise ToolError(
                f"Couldn't recognize a domain from the offer title '{source_title}' "
                "(probably an older offer with no domain in the title, just a human brand name). "
                "Enter the domain manually."
            )

        if dry_run:
            log("=== MODE: DRY-RUN - NOTHING will be written to the admin, screenshots and plan only ===\n")

        log(f"Domain (from title '{source_title}'): {domain}")
        log(f"Platform: {PLATFORMS[platform]['label']} | Niche: {niche} (niche_id={resolved_niche_id}), valid LP numbers: {valid_lp_numbers}\n")

        all_rows = get_landing_rows(admin_page, admin_base, offer_id)
        rows_by_lp: dict[int, dict] = {}
        unparsed_rows: list[dict] = []
        for row in all_rows:
            lp_number = parse_lp_number_from_title(row["title"])
            if lp_number is None:
                unparsed_rows.append(row)
            else:
                rows_by_lp[lp_number] = row

        if unparsed_rows and only_lp is None:
            log(f"Skipping {len(unparsed_rows)} row(s) with no recognizable LP number (manual review):")
            for row in unparsed_rows:
                log(f"  [{row['id']}] {row['title']} - {row['url_preview'] or row['url']}")
            log("")

        for lp_number in valid_lp_numbers:
            path = build_lp_path(lp_number, resolved_niche_id)
            preview_url = build_preview_url(domain, path)
            full_url = build_full_url_template(domain, path)
            title = build_title(lp_number, niche)
            existing = rows_by_lp.get(lp_number)

            label = f"LP{lp_number}"
            screenshot_path = None
            try:
                if existing and row_matches_expected(existing, preview_url):
                    log(f"[{label}] already matches ({preview_url}) - skipping")
                    skipped.append(lp_number)
                    continue

                action = "edit" if existing else "add"
                log(f"[{label}] {action} -> {preview_url}")
                screenshot_path = screenshot_lp(browser, domain, path, afid, viewport)
                log(f"  screenshot: {screenshot_path}")

                if dry_run:
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
                log("  saved to admin")
            except Exception as exc:
                log(f"  ERROR: {exc}")
                failed.append(lp_number)
            finally:
                if screenshot_path and not dry_run:
                    screenshot_path.unlink(missing_ok=True)

        if only_lp is None:
            for lp_number, row in rows_by_lp.items():
                if lp_number in NICHE_LP_NUMBERS[niche]:
                    continue
                if row_is_untouchable(row["status"]):
                    continue
                label = f"LP{lp_number} (outside {niche}'s valid set)"
                log(f"[{label}] [{row['id']}] {row['title']} -> pause")
                if dry_run:
                    paused.append(lp_number)
                    continue
                try:
                    row_locator = open_inline_edit(admin_page, row["id"])
                    fill_inline_form(row_locator, "inline_edit", status="paused")
                    submit_inline_form(admin_page, row_locator, "inline_edit")
                    log("  status set to paused")
                    paused.append(lp_number)
                except Exception as exc:
                    log(f"  ERROR: {exc}")
                    failed.append(lp_number)

        browser.close()

    dry_run_note = " [DRY-RUN - NOTHING was saved to the admin]" if dry_run else ""
    log(
        f"\nDone{dry_run_note}. Created: {len(created)}, updated: {len(updated)}, "
        f"paused: {len(paused)}, unchanged: {len(skipped)}, errors: {len(failed)}"
    )
    if failed:
        log("LPs with errors: " + ", ".join(str(n) for n in failed))

    return {
        "domain": domain,
        "dry_run": dry_run,
        "created": created,
        "updated": updated,
        "paused": paused,
        "skipped": skipped,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, choices=sorted(PLATFORMS.keys()), help="Which admin to use (default daoofleads)")
    parser.add_argument("--offer-id", required=True, help="Offer ID in the admin")
    parser.add_argument(
        "--niche",
        required=True,
        choices=sorted(NICHE_LP_NUMBERS.keys()),
        type=str.upper,
        help="The offer's niche - determines the valid LP number set and niche_id",
    )
    parser.add_argument(
        "--niche-id",
        help="Force niche_id manually (overrides the value from config.py)",
    )
    parser.add_argument("--afid", default=DEFAULT_AFID, help=f"affiliate ID used for screenshots (default {DEFAULT_AFID})")
    parser.add_argument("--width", type=int, default=DEFAULT_VIEWPORT["width"])
    parser.add_argument("--height", type=int, default=DEFAULT_VIEWPORT["height"])
    parser.add_argument("--domain", help="force the domain manually (skips deriving it from the offer title)")
    parser.add_argument("--only-lp", type=int, help="process just one specific LP number, for testing")
    parser.add_argument("--dry-run", action="store_true", help="only take screenshots and print the plan, don't upload/save anything to the admin")
    parser.add_argument("--headless", action="store_true", help="run the browser without a visible window")
    args = parser.parse_args()

    try:
        run_tool(
            platform=args.platform,
            offer_id=args.offer_id,
            niche=args.niche,
            niche_id=args.niche_id,
            afid=args.afid,
            width=args.width,
            height=args.height,
            domain=args.domain,
            only_lp=args.only_lp,
            dry_run=args.dry_run,
            headless=args.headless,
            log=print,
        )
    except ToolError as exc:
        print(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
