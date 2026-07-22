"""
dynamic_checklist.py — v5 (Optimisé)
=====================================
Checklist réglementaire DYNAMIQUE — 23 recommandations FATF pertinentes.

ARCHITECTURE CORRIGÉE :
  Checklist  → définit LES OBLIGATIONS (source : fatf_recommendations_2025.pdf)
  Retrieval  → trouve le contenu compliance (source : TOUS les documents Weaviate)
  Ces deux rôles sont distincts et complémentaires.

SÉLECTION DES RECOMMANDATIONS :
  Sur 40 recommandations FATF :
    16 HAUTEMENT PERTINENTES (directement actionnables au niveau transaction)
     7 MOYENNEMENT PERTINENTES (contextuelles, indirectes)
    17 NON PERTINENTES (institutionnelles, coopération internationale)
  → On retient les 23 pertinentes.

STRATÉGIE BUILD :
  Recherche par TITRE OFFICIEL dans fatf_recommendations_2025.pdf uniquement.
  Pourquoi ? Les titres exacts matchent les chunks du document FATF.
  Les autres docs (BCT, EBA, Wolfsberg) sont déjà dans Weaviate
  et seront trouvés par le Retrieval Agent — pas besoin de les dupliquer ici.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("dynamic_checklist")


# ═══════════════════════════════════════════════════════════════════════
#  23 RECOMMANDATIONS FATF PERTINENTES
#  (sur 40 totales — filtrées pour l'analyse transactionnelle AML)
#
#  Format : {numero: (titre_officiel_exact, [keywords_couverture], pertinence)}
#
#  Titres exacts issus de : FATF Recommendations 2023
# ═══════════════════════════════════════════════════════════════════════

FATF_CATALOGUE = {

    # ── 16 HAUTEMENT PERTINENTES ──────────────────────────────────────
    # Directement actionnables au niveau d'une transaction financière

    6:  ("Targeted financial sanctions related to terrorism and terrorist financing",
         ["targeted financial sanctions", "asset freeze", "UN designation",
          "terrorism financing sanctions", "blocking", "UNSCR 1267", "UNSCR 1373",
          "sanctions list", "designated person"],
         "HIGH"),

    7:  ("Targeted financial sanctions related to proliferation",
         ["proliferation financing", "WMD", "weapons of mass destruction",
          "proliferation sanctions", "UNSCR 1718", "UNSCR 2231",
          "nuclear weapons", "ballistic missiles"],
         "HIGH"),

    10: ("Customer due diligence",
         ["customer due diligence", "CDD", "identification", "verification",
          "beneficial owner", "beneficial ownership", "KYC", "identity",
          "ongoing due diligence", "customer identification program"],
         "HIGH"),

    11: ("Record keeping",
         ["record keeping", "records", "documentation", "retention",
          "five years", "data retention", "archive", "transaction records",
          "customer files"],
         "HIGH"),

    12: ("Politically exposed persons",
         ["politically exposed", "PEP", "senior official",
          "public function", "prominent function", "senior management approval",
          "source of wealth", "enhanced measures PEP"],
         "HIGH"),

    13: ("Correspondent banking",
         ["correspondent banking", "respondent institution",
          "payable-through", "correspondent bank", "nostro account",
          "correspondent relationship", "due diligence respondent"],
         "HIGH"),

    14: ("Money or value transfer services",
         ["money value transfer", "MVTS", "money transfer service",
          "remittance", "money order", "transfer operator",
          "hawala", "money service business", "MSB"],
         "HIGH"),

    16: ("Wire transfers",
         ["wire transfer", "travel rule", "originator information",
          "cross-border transfer", "beneficiary information",
          "international transfer", "ordering institution", "intermediary bank"],
         "HIGH"),

    19: ("Higher-risk countries",
         ["high-risk country", "higher-risk", "blacklist", "grey list",
          "countermeasures", "enhanced measures", "FATF list",
          "increased monitoring", "strategic deficiencies"],
         "HIGH"),

    20: ("Reporting of suspicious transactions",
         ["suspicious transaction", "STR", "SAR", "suspicious activity",
          "reporting obligation", "CTAF", "declaration", "filing",
          "FIU report", "suspicious activity report"],
         "HIGH"),

    22: ("Designated non-financial businesses and professions - Customer due diligence",
         ["DNFBP", "designated non-financial", "real estate agent",
          "lawyer", "accountant", "notary", "trust service",
          "casino", "dealer precious metals"],
         "HIGH"),

    23: ("Designated non-financial businesses and professions - Other measures",
         ["DNFBP other measures", "designated non-financial business",
          "STR DNFBP", "suspicious reporting non-financial",
          "professional obligations"],
         "HIGH"),

    24: ("Transparency and beneficial ownership of legal persons",
         ["beneficial ownership legal person", "company ownership",
          "shareholder register", "legal entity transparency",
          "corporate ownership", "shell company", "nominee"],
         "HIGH"),

    25: ("Transparency and beneficial ownership of legal arrangements",
         ["beneficial ownership trust", "legal arrangement",
          "trust transparency", "fiduciary", "nominee arrangement",
          "express trust", "trustee"],
         "HIGH"),

    29: ("Financial intelligence units",
         ["financial intelligence unit", "FIU", "CTAF", "financial analysis",
          "intelligence unit", "suspicious report analysis",
          "financial analysis commission"],
         "HIGH"),

    32: ("Cash couriers",
         ["cash courier", "cash declaration", "border declaration",
          "cross-border cash", "physical currency transport",
          "bearer negotiable instruments", "cash smuggling"],
         "HIGH"),

    # ── 7 MOYENNEMENT PERTINENTES ─────────────────────────────────────
    # Indirectement pertinentes ou contextuelles

    1:  ("Assessing risks and applying a risk-based approach",
         ["risk assessment", "risk-based approach", "national risk", "RBA",
          "inherent risk", "residual risk", "risk appetite"],
         "MEDIUM"),

    15: ("New technologies",
         ["new technologies", "virtual assets", "cryptocurrency", "fintech",
          "digital asset", "VASP", "blockchain", "crypto exchange"],
         "MEDIUM"),

    17: ("Reliance on third parties",
         ["third party reliance", "outsourcing", "introduced business",
          "reliance", "third party CDD", "agent reliance", "delegating CDD"],
         "MEDIUM"),

    18: ("Internal controls and foreign branches and subsidiaries",
         ["internal controls", "compliance programme", "AML programme",
          "foreign branches", "subsidiaries", "group policy",
          "internal audit", "compliance officer"],
         "MEDIUM"),

    21: ("Tipping-off and confidentiality",
         ["tipping-off", "confidentiality", "disclosure prohibition",
          "secrecy", "not disclose", "safe harbour", "good faith disclosure"],
         "MEDIUM"),

    27: ("Powers of supervisors",
         ["supervisory powers", "inspection", "on-site examination",
          "off-site monitoring", "sanction supervisory", "supervisor enforcement"],
         "MEDIUM"),

    35: ("Sanctions",
         ["administrative sanctions", "criminal sanctions", "penalties",
          "dissuasive sanctions", "proportionate sanctions",
          "effective sanctions", "AML penalties"],
         "MEDIUM"),
}


# ═══════════════════════════════════════════════════════════════════════
#  MAPPING TX_TYPE → RECOMMANDATIONS
#
#  Principe : inclure seulement les recommandations DIRECTEMENT applicables
#  à ce type de transaction selon la réglementation tunisienne (BCT/CTAF).
#
#  SWIFT     = virement international → obligations maximales
#  LOCAL     = virement local → obligations basiques
#  DEPOT     = dépôt espèces → cash spécifique
#  MANDAT    = mandat postal/WU → MVTS spécifique
# ═══════════════════════════════════════════════════════════════════════

TX_TYPE_RECS = {

    "SWIFT": [6, 7, 10, 11, 12, 13, 16, 19, 20, 24],
    # R6  : sanctions terrorism (sanctions_screening.py Level 1)
    # R7  : sanctions proliferation
    # R10 : CDD complet (BCT 2018-09 Art. 22-24)
    # R11 : record keeping 5 ans
    # R12 : PEPs (vigilance renforcée)
    # R13 : correspondent banking (banque intermédiaire CITIUS33)
    # R16 : Travel Rule (informations expéditeur/bénéficiaire)
    # R19 : pays blacklist/greylist (risk_country_checker.py Level 1)
    # R20 : STR > 10k USD (threshold_rules.py Level 1)
    # R24 : beneficial ownership (entités opaques, shell companies)

    "VIREMENT_LOCAL": [10, 11, 20],
    # R10 : CDD identification client (BCT 2006-19 Art. 3)
    # R11 : record keeping
    # R20 : STR > 10k TND (BCT 2017-08 Art. 43)

    "DEPOT_ESPECES": [10, 11, 20, 32],
    # R10 : CDD dépôt espèces (BCT 2006-19 Art. 7-8)
    # R11 : enregistrement guichetier
    # R20 : STR > 5k TND + smurfing
    # R32 : cash couriers (espèces transfrontalières)

    "MANDAT": [10, 11, 14, 16, 19, 20],
    # R10 : CDD > 1k TND (Décret-Loi 2021-25 Art. 37)
    # R11 : archivage opérateur MVTS
    # R14 : obligations MVTS (Western Union, MoneyGram)
    # R16 : Travel Rule (mandats internationaux)
    # R19 : pays à risque (mandats vers blacklist/greylist)
    # R20 : STR > 3k TND + suspicion (Décret-Loi 2021-25 Art. 37)
}


@dataclass
class FATFRecommendation:
    rec_id:          str
    number:          int
    title:           str
    description:     str
    tx_types:        list
    keywords:        list
    relevance:       str  = "HIGH"   # HIGH | MEDIUM
    source_doc:      str  = ""
    found_in_corpus: bool = False


@dataclass
class CoverageResult:
    covered:        list  = field(default_factory=list)
    uncovered:      list  = field(default_factory=list)
    coverage_ratio: float = 0.0
    checklist:      list  = field(default_factory=list)
    details:        dict  = field(default_factory=dict)


class DynamicChecklistBuilder:
    """
    Construit un checklist de 23 recommandations FATF pertinentes.

    Build strategy :
      - Source : UNIQUEMENT fatf_recommendations_2025.pdf
        (source officielle des obligations FATF)
      - Requête : TITRE EXACT de la recommandation
        (ex: "Customer due diligence" → trouve le bon chunk)
      - Validation : ≥2 keywords dans le chunk trouvé

    Le Retrieval Agent gère déjà tous les autres documents (BCT, EBA, etc.)
    → La checklist ne duplique pas ce rôle.
    """

    FATF_SOURCE = "fatf_recommendations_2025.pdf"

    def __init__(self, retriever=None,
                 cache_path: Path = None,
                 verbose: bool = False):
        self.retriever  = retriever
        self.cache_path = cache_path or Path("fatf_checklist_cache.json")
        self.verbose    = verbose
        self._recs: dict[int, FATFRecommendation] = {}
        self._built = False

    # ─────────────────────────────────────────────────────────────────
    #  API PUBLIQUE
    # ─────────────────────────────────────────────────────────────────

    def build(self, force_rebuild: bool = False):
        """Construit le catalogue. Appel unique au démarrage."""
        if self._built and not force_rebuild:
            return

        if not force_rebuild and self._load_cache():
            self._built = True
            if self.verbose:
                found = sum(1 for r in self._recs.values() if r.found_in_corpus)
                total = len(self._recs)
                print(f"  ✅ Checklist FATF : {found}/{total} recs dans corpus (depuis cache)")
            return

        if self.retriever:
            self._build_from_corpus()
        else:
            self._build_from_catalogue_only()

        self._save_cache()
        self._built = True

        if self.verbose:
            found = sum(1 for r in self._recs.values() if r.found_in_corpus)
            total = len(self._recs)
            print(f"  ✅ Checklist FATF : {found}/{total} recs dans {self.FATF_SOURCE}")

    def get_for(self, tx_type: str) -> list:
        """Retourne les recommandations FATF applicables à ce tx_type."""
        if not self._built:
            self.build()
        nums = TX_TYPE_RECS.get(tx_type, [10, 11, 20])
        return [self._recs[n] for n in nums if n in self._recs]

    def get_all_high(self, tx_type: str) -> list:
        """Retourne uniquement les recs HIGH pour ce tx_type."""
        return [r for r in self.get_for(tx_type) if r.relevance == "HIGH"]

    def check_coverage(self, recs: list, chunks: list) -> CoverageResult:
        """Vérifie la couverture sans LLM (keywords + article_ref)."""
        covered, uncovered, details = [], [], {}

        for rec in recs:
            chunk = self._find_covering_chunk(rec, chunks)
            if chunk:
                covered.append(rec.rec_id)
                details[rec.rec_id] = {
                    "source":  self._get(chunk, "source_doc"),
                    "article": self._get(chunk, "article_ref"),
                    "method":  chunk.get("_match_method", "?")
                              if isinstance(chunk, dict) else "?",
                }
            else:
                uncovered.append(rec.rec_id)

        ratio = len(covered) / len(recs) if recs else 1.0
        return CoverageResult(covered=covered, uncovered=uncovered,
                              coverage_ratio=ratio, checklist=recs, details=details)

    def format_for_llm(self, recs: list, pre_coverage: CoverageResult = None) -> str:
        """Formate pour le prompt LLM de reflect()."""
        lines = []
        for rec in recs:
            tag = f"[{rec.relevance}]"
            if pre_coverage:
                status = "✓ pré-couvert" if rec.rec_id in pre_coverage.covered else "⚠ possible gap"
                lines.append(f"  {rec.rec_id} {tag}: {rec.title} [{status}]")
            else:
                lines.append(f"  {rec.rec_id} {tag}: {rec.title}")
        return "\n".join(lines)

    def print_catalog(self, tx_type: str = None):
        """Affiche le catalogue pour debug."""
        if tx_type:
            recs = self.get_for(tx_type)
            print(f"\n  CHECKLIST FATF — {tx_type} ({len(recs)} recommandations)")
        else:
            recs = sorted(self._recs.values(), key=lambda r: r.number)
            print(f"\n  CATALOGUE FATF — {len(recs)} recommandations sélectionnées")
        print(f"  {'─'*68}")
        for rec in recs:
            found  = "✅" if rec.found_in_corpus else "⚪"
            relev  = f"[{rec.relevance[:3]}]"
            source = f" ← {rec.source_doc[:35]}" if rec.source_doc else ""
            print(f"  {found} {rec.rec_id:>4} {relev}: {rec.title[:45]}{source}")
        print()

    # ─────────────────────────────────────────────────────────────────
    #  BUILD DEPUIS CORPUS
    # ─────────────────────────────────────────────────────────────────

    def _build_from_corpus(self):
        """
        Cherche chaque recommandation par son TITRE EXACT
        dans fatf_recommendations_2025.pdf uniquement.

        Pourquoi titre et pas numéro ?
          "Customer due diligence" → match exact dans le document
          "FATF Recommendation 10" → moins fiable (chunks numérotés différemment)
        """
        for number, (title, keywords, relevance) in FATF_CATALOGUE.items():
            desc       = ""
            src        = ""
            found      = False
            best_chunk = None
            best_score = 0

            try:
                # Requête = titre officiel exact
                chunks = self.retriever.retrieve(query=title, top_k=8)

                for c in chunks:
                    doc  = self._get(c, "source_doc")
                    text = self._get(c, "text").lower()
                    ref  = self._get(c, "article_ref").lower()

                    # Filtrer : UNIQUEMENT fatf_recommendations_2025.pdf
                    if self.FATF_SOURCE.lower() not in doc.lower():
                        continue

                    # Score : nombre de keywords présents
                    kw_hits = sum(1 for kw in keywords if kw.lower() in text)

                    # Bonus si ref explicite (Numbered N, Rec. N)
                    has_ref = (
                        f"numbered {number}" in ref or
                        f"rec. {number}" in ref or
                        f"recommendation {number}" in text[:200]
                    )
                    score = kw_hits * 2 + (5 if has_ref else 0)

                    if score > best_score:
                        best_score = score
                        best_chunk = c
                        found = kw_hits >= 2  # Au moins 2 keywords pour valider

            except Exception as e:
                logger.debug(f"Query failed R{number}: {e}")

            if best_chunk:
                desc = self._get(best_chunk, "text")[:300]
                src  = self._get(best_chunk, "source_doc")

            tx_types = [tx for tx, nums in TX_TYPE_RECS.items() if number in nums]
            self._recs[number] = FATFRecommendation(
                rec_id=f"R{number}", number=number, title=title,
                description=desc or f"FATF Rec. {number}: {title}",
                tx_types=tx_types, keywords=keywords, relevance=relevance,
                source_doc=src, found_in_corpus=found,
            )

    def _build_from_catalogue_only(self):
        """Fallback sans Weaviate."""
        for number, (title, keywords, relevance) in FATF_CATALOGUE.items():
            tx_types = [tx for tx, nums in TX_TYPE_RECS.items() if number in nums]
            self._recs[number] = FATFRecommendation(
                rec_id=f"R{number}", number=number, title=title,
                description=f"FATF Recommendation {number}: {title}",
                tx_types=tx_types, keywords=keywords, relevance=relevance,
                source_doc="", found_in_corpus=False,
            )
        if self.verbose:
            print(f"  ⚠️  Checklist FATF : {len(self._recs)} recs (mode catalogue, sans Weaviate)")

    # ─────────────────────────────────────────────────────────────────
    #  COVERAGE DETECTION
    # ─────────────────────────────────────────────────────────────────

    def _find_covering_chunk(self, rec: FATFRecommendation, chunks: list):
        """
        Trouve un chunk couvrant cette recommandation parmi les chunks récupérés
        par le Retrieval Agent (qui, lui, cherche dans TOUS les documents).
        """
        for chunk in chunks:
            text = self._get(chunk, "text").lower()
            ref  = self._get(chunk, "article_ref").lower()

            # Méthode 1 : article_ref explicite
            if (f"rec. {rec.number}" in ref or
                f"numbered {rec.number}" in ref or
                f"recommendation {rec.number}" in ref):
                d = self._to_dict(chunk)
                d["_match_method"] = "article_ref"
                return d

            # Méthode 2 : ≥3 keywords dans n'importe quel document
            # (le Retrieval Agent trouve BCT, EBA, Wolfsberg, etc.)
            kw_hits = sum(1 for kw in rec.keywords if kw.lower() in text)
            if kw_hits >= 3:
                d = self._to_dict(chunk)
                d["_match_method"] = f"{kw_hits}_keywords"
                return d

            # Méthode 3 : source FATF + 2 keywords
            src = self._get(chunk, "source_doc").lower()
            if "fatf" in src and kw_hits >= 2:
                d = self._to_dict(chunk)
                d["_match_method"] = f"fatf_src+{kw_hits}kw"
                return d

        return None

    # ─────────────────────────────────────────────────────────────────
    #  CACHE
    # ─────────────────────────────────────────────────────────────────

    def _save_cache(self):
        try:
            data = {str(n): {
                "rec_id": r.rec_id, "number": r.number, "title": r.title,
                "description": r.description, "tx_types": r.tx_types,
                "keywords": r.keywords, "relevance": r.relevance,
                "source_doc": r.source_doc, "found_in_corpus": r.found_in_corpus,
            } for n, r in self._recs.items()}
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Cache save failed: {e}")

    def _load_cache(self) -> bool:
        if not Path(self.cache_path).exists():
            return False
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                data = json.load(f)
            for n_str, d in data.items():
                self._recs[int(n_str)] = FATFRecommendation(**d)
            return bool(self._recs)
        except Exception as e:
            logger.warning(f"Cache load failed: {e}")
            return False

    @staticmethod
    def _get(chunk, field: str, default: str = "") -> str:
        if isinstance(chunk, dict):
            return str(chunk.get(field, default) or default)
        return str(getattr(chunk, field, default) or default)

    @staticmethod
    def _to_dict(chunk) -> dict:
        if isinstance(chunk, dict):
            return dict(chunk)
        return {f: getattr(chunk, f, "")
                for f in ["text", "source_doc", "article_ref", "chunk_role"]}


# ═══════════════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "="*70)
    print("  DYNAMIC CHECKLIST v5 — 23 recommandations FATF pertinentes")
    print("="*70)

    builder = DynamicChecklistBuilder(retriever=None, verbose=True)
    builder.build()

    print(f"  Total catalogue : {len(FATF_CATALOGUE)} recommandations")
    print(f"    → HIGH   : {sum(1 for v in FATF_CATALOGUE.values() if v[2]=='HIGH')}")
    print(f"    → MEDIUM : {sum(1 for v in FATF_CATALOGUE.values() if v[2]=='MEDIUM')}")

    for tx_type in ["SWIFT", "VIREMENT_LOCAL", "DEPOT_ESPECES", "MANDAT"]:
        recs = builder.get_for(tx_type)
        print(f"\n  {tx_type} — {len(recs)} recommandations")
        for r in recs:
            print(f"    {r.rec_id:>4} [{r.relevance[:3]}]: {r.title}")

    print()

    # Test coverage
    print("  TEST COVERAGE — SWIFT")
    mock = [
        {"text": "customer due diligence identification verification beneficial owner CDD",
         "article_ref": "Article 6", "source_doc": "bct_circulaire_2017_08_aml.pdf"},
        {"text": "suspicious transaction STR filing CTAF reporting obligation declaration",
         "article_ref": "Article 43", "source_doc": "bct_circulaire_2017_08_aml.pdf"},
        {"text": "targeted financial sanctions asset freeze UN designation blocking UNSCR 1267",
         "article_ref": "Numbered 6", "source_doc": "fatf_recommendations_2025.pdf"},
        {"text": "wire transfer originator beneficiary information cross-border travel rule",
         "article_ref": "Article 23", "source_doc": "bct_circulaire_2018_09_aml_update.pdf"},
    ]
    recs = builder.get_for("SWIFT")
    cov  = builder.check_coverage(recs, mock)
    print(f"  Coverage : {cov.coverage_ratio:.0%} ({len(cov.covered)}/{len(recs)})")
    print(f"  Couverts : {cov.covered}")
    print(f"  Gaps     : {cov.uncovered}")
    print()
    print("  PROMPT LLM :")
    print(builder.format_for_llm(recs, cov))
    print("\n" + "="*70 + "\n")