"""
evaluate_compliance.py — Harnais d'évaluation du Compliance Agent v6.0
======================================================================
PFE AML/KYC 2025-2026 | CRISP-DM Phase 5 — Évaluation quantitative

OBJECTIF : mesurer les objectifs du PFE de manière reproductible et défendable.

4 MÉTRIQUES MESURÉES :
  1. ACCURACY DÉCISIONNELLE  — % de décisions correctes + matrice de confusion
                                (cible : ≥ 85%)
  2. COUVERTURE FATF         — % moyen de recommandations FATF couvertes par tx
                                (cible PFE : ≥ 90%)
  3. TAUX D'HALLUCINATION     — % de règles SEMANTIC non ancrées dans le corpus
                                (cible PFE : < 2%)
  4. STABILITÉ DÉCISIONNELLE  — accord inter-runs + σ(score) sur N exécutions
                                (cible : accord 100%)

USAGE :
  # Évaluation complète (nécessite Neo4j + Groq + Weaviate + Retrieval Agent)
  python evaluate_compliance.py --full

  # Évaluation décisionnelle seule (Compliance Agent + signaux L1 fournis)
  python evaluate_compliance.py

  # Avec test de stabilité (chaque scénario lancé 5×)
  python evaluate_compliance.py --full --stability 5

  # Sous-ensemble par catégorie
  python evaluate_compliance.py --category BLOCK

  # Export des résultats
  python evaluate_compliance.py --full --output resultats_eval.json --markdown rapport_eval.md

MODES :
  - Par défaut : COMPLIANCE-ONLY. Le CA est testé avec les signaux L1 des
    scénarios (chunks vides). Mesure l'accuracy décisionnelle + le scoring.
    L'hallucination est triviale (pas de sémantique sans chunks).
  - --full : PIPELINE COMPLET. Lance Retrieval pour fournir de vrais chunks au CA.
    Mesure RÉELLE de la couverture FATF et du taux d'hallucination.
"""

import sys
import os
import json
import argparse
import statistics
from datetime import datetime
from collections import defaultdict, Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from evaluation_scenarios import SCENARIOS, scenario_stats


# ══════════════════════════════════════════════════════════════════════
#  FONCTIONS DE MÉTRIQUE (pures, testables)
# ══════════════════════════════════════════════════════════════════════

DECISION_ORDER = ["PASS", "REVIEW", "KYC", "EDD", "STR", "BLOCK"]


def is_decision_correct(obtained: str, expected: str, acceptable: list = None) -> bool:
    """
    Une décision est correcte si elle correspond exactement à l'attendu,
    OU si elle figure dans la liste des décisions acceptables (cas limites
    où plusieurs décisions sont défendables réglementairement).
    """
    if obtained == expected:
        return True
    if acceptable and obtained in acceptable:
        return True
    return False


def severity_distance(d1: str, d2: str) -> int:
    """Distance ordinale entre deux décisions (0 = identique)."""
    try:
        return abs(DECISION_ORDER.index(d1) - DECISION_ORDER.index(d2))
    except ValueError:
        return 99


def compute_accuracy(results: list) -> dict:
    """
    Calcule l'accuracy décisionnelle.
    results : liste de dicts {id, expected, obtained, acceptable, correct}
    """
    n = len(results)
    if n == 0:
        return {"accuracy": 0.0, "n": 0}
    correct = sum(1 for r in results if r["correct"])
    # Accuracy stricte (match exact uniquement)
    strict = sum(1 for r in results if r["obtained"] == r["expected"])
    # Erreurs graves : distance ≥ 2 (ex. BLOCK attendu, PASS obtenu)
    severe = sum(1 for r in results
                 if not r["correct"] and severity_distance(r["obtained"], r["expected"]) >= 2)
    return {
        "accuracy":        round(correct / n, 4),
        "accuracy_strict": round(strict / n, 4),
        "n":               n,
        "correct":         correct,
        "severe_errors":   severe,
    }


def compute_confusion_matrix(results: list) -> dict:
    """Matrice de confusion attendu × obtenu."""
    matrix = defaultdict(lambda: defaultdict(int))
    for r in results:
        matrix[r["expected"]][r["obtained"]] += 1
    # Conversion en dict normal
    return {exp: dict(obt) for exp, obt in matrix.items()}


def compute_fatf_coverage(results: list) -> dict:
    """
    Couverture FATF moyenne : pour chaque scénario, % de recommandations
    de la checklist effectivement couvertes par au moins une règle.
    """
    coverages = []
    per_scenario = []
    for r in results:
        cov = r.get("fatf_coverage", {})
        if not cov:
            continue
        covered = sum(1 for v in cov.values() if v)
        total = len(cov)
        pct = covered / total if total else 0.0
        coverages.append(pct)
        per_scenario.append({"id": r["id"], "covered": covered,
                             "total": total, "pct": round(pct, 3)})
    if not coverages:
        return {"mean_coverage": None, "note": "Nécessite --full (chunks requis)"}
    return {
        "mean_coverage": round(statistics.mean(coverages), 4),
        "min_coverage":  round(min(coverages), 4),
        "max_coverage":  round(max(coverages), 4),
        "per_scenario":  per_scenario,
    }


