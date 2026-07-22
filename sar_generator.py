"""
sar_generator.py — Suspicious Activity Report Generator (Niveau 3)
====================================================================
Génère un rapport SAR complet pour l'admin/superviseur, consolidant :

  1. Décision du Compliance Agent (règles BCT/FATF, lois violées/respectées)
  2. Score ML (LightGBM + LSTM-AE) avec décomposition SHAP
  3. Profil client (ClientScoringAgent) — PEP, FATCA, KYC, STR antérieurs
  4. Recommandation système pour l'admin

OBJECTIF : Donner à l'admin TOUS les éléments en un seul rapport pour qu'il
puisse approuver ou refuser sans avoir à fouiller dans le pipeline.

ENTRÉES :
  • compliance_decision : ComplianceDecision (du Compliance Agent)
  • ml_result           : dict {ml_score, shap_top, ml_decision}
  • client_result       : ClientRiskScore (du ClientScoringAgent)
  • transaction         : dict (champs de la transaction originale)

SORTIES :
  • SARReport (dataclass) — structuré pour le dashboard
  • .to_markdown()       — rapport lisible pour l'admin
  • .to_json()           — JSON exploitable par le frontend

ARCHITECTURE DU SCORE COMBINÉ :
  combined = max(ca_score, 0.6 × ca_score + 0.4 × ml_score)
  → Garde-fou : le ML ne peut JAMAIS rabaisser une décision RAG
  → Le ML ne fait que renforcer (monter le risque)
  → Cohérent avec "symbolique prévaut" du Compliance Agent
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any


# ═══════════════════════════════════════════════════════════════════════
#  DATACLASS SARReport
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class SARReport:
    """Structure complète du rapport SAR."""
    # ── Identification ──────────────────────────────────────────────
    sar_id: str = field(default_factory=lambda: f"SAR-{uuid.uuid4().hex[:8].upper()}")
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    declarant_bank: str = "Banque Tunisienne (PFE 2025-2026)"
    
    # ── Décision système ────────────────────────────────────────────
    decision_trigger: str = ""              # PASS | STR | BLOCK | EDD
    recommendation: str = ""                # APPROUVER | REFUSER | EDD requise
    
    # ── Scores ──────────────────────────────────────────────────────
    risk_score_compliance: float = 0.0      # 0-100 (du Compliance Agent)
    risk_score_ml: float = 0.0              # 0-100 (LightGBM)
    risk_score_combined: float = 0.0        # 0-100 (fusion avec garde-fou)
    risk_level: str = ""                    # FAIBLE | MOYEN | ÉLEVÉ | CRITIQUE

    # ── Divergence RAG/ML (Fix 6.3) ──────────────────────────────────
    divergence_flag: str = ""               # FAIBLE | MOYENNE | ÉLEVÉE
    divergence_action: str = ""
    # ── Pattern AML métier (Fix 6.4) ─────────────────────────────────
    aml_pattern: str = "NORMAL"
    aml_pattern_explanation: str = ""
    aml_all_patterns: list = field(default_factory=list)

    # ── Transaction ─────────────────────────────────────────────────
    transaction: dict = field(default_factory=dict)
    
    # ── Profil client ───────────────────────────────────────────────
    client_profile: dict = field(default_factory=dict)
    client_risk_level: str = ""             # FAIBLE | MOYEN | ÉLEVÉ
    client_factors: list = field(default_factory=list)
    fatca_obligation: str = ""              # COLLECT_W9 | NONE
    
    # ── Compliance Agent — RÈGLES ───────────────────────────────────
    violated_rules: list = field(default_factory=list)
    respected_rules: list = field(default_factory=list)
    applicable_rules_count: int = 0
    coverage_fatf: float = 0.0              # pourcentage
    coverage_bct: float = 0.0
    
    # ── ML — SHAP explainability ────────────────────────────────────
    ml_top_features: list = field(default_factory=list)
    ml_decision: str = ""                   # FRAUDE_SUSPECTÉE | LÉGITIME
    ml_reliable: bool = True                # False = profil sans historique → ignoré
    ml_profile_status: str = ""             # PROFIL_MATURE | PROFIL_NOUVEAU
    
    # ── Documents requis ────────────────────────────────────────────
    required_documents: list = field(default_factory=list)
    
    # ── Narratif ────────────────────────────────────────────────────
    narrative: str = ""
    # Synthèse exécutive courte (LLM « rédacteur sous contrainte » si dispo,
    # sinon repli déterministe) — c'est CE QUE L'ADMIN LIT EN PREMIER.
    executive_summary: str = ""
    executive_summary_source: str = "deterministe"   # "llm" | "deterministe"
    
    # ── Audit ───────────────────────────────────────────────────────
    audit_trail: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    def to_json(self, path: Optional[str] = None) -> str:
        data = json.dumps(self.to_dict(), indent=2, ensure_ascii=False,
                          default=str)
        if path:
            Path(path).write_text(data, encoding="utf-8")
        return data
    
    def to_markdown(self, path: Optional[str] = None) -> str:
        md = _render_markdown(self)
        if path:
            Path(path).write_text(md, encoding="utf-8")
        return md


# ═══════════════════════════════════════════════════════════════════════
#  GÉNÉRATEUR SAR
# ═══════════════════════════════════════════════════════════════════════

class SARGenerator:
    """Génère un rapport SAR à partir des sorties des 3 composants."""
    
    # Seuils pour le niveau de risque global
    RISK_THRESHOLDS = {
        "CRITIQUE": 80,
        "ÉLEVÉ":    50,
        "MOYEN":    25,
        "FAIBLE":    0,
    }
    
    def __init__(self, verbose: bool = False, use_llm: bool = True):
        self.verbose = verbose
        self.use_llm = use_llm
        self._llm = None          # lazy — ResilientLLM (rotation clés × modèles)
        self._llm_tried = False

    @property
    def llm(self):
        """Client LLM résilient (groq_keys.json : rotation clé externe ×
        modèle interne + cache fichier). None si indisponible → repli
        déterministe, le SAR ne casse JAMAIS."""
        if not self.use_llm:
            return None
        if not self._llm_tried:
            self._llm_tried = True
            try:
                from llm_client import get_llm
                self._llm = get_llm()
            except Exception:
                self._llm = None
        return self._llm
    
    def generate(self,
                 compliance_decision,
                 ml_result: Optional[dict] = None,
                 client_result=None,
                 transaction: Optional[dict] = None,
                 l1_signals: Optional[list] = None) -> SARReport:   # AJOUT (Fix 6.5)
        """
        Génère un SAR pour CHAQUE transaction (pas seulement STR/BLOCK).
        L'admin doit pouvoir consulter le rapport de toute transaction.
        """
        sar = SARReport()
        
        # ── 1. Identification + transaction ─────────────────────────
        sar.transaction = self._summarize_tx(transaction or {})
        
        # ── 2. Profil client ────────────────────────────────────────
        if client_result is not None:
            sar.client_profile = self._summarize_client(client_result)
            sar.client_risk_level = getattr(client_result, "level", "FAIBLE")
            sar.client_factors = list(
                getattr(client_result, "components", {}).keys())
            fatca = getattr(client_result, "fatca", None)
            if fatca is not None:
                sar.fatca_obligation = getattr(fatca, "obligation", "NONE")
        
        # ── 3. Compliance Agent — règles ───────────────────────────
        sar.decision_trigger = getattr(compliance_decision, "decision", "PASS")
        sar.risk_score_compliance = float(
            getattr(compliance_decision, "risk_score", 0))
        sar.applicable_rules_count = len(
            getattr(compliance_decision, "applicable_rules", []))
        
        # Lois VIOLÉES (avec source BCT/FATF exacte)
        sar.violated_rules = self._format_violated(
            getattr(compliance_decision, "violated_rules", []))
        
        # Lois RESPECTÉES (obligations vérifiées et conformes)
        sar.respected_rules = self._format_respected(
            getattr(compliance_decision, "applicable_rules", []),
            getattr(compliance_decision, "violated_rules", []))
        
        # Couvertures — source de vérité = les dicts du ComplianceDecision
        # (rec FATF → bool), même sémantique que l'éval (BCT 84% / FATF 94.7%).
        # L'ancien code lisait stats["fatf_coverage"] qui n'existe pas → 0.0%.
        fatf_cov = getattr(compliance_decision, "fatf_coverage", {}) or {}
        bind_cov = getattr(compliance_decision, "binding_coverage", {}) or {}
        if fatf_cov:
            sar.coverage_fatf = round(
                sum(1 for v in fatf_cov.values() if v)
                / max(len(fatf_cov), 1) * 100, 1)
            sar.coverage_bct = round(
                sum(1 for v in bind_cov.values() if v)
                / max(len(bind_cov), 1) * 100, 1)
        else:
            # Repli (mocks sans dicts) : ratio obligations respectées / vérifiées
            total_rules = len(sar.respected_rules) + len(sar.violated_rules)
            sar.coverage_fatf = round(
                len(sar.respected_rules) / max(total_rules, 1) * 100, 1)
            bct_checked = sum(1 for r in sar.respected_rules + sar.violated_rules
                              if r.get("is_bct_binding"))
            bct_ok = sum(1 for r in sar.respected_rules
                         if r.get("is_bct_binding"))
            sar.coverage_bct = round(bct_ok / max(bct_checked, 1) * 100, 1)
        
        # ── 4. ML score + SHAP ──────────────────────────────────────
        if ml_result:
            ml_proba = float(ml_result.get("ml_score", 0))
            sar.risk_score_ml = round(ml_proba * 100, 1)
            sar.ml_decision = ml_result.get("ml_decision", "INCONNU")
            sar.ml_top_features = self._format_shap(
                ml_result.get("shap_top", []))
            # FIABILITÉ : sur un client SANS historique (PROFIL_NOUVEAU), les
            # features comportementales sont remplies par des médianes → la
            # proba sature (~1.0) quelle que soit la transaction. Ce signal
            # n'est PAS exploitable → il est affiché mais IGNORÉ par la fusion
            # et la divergence (le ML validé hors-ligne reste une 2ᵉ ligne).
            sar.ml_profile_status = ml_result.get("profile_status", "")
            sar.ml_reliable = sar.ml_profile_status == "PROFIL_MATURE"
        
        # ── Pattern AML détecté (Fix 6.4) — juste après la section ML ──
        from pattern_classifier import classify_pattern
        # Utilise TOUTES les features (pas juste top-5) pour la détection des patterns
        ml_feat_dict = ml_result.get("all_features", {}) if ml_result else {}
        # Fallback si all_features absent (rétrocompat)
        if not ml_feat_dict and ml_result:
            for shap_entry in ml_result.get("shap_top", []):
                if isinstance(shap_entry, dict):
                    ml_feat_dict[shap_entry.get("feature", "")] = shap_entry.get("value", 0)

        # Signaux L1 passés directement en paramètre (source unique de vérité)
        l1_signals_local = l1_signals if l1_signals else []
        pattern_result = classify_pattern(
            transaction=transaction or {},
            ml_features=ml_feat_dict,
            l1_signals=l1_signals_local)
        sar.aml_pattern = pattern_result["primary_pattern"]
        sar.aml_pattern_explanation = pattern_result["summary"]
        sar.aml_all_patterns = pattern_result["all_patterns"]

        # ── 5. Score combiné (Fix 6.2 + fiabilité profil) ────────────
        # Le ML NE CONTRIBUE QUE si (a) il a signalé FRAUDE_SUSPECTÉE ET
        # (b) le profil client a un VRAI historique (PROFIL_MATURE). Sur un
        # profil nouveau la proba saturée (100 %) gonflerait artificiellement
        # le score combiné de n'importe quelle transaction.
        ca_score_norm = sar.risk_score_compliance / 100.0
        if (ml_result and ml_result.get("ml_decision") == "FRAUDE_SUSPECTÉE"
                and sar.ml_reliable):
            ml_contribution = float(ml_result.get("ml_score", 0))
        else:
            ml_contribution = 0.0   # ML LÉGITIME ou non fiable → n'ajoute rien
        fused = 0.6 * ca_score_norm + 0.4 * ml_contribution
        combined = max(ca_score_norm, fused)   # ML ne rabaisse jamais le RAG
        sar.risk_score_combined = round(combined * 100, 1)
        sar.risk_level = self._classify_risk(sar.risk_score_combined)

        # ── Détection de divergence RAG/ML (Fix 6.3, logique corrigée) ──
        #  Concordance = les deux voient la même chose : FRAUDE↔(EDD/STR/BLOCK)
        #  et LÉGITIME↔(PASS/REVIEW). L'ancien code tombait en « MOYENNE »
        #  pour STR+FRAUDE qui sont pourtant CONCORDANTS.
        ca_dec = sar.decision_trigger
        ml_dec = sar.ml_decision
        ca_alerte = ca_dec in ("EDD", "STR", "BLOCK")
        if ml_result and not sar.ml_reliable:
            sar.divergence_flag = "NON ÉVALUÉE"
            sar.divergence_action = (
                "Client sans historique comportemental (profil nouveau) : la "
                "probabilité ML n'est pas calibrée sur ce cas et est IGNORÉE "
                "(ni score, ni divergence). La décision repose sur les règles "
                "L1 + RAG. Le ML s'activera quand l'historique du client sera "
                "suffisant (≥ 5 transactions).")
        elif ml_dec == "FRAUDE_SUSPECTÉE" and ca_alerte:
            sar.divergence_flag = "FAIBLE"
            sar.divergence_action = ("RAG et ML concordent (tous deux "
                                     "signalent l'opération) — décision robuste.")
        elif ml_dec == "LÉGITIME" and not ca_alerte:
            sar.divergence_flag = "FAIBLE"
            sar.divergence_action = "RAG et ML concordent — décision robuste."
        elif ml_dec == "FRAUDE_SUSPECTÉE" and not ca_alerte:
            sar.divergence_flag = "ÉLEVÉE"
            sar.divergence_action = (
                "REVUE HUMAINE — Les règles RAG considèrent la transaction "
                "conforme mais le ML détecte un pattern suspect. Cela peut "
                "indiquer un schéma nouveau non couvert par les règles, ou un "
                "biais temporel du modèle (hour/day_of_week).")
        elif ml_dec == "LÉGITIME" and ca_dec in ("STR", "BLOCK"):
            sar.divergence_flag = "ÉLEVÉE"
            sar.divergence_action = (
                "Le ML ne détecte pas d'anomalie statistique mais les règles "
                "réglementaires sont violées. Le symbolique prévaut — la décision "
                "RAG est maintenue.")
        else:
            sar.divergence_flag = "MOYENNE"
            sar.divergence_action = "Décisions partiellement concordantes (EDD vs LÉGITIME)."
        
        # ── 6. Recommandation pour l'admin ──────────────────────────
        sar.recommendation = self._recommend(sar)
        
        # ── 7. Documents requis pour la prise de décision ──────────
        sar.required_documents = self._required_docs(
            compliance_decision, client_result)
        
        # ── 8. Narratif déterministe (template, PAS de LLM) ────────
        sar.narrative = self._build_narrative(sar)

        # ── 8bis. Synthèse exécutive — LLM « rédacteur sous contrainte » ──
        #  Le LLM ne DÉCIDE rien : il reformule les faits déjà calculés en un
        #  paragraphe court et lisible. Repli déterministe si LLM absent/échec.
        sar.executive_summary = self._executive_summary(sar)
        
        # ── 9. Audit trail ──────────────────────────────────────────
        sar.audit_trail = [
            {"step": "compliance_agent",
             "decision": sar.decision_trigger,
             "score": sar.risk_score_compliance,
             "violations": len(sar.violated_rules)},
            {"step": "ml_fraud_detector",
             "score": sar.risk_score_ml,
             "decision": sar.ml_decision},
            {"step": "client_scoring",
             "level": sar.client_risk_level,
             "factors": sar.client_factors},
            {"step": "fusion",
             "formula": "max(ca, 0.6*ca + 0.4*ml)",
             "combined": sar.risk_score_combined},
        ]
        
        return sar
    
    # ── Helpers ─────────────────────────────────────────────────────
    
    def _summarize_tx(self, tx: dict) -> dict:
        return {
            "type": tx.get("tx_type") or tx.get("transaction_type", "?"),
            "amount": tx.get("amount", 0),
            "currency": tx.get("currency", ""),
            "sender": tx.get("sender_name", ""),
            "sender_country": tx.get("sender_country", ""),
            "beneficiary": (tx.get("beneficiary_name") 
                            or tx.get("receiver_name", "")),
            "beneficiary_country": (tx.get("destination_country")
                                    or tx.get("receiver_country", "")),
            "motive": tx.get("motive", ""),
            "has_justification": tx.get("has_justification"),
            "timestamp": tx.get("timestamp", ""),
        }
    
    def _summarize_client(self, client_result) -> dict:
        return {
            "client_id": getattr(client_result, "client_id", "?"),
            "level": getattr(client_result, "level", "FAIBLE"),
            "score": float(getattr(client_result, "score", 0)),
            "is_pep": "pep_foreign" in getattr(client_result, "components", {})
                      or "pep_domestic" in getattr(client_result, "components", {}),
            "kyc_complete": "incomplete_kyc" not in getattr(
                client_result, "components", {}),
            "prior_strs": "prior_strs" in getattr(
                client_result, "components", {}),
        }
    
    def _format_violated(self, violated_rules) -> list:
        out = []
        for r in violated_rules:
            src = getattr(r, "source_doc", "?")
            # level1_* = seuils/sanctions BCT codés au Niveau 1 → contraignant
            is_bct = (src.startswith("bct_") or src.startswith("level1")
                      or "loi_" in src or "ctaf" in src)
            out.append({
                "rule_text": getattr(r, "rule_text", "")[:200],
                "source": src,
                "is_bct_binding": bool(is_bct),
                "article_ref": getattr(r, "rule_ref", "?"),
                "fatf_rec": getattr(r, "fatf_rec", "?"),
                "severity": getattr(r, "severity", "HIGH"),
                "cross_validated": bool(getattr(r, "cross_validated", False)),
                "provenance": getattr(r, "provenance", "?"),
            })
        return out
    
    def _format_respected(self, applicable_rules, violated_rules) -> list:
        """Liste des obligations vérifiées et CONFORMES."""
        violated_keys = {(getattr(v, "source_doc", ""),
                          getattr(v, "rule_ref", ""))
                         for v in violated_rules}
        out = []
        for r in applicable_rules:
            key = (getattr(r, "source_doc", ""), getattr(r, "rule_ref", ""))
            if key in violated_keys:
                continue
            src = getattr(r, "source_doc", "?")
            # level1_* = seuils/sanctions BCT codés au Niveau 1 → contraignant
            is_bct = (src.startswith("bct_") or src.startswith("level1")
                      or "loi_" in src or "ctaf" in src)
            out.append({
                "rule_text": getattr(r, "rule_text", "")[:150],
                "source": src,
                "is_bct_binding": bool(is_bct),
                "article_ref": getattr(r, "rule_ref", "?"),
                "fatf_rec": getattr(r, "fatf_rec", "?"),
                "status": "CONFORME",
            })
        return out
    
    def _format_shap(self, shap_top: list) -> list:
        """Convertit le top SHAP en liste lisible."""
        out = []
        for entry in shap_top[:5]:
            if isinstance(entry, dict):
                out.append({
                    "feature": entry.get("feature", "?"),
                    "value": entry.get("value", 0),
                    "shap": entry.get("shap", 0),
                    "pushes": entry.get("pushes", "?"),
                    "interpretation": self._interpret_feature(
                        entry.get("feature", ""), entry.get("value", 0))
                })
        return out
    
    def _interpret_feature(self, feature: str, value) -> str:
        """Explication métier d'une feature SHAP pour l'admin."""
        interpretations = {
            "reconstruction_error": "Séquence temporelle anormale détectée "
                                    "par l'autoencoder LSTM",
            "sender_fanout_ratio":  "Émetteur diversifie vers de nombreux "
                                    "récepteurs (pattern smurfing)",
            "receiver_fanin_ratio": "Récepteur collecte de nombreux émetteurs "
                                    "(pattern de collecte)",
            "amount_vs_sender_mean": "Montant anormal par rapport à "
                                     "l'historique de l'émetteur",
            "is_night":              "Transaction effectuée la nuit",
            "hour":                  "Heure inhabituelle pour ce type de "
                                     "transaction",
            "currency_mismatch":     "Conversion de devise (layering possible)",
            "just_below_10k":        "Montant juste sous le seuil de 10 000 "
                                     "(évitement potentiel)",
            "is_unique_pair":        "Première transaction entre émetteur et "
                                     "récepteur (paire jetable possible)",
        }
        return interpretations.get(feature, "Indicateur statistique")
    
    def _classify_risk(self, score: float) -> str:
        for level, thr in self.RISK_THRESHOLDS.items():
            if score >= thr:
                return level
        return "FAIBLE"
    
    def _recommend(self, sar: SARReport) -> str:
        if sar.decision_trigger == "BLOCK":
            return "REFUSER — Sanctions ou interdictions BCT/ONU violées"
        if sar.decision_trigger == "STR":
            return ("DÉCLARER À LA CTAF — Soupçon de blanchiment, "
                    "déclaration obligatoire dans les 48h")
        if sar.decision_trigger == "EDD":
            return ("DILIGENCE RENFORCÉE REQUISE — Recueillir documents "
                    "complémentaires avant décision")
        if sar.risk_score_combined >= 50:
            return ("VALIDATION HUMAINE — Risque modéré, examiner les "
                    "éléments SHAP et le profil client")
        return "APPROUVER — Faible risque, contrôles automatiques validés"
    
    def _required_docs(self, decision, client) -> list:
        docs = []
        if hasattr(decision, "required_documents"):
            docs.extend(decision.required_documents)
        # Sinon, déduction depuis la décision
        if not docs:
            if getattr(decision, "risk_score", 0) >= 45:
                docs.append("Justificatif économique de l'opération (facture, "
                            "contrat)")
            if getattr(decision, "decision", "") == "EDD":
                docs.append("Source des fonds documentée")
            if client and "incomplete_kyc" in getattr(client, "components", {}):
                docs.append("Dossier KYC complet (CIN, justificatif domicile)")
            if client and ("pep_foreign" in getattr(client, "components", {})
                           or "pep_domestic" in getattr(client, "components", {})):
                docs.append("Déclaration PEP + autorisation hiérarchique (R12)")
            if getattr(decision, "decision", "") in ("STR", "BLOCK"):
                docs.append("Formulaire goAML pour transmission CTAF")
        return docs
    
    # ── Synthèse exécutive (LLM rédacteur sous contrainte) ──────────

    def _executive_summary(self, sar: SARReport) -> str:
        """Paragraphe court (4-7 phrases) que l'admin lit en premier.

        GARDE-FOUS anti-hallucination :
          • le LLM reçoit UNIQUEMENT les faits déjà calculés (JSON) ;
          • consigne stricte : ne rien inventer, aucun chiffre/loi nouveau ;
          • temp=0 → réponse déterministe + cache fichier (démo rejouable) ;
          • toute erreur → repli 100 % déterministe (le SAR sort toujours).
        """
        fallback = self._fallback_summary(sar)
        llm = self.llm
        if llm is None:
            return fallback

        tx = sar.transaction
        facts = {
            "operation": f"{tx.get('type')} {tx.get('amount', 0):,} {tx.get('currency', '')} "
                         f"de {tx.get('sender') or '?'} ({tx.get('sender_country') or '?'}) "
                         f"vers {tx.get('beneficiary') or '?'} ({tx.get('beneficiary_country') or '?'})",
            "motif": tx.get("motive") or "non renseigné",
            "justificatif_fourni": bool(tx.get("has_justification")),
            "decision_systeme": sar.decision_trigger,
            "score_combine": sar.risk_score_combined,
            "niveau_risque": sar.risk_level,
            "violations": [
                f"{r['source']} {r['article_ref']} (FATF {r['fatf_rec']}) : {r['rule_text'][:100]}"
                for r in sar.violated_rules[:3]
            ],
            "nb_violations_total": len(sar.violated_rules),
            "nb_obligations_conformes": len(sar.respected_rules),
            "pattern_aml": sar.aml_pattern,
            "pattern_explication": sar.aml_pattern_explanation,
            "profil_client": {
                "niveau": sar.client_risk_level,
                "pep": sar.client_profile.get("is_pep", False),
                "kyc_complet": sar.client_profile.get("kyc_complete", True),
            } if sar.client_profile else None,
            "ml": {"decision": sar.ml_decision, "proba_brute_pct": sar.risk_score_ml},
            "divergence_rag_ml": sar.divergence_flag,
            "recommandation": sar.recommendation,
        }
        prompt = (
            "Tu es rédacteur conformité dans une banque tunisienne. Rédige la "
            "SYNTHÈSE EXÉCUTIVE (un seul paragraphe, 4 à 7 phrases, en français "
            "professionnel) d'un rapport de soupçon, à partir des FAITS ci-dessous.\n"
            "CONTRAINTES STRICTES :\n"
            "- Utilise UNIQUEMENT ces faits. N'invente RIEN : aucun chiffre, "
            "aucune loi, aucun nom qui n'y figure pas.\n"
            "- Pas de titre, pas de liste, pas de markdown, AUCUN emoji : "
            "un paragraphe fluide.\n"
            "- Commence par décrire l'opération, puis la décision et sa raison "
            "principale, puis le contexte client/ML si pertinent.\n"
            "- Termine EXACTEMENT par : « Recommandation : "
            f"{sar.recommendation}. »\n\n"
            f"FAITS (JSON) :\n{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}"
        )
        try:
            resp = llm.chat.completions.create(
                model=None,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=400)
            text = (resp.choices[0].message.content or "").strip()
            # Garde : réponse vide/trop courte ou trop longue → repli
            if 100 <= len(text) <= 1800:
                sar.executive_summary_source = "llm"
                return text
        except Exception:
            pass
        return fallback

    @staticmethod
    def _fallback_summary(sar: SARReport) -> str:
        """Synthèse déterministe (aucun LLM) — toujours disponible."""
        tx = sar.transaction
        s = (f"La transaction {tx.get('type', '?')} de "
             f"{tx.get('amount', 0):,} {tx.get('currency', '')} "
             f"({tx.get('sender_country') or '?'} → {tx.get('beneficiary_country') or '?'}) "
             f"a reçu la décision {sar.decision_trigger} "
             f"(score combiné {sar.risk_score_combined:.0f}/100, niveau {sar.risk_level}). ")
        if sar.violated_rules:
            r = sar.violated_rules[0]
            s += (f"{len(sar.violated_rules)} obligation(s) violée(s), dont "
                  f"{r['source']} {r['article_ref']} (FATF {r['fatf_rec']}). ")
        else:
            s += f"{len(sar.respected_rules)} obligation(s) vérifiée(s), aucune violation. "
        if sar.aml_pattern and sar.aml_pattern != "NORMAL":
            s += f"Pattern AML détecté : {sar.aml_pattern}. "
        if sar.client_risk_level and sar.client_risk_level != "FAIBLE":
            s += f"Le profil client est à risque structurel {sar.client_risk_level}. "
        s += f"Recommandation : {sar.recommendation}."
        return s

    def _build_narrative(self, sar: SARReport) -> str:
        """Narratif déterministe — assemble les faits, ZÉRO LLM."""
        parts = []
        tx = sar.transaction
        
        # Description factuelle
        parts.append(
            f"La transaction {tx['type']} d'un montant de "
            f"{tx['amount']:,} {tx['currency']} de "
            f"{tx['sender'] or '?'} ({tx['sender_country'] or '?'}) vers "
            f"{tx['beneficiary'] or '?'} ({tx['beneficiary_country'] or '?'}) "
            f"a été évaluée par le système AML/KYC."
        )
        
        # Décision système
        parts.append(
            f"\n**Décision système** : {sar.decision_trigger} "
            f"(score combiné {sar.risk_score_combined}/100, "
            f"niveau {sar.risk_level})."
        )
        
        # Profil client
        if sar.client_profile:
            cp = sar.client_profile
            parts.append(
                f"\n**Profil client** : niveau de risque structurel "
                f"{sar.client_risk_level} (score {cp['score']:.0f}/100). "
                f"PEP : {'OUI' if cp.get('is_pep') else 'non'}. "
                f"KYC : {'complet' if cp.get('kyc_complete') else 'INCOMPLET'}. "
                f"STR antérieurs : {'OUI' if cp.get('prior_strs') else 'non'}."
            )
        
        # Violations
        if sar.violated_rules:
            parts.append(
                f"\n**{len(sar.violated_rules)} obligation(s) violée(s)** :"
            )
            for r in sar.violated_rules[:5]:
                tag = "BCT" if r["is_bct_binding"] else "FATF"
                parts.append(
                    f"  • [{tag}] {r['source']} {r['article_ref']} "
                    f"(réf. FATF {r['fatf_rec']}) : {r['rule_text'][:100]}"
                )
        
        # ML
        if sar.risk_score_ml > 0:
            parts.append(
                f"\n**Analyse ML** : probabilité de fraude "
                f"{sar.risk_score_ml}/100 ({sar.ml_decision})."
            )
            if sar.ml_top_features:
                parts.append("Facteurs ML dominants :")
                for f in sar.ml_top_features[:3]:
                    parts.append(
                        f"  • {f['feature']} ({f['pushes']}) : "
                        f"{f['interpretation']}"
                    )
        
        # Recommandation
        parts.append(f"\n**Recommandation** : {sar.recommendation}")
        
        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
#  RENDU MARKDOWN POUR L'ADMIN
# ═══════════════════════════════════════════════════════════════════════

def _render_markdown(sar: SARReport) -> str:
    """Rapport COMPACT et hiérarchisé : l'essentiel tient sur un écran.

    Structure (refonte lisibilité — l'admin lit plusieurs rapports d'affilée) :
      1. En-tête + recommandation (2 lignes)
      2. Synthèse exécutive (le paragraphe à lire)
      3. Un seul tableau de scores + divergence en une ligne
      4. Violations (le cœur juridique — tableau conservé)
      5. Lignes compactes : conformité/couverture · pattern · client · ML top-3
      6. Documents requis + actions admin
    Le détail complet reste disponible en JSON (sar.to_json / audit_trail).
    """
    md = []
    tx = sar.transaction

    # ── 1. EN-TÊTE ──────────────────────────────────────────────────
    md.append(f"# {sar.sar_id} — **{sar.decision_trigger}** · "
              f"{sar.risk_level} ({sar.risk_score_combined:.0f}/100)")
    md.append("")
    md.append(f"`{tx.get('type', '?')}` · **{tx.get('amount', 0):,} {tx.get('currency', '')}** · "
              f"{tx.get('sender') or '?'} ({tx.get('sender_country') or '?'}) → "
              f"{tx.get('beneficiary') or '?'} ({tx.get('beneficiary_country') or '?'}) · "
              f"motif : {tx.get('motive') or '—'} · "
              f"justificatif : {'oui' if tx.get('has_justification') else 'non'}")
    md.append("")
    md.append(f"> ### {sar.recommendation}")
    md.append("")

    # ── 2. SYNTHÈSE EXÉCUTIVE (le paragraphe que l'admin lit) ───────
    md.append("## Synthèse")
    md.append("")
    md.append(sar.executive_summary or sar.narrative)
    src_note = ("rédigée par LLM sous contrainte de faits — aucun fait inventé"
                if sar.executive_summary_source == "llm"
                else "générée de façon déterministe")
    md.append("")
    md.append(f"*Synthèse {src_note}. Détail complet : JSON du rapport.*")
    md.append("")

    # ── 3. SCORES (un seul tableau) ─────────────────────────────────
    ml_disp = f"{sar.risk_score_ml:.0f}% brut → {sar.ml_decision or 'N/A'}" \
        if sar.risk_score_ml else "N/A"
    if sar.risk_score_ml and not sar.ml_reliable:
        ml_disp += " (profil nouveau — IGNORÉ)"
    md.append("## Scores")
    md.append("")
    md.append("| Compliance (règles) | ML (indicatif) | Client | **Combiné** |")
    md.append("|---|---|---|---|")
    md.append(f"| {sar.risk_score_compliance:.0f}/100 → {sar.decision_trigger} "
              f"| {ml_disp} | {sar.client_risk_level or '—'} "
              f"| **{sar.risk_score_combined:.0f}/100 · {sar.risk_level}** |")
    md.append("")
    md.append(f"**Divergence RAG↔ML** : {sar.divergence_flag} — "
              f"{sar.divergence_action}")
    md.append("")

    # ── 4. VIOLATIONS (le cœur juridique) ───────────────────────────
    if sar.violated_rules:
        md.append(f"## {len(sar.violated_rules)} obligation(s) VIOLÉE(S)")
        md.append("")
        md.append("| # | Source | Article | FATF | Sévérité | Obligation |")
        md.append("|---|---|---|---|---|---|")
        for i, r in enumerate(sar.violated_rules[:5], 1):
            badge = "BCT" if r["is_bct_binding"] else "Int."
            cross = " (recoupée)" if r.get("cross_validated") else ""
            md.append(f"| {i} | {badge} `{r['source']}` | {r['article_ref']} "
                      f"| {r['fatf_rec']}{cross} | **{r['severity']}** "
                      f"| {r['rule_text'][:70]}… |")
        if len(sar.violated_rules) > 5:
            md.append(f"")
            md.append(f"*… et {len(sar.violated_rules) - 5} autre(s) — voir JSON.*")
        md.append("")
    else:
        md.append("## Aucune obligation violée")
        md.append("")

    # ── 5. LIGNES COMPACTES ─────────────────────────────────────────
    top_sources = sorted({r['source'] for r in sar.respected_rules[:6]})
    md.append(f"**Conformité** : {len(sar.respected_rules)} obligation(s) "
              f"vérifiée(s) conforme(s)"
              + (f" ({', '.join(s.replace('.pdf', '') for s in top_sources[:3])}…)"
                 if top_sources else "")
              + f" · couverture BCT {sar.coverage_bct:.0f}% / FATF {sar.coverage_fatf:.0f}%")
    md.append("")

    md.append(f"**Pattern AML** : {sar.aml_pattern} — "
              f"{sar.aml_pattern_explanation}")
    md.append("")

    if sar.client_profile:
        cp = sar.client_profile
        extras = []
        if cp.get("is_pep"):
            extras.append("PEP")
        if not cp.get("kyc_complete", True):
            extras.append("KYC incomplet")
        if cp.get("prior_strs"):
            extras.append("STR antérieurs")
        if sar.fatca_obligation and sar.fatca_obligation != "NONE":
            extras.append(f"FATCA : {sar.fatca_obligation}")
        md.append(f"**Client** `{cp.get('client_id', '?')}` : risque "
                  f"{cp.get('level', '?')} ({cp.get('score', 0):.0f}/100)"
                  + (" · " + " · ".join(extras) if extras else ""))
        md.append("")

    if sar.ml_top_features:
        feats = " · ".join(
            f"`{f['feature']}` ({f.get('interpretation', '')})"
            for f in sar.ml_top_features[:3])
        md.append(f"**ML (SHAP top 3)** : {feats}")
        md.append("")

    # ── 6. DOCUMENTS + ACTIONS ──────────────────────────────────────
    if sar.required_documents:
        md.append("## Documents requis")
        md.append("")
        for i, doc in enumerate(sar.required_documents, 1):
            md.append(f"{i}. {doc}")
        md.append("")

    md.append("---")
    md.append("**Actions** : [ ] APPROUVER · [ ] REFUSER · "
              "[ ] DEMANDER DOCUMENTS · [ ] ANNOTER (faux positif / vraie fraude)")
    md.append("")
    md.append(f"*{sar.declarant_bank} · généré le {sar.generated_at[:19]} — "
              f"système AML/KYC Agentic RAG, PFE 2025-2026*")

    return "\n".join(md)


# ═══════════════════════════════════════════════════════════════════════
#  EXÉCUTION DIRECTE — DÉMO
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  SAR Generator — Démo standalone")
    print("=" * 70)
    print()
    print("Pour utiliser : python demo_sar.py")
    print()
    print("Exemple d'utilisation programmatique :")
    print()
    print("  from sar_generator import SARGenerator")
    print("  gen = SARGenerator()")
    print("  sar = gen.generate(compliance_decision, ml_result, "
          "client_result, transaction)")
    print("  md = sar.to_markdown('sar_report.md')")
    print("  js = sar.to_json('sar_report.json')")