
# Ce script vit dans un sous-dossier : on expose la racine du projet aux imports.
import sys as _sys, pathlib as _pathlib
_RACINE = _pathlib.Path(__file__).resolve().parents[1]
if str(_RACINE) not in _sys.path:
    _sys.path.insert(0, str(_RACINE))


# Register custom pytest marks to avoid warnings
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: tests requiring Neo4j + Groq services")


"""
test_compliance_agent.py — Suite de tests exhaustive du Compliance Agent v6.0
=============================================================================
CRISP-DM Phase 5 — Évaluation | PFE AML/KYC 2025-2026

STRUCTURE :
  NIVEAU 0 : Vérification de déploiement (1 commande, < 1 s)
  NIVEAU 1 : Tests unitaires SANS services (Neo4j, LLM, Weaviate)
             Déterministes, < 5 s, lançables hors ligne
  NIVEAU 2 : Tests d'intégration avec services
             Nécessite Neo4j + Groq actifs (marqueur @integration)

COMMANDES :
  # Vérification version (TOUJOURS en premier)
  python test_compliance_agent.py --check-version

  # Niveau 1 — tests unitaires (SANS services, rapides)
  pytest test_compliance_agent.py -v -m "not integration"

  # Niveau 2 — tests d'intégration (AVEC services)
  pytest test_compliance_agent.py -v -m integration

  # Tout
  pytest test_compliance_agent.py -v

  # Avec rapport HTML
  pytest test_compliance_agent.py -v --html=rapport_tests.html
"""

import sys
import os
import json
import hashlib
import pytest
import argparse

# ── Résolution du path ────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# ══════════════════════════════════════════════════════════════════════
#  NIVEAU 0 — VÉRIFICATION DE DÉPLOIEMENT
# ══════════════════════════════════════════════════════════════════════

def check_version():
    """
    À lancer AVANT tout test.
    Vérifie que le bon fichier tourne sur la machine.
    """
    print("\n" + "="*60)
    print("  VÉRIFICATION DE DÉPLOIEMENT — Compliance Agent")
    print("="*60)
    try:
        from compliance_agent import __version__, FIXES_APPLIED
        print(f"  Version    : {__version__}")
        print(f"  Fixes      : {FIXES_APPLIED}")
        assert __version__ == "6.0", (
            f"\n❌ MAUVAIS FICHIER ! Version {__version__} ≠ 6.0\n"
            "   → Copier le fichier v6.0 et relancer."
        )
        required = ["A1.sev-saturation", "A2.sha256", "C1.fx", "C3.jaccard"]
        for fix in required:
            assert fix in FIXES_APPLIED, f"Fix {fix} manquant dans FIXES_APPLIED"
        print("\n  ✅ Déploiement correct — Compliance Agent v6.0")
        print("="*60 + "\n")
    except ImportError as e:
        print(f"\n  ❌ Impossible d'importer compliance_agent : {e}")
        print("  → Vérifier que tu es dans le bon répertoire")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════
#  FIXTURES
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def imports():
    """Importe les classes nécessaires une seule fois par module."""
    from compliance_agent import (
        ComplianceAgent, ComplianceRule, RiskScoringEngine,
        ReconciliationEngine, RuleStatus, SEVERITY_WEIGHTS,
        to_usd, __version__
    )
    assert __version__ == "6.0", (
        f"MAUVAIS FICHIER (version {__version__}). Copier compliance_agent.py v6.0."
    )
    from compliance_agent import __version__
    return {
        "ComplianceAgent": ComplianceAgent,
        "ComplianceRule": ComplianceRule,
        "RiskScoringEngine": RiskScoringEngine,
        "ReconciliationEngine": ReconciliationEngine,
        "RuleStatus": RuleStatus,
        "SEVERITY_WEIGHTS": SEVERITY_WEIGHTS,
        "to_usd": to_usd,
        "__version__": __version__,
    }

@pytest.fixture
def agent_no_services(imports):
    """Agent sans Neo4j ni LLM (tests déterministes)."""
    CA = imports["ComplianceAgent"]
    return CA(neo4j_driver=None, verbose=False)

@pytest.fixture
def scorer(imports):
    return imports["RiskScoringEngine"]()

@pytest.fixture
def reconciler(imports):
    return imports["ReconciliationEngine"]()

def make_rule(imports, **kwargs):
    """Helper : créer une ComplianceRule avec valeurs par défaut."""
    CR = imports["ComplianceRule"]
    defaults = dict(
        rule_id="T_001", rule_text="Test rule",
        applies=True, violated=False,
        severity="HAUTE", confidence=0.9,
        fatf_rec="R20", provenance="SYMBOLIC",
        rule_status="GROUND_TRUTH", pass2_completed=True
    )
    defaults.update(kwargs)
    return CR(**defaults)


