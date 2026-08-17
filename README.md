# LP screenshot tool

Podle zadané niche zjistí platnou sadu LP čísel offeru, projde je jedno po
druhém v administraci `affiliates.daoofleads.com/.../landing`, doplní
chybějící řádky (Add), opraví ty se špatnou/starou doménou (Edit) a řádky
mimo platnou sadu niche pozastaví (status Paused, obsah beze změny). Ke
každému nově vytvořenému/opravenému řádku pořídí čistý screenshot LP stránky
(bez cookie lišty / age-gate overlaye) a nahraje ho jako náhled.

Běží samostatně, lokálně na tvém počítači - nemá nic společného s nasazenou
appkou na Vercelu (ta by headless prohlížeč se přihlášením spustit neuměla).

## Instalace

```bash
cd scripts/lp-tool
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -r requirements.txt
playwright install chromium
```

## Použití

**1. Jednou se přihlásit** (heslo se nikam neukládá, jen se otevře skutečné
okno prohlížeče, přihlásíš se v něm ručně a uloží se jen výsledná session):

```bash
python login.py
```

Vytvoří se `storage_state.json` (je v `.gitignore`, nikdy se nekomituje).
Spusť znovu, kdykoliv session vyprší.

**2. Zkušební běh na jedné LP** (jen screenshot + výpis plánu, nic se
nenahraje ani neukládá do administrace):

```bash
python lp_tool.py --offer-id 16689 --niche ADULT --only-lp 10 --dry-run
```

**3. Jedna konkrétní LP i s uploadem do administrace:**

```bash
python lp_tool.py --offer-id 16689 --niche ADULT --only-lp 10
```

**4. Celý offer** (doplní/opraví všechny LP z platné sady dané niche,
pozastaví ty mimo ni):

```bash
python lp_tool.py --offer-id 16689 --niche ADULT
```

### Volitelné parametry

| Parametr | Výchozí | Popis |
|---|---|---|
| `--niche-id` | podle `--niche` (config.py) | vynutit niche_id ručně - **povinné pro `--niche TRANS`**, dokud nebude potvrzeno v zadání |
| `--afid` | `2792` | affiliate ID použité v URL při screenshotování |
| `--width` / `--height` | `1250` / `825` | rozlišení screenshotu |
| `--domain` | (odvodí se z popisku offeru) | vynutit doménu manuálně, když se odvození nepovede |
| `--only-lp` | (celá platná sada) | zpracovat jen jedno konkrétní LP číslo, pro testování |
| `--dry-run` | vypnuto | jen screenshoty a výpis plánu, žádné zápisy do administrace |
| `--headless` | vypnuto | běžet bez viditelného okna prohlížeče |

## Jak se rozhoduje, co se s daným LP udělá

Pro každé LP číslo z platné sady dané niche (`NICHE_LP_NUMBERS` v `config.py`):

1. Řádek v gridu se dohledá podle čísla LP rozpoznaného z jeho **URL preview**
   (vzor `/lp/{N}/{cokoliv}/{niche_id}/`), ne podle ID řádku ani title.
2. Pokud řádek neexistuje → **Add** (nový screenshot, nový řádek, status `active`).
3. Pokud řádek existuje, ale jeho URL preview neodpovídá očekávané doméně a
   cestě → **Edit** (nový screenshot, přepsané title/URL/URL preview, status
   se nemění).
4. Pokud řádek existuje a už odpovídá → nic se nedělá.

Po zpracování platné sady se ještě jednou projdou všechny řádky gridu: každý,
jehož LP číslo **není** v platné sadě dané niche a ještě není `Paused`, se
přepne na `Paused` - jeho obsah (URL, screenshot, title) se nemění.

Řádky, u kterých se z URL preview nepodaří vytáhnout žádné LP číslo (starý/cizí
formát), se vypíšou zvlášť a nechají se bez zásahu - je potřeba je zkontrolovat
ručně.

## Jak se odvozuje doména

Doména se **vždy** bere z popisku (title) offeru v administraci, ne z
existující URL v gridu (ta může být zastaralá/špatně zadaná) a ne ze statické
mapy niche → domény ze zadání (ta slouží jen jako reference pro platná LP
čísla a niche_id). Popisek má tvar `"sexkontakt.com - GERMANY - ADULT - REV"`
- první segment před ` - ` je doména. Nejdřív se zkusí najít v
`data/offers.json` (lokální cache appky TL), a pokud offer tam není, dotáhne
se přímo z administrace.

## Formuláře v adminu

Add/Edit formuláře se ovládají přes jejich skutečné `name` atributy
(`inline_add[...]` / `inline_edit[...]`) tak, jak je zdokumentoval admin -
žádné hádání podle obsahu pole jako dřív. Nástroj nikdy nevolá mazání řádků
(`do=deleteLanding`) - to zůstává vždy ruční akce.

## Známá omezení / co dolaďovat

- **Trans niche_id není potvrzené** (chybí referenční příklad v zadání) -
  `--niche TRANS` bez `--niche-id` skončí chybou.
- Nástroj scrapuje admin grid podle textu hlaviček tabulky (`ID`, `Title`,
  `URL`, `URL preview`, `Status`). Pokud administrace v budoucnu změní
  strukturu tabulky, hlásí to skript jasnou chybou (ne tichým selháním) -
  stačí upravit `get_landing_rows()` v `lp_tool.py`.
- Potvrzení uložení se čeká jako text odpovídající `Successfully
  (added|edited|created|inserted)` (case-insensitive) - přesný text hlášky
  administrace nebyl k dispozici pro Add, jen pro Edit ("Successfully
  edited"); pokud administrace hlásí něco jiného, selže to hlasitou chybou
  a je potřeba upravit `SUCCESS_TEXT_RE` v `lp_tool.py`.
- Detekce cookie lišty a age gate je založená na seznamu textů tlačítek v
  `config.py` (`DISMISS_BUTTON_TEXTS`), s fallbackem na klíčová slova
  (`DISMISS_FALLBACK_KEYWORDS`) podle zadání. Nová doména s jiným textem
  tlačítka mimo oba seznamy je potřeba doplnit.
