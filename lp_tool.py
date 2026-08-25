"""
Tool for bulk creating/fixing landing page records and their previews in the
affiliates.{platform}.com admin, according to the niche's valid set of LP
numbers.

Steps for a given offer_id (+ optional niche):
  1. The domain is taken from the offer's title in the admin, NEVER from a
     static niche -> domain map and NEVER from the existing URL in the grid
     (that one can be wrong/stale).
  2. The niche is taken from --niche if given, otherwise auto-detected from
     that same offer title (a niche name found as a substring). If both a
     given --niche and the detected niche exist but disagree, the given
     value wins but a warning is printed. Determines the valid set of LP
     numbers and the niche_id (config.py).
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
    python login.py --platform daoofleads                        # once, log in manually
    python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT --dry-run
    python lp_tool.py --platform daoofleads --offer-id 16689 --dry-run           # niche auto-detected
    python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT --only-lp 10
    python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

from config import (
    AGE_GATE_BUTTON_TEXTS,
    DEFAULT_AFID,
    DEFAULT_PLATFORM,
    DEFAULT_VIEWPORT,
    DISMISS_FALLBACK_KEYWORDS,
    NICHE_IDS,
    NICHE_LP_NUMBERS,
    PLATFORMS,
    build_full_url_template,
    build_lp_path,
    build_preview_url,
    build_title,
    domain_from_offer_title,
    niche_from_offer_title,
    parse_lp_number_arg,
    parse_lp_number_from_title,
)

STORAGE_STATE_DIR = Path(__file__).parent
SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
SUCCESS_TEXT_RE = re.compile(r"Successfully (added|edited|created|inserted)", re.IGNORECASE)


class ToolError(Exception):
    """An error that stops the whole run (missing login, unrecognized
    domain/niche, invalid niche) - as opposed to a single LP's error, which
    just gets logged and the run continues."""


def storage_state_path(platform: str) -> Path:
    return STORAGE_STATE_DIR / PLATFORMS[platform]["storage_state_filename"]


def _check_admin_access(page: Page, platform: str, offer_id: str) -> None:
    """The admin never errors outright for either of these - it silently
    redirects instead, so without this check either case shows up as a
    confusing 30s timeout on whatever selector we expected next, deep
    inside an unrelated function. Call right after navigating to an admin
    page, before waiting on any of its content.

    Two distinct redirect targets, two distinct causes:
      - /admin/account/login - the saved session has expired.
      - /admin/account/denied - the session is fine, but this offer ID
        belongs to a different platform/account than the one selected
        (each platform is a separate admin account with its own offers)."""
    if "/admin/account/login" in page.url:
        raise ToolError(
            f"Your saved login session for {platform} has expired - log in again "
            f"(python login.py --platform {platform}, or the Log in again button)."
        )
    if "/admin/account/denied" in page.url:
        raise ToolError(
            f"Offer {offer_id} isn't accessible under {PLATFORMS[platform]['label']} (permission denied). "
            "It's likely registered under a different platform - double-check the Platform dropdown and try again."
        )


CONSENT_REJECT_HINTS = ["reject", "decline", "deny", "refuse", "rall", "disagree"]
CONSENT_MANAGE_HINTS = ["settings", "preferences", "manage", "choose", "customize", "customise", "options"]


def _find_visible_cookie_dialog(page: Page, timeout_ms: int = 4000):
    """Finds a visible dialog/banner that mentions the word "cookie" (that
    word stays untranslated in most languages). Must be VISIBLE, not just
    present in the DOM - after opening a detailed settings panel, the
    original simple banner is often still in the DOM as a wrapper around it
    (e.g. zralalaska.cz nests its detailed settings panel DIRECTLY INSIDE
    the simple banner, not as a sibling), so the LAST (= most nested, i.e.
    currently relevant) visible match is used, not the first.

    Retries for up to `timeout_ms` - some cookie banners (e.g.
    zralalaska.cz) only appear ~2s after the page loads (the library's own
    JS timer), so a single check right after page.goto() would miss them
    entirely."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            candidates = page.locator('[role="dialog"], [role="alertdialog"]').filter(
                has_text=re.compile("cookie", re.IGNORECASE)
            )
            last_visible = None
            for i in range(candidates.count()):
                candidate = candidates.nth(i)
                if candidate.is_visible():
                    last_visible = candidate
            if last_visible is not None:
                return last_visible
        except Exception:
            pass
        page.wait_for_timeout(200)
    return None


def _click_hinted(scope, hints: list[str]) -> bool:
    """Clicks the first element within `scope` whose id/class/data-cc
    contains one of `hints` - those attributes stay in English in the code
    even on a translated site, making them a more language-independent
    signal than the button's visible text."""
    selector = ", ".join(f"[id*='{h}' i], [class*='{h}' i], [data-cc*='{h}' i]" for h in hints)
    try:
        btn = scope.locator(selector).first
        if btn.count() > 0:
            btn.click(timeout=2000)
            return True
    except Exception:
        pass
    return False