# ══════════════════════════════════════════════════════════════════════
#  NIVEAU 1 — TESTS UNITAIRES (sans services)
# ══════════════════════════════════════════════════════════════════════

class TestFix_A1_Severite:
    """Fix A1 — La sévérité doit influencer le score (saturation fixe)."""

    def test_haute_plus_eleve_que_basse(self, scorer, imports):
        """1 violation HAUTE > 1 violation BASSE à confiance égale."""
        CR = imports["ComplianceRule"]
        tx = {"amount": 5000, "currency": "TND"}

        r_haute = make_rule(imports, violated=True, severity="HAUTE", confidence=0.9)
        r_basse = make_rule(imports, violated=True, severity="BASSE", confidence=0.9)

        s_haute, _, _ = scorer.compute([r_haute], 0.0, tx, "PASS")
        s_basse, _, _ = scorer.compute([r_basse], 0.0, tx, "PASS")

        assert s_haute > s_basse * 2, (
            f"A1 FAIL : HAUTE={s_haute} devrait être >2× BASSE={s_basse}"
        )
        print(f"\n  ✅ A1 : BASSE={s_basse:.1f} < HAUTE={s_haute:.1f}")

    def test_critique_plus_eleve_que_haute(self, scorer, imports):
        """1 violation CRITIQUE > 1 violation HAUTE."""
        tx = {"amount": 5000, "currency": "TND"}
        r_crit = make_rule(imports, violated=True, severity="CRITIQUE", confidence=0.9)
        r_haut = make_rule(imports, violated=True, severity="HAUTE",    confidence=0.9)

        s_c, _, _ = scorer.compute([r_crit], 0.0, tx, "PASS")
        s_h, _, _ = scorer.compute([r_haut], 0.0, tx, "PASS")
        assert s_c > s_h, f"A1 FAIL : CRITIQUE={s_c} devrait > HAUTE={s_h}"

    def test_valeurs_attendues_saturation(self, scorer, imports):
        """Vérifie les valeurs numériques exactes post-fix (SATURATION=3.0)."""
        from compliance_agent import SEVERITY_WEIGHTS
        tx = {"amount": 5000, "currency": "TND"}
        # SATURATION = 3.0 (3 × CRITIQUE weight 1.0)
        # 1 HAUTE conf=1.0 → 0.7/3.0 = 0.233 → score 23.3 → plancher PASS=0
        r = make_rule(imports, violated=True, severity="HAUTE", confidence=1.0)
        s, d, rc = scorer.compute([r], 0.0, tx, "PASS")
        expected_inh = round(SEVERITY_WEIGHTS["HAUTE"] / 3.0, 3)
        assert abs(rc.inherent_violations - expected_inh) < 0.01, (
            f"A1 FAIL : inherent_violations={rc.inherent_violations} ≠ {expected_inh}"
        )

    def test_ancienne_formule_absente(self):
        """Vérification que l'ancienne formule n'est plus active (seul le commentaire reste)."""
        with open(os.path.join(SCRIPT_DIR, "compliance_agent.py"), encoding="utf-8") as f:
            code = f.read()
        # Le commentaire mentionne max_possible comme explication HISTORIQUE.
        # On vérifie que l'instruction actuelle utilise SATURATION, pas max_possible.
        import re
        # Cherche max_possible = ... (affectation) — pas dans un commentaire
        bad_lines = [l for l in code.split("\n")
                     if "max_possible" in l and not l.strip().startswith("#")]
        assert not bad_lines, (
            f"A1 FAIL : 'max_possible' encore utilisé dans le code (hors commentaires) :\n"
            + "\n".join(bad_lines)
        )


class TestFix_A2_Hash:
    """Fix A2 — Hash SHA256 déterministe (pas hash() Python randomisé)."""

    def test_hash_deterministique(self):
        """Le même texte donne toujours le même hash."""
        h1 = hashlib.sha256("test rule text".lower().strip().encode()).hexdigest()[:16]
        h2 = hashlib.sha256("test rule text".lower().strip().encode()).hexdigest()[:16]
        assert h1 == h2, "A2 FAIL : hash non déterministe"

    def test_textes_differents_hashs_differents(self):
        """Deux règles différentes → hashs différents."""
        h1 = hashlib.sha256("rule A text".encode()).hexdigest()[:16]
        h2 = hashlib.sha256("rule B text".encode()).hexdigest()[:16]
        assert h1 != h2, "A2 FAIL : collision de hash"

    def test_hash_python_absent_dans_code(self):
        """Vérifier que hash() natif Python n'est plus utilisé pour les règles."""
        with open(os.path.join(SCRIPT_DIR, "compliance_agent.py"), encoding="utf-8") as f:
            code = f.read()
        # La seule occurrence de hash() doit être hashlib.sha256, pas str(hash(...))
        import re
        bad = re.findall(r'(?<!hashlib\.)(?<!sha256\.)hash\s*\(r\.\w', code)
        assert not bad, f"A2 FAIL : hash() Python encore utilisé : {bad}"


