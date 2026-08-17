# LP preview tool

Given a niche, figures out the offer's valid set of LP numbers, walks
through them one by one in the admin's `.../landing` grid, fills in missing
rows (Add), fixes rows with a wrong/stale domain (Edit), and pauses rows
outside the niche's valid set (status Paused, content untouched). For every
newly created/fixed row it takes a clean screenshot of the LP page (no
cookie banner / age-gate overlay) and uploads it as the preview.

Supports three admin platforms (DaoOfLeads, ImaxCash, OnlineDatingKings) -
same underlying admin software, just a different domain and login session
per platform.

Runs entirely on your own computer - has nothing to do with the deployed
TL app on Vercel (that one couldn't run a headless browser with a login
session anyway).

## Two ways to use it

**Web app (recommended)** - a form in the browser instead of terminal
commands. See "Web app" below.

**CLI** - the original command-line usage, still fully supported. See "CLI"
below.

## Installation

```bash
cd LP-tool
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
playwright install chromium
```

## Web app

**Easiest**: double-click the "LP tool" shortcut on the desktop (or
`Start LP tool.bat` inside this folder). It starts the app in the
background and opens it in your browser automatically - no terminal
needed.

Manually:
```bash
.venv\Scripts\python.exe app.py
```
then open `http://127.0.0.1:5000`.

The app has two pages:
1. **Login** (`/`) - shows login status for all three platforms, with a
   "Log in" button that opens a real browser window for you to log in
   manually (your password is never seen or stored by the app). Click
   "Done, save session" once you're logged in.
2. **Tool** (`/tool`) - the offer form (platform, offer ID, niche,
   dry-run/headless toggles, advanced options) and a live progress log.

It always processes one offer at a time, deliberately, for control.

If the app stops responding, a red banner appears telling you to restart it
via the desktop shortcut. Its output is logged to `app.log` in this folder
for troubleshooting.

## CLI

**1. Log in once per platform** (password is never saved anywhere, a real
browser window opens, you log in manually, and only the resulting session
gets saved):

```bash
python login.py --platform daoofleads
```

Creates `storage_state_daoofleads.json` (listed in `.gitignore`, never
committed). Run again whenever the session expires.

**2. Test run on a single LP** (screenshot + plan only, nothing gets
uploaded or saved to the admin):

```bash
python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT --only-lp 10 --dry-run
```

**3. One specific LP, uploaded to the admin for real:**

```bash
python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT --only-lp 10
```

**4. The whole offer** (fills in/fixes every LP in the niche's valid set,
pauses the ones outside it):

```bash
python lp_tool.py --platform daoofleads --offer-id 16689 --niche ADULT
```

### Optional parameters

| Parameter | Default | Description |
|---|---|---|
| `--platform` | `daoofleads` | which admin platform to use (`daoofleads` / `imaxcash` / `onlinedatingkings`) |
| `--niche-id` | based on `--niche` (config.py) | force niche_id manually, overrides the value from `config.py` |
| `--afid` | `2792` | affiliate ID used in the URL when screenshotting |
| `--width` / `--height` | `1250` / `825` | screenshot resolution |
| `--domain` | (derived from the offer title) | force the domain manually, when derivation fails |
| `--only-lp` | (the whole valid set) | process just one specific LP number, for testing |
| `--dry-run` | off | screenshots and plan only, no writes to the admin |
| `--headless` | off | run without a visible browser window |

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
(URL, screenshot, title) is left untouched.

Rows whose Title doesn't match the `LP<number>...` pattern (old/foreign
format) are printed separately and left alone - they need manual review.

## How the domain is derived

The domain is **always** taken from the offer's title in the admin, never
from the existing URL in the grid (that one can be stale/wrong) and never
from a static niche → domain map (that's only used as a reference for valid
LP numbers and niche_id). The title looks like
`"sexkontakt.com - GERMANY - ADULT - REV"` - whichever segment (split on
` - `) looks like a domain is used; usually that's the first one, but
test/cloned offers sometimes have a descriptive prefix before it.

## Admin forms

Add/Edit forms are driven through their real `name` attributes
(`inline_add[...]` / `inline_edit[...]`) as documented by the admin - no
more guessing based on field content. The tool never calls the delete
action (`do=deleteLanding`) - that always stays a manual action.

## Known limitations / things to tune

- The tool scrapes the admin grid by the table's header text (`ID`,
  `Title`, `URL`, `URL preview`, `Status`). If the admin's table structure
  ever changes, the script fails with a clear error (not a silent
  failure) - just update `get_landing_rows()` in `lp_tool.py`.
- Save confirmation is detected by waiting for text matching `Successfully
  (added|edited|created|inserted)` (case-insensitive) - the exact wording
  wasn't available for Add, only for Edit ("Successfully edited"); if the
  admin reports something else, it fails loudly and `SUCCESS_TEXT_RE` in
  `lp_tool.py` needs updating.
- Cookie banner and age-gate detection is based on a list of button texts
  in `config.py` (`DISMISS_BUTTON_TEXTS`), with a keyword fallback
  (`DISMISS_FALLBACK_KEYWORDS`). A new domain with a different button text
  outside both lists needs to be added.