def compute_binding_coverage(results: list) -> dict:
    """
    Couverture par SOURCE CONTRAIGNANTE (droit tunisien BCT/loi/CTAF) :
    % moyen de recs couvertes par une source contraignante, pas seulement
    par un standard international (FATF). Mesure « le système se base-t-il
    sur le droit tunisien applicable ».
    """
    covs = []
    for r in results:
        cov = r.get("binding_coverage", {})
        if not cov:
            continue
        covs.append(sum(1 for v in cov.values() if v) / len(cov))
    if not covs:
        return {"mean_coverage": None}
    return {
        "mean_coverage": round(statistics.mean(covs), 4),
        "min_coverage":  round(min(covs), 4),
        "max_coverage":  round(max(covs), 4),
    }


def compute_evidence_coverage(results: list) -> dict:
    """
    Couverture ADOSSÉE À UNE PREUVE : % de recs couvertes par une règle
    SÉMANTIQUE au snippet ancré (≠ couverture symbolique générique).

    Distingue « le corpus connaît l'obligation » (symbolique) de « le système
    a trouvé une preuve contextuelle pour CETTE transaction » (sémantique).
    """
    coverages = []
    for r in results:
        cov = r.get("evidence_coverage", {})
        if not cov:
            continue
        covered = sum(1 for v in cov.values() if v)
        total = len(cov)
        coverages.append(covered / total if total else 0.0)
    if not coverages:
        return {"mean_coverage": None,
                "note": "Aucune preuve sémantique (utiliser --stress-semantic)"}
    return {
        "mean_coverage": round(statistics.mean(coverages), 4),
        "min_coverage":  round(min(coverages), 4),
        "max_coverage":  round(max(coverages), 4),
        "n_scenarios":   len(coverages),
    }


def compute_hallucination_rate(results: list) -> dict:
    """
    Taux d'hallucination CORRIGÉ : % de règles SEMANTIC dont le snippet est
    PRÉSENT (le LLM en a produit un) mais NON ancré dans le corpus.

    DISTINCTION CRITIQUE (apprise du run quota-épuisé) :
      - Règle SANS snippet (snippet vide) = échec d'extraction LLM (429/timeout)
        → NON comptée comme hallucination (c'est une panne réseau, pas le modèle)
      - Règle AVEC snippet non trouvé dans le corpus = VRAIE hallucination
      - Règle AVEC snippet ancré (verbatim/trigramme) = correcte

    Cette définition mesure la fidélité du LLM quand il PRODUIT une preuve,
    pas la disponibilité du service.
    """
    total_with_snippet = 0     # règles où le LLM a produit un snippet
    hallucinated = 0           # snippet présent mais non ancré
    grounded_verbatim = 0
    grounded_trigram = 0
    no_snippet = 0             # échec extraction (exclu du dénominateur)
    details = []
    for r in results:
        for rule in r.get("semantic_rules_detail", []):
            has_snippet = bool(rule.get("has_snippet", rule.get("snippet_ok", False)))
            if not has_snippet and not rule.get("snippet_text"):
                # Pas de snippet → échec extraction, on EXCLUT
                no_snippet += 1
                continue
            total_with_snippet += 1
            if rule.get("snippet_verbatim", False):
                grounded_verbatim += 1
            elif rule.get("snippet_ok", False):
                grounded_trigram += 1
            else:
                hallucinated += 1
                details.append({"scenario": r["id"], "rule": rule.get("rule_id"),
                               "text": rule.get("text", "")[:60]})
    if total_with_snippet == 0:
        return {"hallucination_rate": None,
                "total_semantic_rules": 0,
                "no_snippet_extraction_failures": no_snippet,
                "note": ("Aucune règle sémantique avec snippet exploitable "
                         "(mode compliance-only, pas de gaps, ou échecs LLM)")}
    return {
        "hallucination_rate":   round(hallucinated / total_with_snippet, 4),
        "total_semantic_rules": total_with_snippet,
        "hallucinated":         hallucinated,
        "grounded_verbatim":    grounded_verbatim,
        "grounded_trigram":     grounded_trigram,
        "no_snippet_extraction_failures": no_snippet,  # exclus (pannes LLM)
        "details":              details[:10],
    }