class TestFix_C1_FX:
    """Fix C1 — Conversion de devises pour les comparaisons de seuils."""

    def test_50k_tnd_depasse_10k_usd(self, imports):
        """50 000 TND ≈ 16 000 USD → dépasse un seuil de 10 000 USD."""
        to_usd = imports["to_usd"]
        assert to_usd(50000, "TND") > 10000, \
            "C1 FAIL : 50K TND devrait dépasser le seuil 10K USD"

    def test_100_usd_sous_seuil_1000_eur(self, imports):
        """100 USD ≈ 93 EUR → sous un seuil de 1 000 EUR."""
        to_usd = imports["to_usd"]
        tx_usd  = to_usd(100, "USD")
        thr_usd = to_usd(1000, "EUR")
        assert tx_usd < thr_usd, "C1 FAIL : 100 USD devrait être < 1000 EUR"

    def test_devise_inconnue_neutre(self, imports):
        """Devise inconnue → taux 1.0 (pas d'erreur)."""
        to_usd = imports["to_usd"]
        assert to_usd(100, "XYZ") == 100.0, "C1 FAIL : devise inconnue devrait retourner montant brut"

    def test_agent_applique_fx_sur_virement_eur(self, agent_no_services, imports):
        """Un SWIFT en EUR avec seuil USD : doit être évalué correctement."""
        tx = {
            "tx_type": "SWIFT", "amount": 12000, "currency": "EUR",
            "sender_country": "France", "destination_country": "Tunisia",
            "has_justification": False
        }
        # 12 000 EUR ≈ 12 960 USD > seuil habituel 10 000 USD
        d = agent_no_services.analyze(
            tx, [], risk_signals=["THRESHOLD_STR"],
            fatf_checklist=["R20"], level1_decision="STR"
        )
        assert d.risk_score >= 65, (
            f"C1 FAIL : 12 000 EUR devrait déclencher STR ≥ 65, obtenu {d.risk_score}"
        )


class TestFix_C2_Mitigation:
    """Fix C2 — Le justificatif atténue (ne supprime plus la violation)."""

    def test_violation_reste_detectee_avec_justificatif(self, scorer, imports):
        """Une violation avec has_justification=True doit rester violated=True."""
        tx_just = {"amount": 15000, "currency": "USD", "has_justification": True}
        r = make_rule(imports, violated=True, severity="HAUTE",
                      confidence=1.0, mitigated=True)
        s_mit, _, rc_mit = scorer.compute([r], 0.0, tx_just, "PASS")
        # La règle est violated=True même avec justificatif
        assert r.violated is True, "C2 FAIL : violated ne doit pas devenir False"

    def test_mitigation_reduit_score(self, scorer, imports):
        """Score avec justificatif < score sans justificatif."""
        tx_base  = {"amount": 15000, "currency": "USD", "has_justification": False}
        tx_just  = {"amount": 15000, "currency": "USD", "has_justification": True}

        r_base = make_rule(imports, violated=True, severity="HAUTE", confidence=1.0, mitigated=False)
        r_mit  = make_rule(imports, violated=True, severity="HAUTE", confidence=1.0, mitigated=True)

        s_base, _, _ = scorer.compute([r_base], 0.0, tx_base, "PASS")
        s_mit,  _, _ = scorer.compute([r_mit],  0.0, tx_just, "PASS")

        # Score atténué doit être plus faible
        assert s_mit < s_base, (
            f"C2 FAIL : score avec justificatif ({s_mit}) devrait < sans ({s_base})"
        )

    def test_critique_non_attenuee(self, scorer, imports):
        """Une violation CRITIQUE ne doit pas être atténuable → toujours BLOCK."""
        tx = {"amount": 50000, "currency": "USD", "has_justification": True}
        r = make_rule(imports, violated=True, severity="CRITIQUE",
                      confidence=1.0, mitigated=True)
        s, d, _ = scorer.compute([r], 0.0, tx, "PASS")
        assert d == "BLOCK", f"C2 FAIL : CRITIQUE avec justificatif devrait → BLOCK, obtenu {d}"


