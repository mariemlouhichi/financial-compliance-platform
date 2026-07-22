"""
level1_rules/risk_country_checker.py
=====================================
Verification pays a risque selon FATF black/grey lists.

SOURCE : data/fatf_lists.json
  Format reel :
    { "black_list": ["KP","IR","MM"],
      "grey_list":  ["DZ","AO",...],
      "black_names": {"KP": "Democratic People's Republic of Korea", ...},
      "grey_names":  {"DZ": "Algeria", ...},
      "last_updated": "2026-02-13" }

LOGIQUE :
  Black list (haut risque) -> BLOCK immediat
  Grey list  (surveillance) -> EDD obligatoire

USAGE :
  from level1_rules.risk_country_checker import RiskCountryChecker

  checker = RiskCountryChecker()
  result  = checker.check("Iran")        # par nom
  result  = checker.check("IR")          # par code ISO
  result  = checker.check("North Korea") # par alias

  if result.action == "BLOCK":
      print(result.reason)
"""

import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Fix encodage Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


@dataclass
class CountryRiskResult:
    """Resultat d'une verification pays."""
    country:      str
    iso_code:     str   # Code ISO 2 lettres si connu
    is_risky:     bool
    risk_level:   str   # "BLACK", "GREY", "NONE"
    action:       str   # "BLOCK", "EDD", "PASS"
    reason:       str
    last_updated: str