def compute_stability(stability_runs: dict) -> dict:
    """
    Stabilité décisionnelle : pour chaque scénario lancé N fois,
    mesure l'accord décisionnel et σ(score).
    stability_runs : {scenario_id: [(decision, score), ...]}
    """
    perfect_agreement = 0
    score_stds = []
    details = []
    for sid, runs in stability_runs.items():
        decisions = [d for d, s in runs]
        scores = [s for d, s in runs]
        n_unique = len(set(decisions))
        agree = n_unique == 1
        if agree:
            perfect_agreement += 1
        std = statistics.stdev(scores) if len(scores) > 1 else 0.0
        score_stds.append(std)
        details.append({
            "id": sid, "n_runs": len(runs),
            "decisions": list(set(decisions)),
            "agreement": agree,
            "score_std": round(std, 2),
            "score_mean": round(statistics.mean(scores), 1),
        })
    n = len(stability_runs)
    return {
        "decision_agreement_rate": round(perfect_agreement / n, 4) if n else None,
        "mean_score_std":          round(statistics.mean(score_stds), 3) if score_stds else None,
        "max_score_std":           round(max(score_stds), 3) if score_stds else None,
        "scenarios_perfect":       perfect_agreement,
        "scenarios_total":         n,
        "details":                 details,
    }


# ══════════════════════════════════════════════════════════════════════
#  HARNAIS D'ÉVALUATION
# ══════════════════════════════════════════════════════════════════════