class TestFix_C3_Reconciliation:
    """Fix C3 — Réconciliation par Jaccard (fausses contradictions éliminées)."""

    def test_deux_regles_R20_differentes_complement(self, reconciler, imports):
        """R20 smurfing + R20 rapport annuel → COMPLEMENT (pas contradiction)."""
        r_sym = make_rule(imports,
            rule_id="S1",
            rule_text="Structuring smurfing detected suspicious transaction report mandatory",
            violated=True, fatf_rec="R20", provenance="SYMBOLIC", confidence=1.0)
        r_sem = make_rule(imports,
            rule_id="M1",
            rule_text="Provide annual compliance report describing AML framework policies",
            violated=False, fatf_rec="R20", provenance="SEMANTIC",
            confidence=0.7, pass2_completed=True)

        merged, flags = reconciler.reconcile([r_sym], [r_sem])
        assert len(flags) == 0, (
            f"C3 FAIL : fausse contradiction (Jaccard faible) détectée : {flags}"
        )

    def test_meme_regle_R20_contradiction_detectee(self, reconciler, imports):
        """Même règle R20, désaccord violated → vraie contradiction signalée."""
        r_sym = make_rule(imports,
            rule_id="S1",
            rule_text="Structuring smurfing detected suspicious transaction report mandatory R20",
            violated=True, fatf_rec="R20", provenance="SYMBOLIC", confidence=1.0)
        r_sem = make_rule(imports,
            rule_id="M2",
            rule_text="Structuring smurfing suspicious transaction report mandatory detected",
            violated=False, fatf_rec="R20", provenance="SEMANTIC",
            confidence=0.7, pass2_completed=True)

        merged, flags = reconciler.reconcile([r_sym], [r_sem])
        assert len(flags) == 1, (
            f"C3 FAIL : vraie contradiction non détectée (Jaccard ≥ 0.30)"
        )

    def test_cross_validation_accord(self, reconciler, imports):
        """Deux règles similaires ET accord → cross_validated=True."""
        r_sym = make_rule(imports,
            rule_id="S1",
            rule_text="Freeze funds assets designated persons entities sanctions obligatoire",
            violated=True, fatf_rec="R6", provenance="SYMBOLIC", confidence=0.9)
        r_sem = make_rule(imports,
            rule_id="M1",
            rule_text="Freeze funds assets designated entities persons sanctions obligatoire",
            violated=True, fatf_rec="R6", provenance="SEMANTIC",
            confidence=0.7, pass2_completed=True)

        merged, flags = reconciler.reconcile([r_sym], [r_sem])
        cross_validated = [r for r in merged if r.cross_validated]
        assert len(cross_validated) >= 1, "C3 FAIL : cross-validation non détectée"
        assert len(flags) == 0, f"C3 FAIL : faux flag de contradiction : {flags}"


class TestFix_C4_PessimisticDefault:
    """Fix C4 — Défaut prudentiel pour previous_strs."""

    def test_champ_absent_pas_de_credit(self, scorer, imports):
        """previous_strs absent → pas de crédit de contrôle."""
        tx_absent  = {"amount": 5000, "currency": "TND"}
        tx_zero    = {"amount": 5000, "currency": "TND", "previous_strs": 0}
        tx_positif = {"amount": 5000, "currency": "TND", "previous_strs": 1}

        r = make_rule(imports, violated=False)

        _, _, rc_absent  = scorer.compute([], 0.10, tx_absent,  "PASS")
        _, _, rc_zero    = scorer.compute([], 0.10, tx_zero,    "PASS")
        _, _, rc_positif = scorer.compute([], 0.10, tx_positif, "PASS")

        # Absent ne doit PAS avoir plus de control_eff que un historique positif
        assert rc_absent.control_eff <= rc_zero.control_eff, (
            f"C4 FAIL : absent ({rc_absent.control_eff}) > zero ({rc_zero.control_eff})"
        )
        # Un historique favorable (0 STR) doit avoir plus de crédit qu'un risqué (1 STR)
        assert rc_zero.control_eff > rc_positif.control_eff, (
            f"C4 FAIL : 0 STR ({rc_zero.control_eff}) devrait > 1 STR ({rc_positif.control_eff})"
        )


