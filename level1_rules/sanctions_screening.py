"""
level1_rules/sanctions_screening.py
====================================
Screening des sanctions (ONU) et PEPs en O(1).

AMELIORATIONS v2 :
  1. Resolution des alias  — remonte au record principal si alias_of present
  2. Validation des entrees — rejette noms vides / trop courts
  3. Audit logging          — trace chaque hit dans output/screening_audit.log
  4. Fuzzy matching         — fallback rapidfuzz si match exact echoue (UN only)

SOURCES :
  - ONU : data/un_sanctions.json (converti depuis XML)
  - PEPs: data/peps.json (OpenSanctions)

USAGE :
  from level1_rules.sanctions_screening import SanctionsScreener

  screener = SanctionsScreener()
  result = screener.check("Ahmed Al-Qahtani", entity_type="individual")

  if result.is_sanctioned:
      print(f"BLOCK: {result.reason}")
"""

import json
import logging
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Fix encodage Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Seuil de confiance pour le fuzzy matching (0-100)
FUZZY_THRESHOLD = 90
# Longueur minimale d'un nom valide
MIN_NAME_LENGTH = 4
# Nombre minimal de lettres dans un nom (evite "M.", "Dr", etc.)
MIN_ALPHA_CHARS = 3


@dataclass
class SanctionResult:
    """Resultat d'un screening de sanctions."""
    is_sanctioned: bool
    match_type:    str    # "UN_INDIVIDUAL", "UN_ENTITY", "PEP", "NONE"
    matched_name:  str    # Nom trouve dans la liste
    reason:        str    # Raison de la sanction
    confidence:    float  # 0.0-1.0 (1.0 = exact, <1.0 = fuzzy)
    source:        str    # "UN Consolidated List" / "OpenSanctions"
    matched_via:   str = ""  # "exact", "alias", "fuzzy" ou ""