class EvaluationHarness:
    def __init__(self, full_pipeline=False, verbose=False, build_pipeline=None,
                 dual_mode=False):
        self.full = full_pipeline           # le run PRINCIPAL utilise-t-il des chunks ?
        self.verbose = verbose
        self.dual_mode = dual_mode          # mode production 3 zones (force sémantique contextuel)
        self.agent = None
        self.pipeline = None
        # On construit le pipeline si --full OU si le stress/dual en a besoin
        self._want_pipeline = full_pipeline if build_pipeline is None else build_pipeline
        self._init_components()

    def _init_components(self):
        """Initialise le Compliance Agent (+ pipeline si requis)."""
        from compliance_agent import ComplianceAgent, __version__
        assert __version__ == "6.0", f"Mauvais fichier (v{__version__})"

        # Neo4j (optionnel mais recommandé)
        driver = None
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver("bolt://localhost:7687",
                                          auth=("neo4j", "amlkyc2025"))
            driver.verify_connectivity()
            print("  ✅ Neo4j connecté")
        except Exception as e:
            print(f"  ⚠️  Neo4j indisponible ({e}) — chemin symbolique limité")

        self.agent = ComplianceAgent(neo4j_driver=driver, verbose=self.verbose)
        self.driver = driver          # conservé pour fermeture propre
        self._retriever = None

        if self._want_pipeline:
            self.pipeline = self._build_pipeline()

    def close(self):
        """Ferme proprement Neo4j + le pipeline Weaviate (évite les ResourceWarning)."""
        for obj_name in ("driver", "_retriever"):
            obj = getattr(self, obj_name, None)
            if obj is not None:
                try: obj.close()
                except Exception: pass

    def _build_pipeline(self):
        """
        Pipeline de retrieval EXACTEMENT calqué sur test_integration.py de Mariem :
          - planner.plan(planning_tx, risk_signals, customer_history) → PlanningResult
          - PlanningResult.questions : list[str]
          - retriever.retrieve(query=, tx_type=, jurisdiction=, skip_cache=, top_k=)
          - planner.reflect(transaction=, questions_asked=, chunks_retrieved=) → round 2
        Retourne retrieve_chunks(tx, signals, checklist) → list[chunks]
        """
        try:
            from planning_agent import PlanningAgent
            from retrieval_agent import RetrievalPipeline
            planner   = PlanningAgent(verbose=False)
            retriever = RetrievalPipeline(verbose=False)
            self._retriever = retriever   # conservé pour fermeture propre

            def _infer_jurisdiction(tx):
                # Domestique si Tunisie↔Tunisie, sinon international
                sc = (tx.get("sender_country") or "").lower()
                dc = (tx.get("destination_country") or "").lower()
                if "tunis" in sc and "tunis" in dc:
                    return "Tunisia"
                return "International"

            def retrieve_chunks(tx, signals, checklist):
                tx_type      = tx.get("tx_type", "SWIFT")
                jurisdiction = _infer_jurisdiction(tx)
                all_chunks   = []

                # 1. Planning
                try:
                    plan = planner.plan(tx, signals, None)
                    questions = list(getattr(plan, "questions", []) or [])
                except Exception as e:
                    if self.verbose:
                        print(f"     planning échoué : {e}")
                    return []

                # 2. Retrieval Round 1
                for q in questions:
                    try:
                        chunks = retriever.retrieve(
                            query=q, tx_type=tx_type, jurisdiction=jurisdiction,
                            skip_cache=True, top_k=5)
                        all_chunks.extend(chunks or [])
                    except Exception:
                        continue

                # 3. Reflection + Round 2 (gaps)
                try:
                    reflection = planner.reflect(
                        transaction=tx,
                        questions_asked=questions,
                        chunks_retrieved=all_chunks)
                    if (not getattr(reflection, "is_sufficient", True)
                            and getattr(reflection, "new_questions", None)):
                        for q in reflection.new_questions:
                            try:
                                chunks = retriever.retrieve(
                                    query=q, tx_type=tx_type,
                                    jurisdiction=jurisdiction,
                                    skip_cache=True, top_k=5)
                                all_chunks.extend(chunks or [])
                            except Exception:
                                continue
                except Exception:
                    pass

                # Dédoublonnage par (source_doc, article_ref)
                seen, unique = set(), []
                for c in all_chunks:
                    sd = getattr(c, "source_doc", None) or (
                        c.get("source_doc") if isinstance(c, dict) else None)
                    ar = getattr(c, "article_ref", None) or (
                        c.get("article_ref") if isinstance(c, dict) else None)
                    key = (sd, ar)
                    if key not in seen:
                        seen.add(key)
                        unique.append(c)
                return unique

            print("  ✅ Pipeline retrieval construit (PlanningAgent + RetrievalPipeline)")
            return retrieve_chunks
        except Exception as e:
            import traceback
            print(f"  ⚠️  Pipeline retrieval indisponible ({e})")
            if self.verbose:
                traceback.print_exc()
            print("     → Mode compliance-only : couverture/hallucination non mesurées")
            return None

    def _analyze_scenario(self, scenario, force_semantic: bool = False):
        """Lance l'analyse d'un scénario, retourne le résultat structuré.

        force_semantic : mode --stress-semantic. Force le chemin LLM sur toutes
        les recs de la checklist (même couvertes symboliquement) → rend
        l'hallucination mesurable + distingue couverture générique vs preuve.
        """
        tx = scenario["transaction"]
        signals = scenario["risk_signals"]
        checklist = scenario["fatf_checklist"]
        l1 = scenario["level1_decision"]

        # Récupération des chunks : si --full (run principal) OU si stress forcé
        chunks = []
        if (self.full or force_semantic) and self.pipeline:
            try:
                chunks = self.pipeline(tx, signals, checklist)
            except Exception as e:
                if self.verbose:
                    print(f"     retrieval échoué pour {scenario['id']}: {e}")

        checklist_ids = [rec.rec_id if hasattr(rec, "rec_id") else str(rec)
                         for rec in checklist]
        force = set(checklist_ids) if force_semantic else None
        decision = self.agent.analyze(tx, chunks, signals, checklist, l1,
                                      force_gap_recs=force,
                                      dual_mode=self.dual_mode)

        # Couverture ADOSSÉE À UNE PREUVE : une rec n'est "evidence-covered"
        # que si une règle SÉMANTIQUE/RÉCONCILIÉE au snippet ancré la couvre
        # (≠ couverture symbolique générique qui atteste juste l'obligation).
        evidence_cov = {}
        for rid in checklist_ids:
            evidence_cov[rid] = any(
                r.fatf_rec == rid
                and getattr(r, "provenance", "") in ("SEMANTIC", "RECONCILED")
                and getattr(r, "snippet_ok", False)
                for r in decision.applicable_rules
            )

        # Extraction des détails des règles sémantiques (pour hallucination)
        semantic_detail = []
        for r in decision.applicable_rules:
            if getattr(r, "provenance", "") in ("SEMANTIC", "RECONCILED"):
                snippet_text = getattr(r, "source_snippet", "") or ""
                semantic_detail.append({
                    "rule_id": r.rule_id,
                    "text": r.rule_text[:80],
                    "has_snippet": len(snippet_text.strip()) > 10,
                    "snippet_text": snippet_text[:40],
                    "snippet_ok": getattr(r, "snippet_ok", False),
                    "snippet_verbatim": getattr(r, "snippet_verbatim", False),
                    "confidence": r.confidence,
                })

        return {
            "id": scenario["id"],
            "category": scenario["category"],
            "expected": scenario["expected"],
            "obtained": decision.decision,
            "acceptable": scenario.get("acceptable"),
            "correct": is_decision_correct(
                decision.decision, scenario["expected"],
                scenario.get("acceptable")),
            "risk_score": decision.risk_score,
            "fatf_coverage": decision.fatf_coverage,
            "binding_coverage": decision.binding_coverage,
            "evidence_coverage": evidence_cov,
            "n_violations": len(decision.violated_rules),
            "n_semantic": decision.stats.get("semantic_rules", 0),
            "contradictions": decision.stats.get("contradictions", 0),
            "semantic_rules_detail": semantic_detail,
            "justification": scenario.get("justification", ""),
        }

    def run(self, scenarios, stability_n=1):
        """Lance l'évaluation complète."""
        print(f"\n{'='*70}")
        print(f"  ÉVALUATION COMPLIANCE AGENT v6.0 — {len(scenarios)} scénarios")
        print(f"  Mode : {'PIPELINE COMPLET' if self.full else 'COMPLIANCE-ONLY'}")
        if stability_n > 1:
            print(f"  Stabilité : {stability_n} runs par scénario")
        print(f"{'='*70}\n")

        results = []
        stability_runs = defaultdict(list)

        for i, sc in enumerate(scenarios, 1):
            # Premier run = run de référence pour les métriques
            res = self._analyze_scenario(sc)
            results.append(res)
            stability_runs[sc["id"]].append((res["obtained"], res["risk_score"]))

            mark = "✅" if res["correct"] else "❌"
            print(f"  [{i:2d}/{len(scenarios)}] {mark} {sc['id']:<10} "
                  f"attendu={res['expected']:<6} obtenu={res['obtained']:<6} "
                  f"({res['risk_score']:.0f}/100)")

            # Runs supplémentaires pour la stabilité
            for _ in range(stability_n - 1):
                extra = self._analyze_scenario(sc)
                stability_runs[sc["id"]].append(
                    (extra["obtained"], extra["risk_score"]))

        # ── Calcul des métriques ──────────────────────────────────────
        metrics = {
            "meta": {
                "version": "6.0",
                "timestamp": datetime.now().isoformat(),
                "mode": "full" if self.full else "compliance-only",
                "n_scenarios": len(scenarios),
                "stability_runs": stability_n,
                "distribution": scenario_stats(),
            },
            "accuracy": compute_accuracy(results),
            "confusion_matrix": compute_confusion_matrix(results),
            "fatf_coverage": compute_fatf_coverage(results),
            "binding_coverage": compute_binding_coverage(results),
            "evidence_coverage": compute_evidence_coverage(results),
            "hallucination": compute_hallucination_rate(results),
        }
        if stability_n > 1:
            metrics["stability"] = compute_stability(stability_runs)

        # Comptage des échecs LLM (transparence sur la fiabilité du run)
        llm_failures = sum(1 for r in results
                           if r.get("n_semantic", 0) == 0 and self.full)
        metrics["meta"]["llm_failures_suspected"] = llm_failures
        if llm_failures > len(scenarios) * 0.3:
            print(f"\n  ⚠️  ATTENTION : {llm_failures}/{len(scenarios)} scénarios sans "
                  f"règle sémantique.")
            print(f"     Cause probable : quota LLM épuisé (429) ou service indisponible.")
            print(f"     → Couverture FATF et hallucination NON FIABLES sur ce run.")
            print(f"     → Attendre le reset quota et relancer SANS --stability.")

        metrics["_results"] = results
        return metrics

    def run_stress(self, scenarios, n: int) -> dict:
        """
        MODE --stress-semantic : force le chemin LLM sur un sous-échantillon
        de n scénarios (toutes les recs de leur checklist), indépendamment de
        la couverture symbolique. Objectif double :
          1. Rendre le TAUX D'HALLUCINATION mesurable (le sémantique tourne).
          2. Mesurer la couverture ADOSSÉE À UNE PREUVE (vs générique).
        """
        sample = scenarios[:n]
        print(f"\n{'='*70}")
        print(f"  STRESS SÉMANTIQUE — {len(sample)} scénarios, gaps forcés")
        print(f"  (force le LLM même si le symbolique couvre déjà)")
        print(f"{'='*70}\n")

        results = []
        for i, sc in enumerate(sample, 1):
            res = self._analyze_scenario(sc, force_semantic=True)
            results.append(res)
            n_sem = res.get("n_semantic", 0)
            ev = res.get("evidence_coverage", {})
            ev_n = sum(1 for v in ev.values() if v)
            print(f"  [{i:2d}/{len(sample)}] {sc['id']:<14} "
                  f"sémantique={n_sem:<2} preuve={ev_n}/{len(ev)} "
                  f"décision={res['obtained']}")

        return {
            "n_stressed":        len(sample),
            "hallucination":     compute_hallucination_rate(results),
            "evidence_coverage": compute_evidence_coverage(results),
            "symbolic_coverage": compute_fatf_coverage(results),
            "binding_coverage":  compute_binding_coverage(results),
            "_results":          results,
        }