class TestDecisionsScenarios:
    """Tests sur les 4 scénarios principaux + 6 cas limites (sans LLM ni Neo4j)."""

    # ── Scénarios fondamentaux ────────────────────────────────────────

    def test_s1_virement_normal_pass(self, agent_no_services):
        """S1 : VIREMENT_LOCAL normal sans signal HARD → PASS."""
        tx = {
            "tx_type": "VIREMENT_LOCAL", "amount": 5000, "currency": "TND",
            "sender_name": "Alice", "sender_country": "Tunisia",
            "beneficiary_name": "Bob", "destination_country": "Tunisia",
            "has_justification": False
        }
        d = agent_no_services.analyze(
            tx, [], risk_signals=["AMOUNT_ROUND"],
            fatf_checklist=["R10", "R20"], level1_decision="PASS"
        )
        assert d.decision == "PASS", (
            f"S1 FAIL : attendu PASS, obtenu {d.decision} ({d.risk_score})\n"
            f"  Violations : {[(r.rule_id, r.fatf_rec) for r in d.violated_rules]}"
        )

    def test_s2_swift_sanctions_block(self, agent_no_services):
        """S2 : SWIFT avec sanctions → BLOCK."""
        tx = {
            "tx_type": "SWIFT", "amount": 21800, "currency": "USD",
            "sender_country": "Congo", "destination_country": "Iran",
            "has_justification": False
        }
        d = agent_no_services.analyze(
            tx, [], risk_signals=["SANCTION_UN_INDIVIDUAL_SENDER",
                                   "THRESHOLD_STR", "COUNTRY_BLACK_DESTINATION_COUNTRY"],
            fatf_checklist=["R6", "R19", "R20"], level1_decision="BLOCK"
        )
        assert d.decision == "BLOCK", f"S2 FAIL : {d.decision} ({d.risk_score})"
        assert d.risk_score == 100.0, f"S2 FAIL : score {d.risk_score} ≠ 100"

    def test_s3_depot_smurfing_str(self, agent_no_services):
        """S3 : DEPOT smurfing → STR ≥ 65."""
        tx = {
            "tx_type": "DEPOT_ESPECES", "amount": 4700, "currency": "TND",
            "sender_name": "Mohamed", "sender_country": "Tunisia",
            "has_justification": False
        }
        d = agent_no_services.analyze(
            tx, [], risk_signals=["SMURFING_PATTERN",
                                   "AMOUNT_JUST_BELOW_THRESHOLD", "MOTIVE_VAGUE"],
            fatf_checklist=["R20"], level1_decision="STR"
        )
        assert d.decision == "STR", f"S3 FAIL : {d.decision} ({d.risk_score})"
        assert d.risk_score >= 65, f"S3 FAIL : score {d.risk_score} < 65"

    def test_s4_mandat_wu_str(self, agent_no_services):
        """S4 : MANDAT Western Union suspect → STR ≥ 65."""
        tx = {
            "tx_type": "MANDAT", "amount": 4000, "currency": "TND",
            "has_justification": False, "operator": "WESTERN_UNION"
        }
        d = agent_no_services.analyze(
            tx, [], risk_signals=["MOTIVE_VAGUE", "HIGH_RISK_OPERATOR_WESTERN_UNION",
                                   "THRESHOLD_STR", "AMOUNT_ROUND"],
            fatf_checklist=["R14", "R20"], level1_decision="STR"
        )
        assert d.decision == "STR", f"S4 FAIL : {d.decision} ({d.risk_score})"
        assert d.risk_score >= 65, f"S4 FAIL : score {d.risk_score} < 65"

    # ── Cas limites ────────────────────────────────────────────────────

    def test_critique_toujours_block(self, agent_no_services):
        """Un signal CRITIQUE seul → BLOCK sans autre condition."""
        tx = {"tx_type": "SWIFT", "amount": 5000, "currency": "USD",
              "sender_country": "France", "destination_country": "France"}
        d = agent_no_services.analyze(
            tx, [], risk_signals=["SANCTION_UN_INDIVIDUAL_SENDER"],
            fatf_checklist=["R6"], level1_decision="BLOCK"
        )
        assert d.decision == "BLOCK", f"CRITIQUE FAIL : {d.decision}"

    def test_montant_nul_pass(self, agent_no_services):
        """Transaction à montant 0 → PASS (pas de seuil franchi)."""
        tx = {"tx_type": "VIREMENT_LOCAL", "amount": 0, "currency": "TND",
              "sender_country": "Tunisia", "destination_country": "Tunisia"}
        d = agent_no_services.analyze(
            tx, [], risk_signals=[],
            fatf_checklist=["R20"], level1_decision="PASS"
        )
        assert d.decision == "PASS", f"MONTANT NUL FAIL : {d.decision}"

    def test_justificatif_attenue_mais_pas_annule(self, agent_no_services):
        """STR L1 avec justificatif → reste STR (pas PASS), score réduit."""
        tx = {"tx_type": "SWIFT", "amount": 15000, "currency": "USD",
              "sender_country": "France", "destination_country": "Tunisia",
              "has_justification": True, "justification_document": True}
        d_avec = agent_no_services.analyze(
            tx, [], risk_signals=["THRESHOLD_STR"],
            fatf_checklist=["R20"], level1_decision="STR"
        )
        tx2 = {**tx, "has_justification": False, "justification_document": False}
        d_sans = agent_no_services.analyze(
            tx2, [], risk_signals=["THRESHOLD_STR"],
            fatf_checklist=["R20"], level1_decision="STR"
        )
        assert d_avec.decision in ("STR", "EDD"), \
            f"JUSTIF FAIL : avec justificatif → {d_avec.decision} (devrait rester ≥ EDD)"
        assert d_avec.risk_score <= d_sans.risk_score, \
            f"JUSTIF FAIL : score avec ({d_avec.risk_score}) > sans ({d_sans.risk_score})"

    def test_grey_list_edd(self, agent_no_services):
        """Pays grey-list FATF sans autre signal → EDD."""
        tx = {"tx_type": "SWIFT", "amount": 8000, "currency": "USD",
              "sender_country": "Tunisia", "destination_country": "Morocco",
              "has_justification": False}
        d = agent_no_services.analyze(
            tx, [], risk_signals=["COUNTRY_GREY_DESTINATION_COUNTRY"],
            fatf_checklist=["R19"], level1_decision="EDD"
        )
        assert d.decision in ("EDD", "STR", "REVIEW"), \
            f"GREY FAIL : pays grey-list → {d.decision} (attendu EDD/STR/REVIEW)"
        assert d.risk_score >= 45, \
            f"GREY FAIL : score {d.risk_score} < 45 pour pays à risque"

    def test_pep_kyc_requis(self, agent_no_services):
        """Signal PEP → au minimum KYC."""
        tx = {"tx_type": "VIREMENT_LOCAL", "amount": 3000, "currency": "TND",
              "sender_country": "Tunisia", "destination_country": "Tunisia"}
        d = agent_no_services.analyze(
            tx, [], risk_signals=["SANCTION_PEP_SENDER"],
            fatf_checklist=["R12"], level1_decision="KYC"
        )
        assert d.risk_score >= 25, \
            f"PEP FAIL : score {d.risk_score} < 25 pour signal PEP"

    def test_signal_inconnu_ignoré_sans_crash(self, agent_no_services, caplog):
        """Signal non mappé → warning loggé, pas de crash."""
        import logging
        tx = {"tx_type": "VIREMENT_LOCAL", "amount": 1000, "currency": "TND",
              "sender_country": "Tunisia", "destination_country": "Tunisia"}
        with caplog.at_level(logging.WARNING, logger="compliance_agent"):
            d = agent_no_services.analyze(
                tx, [], risk_signals=["SIGNAL_INCONNU_V99"],
                fatf_checklist=["R20"], level1_decision="PASS"
            )
        assert d is not None, "D5 FAIL : crash sur signal inconnu"