def _dismiss_cookie_banner(page: Page, timeout_ms: int = 4000) -> bool:
    """Tries to reject the cookie banner. Returns True if something was
    clicked.

    Rejected deliberately, regardless of the site's language: button
    id/class/data-cc attributes stay in English in the code (e.g.
    "s-rall-bn" for "Reject all"), even when the displayed text is
    Czech/German/etc. A direct "reject" button is tried first; if the
    banner only offers "accept" + "manage/choose" (two-step banners, see
    zralalaska.cz), that's opened first and reject is tried again in the
    freshly-opened panel. If reject can't be found anywhere, "accept" is
    NOT clicked as a substitute - better to leave the banner in the
    screenshot than accidentally consent to cookies."""
    dialog = _find_visible_cookie_dialog(page, timeout_ms=timeout_ms)
    if not dialog:
        return False
    if _click_hinted(dialog, CONSENT_REJECT_HINTS):
        return True
    if _click_hinted(dialog, CONSENT_MANAGE_HINTS):
        page.wait_for_timeout(400)
        dialog2 = _find_visible_cookie_dialog(page, timeout_ms=1500)
        if dialog2:
            return _click_hinted(dialog2, CONSENT_REJECT_HINTS)
    return False


def _dismiss_age_gate(page: Page) -> bool:
    """Tries to click the "I'm 18+" self-declaration button from the exact
    text list (AGE_GATE_BUTTON_TEXTS). Returns True if something was
    clicked.

    An exact text list, not generic id/class detection like the cookie
    banner - picking wrong here means leaving to an "I'm underage" page,
    not just a differently-worded banner (the "I'm underage" choice could
    easily have an id containing something like "no"/"deny" too), so
    generic keyword detection wouldn't be safe here."""
    clicked = False
    for text in AGE_GATE_BUTTON_TEXTS:
        try:
            btn = page.get_by_text(text, exact=False)
            if btn.count() > 0:
                btn.first.click(timeout=1500)
                clicked = True
        except Exception:
            continue
    return clicked


def dismiss_overlays(page: Page) -> None:
    """Best-effort dismissal of the age gate / cookie banner. Doesn't fail
    if it finds nothing.

    The age gate and cookie banner commonly stack on top of each other
    (typically age gate on top, cookie banner underneath) and not always in
    the same order. Trying each only once in a fixed order ("cookie first,
    then age gate") would mean a click on a cookie banner covered by the
    age gate fails, and the cookie banner never gets retried even though it
    would be freely reachable once the age gate is gone. So both are tried
    in a loop, until a round produces no action.
    """
    dismissed_any = False
    for i in range(3):
        acted_age = _dismiss_age_gate(page)
        page.wait_for_timeout(200)
        acted_cookie = _dismiss_cookie_banner(page, timeout_ms=4000 if i == 0 else 800)
        dismissed_any = dismissed_any or acted_age or acted_cookie
        if not acted_age and not acted_cookie:
            break
        page.wait_for_timeout(200)

    if dismissed_any:
        return

    # Fallback for when nothing above matched - looks for a keyword among
    # clickable elements, clicks at most the first match found (caution
    # against clicking something unrelated by mistake).
    for keyword in DISMISS_FALLBACK_KEYWORDS:
        try:
            candidate = page.locator(f"button:has-text('{keyword}'), a:has-text('{keyword}'), input[type=submit][value*='{keyword}' i]").first
            if candidate.count() > 0:
                candidate.click(timeout=2000)
                page.wait_for_timeout(300)
                break
        except Exception:
            continue


def freeze_blinking_elements(page: Page) -> None:
    """Some LPs (e.g. the dating-service.info template family) use a
    `.blink` CSS class on the registration/login button, animated via
    `animation: 1s steps(1, start) infinite blink-animation` with a
    keyframe that sets `color: transparent` at 50% - i.e. the button's text
    disappears for half of every one-second cycle. A screenshot taken at a
    random moment has a coin-flip chance of catching it mid-blink with
    invisible text. Injecting a stylesheet that disables the animation
    freezes it on its normal (visible) resting color, regardless of when
    the screenshot happens to be taken."""
    try:
        page.add_style_tag(content=".blink { animation: none !important; }")
    except Exception:
        pass


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
    # Clicking a button inside a cookie/age-gate overlay can leave the page
    # scrolled - Playwright's click() scrolls the clicked element into view
    # first, and some sites' own JS also shifts scroll position as part of
    # closing the overlay (confirmed on szukajtrans.com: scrollY went from
    # 0 to 297 after dismiss_overlays()). Without resetting it, the
    # screenshot captures whatever now happens to be in view instead of the
    # page's actual top, which looks like a completely different/wrong
    # template even though it's the same page just scrolled down.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    freeze_blinking_elements(page)

    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    out_path = SCREENSHOTS_DIR / f"lp-screenshot-{uuid.uuid4().hex}.png"
    page.screenshot(path=str(out_path))
    context.close()
    return out_path