# ══════════════════════════════════════════════════════════════════════
#  RAPPORT
# ══════════════════════════════════════════════════════════════════════

def print_report(metrics):
    """Affiche le rapport d'évaluation en console."""
    print(f"\n{'='*70}")
    print(f"  RÉSULTATS D'ÉVALUATION")
    print(f"{'='*70}\n")

    # ── Accuracy ──
    acc = metrics["accuracy"]
    cible_acc = "✅" if acc["accuracy"] >= 0.85 else "⚠️"
    print(f"  1. ACCURACY DÉCISIONNELLE")
    print(f"     Accuracy (avec cas acceptables) : {acc['accuracy']:.1%} {cible_acc} (cible ≥ 85%)")
    print(f"     Accuracy stricte (match exact)  : {acc['accuracy_strict']:.1%}")
    print(f"     Correct : {acc['correct']}/{acc['n']} | Erreurs graves : {acc['severe_errors']}")

    # ── Matrice de confusion ──
    print(f"\n  2. MATRICE DE CONFUSION (attendu → obtenu)")
    cm = metrics["confusion_matrix"]
    all_decisions = sorted(set(
        list(cm.keys()) + [o for row in cm.values() for o in row.keys()]),
        key=lambda d: DECISION_ORDER.index(d) if d in DECISION_ORDER else 99)
    header = "        " + "".join(f"{d[:5]:>7}" for d in all_decisions)
    print(f"     {header}")
    for exp in all_decisions:
        if exp not in cm:
            continue
        row = "".join(f"{cm[exp].get(o, 0):>7}" for o in all_decisions)
        print(f"     {exp:<7} {row}")

    # ── Couverture réglementaire (BCT contraignante + FATF référence) ──
    print(f"\n  3. COUVERTURE RÉGLEMENTAIRE")
    bnd = metrics.get("binding_coverage", {})
    if bnd.get("mean_coverage") is not None:
        print(f"     BCT contraignante (droit TN) : {bnd['mean_coverage']:.1%}  "
              f"[min {bnd['min_coverage']:.0%} · max {bnd['max_coverage']:.0%}]")
    cov = metrics["fatf_coverage"]
    if cov.get("mean_coverage") is not None:
        cible_cov = "✅" if cov["mean_coverage"] >= 0.90 else "⚠️"
        print(f"     FATF (référence intl)        : {cov['mean_coverage']:.1%} {cible_cov} (cible ≥ 90%)")
        print(f"     Min : {cov['min_coverage']:.1%} | Max : {cov['max_coverage']:.1%}")
    else:
        print(f"     {cov.get('note', 'Non mesurée')}")
    ev = metrics.get("evidence_coverage", {})
    if ev.get("mean_coverage") is not None:
        print(f"     Adossée à une preuve (LLM)   : {ev['mean_coverage']:.1%}  "
              f"« preuve contextuelle vérifiée »")

    # ── Hallucination ──
    print(f"\n  4. TAUX D'HALLUCINATION")
    hal = metrics["hallucination"]
    if hal.get("hallucination_rate") is not None:
        cible_hal = "✅" if hal["hallucination_rate"] < 0.02 else "⚠️"
        print(f"     Taux d'hallucination : {hal['hallucination_rate']:.2%} {cible_hal} (cible < 2%)")
        print(f"     Règles avec snippet : {hal['total_semantic_rules']} "
              f"(verbatim={hal.get('grounded_verbatim',0)}, "
              f"trigramme={hal.get('grounded_trigram',0)}, "
              f"hallucinées={hal['hallucinated']})")
        if hal.get("no_snippet_extraction_failures", 0):
            print(f"     Échecs extraction LLM (exclus) : "
                  f"{hal['no_snippet_extraction_failures']} règles sans snippet")
    else:
        print(f"     {hal.get('note', 'Non mesurée')}")
        if hal.get("no_snippet_extraction_failures", 0):
            print(f"     ({hal['no_snippet_extraction_failures']} règles sans snippet "
                  f"— échecs LLM probables)")

    # ── Stabilité ──
    if "stability" in metrics:
        print(f"\n  5. STABILITÉ DÉCISIONNELLE")
        st = metrics["stability"]
        cible_st = "✅" if st["decision_agreement_rate"] == 1.0 else "⚠️"
        print(f"     Accord décisionnel : {st['decision_agreement_rate']:.1%} {cible_st} (cible 100%)")
        print(f"     σ(score) moyen : {st['mean_score_std']:.2f} "
              f"| max : {st['max_score_std']:.2f}")
        print(f"     Scénarios stables : {st['scenarios_perfect']}/{st['scenarios_total']}")

    # ── Erreurs détaillées ──
    erreurs = [r for r in metrics["_results"] if not r["correct"]]
    if erreurs:
        print(f"\n  ⚠️  SCÉNARIOS ÉCHOUÉS ({len(erreurs)})")
        for r in erreurs:
            print(f"     {r['id']:<10} attendu={r['expected']:<6} "
                  f"obtenu={r['obtained']:<6} ({r['risk_score']:.0f}/100)")
            print(f"        Justification : {r['justification'][:80]}")

    print(f"\n{'='*70}\n")