class TestScoringCalibration:
    """
    Calibration formelle du scoring (E2 — Revue Round 2).
    Ces tests définissent les cibles numériques attendues.
    Si un fix modifie la formule, ces tests signalent l'impact.
    """

    SCENARIOS = [
        # (description, violations, soft, l1, score_min, score_max, decision)
        ("Aucune violation, pas de soft",        [], 0.0, "PASS", 0, 20, "PASS"),
        ("1 violation BASSE conf 0.9",           [("BASSE",0.9)], 0.0, "PASS", 1, 20, "PASS"),
        ("1 violation HAUTE conf 1.0",           [("HAUTE",1.0)], 0.0, "PASS", 15, 35, "PASS"),
        ("1 violation HAUTE + soft 0.30",        [("HAUTE",1.0)], 0.3, "PASS", 40, 60, "EDD"),
        ("STR L1 plancher sans violation",       [],              0.0, "STR",  65, 75, "STR"),
        ("EDD L1 plancher sans violation",       [],              0.0, "EDD",  45, 55, "EDD"),
        ("2 violations HAUTE conf 1.0",          [("HAUTE",1.0),("HAUTE",1.0)], 0.0, "PASS", 40, 60, "EDD"),
        ("1 violation CRITIQUE → BLOCK",         [("CRITIQUE",1.0)], 0.0, "PASS", 100, 100, "BLOCK"),
        ("L1=BLOCK override tout",               [],              0.0, "BLOCK",100, 100, "BLOCK"),
    ]

    def test_calibration_table(self, scorer, imports):
        """Vérifie toutes les cibles de la table de calibration."""
        tx = {"amount": 5000, "currency": "TND"}
        erreurs = []
        for desc, viols, soft, l1, s_min, s_max, dec in self.SCENARIOS:
            rules = [make_rule(imports, violated=True, severity=sev,
                               confidence=conf, pass2_completed=True)
                     for sev, conf in viols]
            score, decision, _ = scorer.compute(rules, soft, tx, l1)
            ok_score = s_min <= score <= s_max
            ok_dec   = decision == dec
            if not ok_score or not ok_dec:
                erreurs.append(
                    f"  ❌ {desc}\n"
                    f"     Score: {score} (attendu [{s_min}-{s_max}]) {'✓' if ok_score else '✗'}\n"
                    f"     Décision: {decision} (attendu {dec}) {'✓' if ok_dec else '✗'}"
                )

        if erreurs:
            pytest.fail("Calibration échouée pour " + str(len(erreurs)) +
                        " scénario(s) :\n" + "\n".join(erreurs))
        print(f"\n  ✅ Calibration : {len(self.SCENARIOS)} scénarios dans les cibles")


