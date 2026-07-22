"""
clients_db_loader.py — Chargeur de la base clients JSON
=========================================================
Convertit un dict JSON en ClientProfile compatible avec ClientScoringAgent.

ARCHITECTURE :
  En production : ClientProfile viendrait de PostgreSQL
  Pour la démo  : ClientProfile vient de clients_db.json
  L'INTERFACE EST IDENTIQUE — seul le backend change.

USAGE :
  loader = ClientDBLoader()
  profile = loader.get_profile("100428660")
  if profile:
      score = scoring_agent.score_client(profile)
"""
import json
from pathlib import Path
from typing import Optional
from datetime import datetime

from client_scoring_agent import ClientProfile


class ClientDBLoader:
    """Charge les profils clients depuis un fichier JSON local.
    
    En production, remplacer par un connecteur PostgreSQL avec la même API.
    """
    
    def __init__(self, json_path: str = "clients_db.json", verbose: bool = False):
        self.json_path = Path(json_path)
        self.verbose = verbose
        self._cache: dict = {}
        self._load()
    
    def _load(self) -> None:
        """Charge le fichier JSON en mémoire."""
        if not self.json_path.exists():
            print(f"  ⚠️ {self.json_path} introuvable — base vide")
            self._cache = {}
            return
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._cache = data.get("clients", {})
        meta = data.get("_metadata", {})
        if self.verbose:
            print(f"  ✓ Base clients chargée : {len(self._cache)} profils "
                  f"(version {meta.get('version', '?')})")
    
    def get_profile(self, client_id: str,
                    fallback_default: bool = True) -> Optional[ClientProfile]:
        """Récupère un profil client comme ClientProfile.
        
        Args:
            client_id : identifiant du compte
            fallback_default : si True et client absent, retourne DEFAULT
        
        Returns:
            ClientProfile si trouvé, sinon DEFAULT (ou None)
        """
        if not client_id:
            return self._get_default() if fallback_default else None
        
        raw = self._cache.get(str(client_id))
        if raw is None:
            return self._get_default() if fallback_default else None
        
        return self._to_profile(raw)
    
    def _get_default(self) -> Optional[ClientProfile]:
        """Retourne le profil DEFAULT (client jamais vu)."""
        raw = self._cache.get("DEFAULT")
        if raw is None:
            return None
        return self._to_profile(raw)
    
    def _to_profile(self, raw: dict) -> ClientProfile:
        """Convertit un dict JSON en ClientProfile (champs alignés avec
        la dataclass de client_scoring_agent.py).
        """
        # Calcul de l'âge du compte (proxy KYC ancienneté)
        account_age_days = None
        if raw.get("account_open_date"):
            try:
                opened = datetime.fromisoformat(raw["account_open_date"])
                account_age_days = (datetime.now() - opened).days
            except (ValueError, TypeError):
                pass
        
        # account_type : explicite s'il est fourni (page « Mon profil »),
        # sinon dérivé du business_type. Le RiskFactorScorer attend
        # INDIVIDUAL / BUSINESS / CORRESPONDENT / TRUST.
        business = (raw.get("business_type") or "").lower()
        explicit_at = (raw.get("account_type") or "").upper()
        if explicit_at in ("INDIVIDUAL", "BUSINESS", "CORRESPONDENT", "TRUST"):
            account_type = explicit_at
        elif business in ("salary_account", "retail"):
            account_type = "INDIVIDUAL"
        elif business in ("import_export", "consulting", "manufacturing",
                          "wholesale", "real_estate", "money_exchange",
                          "precious_metals", "art_dealer", "crypto", "casino"):
            account_type = "BUSINESS"
        else:
            account_type = "INDIVIDUAL"
        
        # Tax residences : déduit du pays de résidence et FATCA
        tax_residences = []
        country = raw.get("country_of_residence", "")
        if country and country != "Unknown":
            # Mapping pays → code 2 lettres
            country_codes = {
                "Tunisia": "TN", "France": "FR", "Germany": "DE",
                "Italy": "IT", "Spain": "ES", "Morocco": "MA",
                "Algeria": "DZ", "Congo": "CG", "United States": "US",
                "Iran": "IR", "Unknown": "",
            }
            code = country_codes.get(country, "")
            if code:
                tax_residences.append(code)
        if raw.get("fatca_status") == "US_PERSON":
            if "US" not in tax_residences:
                tax_residences.append("US")
        # Résidences fiscales étrangères saisies explicitement (CRS)
        for code in (raw.get("tax_residences") or []):
            c = str(code).upper()[:2]
            if c and c not in tax_residences:
                tax_residences.append(c)
        
        # us_indicia (déduit du FATCA status)
        us_indicia = []
        if raw.get("fatca_status") == "US_PERSON":
            us_indicia.append("us_citizen")
        
        return ClientProfile(
            client_id=raw.get("client_id", "UNKNOWN"),
            full_name=raw.get("full_name", ""),
            nationality=raw.get("nationality", ""),
            country_residence=raw.get("country_of_residence", ""),
            tax_residences=tax_residences,
            birth_country=raw.get("country_of_residence", ""),  # approximation
            # underscore → espace : le RiskFactorScorer matche des mots-clés
            # avec espaces ("import export", "real estate"…) ; sans cette
            # normalisation, les codes "import_export" ne déclencheraient jamais
            # le facteur high_risk_profession.
            profession=(raw.get("business_type", "") or "").replace("_", " "),
            is_pep=raw.get("is_pep", False),
            pep_type=raw.get("pep_type") or "",
            account_type=account_type,
            account_age_days=account_age_days,
            previous_strs=raw.get("prior_strs", 0),
            kyc_complete=raw.get("kyc_complete", False),
            beneficial_owner_identified=raw.get("beneficial_owner_identified", True),
            us_indicia=us_indicia,
            has_w9=False,
            has_w8=False,
        )
    
    def exists(self, client_id: str) -> bool:
        """True si le client existe dans la base (hors DEFAULT)."""
        return (client_id and str(client_id) in self._cache 
                and str(client_id) != "DEFAULT")
    
    def get_raw(self, client_id: str) -> Optional[dict]:
        """Retourne le dict brut (pour le SAR Generator si besoin)."""
        return self._cache.get(str(client_id))
    
    def add_new_client(self, client_id: str, profile_dict: dict,
                       save: bool = True) -> None:
        """Ajoute un nouveau client à la base.
        
        Pour quand un client inconnu effectue sa première transaction
        et remplit le formulaire (cas que tu as mentionné).
        """
        self._cache[str(client_id)] = profile_dict
        if save:
            data = {
                "_metadata": {
                    "description": "Base clients hybride mise à jour",
                    "version": "2.1",
                    "last_update": datetime.now().isoformat(),
                    "total_clients": len(self._cache),
                },
                "clients": self._cache,
            }
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════
#  TEST STANDALONE
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n  CLIENT DB LOADER — test\n" + "=" * 64)
    
    loader = ClientDBLoader(verbose=True)
    
    test_ids = [
        "100428660",  # OVERRIDE Congo
        "100428780",  # OVERRIDE PEP
        "801176C70",  # Synthetic enrichment
        "802559360",  # Absent → DEFAULT
        "INEXISTANT", # Absent → DEFAULT
    ]
    
    for cid in test_ids:
        print(f"\n  Test : {cid}")
        if loader.exists(cid):
            print(f"    ✓ Trouvé dans la base")
        else:
            print(f"    ⚠️ ABSENT — fallback DEFAULT")
        
        profile = loader.get_profile(cid)
        if profile:
            print(f"    Profile :")
            print(f"      client_id            = {profile.client_id}")
            print(f"      nationality          = {profile.nationality}")
            print(f"      country_residence    = {profile.country_residence}")
            print(f"      tax_residences       = {profile.tax_residences}")
            print(f"      is_pep               = {profile.is_pep}")
            print(f"      pep_type             = {profile.pep_type}")
            print(f"      profession           = {profile.profession}")
            print(f"      account_type         = {profile.account_type}")
            print(f"      account_age_days     = {profile.account_age_days}")
            print(f"      previous_strs        = {profile.previous_strs}")
            print(f"      kyc_complete         = {profile.kyc_complete}")
            print(f"      us_indicia           = {profile.us_indicia}")