def print_stress_report(stress: dict):
    """Affiche les résultats du mode --stress-semantic."""
    print(f"\n{'='*70}")
    print(f"  RÉSULTATS STRESS SÉMANTIQUE ({stress['n_stressed']} scénarios forcés)")
    print(f"{'='*70}\n")

    # ── Distinction des 3 couvertures ──
    sym = stress["symbolic_coverage"].get("mean_coverage")
    bnd = stress.get("binding_coverage", {}).get("mean_coverage")
    ev  = stress["evidence_coverage"].get("mean_coverage")
    print(f"  COUVERTURE — trois mesures distinctes :")
    if sym is not None:
        print(f"     Symbolique (générique)      : {sym:.1%}  "
              f"« le corpus connaît l'obligation »")
    if bnd is not None:
        print(f"     BCT contraignante (droit TN): {bnd:.1%}  "
              f"« source tunisienne contraignante »")
    if ev is not None:
        print(f"     Adossée à une preuve (LLM)  : {ev:.1%}  "
              f"« preuve contextuelle pour CETTE tx »")
    else:
        print(f"     Adossée à une preuve (LLM)  : {stress['evidence_coverage'].get('note','N/A')}")

    # ── Hallucination ──
    hal = stress["hallucination"]
    print(f"\n  TAUX D'HALLUCINATION (chemin sémantique forcé) :")
    if hal.get("hallucination_rate") is not None:
        cible = "✅" if hal["hallucination_rate"] < 0.02 else "⚠️"
        print(f"     Taux : {hal['hallucination_rate']:.2%} {cible} (cible < 2%)")
        print(f"     Règles sémantiques avec snippet : {hal['total_semantic_rules']} "
              f"(verbatim={hal.get('grounded_verbatim',0)}, "
              f"trigramme={hal.get('grounded_trigram',0)}, "
              f"hallucinées={hal.get('hallucinated',0)})")
        if hal.get("no_snippet_extraction_failures"):
            print(f"     Échecs extraction (exclus) : {hal['no_snippet_extraction_failures']}")
    else:
        print(f"     {hal.get('note','Non mesurable')}")
        if hal.get("no_snippet_extraction_failures"):
            print(f"     ({hal['no_snippet_extraction_failures']} règles sans snippet — échecs LLM)")
    print(f"\n{'='*70}\n")