def fetch_offer_title_from_admin(admin_page: Page, admin_base: str, offer_id: str, platform: str) -> str:
    """Pulls the offer's title from its main edit page in the admin. It's
    not the <h1> - that's just a generic "Offer offer edit" heading, the
    same on every offer - the real title is in the <strong> inside the
    .navbar-brand link in the page's content header (scoped to
    section.content, since the top AdminLTE header has its own separate
    brand/logo link)."""
    admin_page.goto(f"{admin_base}/en/admin/offer/edit/{offer_id}?locale=en", wait_until="networkidle")
    _check_admin_access(admin_page, platform, offer_id)
    return admin_page.locator("section.content nav.navbar a.navbar-brand strong").first.inner_text()


def get_landing_rows(admin_page: Page, admin_base: str, offer_id: str, platform: str) -> list[dict]:
    grid_url = f"{admin_base}/en/admin/offer/edit/{offer_id}/landing?locale=en&landingGrid-perPage=200"
    admin_page.goto(grid_url, wait_until="networkidle")
    _check_admin_access(admin_page, platform, offer_id)

    table = admin_page.locator("table").first

    # thead has more than one <tr> (a standalone row with just the
    # select-all checkbox, a row with the column labels, a row with the
    # filter inputs) - the label row needs to be found by CONTENT, not by
    # position, otherwise the columns shift relative to the real <td> in
    # each row (see README - "Known limitations").
    required = ["id", "title", "preview", "url", "url preview", "status"]
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
                # A row with a preview image has an empty cell here (just
                # an <img>, no text) - "N/A" means the row exists but never
                # got a screenshot uploaded (e.g. an older row created
                # before this tool managed it), which needs the same fix
                # as a wrong URL - see row_matches_expected()'s caller.
                "has_preview": tds[col["preview"]].strip().upper() != "N/A",
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
    # The success flash can render before the grid's AJAX reload/redraw of
    # the row list actually finishes. Without waiting for that to settle,
    # immediately opening the NEXT row's inline add/edit form risks
    # operating on a row element that's mid-replacement - observed in
    # practice (offer 16793, LP20: "Successfully added" showed, but the row
    # never actually existed; it worked fine on a retry once the grid had
    # settled).
    try:
        admin_page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    admin_page.wait_for_timeout(500)


def _strip_www(url: str) -> str:
    return url.rstrip("/").replace("://www.", "://", 1)


