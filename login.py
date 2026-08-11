"""
Jednorázové přihlášení do administrace.

Otevře skutečné (ne headless) okno prohlížeče na přihlašovací stránku.
Přihlas se tam RUČNĚ (nikam se tvoje heslo neukládá ani neposílá jinam).
Po přihlášení se v terminálu potvrdí Enterem a uloží se přihlašovací
stav (cookies/session) do storage_state.json, který pak používá lp_tool.py.

Spusť znovu, kdykoliv session vyprší (typicky po delší době nečinnosti).

Použití:
    python login.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ADMIN_LOGIN_URL = "https://affiliates.daoofleads.com/en/admin/sign/in"
STORAGE_STATE_FILE = Path(__file__).parent / "storage_state.json"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(ADMIN_LOGIN_URL)

        input(
            "\nPřihlas se v otevřeném okně prohlížeče do administrace.\n"
            "Až budeš přihlášený/á a vidíš administraci, vrať se sem a stiskni Enter... "
        )

        context.storage_state(path=str(STORAGE_STATE_FILE))
        browser.close()

    print(f"Hotovo, přihlašovací stav uložen do {STORAGE_STATE_FILE}")


if __name__ == "__main__":
    main()
