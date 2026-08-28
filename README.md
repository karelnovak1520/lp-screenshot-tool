# Affil Automation

## At a glance

**What it does** - two tools in one local app, for three affiliate admin
platforms (**DaoOfLeads**, **ImaxCash**, **OnlineDatingKings**):

- **LP Preview Tool** - for a given offer ID, fixes up its landing-page rows
  in the admin to match what the niche requires (adds missing ones, fixes
  stale ones, pauses ones that don't belong) and takes a clean screenshot of
  each.
- **Tracking Link Generator** - paste an example tracking link + a list of
  offer IDs, get a correct tracking link back per offer, domain looked up
  automatically.

**How to use it** - double-click the **Affil Automation** shortcut on the
Desktop. A browser opens at `http://127.0.0.1:5001` with a home page linking
to both tools. First time, log in for whichever platform you need (see
"Logging in" below) - everything after that is point-and-click in the
browser.

**What you need installed** - Windows or macOS, **Python 3.10+**, and a
one-time setup that installs the dependencies + Playwright's bundled
Chromium (~200 MB, see "Setup" below). Nothing else.

Runs entirely on your own computer via a real, logged-in browser session -
it has nothing to do with the Tracking Links app deployed on Vercel (that
one can't run a headless browser with a persistent admin login).

## What you need

- **macOS or Windows**, with **Python 3.10+** installed.
- Network access to the admin(s) you use: `affiliates.daoofleads.com`,
  `affiliates.imaxcash.com`, `affiliates.onlinedatingkings.com`.
- A valid **admin login** (username/password) for whichever platform(s)
  you'll use - the app opens a real browser window for you to type it into
  yourself; it's never seen, typed, or stored by the tool itself, only the
  resulting session cookie is saved (see "Logging in" below).
- About 200 MB free for Playwright's bundled Chromium (installed once,
  during setup).

## Setup (once)

```bash
cd Affil-Automation
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

(On Windows: `.venv\Scripts\activate` instead of `source .venv/bin/activate`.)

## Running it

**macOS** - double-click `Start LP tool.command` on the Desktop (or wherever
you put a shortcut to it). It kills any already-running copy first (so you
never end up serving stale code after an update), starts the app in the
background, and opens it in your browser automatically.

**Windows** - double-click `Start LP tool.bat` - same behavior.

**Manually, from a terminal (either OS):**

```bash
source .venv/bin/activate   # .venv\Scripts\activate on Windows
python app.py
```

Then open **http://127.0.0.1:5001** (not 5000 - on macOS that port is
squatted by the AirPlay Receiver, which silently answers instead of the app
and shows a confusing 403 error in the browser).

Its own log is written to `app.log` in this folder, useful for
troubleshooting if something looks wrong in the browser.

## Logging in

Nothing works until you're logged in for at least one platform. From the
Home page, or from the "Login status" box at the top of either tool page,
click **Log in** (or **Log in again**, once a session has expired - on
these admins that tends to happen within about a day) next to the platform
you need:

1. A real, separate Chromium window opens at that platform's own login
   page.
2. Log in there yourself, normally.
3. Once you can see the admin, come back to the app and click **"Done,
   save session"**.

That saves the session to `storage_state_<platform>.json` in this folder -
**gitignored, never committed, never leaves your computer**. Logging in
again for DaoOfLeads or ImaxCash also refreshes the local offer list used
by the Tracking Link Generator's search (see below) - no separate step
needed.

Logging in *inside* a run's own browser window (visible only when
"Headless" is unchecked) does **not** save anything - that window is just
the automation doing its work, not a login flow. Always use the "Log in" /
"Log in again" button for that.

## What the tool can do

- **LP preview tool** (`/tool`) - for a given offer ID (+ optional niche,
  auto-detected from the offer title if omitted): adds missing LP rows,
  fixes rows pointing at the wrong domain, and pauses rows outside the
  niche's valid set - **never rewriting the content of a paused row**,
  only its status. Every created/fixed row gets a fresh screenshot with
  the site's cookie banner and age gate auto-dismissed (any language).
  Supports a dry-run mode (screenshots and a plan only, nothing written to
  the admin) and a single-LP test mode.
- **Tracking Link Generator** (`/links`) - paste any real example tracking
  link (real numbers in it are fine, they're auto-detected and replaced),
  lock in your affiliate ID, then search and add offer IDs by ID or name.
  Generates one correct link per offer, with the network's required
  `&ext_id=...&source=...` suffix always appended. Offer lookups use a
  local cache first (instant) and fall back to a live admin lookup for
  anything not cached yet. Results show each offer's country flag, flag
  India offers with a safety overlay (their tracking format is completely
  different and must be pulled manually), and include one-click "Clear
  results", "Start over", and "Copy all" actions.
- **Automatic offer cache** - every login for DaoOfLeads/ImaxCash pulls a
  fresh CSV export of that platform's offers and refreshes a local,
  gitignored cache (`offers_cache.json`), reporting what's new and what
  just got paused - this is what powers the Tracking Link Generator's
  search. There's also a manual "Refresh now" button on that page if you
  know new offers exist but don't want to log in again.
- **Clear errors instead of confusing timeouts** - an expired session or an
  offer ID that belongs to a different platform's admin account both used
  to show up as an opaque 30-second timeout; both now surface as a plain,
  actionable message (and, for the wrong-platform case, a pop-up warning
  telling you to double-check the Platform dropdown).
- **`sync_offers.py`** - a separate, manual script (not run by the web app)
  that pushes the same CSV data into the *other*, publicly-deployed
  Tracking Links app's `data/offers.json`. Kept deliberately separate from
  the automatic local cache above, since its output is what actually goes
  out on the public site.

It always processes one offer/run at a time, by design, for control - no
bulk/background queue.

## CLI (optional, for scripting/testing)

Both tools are also available as scripts, without the web UI.

**LP preview tool:**

```bash
python login.py --platform daoofleads                       # once, or whenever the session expires
python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT --only-lp 10 --dry-run
python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT      # one specific LP, for real
python lp_tool.py --platform daoofleads --offer-id 16689                    # whole offer, niche auto-detected
```

| Parameter | Default | Description |
|---|---|---|
| `--platform` | `daoofleads` | which admin platform to use (`daoofleads` / `imaxcash` / `onlinedatingkings`) |
| `--niche` | auto-detected from the offer title | the offer's niche; if given and it disagrees with the title, a warning is printed but the given value still wins |
| `--niche-id` | based on `--niche` (config.py) | force niche_id manually, overrides the value from `config.py` |
| `--afid` | `2792` | affiliate ID used in the URL when screenshotting |
| `--width` / `--height` | `1250` / `825` | screenshot resolution |
| `--domain` | (derived from the offer title) | force the domain manually, when derivation fails |
| `--only-lp` | (the whole valid set) | process just one specific LP number, for testing |
| `--dry-run` | off | screenshots and plan only, no writes to the admin (screenshots are kept in `screenshots/` for review instead of being deleted) |
| `--headless` | off | run without a visible browser window |

**Tracking Link Generator:**

```bash
python link_tool.py --platform daoofleads --offer-id 16793,16813 \
    --aff-id 2792 --template "https://hubaffillink.eu/?aff_id=22513&offer_id=13923"
```

**Offer sync (into the other, publicly-deployed TL app):**

```bash
python sync_offers.py --out ../LP-tool/data/offers.json
```

## How it decides what to do with a given LP

For every LP number in the niche's valid set (`NICHE_LP_NUMBERS` in
`config.py`):

1. The row in the grid is matched by the LP number recognized from its
   **Title** (shaped like `LP{N} - {NICHE}`) - not by row ID and not by
   URL, because cloned rows carry over a foreign domain (and possibly a
   foreign LP number) from the source offer in their URL, while Title stays
   correct for the target slot.
2. If the row doesn't exist → **Add** (new screenshot, new row, status `active`).
3. If the row exists but its URL preview doesn't match the expected domain
   and path → **Edit** (new screenshot, rewritten title/URL/URL preview,
   status unchanged).
4. If the row exists and already matches → nothing happens.

After processing the valid set, every row in the grid is checked once more:
any row whose LP number is **not** in the niche's valid set, and isn't
already `Paused` or `Deleted`, gets switched to `Paused` - its content
(URL, screenshot, title) is left untouched (never fabricated for a number
that was never specified for that niche).

Rows whose Title doesn't match the `LP<number>...` pattern (old/foreign
format) are printed separately and left alone - they need manual review.

## How the niche and domain are derived

The **domain** is always taken from the offer's title in the admin, never
from the existing URL in the grid (that one can be stale/wrong) and never
from a static niche → domain map (that one is only used as a reference for
valid LP numbers and niche_id). The title looks like
`"sexkontakt.com - GERMANY - ADULT - REV"` - whichever segment (split on
` - `) looks like a domain is used; usually that's the first one, but
test/cloned offers sometimes have a descriptive prefix before it (e.g.
`"TEST - minasdivinas.com - ADULT - ..."`).

The **niche** works the same way when `--niche` is omitted: whichever
`NICHE_LP_NUMBERS` key (ADULT, FLIRT, BDSM, MILF, SENIOR, TRANS) appears as
a substring of the title is used (matches even with an extra suffix, e.g.
`"SENIORS 50+"` still matches `SENIOR`). If `--niche` is given explicitly
and it disagrees with what the title says, a warning is printed but the
given value still wins - useful for testing, and a safety net against a
copy-pasted offer_id that doesn't match the niche you meant to run.

## Cookie banner and age gate

Handled differently on purpose, because getting them wrong has very
different consequences:

- **Cookie banner**: dismissed generically, regardless of the site's
  language. A visible dialog mentioning the word "cookie" is found, and a
  "reject"-ish element is clicked based on its `id`/`class`/`data-cc`
  attribute (those stay in English in the code even on a translated site,
  e.g. `id="s-rall-bn"` for "Odmítnout vše"/"Reject all") - trying a direct
  reject button first, then falling back to opening "manage/settings" and
  retrying reject inside it. If no reject option can be found anywhere,
  "accept" is deliberately **not** clicked as a substitute - a banner left
  in the screenshot is preferable to silently consenting to cookies. Cookie
  banners and the age gate can stack on top of each other in either order,
  so both are retried in a loop until a round produces no action, instead
  of each being tried only once in a fixed order.
- **Age gate** ("I'm 18+" self-declaration): matched against an exact,
  maintained list of texts per language (`AGE_GATE_BUTTON_TEXTS` in
  `config.py`), not generic keyword/attribute detection - picking the wrong
  option here means leaving to an "I'm underage" page instead of just a
  differently-styled banner, so guessing isn't safe. A new domain with an
  age-gate wording outside the list needs it added there (grab the exact
  text from the page).
- Last-resort fallback: `DISMISS_FALLBACK_KEYWORDS` in `config.py`, a small
  set of keywords (`"18"`, `"adult"`, ...) tried against clickable elements
  when nothing else matched.

## Admin forms

Add/Edit forms are driven through their real `name` attributes
(`inline_add[...]` / `inline_edit[...]`) as documented by the admin - no
guessing based on field content. The tool never calls the delete action
(`do=deleteLanding`) - that always stays a manual action.

After every inline save, the tool waits for the grid to settle (network
idle + a short fixed delay) before touching the next row - the "Successfully
added/edited" flash can render before the grid's AJAX reload of the row
list actually finishes, and opening the next row's form too early risks
operating on an element that's mid-replacement.

## Session and permission errors

Two situations the admin doesn't error on directly - it silently redirects
instead - are detected explicitly right after navigating, instead of
surfacing as a generic 30-second locator timeout:

- **Expired session** (redirected to the login page) → a clear message
  telling you to log in again for that platform.
- **Wrong platform** (redirected to an "access denied" page - the offer ID
  is valid, just registered under a *different* platform's admin account)
  → a clear message naming the offer and platform, plus a pop-up warning in
  the LP preview tool telling you to double-check the Platform dropdown.

## Known limitations / things to tune

- The tool scrapes the admin grid by the table's header text (`ID`,
  `Title`, `URL`, `URL preview`, `Status`). If the admin's table structure
  ever changes, the script fails with a clear error (not a silent
  failure) - just update `get_landing_rows()` in `lp_tool.py`.
- Save confirmation is detected by waiting for text matching `Successfully
  (added|edited|created|inserted)` (case-insensitive) - if the admin
  reports something else, it fails loudly and `SUCCESS_TEXT_RE` in
  `lp_tool.py` needs updating.
- `TRANS` niche_id and valid LP numbers (`10`, `10.2`, `17`, `27`, `27.1`)
  are confirmed from real data (hledasetrans.cz, tsseeker.com). Other
  niches' valid LP sets come from the original spec and haven't all been
  independently re-verified.
- LP numbers are normally whole, but a niche can have decimal *variants* of
  a slot (TRANS's `10.2` and `27.1` are the first ones) - these are kept as
  `float` throughout (vs. plain `int` for whole numbers) and both types are
  handled consistently everywhere an LP number is parsed, built, or
  compared (`config.py`'s `_to_lp_number()`/`parse_lp_number_arg()`).
- The Tracking Link Generator's offer cache and India-format safety check
  only cover DaoOfLeads and ImaxCash (`PLATFORM_TO_SOURCE` in
  `offer_cache.py`) - OnlineDatingKings isn't part of it.

## Project files

| File | Purpose |
|---|---|
| `app.py` | Flask web app - all routes for both tools, login, and the offer cache endpoints |
| `lp_tool.py` | LP preview tool's core logic (also usable standalone via CLI) |
| `link_tool.py` | Tracking Link Generator's core logic (also usable standalone via CLI) |
| `login.py` | One-time/repeatable manual login flow, saves `storage_state_<platform>.json` |
| `config.py` | Platform list, niche/LP-number tables, domain/niche/title parsing helpers |
| `offer_export.py` | Shared CSV-export fetching/parsing (used by both the local cache and `sync_offers.py`) |
| `offer_cache.py` | The local, auto-refreshed offer cache behind the Tracking Link Generator's search |
| `sync_offers.py` | Manual script - pushes offer data into the separate, publicly-deployed TL app |
| `templates/` | The web app's pages (Home, Login, LP preview tool, Tracking Link Generator) |
| `Start LP tool.command` / `.bat` | Desktop double-click launchers (macOS / Windows) |