def row_matches_expected(row: dict, expected_preview_url: str) -> bool:
    """A bare domain and its "www." form are the same page (see
    normalize_domain() in config.py) - one just redirects to the other, so
    a legacy row using whichever form still works and shouldn't be flagged
    as a mismatch (and rewritten/re-screenshotted) just for that. Compared
    with "www." stripped from both sides for that reason - an actually
    different domain (e.g. a cloned row's foreign domain) still doesn't
    match, since only the "www." prefix is normalized away, not the rest of
    the domain."""
    return _strip_www(row["url_preview"]) == _strip_www(expected_preview_url)


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
    niche: str | None = None,
    niche_id: str | None = None,
    afid: str = DEFAULT_AFID,
    width: int = DEFAULT_VIEWPORT["width"],
    height: int = DEFAULT_VIEWPORT["height"],
    domain: str | None = None,
    only_lp: int | float | None = None,
    dry_run: bool = False,
    headless: bool = False,
    log=print,
    on_screenshot=None,
) -> dict:
    """The tool's main logic, callable both from the CLI (main()) and from
    the web app. Logs progress through the `log(text)` callback and returns
    a result summary. When `dry_run` is on, calls `on_screenshot(lp_number,
    path)` for every screenshot taken, so a caller (the web app) can show a
    live preview - dry-run screenshots aren't deleted, unlike a real run's,
    which get removed right after upload. Raises ToolError for fatal errors
    (missing login, invalid niche, unrecognized domain/niche) - that should
    stop the whole run, as opposed to a single LP's error, which just gets
    logged and the run continues.

    `niche` is optional - when omitted, it's auto-detected from the offer
    title the same way `domain` already is. If both are given and disagree,
    the given value wins but a warning is logged."""
    if platform not in PLATFORMS:
        raise ToolError(f"Unknown platform {platform!r}. Valid: {sorted(PLATFORMS)}")
    admin_base = PLATFORMS[platform]["admin_base"]

    state_path = storage_state_path(platform)
    if not state_path.exists():
        raise ToolError(
            f"You need to log in for platform {platform} first: "
            f"python login.py --platform {platform}"
        )

    if niche is not None:
        niche = niche.upper()
        if niche not in NICHE_LP_NUMBERS:
            raise ToolError(f"Unknown niche {niche!r}. Valid: {sorted(NICHE_LP_NUMBERS)}")

    viewport = {"width": width, "height": height}
    source_title = None

    created, updated, paused, skipped, failed = [], [], [], [], []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        admin_context = browser.new_context(storage_state=str(state_path))
        admin_page = admin_context.new_page()

        if not domain or not niche:
            source_title = fetch_offer_title_from_admin(admin_page, admin_base, offer_id, platform)
            if not domain:
                domain = domain_from_offer_title(source_title)

        if not domain:
            browser.close()
            raise ToolError(
                f"Couldn't recognize a domain from the offer title '{source_title}' "
                "(probably an older offer with no domain in the title, just a human brand name). "
                "Enter the domain manually."
            )

        detected_niche = niche_from_offer_title(source_title) if source_title else None
        if niche and detected_niche and niche != detected_niche:
            log(
                f"WARNING: the given niche ({niche}) doesn't match the niche detected from the "
                f"offer title ('{source_title}' -> {detected_niche}). Using the given value "
                f"({niche}) - double-check offer_id/niche if this is unexpected."
            )
        niche = niche or detected_niche
        if not niche:
            browser.close()
            raise ToolError(
                f"Couldn't recognize a niche from the offer title '{source_title}'. "
                "Give it explicitly via --niche <NICHE>."
            )

        resolved_niche_id = niche_id or NICHE_IDS[niche]
        if not resolved_niche_id:
            browser.close()
            raise ToolError(
                f"niche_id for niche {niche} isn't confirmed (no reference example). "
                "Confirm it manually and run again with niche_id filled in."
            )

        valid_lp_numbers = NICHE_LP_NUMBERS[niche]
        if only_lp is not None:
            valid_lp_numbers = [n for n in valid_lp_numbers if n == only_lp]
            if not valid_lp_numbers:
                browser.close()
                raise ToolError(f"LP {only_lp} isn't in the valid set for niche {niche} ({NICHE_LP_NUMBERS[niche]}).")

        if dry_run:
            log("=== MODE: DRY-RUN - NOTHING will be written to the admin, screenshots and plan only ===\n")

        log(f"Domain (from title '{source_title}'): {domain}")
        log(f"Platform: {PLATFORMS[platform]['label']} | Niche: {niche} (niche_id={resolved_niche_id}), valid LP numbers: {valid_lp_numbers}\n")

        all_rows = get_landing_rows(admin_page, admin_base, offer_id, platform)
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
                url_already_correct = existing and row_matches_expected(existing, preview_url)
                if existing and existing["has_preview"] and url_already_correct:
                    log(f"[{label}] already matches ({preview_url}) - skipping")
                    skipped.append(lp_number)
                    continue
                if url_already_correct and not existing["has_preview"]:
                    log(f"[{label}] URL already correct but preview is missing (N/A) - adding screenshot")

                action = "edit" if existing else "add"
                dry_run_tag = "[DRY RUN] " if dry_run else ""
                log(f"{dry_run_tag}[{label}] {action} -> {preview_url}")
                screenshot_path = screenshot_lp(browser, domain, path, afid, viewport)
                log(f"  screenshot: {screenshot_path}")

                if dry_run:
                    if on_screenshot:
                        on_screenshot(lp_number, screenshot_path)
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
                dry_run_tag = "[DRY RUN] " if dry_run else ""
                log(f"{dry_run_tag}[{label}] [{row['id']}] {row['title']} -> pause")
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
        "niche": niche,
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
        choices=sorted(NICHE_LP_NUMBERS.keys()),
        type=str.upper,
        help="The offer's niche - determines the valid LP number set and niche_id. When omitted, "
             "it's auto-detected from the offer title (same as the domain). If given and it "
             "disagrees with the title, a warning is printed but the given value still wins.",
    )
    parser.add_argument(
        "--niche-id",
        help="Force niche_id manually (overrides the value from config.py)",
    )
    parser.add_argument("--afid", default=DEFAULT_AFID, help=f"affiliate ID used for screenshots (default {DEFAULT_AFID})")
    parser.add_argument("--width", type=int, default=DEFAULT_VIEWPORT["width"])
    parser.add_argument("--height", type=int, default=DEFAULT_VIEWPORT["height"])
    parser.add_argument("--domain", help="force the domain manually (skips deriving it from the offer title)")
    parser.add_argument("--only-lp", type=parse_lp_number_arg, help="process just one specific LP number, for testing (decimal variants like 10.2 are fine)")
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