def export_markdown(metrics, path):
    """Exporte un rapport Markdown COMPLET pour la soutenance (toutes les métriques)."""
    acc = metrics["accuracy"]
    cov = metrics.get("fatf_coverage", {})
    bnd = metrics.get("binding_coverage", {})
    hal_main = metrics.get("hallucination", {})
    stress = metrics.get("stress_semantic")

    def pct(x): return f"{x:.1%}" if x is not None else "N/A"

    # Hallucination "officielle" = celle du stress (mesure réelle), sinon run principal.
    hal_block = None
    if stress and stress.get("hallucination", {}).get("hallucination_rate") is not None:
        hal_block = stress["hallucination"]
    elif hal_main.get("hallucination_rate") is not None:
        hal_block = hal_main
    hal_rate = hal_block["hallucination_rate"] if hal_block else None

    lines = [
        "# Évaluation du Compliance Agent v6.0",
        "",
        f"**Date** : {metrics['meta']['timestamp'][:10]}  ",
        f"**Mode** : {metrics['meta']['mode']}  ",
        f"**Scénarios** : {metrics['meta']['n_scenarios']}",
        "",
        "## 1. Résultats synthétiques",
        "",
        "| Métrique | Valeur | Cible | Statut |",
        "|----------|--------|-------|--------|",
        f"| Accuracy décisionnelle | {acc['accuracy']:.1%} | ≥ 85% | "
        f"{'✅' if acc['accuracy'] >= 0.85 else '⚠️'} |",
        f"| Accuracy stricte | {acc['accuracy_strict']:.1%} | — | — |",
    ]
    if bnd.get("mean_coverage") is not None:
        lines.append(f"| Couverture BCT contraignante | {bnd['mean_coverage']:.1%} | — | ⚖️ droit TN |")
    if cov.get("mean_coverage") is not None:
        lines.append(f"| Couverture FATF (référence) | {cov['mean_coverage']:.1%} | ≥ 90% | "
                     f"{'✅' if cov['mean_coverage'] >= 0.90 else '⚠️'} |")
    if hal_rate is not None:
        lines.append(f"| Taux d'hallucination | {hal_rate:.2%} | < 2% | "
                     f"{'✅' if hal_rate < 0.02 else '⚠️'} |")
    if "stability" in metrics:
        st = metrics["stability"]
        lines.append(f"| Accord inter-runs | {st['decision_agreement_rate']:.1%} | 100% | "
                     f"{'✅' if st['decision_agreement_rate'] == 1.0 else '⚠️'} |")

    # ── 2. Matrice de confusion ──
    cm = metrics["confusion_matrix"]
    order = [d for d in DECISION_ORDER
             if d in cm or any(d in row for row in cm.values())]
    lines += ["", "## 2. Matrice de confusion (attendu → obtenu)", "",
              "| attendu \\\\ obtenu | " + " | ".join(order) + " |",
              "|" + "---|" * (len(order) + 1)]
    for exp in order:
        if exp not in cm:
            continue
        row = " | ".join(str(cm[exp].get(o, 0)) for o in order)
        lines.append(f"| **{exp}** | {row} |")

    # ── 3. Couverture réglementaire — 3 mesures distinctes (depuis le stress) ──
    if stress:
        sym  = stress.get("symbolic_coverage", {}).get("mean_coverage")
        sbnd = stress.get("binding_coverage", {}).get("mean_coverage")
        ev   = stress.get("evidence_coverage", {}).get("mean_coverage")
        lines += ["", "## 3. Couverture réglementaire — 3 mesures distinctes",
                  f"*(stress sémantique sur {stress.get('n_stressed','?')} scénarios)*", "",
                  "| Mesure | Valeur | Signification |",
                  "|--------|--------|---------------|",
                  f"| Symbolique (générique) | {pct(sym)} | le corpus connaît l'obligation |",
                  f"| **BCT contraignante** | {pct(sbnd)} | source tunisienne contraignante |",
                  f"| **Adossée à une preuve** | {pct(ev)} | preuve contextuelle vérifiée pour CETTE tx |",
                  "",
                  "> La hiérarchie générique ≥ contraignante ≥ preuve montre que la "
                  "couverture générique surestime ; la couverture adossée à une preuve "
                  "est la mesure honnête."]

    # ── 4. Anti-hallucination ──
    if hal_block:
        lines += ["", "## 4. Anti-hallucination (gate B4 — snippet ancré)", "",
                  f"- **Taux d'hallucination : {hal_block['hallucination_rate']:.2%}** (cible < 2%)",
                  f"- Règles sémantiques admises avec snippet : {hal_block.get('total_semantic_rules',0)}",
                  f"  - ancrées verbatim : {hal_block.get('grounded_verbatim',0)}",
                  f"  - ancrées trigramme : {hal_block.get('grounded_trigram',0)}",
                  f"  - hallucinées : {hal_block.get('hallucinated',0)}",
                  "",
                  "> Gate B4 : une règle sémantique n'est admise que si son snippet est "
                  "ancré dans le corpus (verbatim ou trigrammes ≥ 60%). Toute obligation "
                  "non sourçable littéralement est rejetée à l'admission."]

    # ── 5. Détail par scénario ──
    lines += ["", "## 5. Détail par scénario", "",
              "| ID | Catégorie | Attendu | Obtenu | Score | Correct |",
              "|----|-----------|---------|--------|-------|---------|"]
    for r in metrics["_results"]:
        mark = "✅" if r["correct"] else "❌"
        lines.append(f"| {r['id']} | {r['category']} | {r['expected']} | "
                     f"{r['obtained']} | {r['risk_score']:.0f} | {mark} |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  📄 Markdown exporté : {path}")