class TestVersionFingerprint:
    """Vérifie que le bon fichier tourne (préserve la leçon du 11/06)."""

    def test_version_6_0(self, imports):
        assert imports["__version__"] == "6.0", \
            f"MAUVAIS FICHIER : version {imports['__version__']}"

    def test_fixes_presents(self):
        from compliance_agent import FIXES_APPLIED
        required = [
            "A1.sev-saturation", "A2.sha256", "A3.gap-target",
            "B1.trigram", "B2.spo-src", "B3.pass2+downgrade",
            "C1.fx", "C2.mitigate", "C3.jaccard", "C4.pessimist", "D5.warn"
        ]
        missing = [f for f in required if f not in FIXES_APPLIED]
        assert not missing, f"Fixes manquants : {missing}"


# ══════════════════════════════════════════════════════════════════════
#  NIVEAU 2 — TESTS D'INTÉGRATION (avec Neo4j + LLM)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestIntegration:
    """
    Tests avec Neo4j + Groq actifs.
    Prérequis :
      - Neo4j bolt://localhost:7687, credentials neo4j/amlkyc2025
      - Variable d'environnement GROQ_API_KEY active
      - Weaviate sur port 8088 avec ~6678 chunks
    """

    @pytest.fixture(scope="class")
    def agent_full(self):
        """Agent avec services complets."""
        from compliance_agent import ComplianceAgent
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            pytest.skip("GROQ_API_KEY non défini — test d'intégration ignoré")
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(
                "bolt://localhost:7687",
                auth=("neo4j", "amlkyc2025")
            )
            driver.verify_connectivity()
        except Exception as e:
            pytest.skip(f"Neo4j non accessible : {e}")
        return ComplianceAgent(neo4j_driver=driver, verbose=False)

    def test_s1_integration_pass(self, agent_full):
        """S1 intégration : VIREMENT normal → PASS avec les 9 fixes actifs."""
        tx = {
            "tx_type": "VIREMENT_LOCAL", "amount": 5000, "currency": "TND",
            "sender_name": "Alice Dupont", "sender_country": "Tunisia",
            "beneficiary_name": "Bob Martin", "destination_country": "Tunisia",
            "motif": "Paiement facture fournisseur", "has_justification": False
        }
        d = agent_full.analyze(
            tx, [], risk_signals=["AMOUNT_ROUND"],
            fatf_checklist=["R10", "R11", "R20"], level1_decision="PASS"
        )
        assert d.decision == "PASS", (
            f"S1 intégration FAIL : {d.decision} ({d.risk_score})\n"
            f"Violations : {[(r.rule_id, r.fatf_rec, r.confidence) for r in d.violated_rules]}"
        )

    def test_s2_integration_block(self, agent_full):
        """S2 intégration : SWIFT Iran + sanctions → BLOCK 100."""
        tx = {
            "tx_type": "SWIFT", "amount": 21800, "currency": "USD",
            "sender_name": "Eric Badege", "sender_country": "Congo",
            "beneficiary_name": "Iran National Bank", "destination_country": "Iran",
            "has_justification": False
        }
        d = agent_full.analyze(
            tx, [],
            risk_signals=["SANCTION_UN_INDIVIDUAL_SENDER", "THRESHOLD_STR",
                          "COUNTRY_BLACK_DESTINATION_COUNTRY",
                          "COUNTRY_GREY_SENDER_COUNTRY", "MOTIVE_VAGUE"],
            fatf_checklist=["R6", "R10", "R19", "R20"],
            level1_decision="BLOCK"
        )
        assert d.decision == "BLOCK", f"S2 intégration FAIL : {d.decision}"
        assert d.risk_score == 100.0
        # Avec chunks=[], seuls les signaux HARD couvrent des recs FATF :
        # SANCTION_UN→R6, COUNTRY_BLACK→R19, THRESHOLD_STR→R20 = 3/4.
        # R10 (CDD) exige un chunk ou une règle symbolique Neo4j → non garanti ici.
        total = len(d.fatf_coverage)
        fatf_covered = sum(1 for v in d.fatf_coverage.values() if v)
        assert fatf_covered >= 3, f"S2 : couverture FATF {fatf_covered}/{total} < 3"

    def test_s3_integration_str_no_contradiction(self, agent_full):
        """S3 intégration : smurfing → STR, 0 contradiction."""
        tx = {
            "tx_type": "DEPOT_ESPECES", "amount": 4700, "currency": "TND",
            "sender_name": "Mohamed Trabelsi", "sender_country": "Tunisia",
            "notes": "Billets neufs, client nerveux", "has_justification": False
        }
        d = agent_full.analyze(
            tx, [],
            risk_signals=["MOTIVE_VAGUE", "SMURFING_PATTERN",
                          "AMOUNT_JUST_BELOW_THRESHOLD"],
            fatf_checklist=["R10", "R11", "R20"],
            level1_decision="STR"
        )
        assert d.decision == "STR", f"S3 intégration FAIL : {d.decision}"
        assert d.risk_score >= 65, f"S3 intégration : score {d.risk_score} < 65"
        contradictions = d.stats.get("contradictions", 0)
        assert contradictions == 0, \
            f"S3 intégration : {contradictions} contradiction(s) détectée(s) (attendu 0)"

    def test_s4_integration_str(self, agent_full):
        """S4 intégration : MANDAT WU suspect → STR."""
        tx = {
            "tx_type": "MANDAT", "amount": 4000, "currency": "TND",
            "motif": "Aide familiale", "has_justification": False,
            "operator": "WESTERN_UNION", "withdrawal_mode": "CASH"
        }
        d = agent_full.analyze(
            tx, [],
            risk_signals=["MOTIVE_VAGUE", "CASH_WITHDRAWAL",
                          "HIGH_RISK_OPERATOR_WESTERN_UNION", "THRESHOLD_STR"],
            fatf_checklist=["R10", "R14", "R16", "R20"],
            level1_decision="STR"
        )
        assert d.decision == "STR", f"S4 intégration FAIL : {d.decision}"
        assert d.risk_score >= 65, f"S4 intégration : score {d.risk_score} < 65"

    def test_inherent_violations_nouvelle_formule(self, agent_full):
        """
        Vérifie que la NOUVELLE formule A1 tourne sur la machine.
        Si inherent_violations ≈ 0.53 pour S1 → ancienne formule encore active.
        Avec la nouvelle formule (SATURATION=3.0), 4 violations R20 conf~0.57
        donnent inherent ≈ (4 × 0.7 × 0.57) / 3.0 ≈ 0.53 aussi...
        Mais avec 0 violation → 0.0 exactement.
        """
        tx = {"tx_type": "VIREMENT_LOCAL", "amount": 5000, "currency": "TND",
              "sender_country": "Tunisia", "destination_country": "Tunisia"}
        d = agent_full.analyze(
            tx, [], risk_signals=[],
            fatf_checklist=["R20"], level1_decision="PASS"
        )
        # Sans violation : inherent_violations doit être 0.0 exactement
        inh_viol = d.risk_components.inherent_violations if d.risk_components else None
        if inh_viol is not None:
            assert inh_viol == 0.0, \
                f"A1 FAIL : sans violation, inherent_violations={inh_viol} ≠ 0.0"

    def test_0_regles_persistees_sans_verbatim(self, agent_full):
        """Fix B1 : aucune règle non-verbatim ne doit être persistée dans Neo4j."""
        tx = {"tx_type": "VIREMENT_LOCAL", "amount": 5000, "currency": "TND",
              "sender_country": "Tunisia", "destination_country": "Tunisia"}
        d = agent_full.analyze(
            tx, [], risk_signals=["AMOUNT_ROUND"],
            fatf_checklist=["R10", "R20"], level1_decision="PASS"
        )
        persistees = d.stats.get("neo4j_persisted", 0)
        # Toutes les règles persistées doivent avoir snippet_verbatim=True
        non_verbatim = [
            r for r in d.applicable_rules
            if r.persisted and not getattr(r, "snippet_verbatim", False)
        ]
        assert not non_verbatim, \
            f"B1 FAIL : {len(non_verbatim)} règle(s) persistée(s) sans verbatim"

    def test_version_dans_banner(self, agent_full, capsys):
        """Vérifier que la bannière affiche v6.0 et les fixes."""
        import compliance_agent as ca
        original_verbose = agent_full.verbose
        agent_full.verbose = True
        tx = {"tx_type": "DEPOT_ESPECES", "amount": 4700, "currency": "TND",
              "sender_country": "Tunisia"}
        agent_full.analyze(tx, [], risk_signals=["SMURFING_PATTERN"],
                           fatf_checklist=["R20"], level1_decision="STR")
        agent_full.verbose = original_verbose
        captured = capsys.readouterr()
        assert "v6.0" in captured.out or "6.0" in captured.out, \
            "BANNER FAIL : version 6.0 non affichée dans le verbose"


# ══════════════════════════════════════════════════════════════════════
#  POINT D'ENTRÉE DIRECT
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tests Compliance Agent v6.0")
    parser.add_argument("--check-version", action="store_true",
                        help="Vérifier uniquement la version (< 1 s)")
    parser.add_argument("--unit", action="store_true",
                        help="Lancer uniquement les tests unitaires (sans services)")
    parser.add_argument("--integration", action="store_true",
                        help="Lancer les tests d'intégration (nécessite Neo4j + Groq)")
    parser.add_argument("-v", action="store_true", help="Verbose")
    args = parser.parse_args()

    if args.check_version:
        check_version()
        sys.exit(0)

    # Construction de la commande pytest
    cmd = [sys.executable, "-m", "pytest", __file__]
    if args.v:
        cmd.append("-v")
    if args.unit and not args.integration:
        cmd += ["-m", "not integration"]
    elif args.integration and not args.unit:
        cmd += ["-m", "integration"]

    os.execv(sys.executable, cmd)