def _setup_audit_logger(log_path: Path) -> logging.Logger:
    """Configure le logger d'audit (append, format CSV-like)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sanctions_audit")
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        # En-tete si fichier vide
        if log_path.stat().st_size == 0 if log_path.exists() else True:
            logger.info("timestamp|input_name|matched_name|match_type|confidence|source|via")
    return logger


class SanctionsScreener:
    """
    Screening sanctions/PEPs en O(1) avec fallback fuzzy (UN uniquement).

    Charge les listes en memoire au demarrage, puis lookup instantane.
    """

    def __init__(self, data_dir: Path = None, audit_log: Path = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent / "data"
        if audit_log is None:
            audit_log = Path(__file__).parent.parent / "output" / "screening_audit.log"

        self.data_dir        = data_dir
        self.un_individuals  = {}
        self.un_entities     = {}
        self.peps            = {}
        self._audit          = _setup_audit_logger(audit_log)
        self._fuzzy_enabled  = False

        self._load_lists()
        self._init_fuzzy()

    # ─────────────────────────────────────────────────────────────────────────
    #  CHARGEMENT DES LISTES
    # ─────────────────────────────────────────────────────────────────────────

    def _load_lists(self):
        """Charge toutes les listes en memoire."""
        # UN sanctions
        un_path = self.data_dir / "un_sanctions.json"
        if un_path.exists():
            with open(un_path, encoding='utf-8') as f:
                un_data = json.load(f)
            self.un_individuals = un_data.get("individuals", {})
            self.un_entities    = un_data.get("entities", {})
            print(f"  OK UN : {len(self.un_individuals):,} individus, "
                  f"{len(self.un_entities):,} entites")
        else:
            print(f"  WARN UN sanctions non trouvees : {un_path}")

        # PEPs (OpenSanctions)
        peps_path = self.data_dir / "peps.json"
        if peps_path.exists():
            with open(peps_path, encoding='utf-8') as f:
                peps_data = json.load(f)
            for entity in peps_data:
                if entity.get("schema") == "Person":
                    names = entity.get("properties", {}).get("name", [])
                    for name in names:
                        key = self._normalize(name)
                        self.peps[key] = {
                            "name":      name,
                            "id":        entity.get("id", ""),
                            "countries": entity.get("properties", {}).get("country", []),
                        }
            print(f"  OK PEPs: {len(self.peps):,} entrees")
        else:
            print(f"  WARN PEPs non trouves : {peps_path}")

    def _init_fuzzy(self):
        """Active rapidfuzz si disponible (fallback silencieux sinon)."""
        try:
            from rapidfuzz import process as rfprocess, fuzz as rffuzz  # noqa: F401
            self._fuzzy_enabled = True
            print("  OK Fuzzy matching : rapidfuzz disponible")
        except ImportError:
            print("  WARN rapidfuzz non installe — fuzzy matching desactive")
            print("       pip install rapidfuzz  pour l'activer")

    # ─────────────────────────────────────────────────────────────────────────
    #  POINT D'ENTREE PRINCIPAL
    # ─────────────────────────────────────────────────────────────────────────

    def check(self, name: str, entity_type: str = "individual") -> SanctionResult:
        """
        Screening d'un nom contre toutes les listes.

        Args:
            name        : Nom a verifier (expediteur/beneficiaire)
            entity_type : "individual" ou "entity"

        Returns:
            SanctionResult avec is_sanctioned=True si match
        """
        # 1. Validation de l'entree
        valid, reason = self._validate_name(name)
        if not valid:
            return self._no_match()

        key = self._normalize(name)

        # 2. UN Individuals
        if entity_type == "individual":
            result = self._check_un_individual(name, key)
            if result.is_sanctioned:
                self._log_hit(name, result)
                return result

        # 3. UN Entities
        if entity_type == "entity":
            result = self._check_un_entity(name, key)
            if result.is_sanctioned:
                self._log_hit(name, result)
                return result

        # 4. PEPs (exact uniquement — liste trop grande pour fuzzy)
        if key in self.peps:
            pep = self.peps[key]
            countries = ", ".join(pep["countries"][:2])
            result = SanctionResult(
                is_sanctioned=True,
                match_type="PEP",
                matched_name=pep["name"],
                reason=f"Politically Exposed Person ({countries})",
                confidence=1.0,
                source="OpenSanctions",
                matched_via="exact",
            )
            self._log_hit(name, result)
            return result

        # 5. Fuzzy matching sur la liste UN (si exact a echoue)
        if self._fuzzy_enabled and entity_type == "individual":
            result = self._fuzzy_match_un(name)
            if result.is_sanctioned:
                self._log_hit(name, result)
                return result

        return self._no_match()

    def batch_check(self, names: list, entity_type: str = "individual") -> list:
        """Screening en batch."""
        return [self.check(n, entity_type) for n in names]

    # ─────────────────────────────────────────────────────────────────────────
    #  VALIDATION DES ENTREES
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_name(name: str) -> tuple:
        """
        Valide un nom avant screening.
        Retourne (True, "") si valide, (False, raison) sinon.
        """
        if not name or not name.strip():
            return False, "nom vide"

        name = name.strip()

        if len(name) < MIN_NAME_LENGTH:
            return False, f"nom trop court ({len(name)} chars, min={MIN_NAME_LENGTH})"

        alpha_count = sum(1 for c in name if c.isalpha())
        if alpha_count < MIN_ALPHA_CHARS:
            return False, f"trop peu de lettres ({alpha_count}, min={MIN_ALPHA_CHARS})"

        return True, ""

    # ─────────────────────────────────────────────────────────────────────────
    #  CHECKS INTERNES
    # ─────────────────────────────────────────────────────────────────────────

    def _check_un_individual(self, original: str, key: str) -> SanctionResult:
        """Verifie un individu dans la liste UN (exact + resolution alias)."""
        if key not in self.un_individuals:
            return self._no_match()

        ind, via = self._resolve_alias(self.un_individuals, key, "individuals")
        return SanctionResult(
            is_sanctioned=True,
            match_type="UN_INDIVIDUAL",
            matched_name=ind.get("full_name", ind.get("name", original)),
            reason=ind.get("reason", "UN Security Council sanctions"),
            confidence=1.0,
            source="UN Consolidated List",
            matched_via=via,
        )

    def _check_un_entity(self, original: str, key: str) -> SanctionResult:
        """Verifie une entite dans la liste UN (exact + resolution alias)."""
        if key not in self.un_entities:
            return self._no_match()

        ent, via = self._resolve_alias(self.un_entities, key, "entities")
        return SanctionResult(
            is_sanctioned=True,
            match_type="UN_ENTITY",
            matched_name=ent.get("name", original),
            reason=ent.get("reason", "UN Security Council sanctions"),
            confidence=1.0,
            source="UN Consolidated List",
            matched_via=via,
        )

    def _resolve_alias(self, index: dict, key: str, index_name: str) -> tuple:
        """
        Si l'entree trouvee est un alias (champ alias_of present),
        remonte au record principal pour recuperer les infos completes.

        Returns:
            (record_dict, match_via)  ou match_via = "exact" ou "alias"
        """
        record = index[key]
        if "alias_of" not in record:
            return record, "exact"

        # C'est un alias — chercher le record principal
        parent_key = self._normalize(record["alias_of"])
        if parent_key in index:
            return index[parent_key], "alias"

        # Record principal introuvable — retourner l'alias tel quel
        return record, "alias"

    def _fuzzy_match_un(self, name: str) -> SanctionResult:
        """
        Fallback fuzzy sur la liste UN individuals.
        Utilise rapidfuzz.process.extractOne (token_sort_ratio).
        Seuil : FUZZY_THRESHOLD (defaut 90).
        """
        from rapidfuzz import process as rfp, fuzz as rff

        # Construire un mapping key -> full_name pour les records principaux
        # (exclure les alias pour eviter les doublons dans le resultat)
        candidates = {
            k: v.get("full_name", k)
            for k, v in self.un_individuals.items()
            if "alias_of" not in v
        }

        input_key = self._normalize(name)
        match = rfp.extractOne(
            input_key,
            candidates.keys(),
            scorer=rff.token_sort_ratio,
            score_cutoff=FUZZY_THRESHOLD,
        )

        if not match:
            return self._no_match()

        matched_key, score, _ = match
        ind = self.un_individuals[matched_key]
        confidence = round(score / 100, 2)

        return SanctionResult(
            is_sanctioned=True,
            match_type="UN_INDIVIDUAL",
            matched_name=ind.get("full_name", matched_key),
            reason=ind.get("reason", "UN Security Council sanctions"),
            confidence=confidence,
            source="UN Consolidated List",
            matched_via=f"fuzzy({score:.0f}%)",
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  AUDIT LOGGING
    # ─────────────────────────────────────────────────────────────────────────

    def _log_hit(self, input_name: str, result: SanctionResult):
        """Enregistre un hit dans le fichier d'audit (format pipe-separated)."""
        ts = datetime.now().isoformat(timespec="seconds")
        self._audit.info(
            f"{ts}|{input_name}|{result.matched_name}|"
            f"{result.match_type}|{result.confidence:.2f}|"
            f"{result.source}|{result.matched_via}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    #  UTILITAIRES
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalise un nom pour lookup : majuscules, sans accents, underscores."""
        name = name.upper()
        name = ''.join(
            c for c in unicodedata.normalize('NFD', name)
            if unicodedata.category(c) != 'Mn'
        )
        name = name.replace(' ', '_').replace('-', '_')
        name = ''.join(c for c in name if c.isalnum() or c == '_')
        return name

    @staticmethod
    def _no_match() -> SanctionResult:
        return SanctionResult(
            is_sanctioned=False,
            match_type="NONE",
            matched_name="",
            reason="",
            confidence=0.0,
            source="",
            matched_via="",
        )


# =============================================================================
#  TEST RAPIDE
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SANCTIONS SCREENER v2 — Test")
    print("=" * 70 + "\n")

    screener = SanctionsScreener()
    print()

    test_cases = [
        # (nom, entity_type, description)
        ("Eric Badege",        "individual", "Match exact UN"),
        ("Eric Badage",        "individual", "Fuzzy match (faute de frappe)"),
        ("Al-Shabaab",         "entity",     "Entite UN sanctionnee"),
        ("Vladimir Putin",     "individual", "PEP attendu"),
        ("John Smith",         "individual", "Aucun match"),
        ("",                   "individual", "Nom vide — validation"),
        ("Mr",                 "individual", "Trop court — validation"),
        ("M.",                 "individual", "Pas assez de lettres — validation"),
    ]

    for name, etype, desc in test_cases:
        result = screener.check(name, etype)
        status = "BLOCK" if result.is_sanctioned else "PASS "
        print(f"  [{status}]  {desc}")
        print(f"           Input  : '{name}'")
        if result.is_sanctioned:
            print(f"           Match  : {result.matched_name}")
            print(f"           Via    : {result.matched_via}  |  Confiance : {result.confidence:.0%}")
            print(f"           Type   : {result.match_type}  |  Source : {result.source}")
            print(f"           Raison : {result.reason[:90]}")
        print()

    print("  Audit log : output/screening_audit.log")
    print("=" * 70 + "\n")