# ══════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Évaluation du Compliance Agent v6.0")
    ap.add_argument("--full", action="store_true",
                    help="Pipeline complet (retrieval réel) pour couverture + hallucination")
    ap.add_argument("--stability", type=int, default=1,
                    help="Nombre de runs par scénario pour la stabilité (défaut 1)")
    ap.add_argument("--generated", action="store_true",
                    help="Utiliser les scénarios GÉNÉRÉS AUTOMATIQUEMENT (oracle Niveau 1) "
                         "au lieu du jeu étiqueté à la main")
    ap.add_argument("--stress-semantic", type=int, default=0, metavar="N",
                    help="Force le chemin LLM sur N scénarios (gaps forcés) pour "
                         "MESURER l'hallucination + la couverture par preuve. Implique --full.")
    ap.add_argument("--dual", action="store_true",
                    help="MODE PRODUCTION 3 zones : symbolique seul sur les faits durs "
                         "(R6/R7/R19), symbolique+sémantique en parallèle sur le contextuel. "
                         "Implique le pipeline complet (chunks).")
    ap.add_argument("--category", default=None,
                    help="Filtrer par catégorie (PASS/REVIEW/EDD/STR/BLOCK)")
    ap.add_argument("--output", default=None, help="Export JSON des résultats")
    ap.add_argument("--markdown", default=None, help="Export tableau Markdown")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.generated:
        from generate_scenarios import build_scenarios
        scenarios = build_scenarios(verbose=True)
        print(f"  Scénarios GÉNÉRÉS automatiquement (oracle Niveau 1) : {len(scenarios)}")
    else:
        scenarios = SCENARIOS
    if args.category:
        scenarios = [s for s in scenarios if s["category"] == args.category.upper()]
        print(f"  Filtrage : {len(scenarios)} scénarios de catégorie {args.category.upper()}")

    # Le run principal utilise des chunks si --full OU --dual (le dual_mode
    # a besoin du sémantique contextuel) ; le stress construit le pipeline à part.
    harness = EvaluationHarness(
        full_pipeline=(args.full or args.dual), verbose=args.verbose,
        build_pipeline=(args.full or args.dual or args.stress_semantic > 0),
        dual_mode=args.dual)
    try:
        metrics = harness.run(scenarios, stability_n=args.stability)

        print_report(metrics)

        # ── MODE STRESS SÉMANTIQUE ───────────────────────────────────
        if args.stress_semantic > 0:
            stress = harness.run_stress(scenarios, args.stress_semantic)
            print_stress_report(stress)
            metrics["stress_semantic"] = {k: v for k, v in stress.items() if k != "_results"}

        if args.output:
            export = {k: v for k, v in metrics.items() if k != "_results"}
            export["results"] = metrics["_results"]
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(export, f, indent=2, ensure_ascii=False, default=str)
            print(f"  💾 JSON exporté : {args.output}")

        if args.markdown:
            export_markdown(metrics, args.markdown)

        exit_code = 0 if metrics["accuracy"]["accuracy"] >= 0.85 else 1
    finally:
        harness.close()   # fermeture propre Neo4j + Weaviate (pas de ResourceWarning)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()