class RiskCountryChecker:
    """
    Verification pays a risque FATF en O(1).
    Accepte codes ISO (IR, KP) et noms complets/alias (Iran, North Korea).
    """

    # Alias communs -> code ISO
    # Couvre les abreviations frequentes dans les donnees bancaires
    _ALIASES: dict = {
        "NORTH KOREA":                              "KP",
        "DPRK":                                     "KP",
        "KOREA NORTH":                              "KP",
        "DEMOCRATIC PEOPLE S REPUBLIC OF KOREA":    "KP",
        "DEMOCRATIC PEOPLES REPUBLIC OF KOREA":     "KP",
        "IRAN":                                     "IR",
        "IRAN ISLAMIC REPUBLIC":                    "IR",
        "ISLAMIC REPUBLIC OF IRAN":                 "IR",
        "MYANMAR":                                  "MM",
        "BURMA":                                    "MM",
        "SYRIA":                                    "SY",
        "SYRIAN ARAB REPUBLIC":                     "SY",
        "LAOS":                                     "LA",
        "LAO PDR":                                  "LA",
        "LAO PEOPLE S DEMOCRATIC REPUBLIC":         "LA",
        "DEMOCRATIC REPUBLIC OF CONGO":             "CD",
        "DEMOCRATIC REPUBLIC OF THE CONGO":         "CD",
        "DRC":                                      "CD",
        "COTE D IVOIRE":                            "CI",
        "IVORY COAST":                              "CI",
        "VIRGIN ISLANDS UK":                        "VG",
        "BRITISH VIRGIN ISLANDS":                   "VG",
        "BVI":                                      "VG",
        "VENEZUELA":                                "VE",
        "BOLIVARIAN REPUBLIC OF VENEZUELA":         "VE",
        "VIETNAM":                                  "VN",
        "VIET NAM":                                 "VN",
        "PAPUA NEW GUINEA":                         "PG",
        "PNG":                                      "PG",
        "SOUTH SUDAN":                              "SS",
        "KUWAIT":                                   "KW",
        "LEBANON":                                  "LB",
        "MONACO":                                   "MC",
        "NAMIBIA":                                  "NA",
        "NEPAL":                                    "NP",
        "KENYA":                                    "KE",
        "HAITI":                                    "HT",
        "CAMEROON":                                 "CM",
        "ANGOLA":                                   "AO",
        "ALGERIA":                                  "DZ",
        "BOLIVIA":                                  "BO",
        "BULGARIA":                                 "BG",
        "YEMEN":                                    "YE",
    }

    # Raisons FATF par defaut (si non specifiees dans le JSON)
    _DEFAULT_REASONS: dict = {
        "BLACK": "Strategic deficiencies in AML/CFT — subject to FATF call for action",
        "GREY":  "Increased monitoring — committed to resolving AML/CFT deficiencies",
    }

    def __init__(self, data_dir: Path = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"

        self.data_dir     = data_dir
        self.last_updated = "Unknown"

        # Index double : ISO code -> (full_name, reason, level)
        self._black: dict = {}
        self._grey:  dict = {}

        self._load_fatf_lists()

    # ─────────────────────────────────────────────────────────────────────────
    #  CHARGEMENT
    # ─────────────────────────────────────────────────────────────────────────

    def _load_fatf_lists(self):
        """Charge les listes FATF depuis JSON (codes ISO + noms)."""
        fatf_path = self.data_dir / "fatf_lists.json"

        if not fatf_path.exists():
            print(f"  WARN FATF lists non trouvees : {fatf_path}")
            print(f"       Utilisation des listes par defaut (feb 2026)")
            self._load_defaults()
            return

        with open(fatf_path, encoding='utf-8') as f:
            data = json.load(f)

        black_names = data.get("black_names", {})
        grey_names  = data.get("grey_names",  {})
        self.last_updated = data.get("last_updated", "Unknown")

        for iso in data.get("black_list", []):
            iso = iso.upper()
            full_name = black_names.get(iso, iso)
            self._black[iso] = {
                "full_name": full_name,
                "reason":    self._DEFAULT_REASONS["BLACK"],
            }

        for iso in data.get("grey_list", []):
            iso = iso.upper()
            full_name = grey_names.get(iso, iso)
            self._grey[iso] = {
                "full_name": full_name,
                "reason":    self._DEFAULT_REASONS["GREY"],
            }

        print(f"  OK FATF : {len(self._black)} black, {len(self._grey)} grey "
              f"(updated: {self.last_updated})")

    def _load_defaults(self):
        """Fallback si fatf_lists.json absent."""
        for iso, name in [("KP", "North Korea"), ("IR", "Iran"), ("MM", "Myanmar")]:
            self._black[iso] = {"full_name": name, "reason": self._DEFAULT_REASONS["BLACK"]}
        for iso, name in [
            ("SY","Syria"),("YE","Yemen"),("LB","Lebanon"),("SS","South Sudan"),
            ("HT","Haiti"),("VE","Venezuela"),("VN","Vietnam"),
        ]:
            self._grey[iso] = {"full_name": name, "reason": self._DEFAULT_REASONS["GREY"]}
        self.last_updated = "2026-02 (defaut)"
        print(f"  OK FATF defaut : {len(self._black)} black, {len(self._grey)} grey")

    # ─────────────────────────────────────────────────────────────────────────
    #  POINT D'ENTREE
    # ─────────────────────────────────────────────────────────────────────────

    def check(self, country: str) -> CountryRiskResult:
        """
        Verifie si un pays est a risque.

        Accepte :
          - Code ISO 2 lettres  : "IR", "KP"
          - Nom complet         : "Iran", "North Korea"
          - Alias courant       : "DPRK", "Burma", "Ivory Coast"

        Returns:
            CountryRiskResult avec action BLOCK / EDD / PASS
        """
        if not country or not country.strip():
            return self._no_risk("")

        iso = self._resolve_iso(country)

        if iso in self._black:
            rec = self._black[iso]
            return CountryRiskResult(
                country=country,
                iso_code=iso,
                is_risky=True,
                risk_level="BLACK",
                action="BLOCK",
                reason=rec["reason"],
                last_updated=self.last_updated,
            )

        if iso in self._grey:
            rec = self._grey[iso]
            return CountryRiskResult(
                country=country,
                iso_code=iso,
                is_risky=True,
                risk_level="GREY",
                action="EDD",
                reason=rec["reason"],
                last_updated=self.last_updated,
            )

        return self._no_risk(country)

    def get_all_high_risk(self) -> dict:
        """Retourne tous les pays a risque indexes par code ISO."""
        return {
            "black": {iso: v["full_name"] for iso, v in self._black.items()},
            "grey":  {iso: v["full_name"] for iso, v in self._grey.items()},
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  RESOLUTION ISO
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_iso(self, country: str) -> str:
        """
        Convertit n'importe quelle forme de pays en code ISO 2 lettres.

        Ordre de resolution :
          1. Code ISO direct (2 chars majuscules)
          2. Table d'alias internes (_ALIASES)
          3. Correspondance partielle sur le nom complet dans les listes FATF
        """
        raw = country.strip()

        # 1. Deja un code ISO valide
        if len(raw) == 2 and raw.isalpha():
            return raw.upper()

        # 2. Normaliser pour lookup dans _ALIASES
        norm = self._normalize_text(raw)
        if norm in self._ALIASES:
            return self._ALIASES[norm]

        # 3. Chercher par nom complet dans les listes chargees
        for iso, rec in {**self._black, **self._grey}.items():
            if self._normalize_text(rec["full_name"]) == norm:
                return iso

        # 4. Correspondance partielle (ex: "Congo" -> CD)
        for iso, rec in {**self._black, **self._grey}.items():
            if norm in self._normalize_text(rec["full_name"]):
                return iso

        # Non resolu — retourner normalise pour eviter un faux PASS
        return norm

    # ─────────────────────────────────────────────────────────────────────────
    #  UTILITAIRES
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Majuscules, sans accents, sans ponctuation superflue."""
        text = text.upper().strip()
        text = ''.join(
            c for c in unicodedata.normalize('NFD', text)
            if unicodedata.category(c) != 'Mn'
        )
        text = text.replace('-', ' ').replace("'", ' ').replace(',', '')
        text = ' '.join(text.split())
        return text

    def _no_risk(self, country: str) -> CountryRiskResult:
        return CountryRiskResult(
            country=country,
            iso_code="",
            is_risky=False,
            risk_level="NONE",
            action="PASS",
            reason="",
            last_updated=self.last_updated,
        )


# =============================================================================
#  TEST RAPIDE
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  RISK COUNTRY CHECKER — Test")
    print("=" * 70 + "\n")

    checker = RiskCountryChecker()

    # Afficher toutes les listes chargees
    all_risk = checker.get_all_high_risk()
    print(f"\n  BLACK LIST ({len(all_risk['black'])} pays) :")
    for iso, name in all_risk["black"].items():
        print(f"    {iso}  {name}")
    print(f"\n  GREY LIST ({len(all_risk['grey'])} pays) :")
    for iso, name in all_risk["grey"].items():
        print(f"    {iso}  {name}")
    print()

    test_cases = [
        # (input, description attendue)
        ("IR",                     "Code ISO direct -> BLOCK (Iran)"),
        ("Iran",                   "Nom complet     -> BLOCK"),
        ("North Korea",            "Alias commun    -> BLOCK (KP)"),
        ("DPRK",                   "Sigle           -> BLOCK (KP)"),
        ("Myanmar",                "Nom complet     -> BLOCK"),
        ("Burma",                  "Ancien nom      -> BLOCK (MM)"),
        ("SY",                     "Code ISO direct -> EDD (Syria)"),
        ("Syria",                  "Nom complet     -> EDD"),
        ("Lao PDR",                "Alias           -> EDD (LA)"),
        ("Democratic Republic of the Congo", "Nom long -> EDD (CD)"),
        ("Ivory Coast",            "Alias anglais   -> EDD (CI)"),
        ("France",                 "Pays safe       -> PASS"),
        ("Tunisia",                "Pays safe       -> PASS"),
        ("",                       "Vide            -> PASS"),
    ]

    print("  " + "-" * 66)
    for country, desc in test_cases:
        result = checker.check(country)
        tag = {"BLOCK": "BLOCK", "EDD": "EDD  ", "PASS": "PASS "}.get(result.action, result.action)
        iso_str = f"[{result.iso_code}]" if result.iso_code else "    "
        print(f"  [{tag}] {iso_str}  {country or '(vide)':<35} {desc}")

    print("\n" + "=" * 70 + "\n")
