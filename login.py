"""
One-time login to the admin.

Opens a real (non-headless) browser window on the given platform's login
page. Log in there MANUALLY (your password is never saved or sent
anywhere else). After logging in, confirm it in the terminal with Enter
and the login state (cookies/session) gets saved to
storage_state_{platform}.json, which lp_tool.py (and the web app) then use.

Run it again whenever the session expires (on this admin that tends to
happen pretty often, sometimes within a day).

Usage:
    python login.py --platform daoofleads
    python login.py --platform imaxcash
    python login.py --platform onlinedatingkings
"""

from __future__ import annotations

import argparse

from playwright.sync_api import sync_playwright

from config import DEFAULT_PLATFORM, PLATFORMS
from lp_tool import storage_state_path


def login_and_save(platform: str, wait_for_confirm=None, log=print) -> None:
    """Opens the given platform's login page, waits for a manual login, and
    saves storage_state. `wait_for_confirm` is a zero-argument function that
    gets called and should block until the user confirms they're logged in
    - the default is a terminal input() (CLI usage), the web app passes its
    own (waiting for a confirmation button click in the app's browser).

    For daoofleads/imaxcash - the two platforms the Tracking Link
    Generator's local offer cache covers - a successful login also
    refreshes that cache right away. This is the ONLY place the cache gets
    refreshed from: tied to logging in (which you already have to do
    periodically anyway, since sessions expire), not a timer. A sync
    failure is logged but never fails the login itself - the login already
    succeeded, which matters more."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform {platform!r}. Valid: {sorted(PLATFORMS)}")

    login_url = f"{PLATFORMS[platform]['admin_base']}/en/admin/sign/in"
    state_path = storage_state_path(platform)

    if wait_for_confirm is None:
        def wait_for_confirm():
            input(
                "\nLog in to the admin in the browser window that just opened.\n"
                "Once you're logged in and can see the admin, come back here and press Enter... "
            )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url)

        wait_for_confirm()

        context.storage_state(path=str(state_path))
        browser.close()

    log(f"Done, login state for {platform} saved to {state_path}")

    from offer_cache import PLATFORM_TO_SOURCE, sync_platform
    if platform in PLATFORM_TO_SOURCE:
        try:
            sync_platform(platform, log=log)
        except Exception as exc:
            log(f"Offer cache refresh failed ({exc}) - login itself is still fine, try again from the Tracking links page.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, choices=sorted(PLATFORMS.keys()))
    args = parser.parse_args()
    login_and_save(args.platform)


if __name__ == "__main__":
    main()
