# -*- coding: utf-8 -*-
"""
evaluate_rag.py — Évaluation du RAG agentique (inspirée de RAGAS + IR classique)
================================================================================
PFE AML/KYC 2025-2026 | Évaluation de la RÉCUPÉRATION et de la GÉNÉRATION,
distincte de evaluate_compliance.py (qui mesure la DÉCISION finale).

Idée directrice (cf. bonnes pratiques « AI Engineer ») :
    « La bonne récupération ne suffit pas. Un système RAG échoue en silence,
      avec des réponses confiantes. Il faut le MESURER, et savoir ne PAS répondre. »

MÉTRIQUES
  Récupération (IR)   : Hit-Rate@k, MRR, Context Precision@k (LLM-juge), latence,
                        voies de récupération (bm25 / vecteur / rrf / graphe / rerank)
  Génération (RAGAS)  : Faithfulness (fidélité/ancrage), Answer Relevancy
  Sûreté              : Abstention — le système sait-il « ne pas répondre » quand
                        les extraits ne suffisent pas ? (test hors-champ dédié)

MÉTHODE
  Jeu de référence (« golden set ») : questions réglementaires AML avec la/les
  source(s) attendue(s) + questions HORS-CHAMP pour tester l'abstention.
  Pour chaque question : on lance le VRAI pipeline de récupération, on synthétise
  la réponse avec le VRAI prompt ancré du système, puis un LLM-juge note.

  Le score n'est pas le résultat : le rapport met en avant les PIRES lignes et
  une hypothèse de cause, pour transformer un chiffre en action d'ingénierie.

PRÉREQUIS  : Weaviate + Neo4j + Groq accessibles (comme evaluate_compliance --full).
             En local : lancer les services + `. .\\cloud_env.ps1` si cloud.
USAGE      : python evaluate_rag.py [--top-k 5] [--sample N] [--no-judge]
SORTIES    : rapport_rag.md  +  resultats_rag.json
"""
import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# Phrase d'abstention EXACTE produite par le système (api/main.py _synthesize_answers).
ABSTENTION = "ne répondent pas directement"


# ════════════════════════════════════════════════════════════════════════════
#  JEU DE RÉFÉRENCE (« golden set »)
#  expected_docs : sous-chaînes attendues dans RetrievalResult.source_doc.
#  Les questions sont en anglais : c'est la langue des requêtes corpus du système
#  (embeddings BAAI/bge-base-en). in_scope=False → on ATTEND une abstention.
# ════════════════════════════════════════════════════════════════════════════
GOLDEN = [
    {"id": "STR_THRESHOLD",
     "query": "suspicious transaction report filing threshold for a local wire transfer, obligation and deadline",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["bct_circulaire_2017_08", "loi_2015_26"], "in_scope": True},

    {"id": "KYC_CDD",
     "query": "customer due diligence and identity verification obligations before executing a transfer",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["bct_circulaire_2017_08", "loi_2015_26", "2024_reglementation_bancaire"], "in_scope": True},

    {"id": "EDD_HIGH_AMOUNT",
     "query": "enhanced due diligence measures for large or unusual transactions without economic justification",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["bct_circulaire_2017_08"], "in_scope": True},

    {"id": "PEP",
     "query": "politically exposed persons obligations enhanced measures source of wealth",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["fatf", "loi_2015_26"], "in_scope": True},

    {"id": "BENEFICIAL_OWNER",
     "query": "beneficial owner identification and verification requirement for legal persons",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["loi_2015_26", "fatf"], "in_scope": True},

    {"id": "SANCTIONS_SCREENING",
     "query": "targeted financial sanctions screening obligation against UN listed persons",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["fatf", "loi_2015_26"], "in_scope": True},

    {"id": "SUSPICIOUS_INDICATORS",
     "query": "obligation to file a suspicious transaction report when an operation has no lawful economic justification",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["fatf", "ctaf"], "in_scope": True},

    {"id": "CORRESPONDENT_BANKING",
     "query": "correspondent banking relationship due diligence obligations",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["basel", "bct_circulaire_2017_08"], "in_scope": True},

    {"id": "RECORD_KEEPING",
     "query": "record keeping and retention period for customer identification and transactions",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["loi_2015_26", "bct_circulaire_2017_08"], "in_scope": True},

    {"id": "TRAVEL_RULE",
     "query": "originator and beneficiary information required to accompany a wire transfer",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["fatf"], "in_scope": True},

    {"id": "STR_DEADLINE_CTAF",
     "query": "obligation to declare suspicious operations to the CTAF financial intelligence unit",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["loi_2015_26", "ctaf", "bct_circulaire_2017_08"], "in_scope": True},

    {"id": "RISK_BASED_APPROACH",
     "query": "obligation to classify each customer by money laundering risk level and apply proportionate due diligence",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["bct_circulaire_2017_08", "loi_2015_26", "fatf"], "in_scope": True},

    {"id": "CASH_DEPOSIT_REPORTING",
     "query": "reporting and record keeping obligations for a cash deposit above the regulatory threshold",
     "tx_type": "DEPOT_ESPECES", "jurisdiction": "Tunisia",
     "expected_docs": ["bct_circulaire", "ctaf", "loi_2015_26"], "in_scope": True},

    {"id": "HIGH_RISK_COUNTRY",
     "query": "enhanced due diligence measures for transactions involving high risk jurisdictions",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["fatf", "ctaf"], "in_scope": True},

    {"id": "TIPPING_OFF",
     "query": "prohibition to inform a customer that a suspicious transaction declaration has been filed against them",
     "tx_type": "VIREMENT_LOCAL", "jurisdiction": "Tunisia",
     "expected_docs": ["loi_2015_26"], "in_scope": True},

    {"id": "ASSET_FREEZE",
     "query": "obligation to freeze without delay the assets of designated persons under sanctions",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["fatf", "loi_2015_26"], "in_scope": True},

    {"id": "SHELL_BANK",
     "query": "prohibition of correspondent banking relationships with shell banks",
     "tx_type": "SWIFT", "jurisdiction": "International",
     "expected_docs": ["basel", "fatf"], "in_scope": True},

    # ── HORS-CHAMP : le système DOIT s'abstenir (aucune source AML pertinente) ──
    {"id": "OOS_VAT",
     "query": "what is the value added tax rate in Japan in 2025",
     "tx_type": None, "jurisdiction": None, "expected_docs": [], "in_scope": False},

    {"id": "OOS_RECIPE",
     "query": "best traditional recipe for Tunisian couscous cooking time",
     "tx_type": None, "jurisdiction": None, "expected_docs": [], "in_scope": False},

    {"id": "OOS_WEATHER",
     "query": "weather forecast temperature in Tunis next weekend",
     "tx_type": None, "jurisdiction": None, "expected_docs": [], "in_scope": False},

    {"id": "OOS_SPORT",
     "query": "which national team won the football world cup in 2022",
     "tx_type": None, "jurisdiction": None, "expected_docs": [], "in_scope": False},

    {"id": "OOS_MATH",
     "query": "how do you solve a quadratic equation using the discriminant formula",
     "tx_type": None, "jurisdiction": None, "expected_docs": [], "in_scope": False},

    {"id": "OOS_TRAVEL",
     "query": "cheapest way to book a flight from Tunis to Paris in summer",
     "tx_type": None, "jurisdiction": None, "expected_docs": [], "in_scope": False},
]


# ════════════════════════════════════════════════════════════════════════════
#  OUTILS LLM-JUGE
# ════════════════════════════════════════════════════════════════════════════

def _ask_json(llm, prompt: str, retries: int = 2) -> dict:
    """Appelle le LLM et parse un objet JSON (tolérant aux ```json et au bruit)."""
    for _ in range(retries + 1):
        try:
            time.sleep(0.6)   # espacement des appels : limite le rate-limit Groq
            resp = llm.chat.completions.create(
                model=None, messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=400)
            raw = (resp.choices[0].message.content or "").strip()
            raw = re.sub(r"```json\s*|```", "", raw)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group(0))
        except Exception:
            time.sleep(1.0)
    return {}


def judge_context_precision(llm, query: str, chunks: list):
    """Fraction des extraits jugés PERTINENTS (0..1) ; None si le juge échoue.

    Format « compte » (plus robuste qu'une liste 1/0 que le modèle rate souvent)."""
    if not chunks:
        return None
    extraits = "\n".join(f"{i}. [{c.source_doc}] {c.text[:200]}"
                         for i, c in enumerate(chunks, 1))
    prompt = (
        "Combien des extraits réglementaires suivants sont PERTINENTS pour "
        "répondre à la question de conformité AML/KYC ? Compte-les précisément.\n"
        f"QUESTION : {query}\nEXTRAITS ({len(chunks)}) :\n{extraits}\n\n"
        f'JSON strict, rien d\'autre : {{"relevant_count": <entier 0 à {len(chunks)}>}}')
    data = _ask_json(llm, prompt)
    if "relevant_count" not in data:
        return None
    try:
        k = max(0, min(len(chunks), int(data["relevant_count"])))
        return round(k / len(chunks), 3)
    except Exception:
        return None


def judge_generation(llm, query: str, answer: str, chunks: list) -> dict:
    """Fidélité (ancrage) + pertinence de la réponse, notées par le LLM (0..1)."""
    extraits = "\n".join(f"- [{c.source_doc}] {c.text[:200]}" for c in chunks) or "(aucun)"
    prompt = (
        "Tu es un évaluateur RAG rigoureux. Évalue la RÉPONSE générée à partir des "
        "EXTRAITS, pour la QUESTION.\n"
        " - faithfulness (0..1) : CHAQUE affirmation de la réponse est-elle "
        "soutenue par les extraits ? (1 = tout est ancré, 0 = inventé)\n"
        " - answer_relevancy (0..1) : la réponse répond-elle DIRECTEMENT à la "
        "question ?\n"
        f"QUESTION : {query}\nEXTRAITS :\n{extraits}\nRÉPONSE : {answer}\n\n"
        'JSON strict : {"faithfulness": 0.0, "answer_relevancy": 0.0, "note": "…"}')
    data = _ask_json(llm, prompt)
    # Juge indisponible (rate-limit) → None (exclu des moyennes, PAS compté comme 0).
    if "faithfulness" not in data and "answer_relevancy" not in data:
        return {"faithfulness": None, "answer_relevancy": None, "note": "juge indisponible"}

    def _num(x):
        try:
            return max(0.0, min(1.0, float(x)))
        except Exception:
            return None
    return {"faithfulness": _num(data.get("faithfulness")),
            "answer_relevancy": _num(data.get("answer_relevancy")),
            "note": str(data.get("note", ""))[:160]}


def synthesize_answer(llm, query: str, chunks: list) -> str:
    """Réutilise le prompt ANCRÉ du système (api/main.py) : réponse ou abstention."""
    extraits = "\n".join(
        f"  [{c.source_doc} · {c.article_ref or '—'}] {c.text[:220]}"
        for c in chunks[:4]) or "  (aucun extrait)"
    prompt = (
        "Tu es analyste conformité AML/KYC dans une banque tunisienne. Réponds en "
        "FRANÇAIS en 1 à 2 phrases à la QUESTION en synthétisant UNIQUEMENT les "
        "EXTRAITS.\n"
        "- Utilise UNIQUEMENT les extraits. N'invente aucune obligation, aucun "
        "seuil, aucun article.\n"
        "- Cite la ou les sources entre parenthèses.\n"
        "- Si les extraits ne répondent pas à la question, écris EXACTEMENT : "
        '"Les extraits trouvés ne répondent pas directement à cette question."\n\n'
        f"QUESTION : {query}\nEXTRAITS :\n{extraits}")
    try:
        resp = llm.chat.completions.create(
            model=None, messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=220)
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"(synthèse indisponible : {str(e)[:60]})"


# ════════════════════════════════════════════════════════════════════════════
#  ÉVALUATION D'UNE QUESTION
# ════════════════════════════════════════════════════════════════════════════

def hit_and_mrr(chunks: list, expected_docs: list):
    """Hit-Rate (au moins un doc attendu dans le top-k) + rang réciproque."""
    if not expected_docs:
        return None, None
    for c in chunks:
        src = (c.source_doc or "").lower()
        if any(exp.lower() in src for exp in expected_docs):
            return 1, round(1.0 / c.rank, 3) if c.rank else 1.0
    return 0, 0.0


def evaluate_query(retriever, llm, item: dict, top_k: int, use_judge: bool) -> dict:
    q = item["query"]
    # skip_cache=True : on mesure la VRAIE récupération hybride (BM25+vecteur+
    # rerank), pas les règles mises en cache par des analyses précédentes.
    chunks, metrics = retriever.retrieve(
        q, tx_type=item.get("tx_type"), jurisdiction=item.get("jurisdiction"),
        top_k=top_k, skip_cache=True, return_metrics=True)

    row = {"id": item["id"], "in_scope": item["in_scope"],
           "n_chunks": len(chunks),
           "top_source": chunks[0].source_doc if chunks else "—",
           "paths": ",".join(sorted({c.retrieval_path for c in chunks})) or "—",
           "latency_ms": round(metrics.total_ms, 0),
           "graph_prefilter_docs": metrics.prefilter_docs,
           "cache_hit": metrics.cache_hit}

    hit, mrr = hit_and_mrr(chunks, item.get("expected_docs", []))
    row["hit@k"], row["mrr"] = hit, mrr

    answer = synthesize_answer(llm, q, chunks) if use_judge else ""
    row["answer"] = answer
    row["abstained"] = ABSTENTION in answer.lower()

    if use_judge and not row["abstained"] and chunks:
        row["context_precision"] = judge_context_precision(llm, q, chunks)
        gen = judge_generation(llm, q, answer, chunks)
        row["faithfulness"] = gen["faithfulness"]
        row["answer_relevancy"] = gen["answer_relevancy"]
        row["note"] = gen["note"]
    else:
        row["context_precision"] = None
        row["faithfulness"] = None
        row["answer_relevancy"] = None
        row["note"] = "abstention" if row["abstained"] else ""
    return row


# ════════════════════════════════════════════════════════════════════════════
#  AGRÉGATION + RAPPORT
# ════════════════════════════════════════════════════════════════════════════

def _avg(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.mean(vals), 3) if vals else None


def build_report(rows: list, top_k: int) -> str:
    ins = [r for r in rows if r["in_scope"]]
    oos = [r for r in rows if not r["in_scope"]]

    hit = _avg([r["hit@k"] for r in ins])
    mrr = _avg([r["mrr"] for r in ins])
    ctxp = _avg([r["context_precision"] for r in ins])
    faith = _avg([r["faithfulness"] for r in ins])
    relv = _avg([r["answer_relevancy"] for r in ins])
    lat = _avg([r["latency_ms"] for r in rows])

    # Sûreté : hors-champ correctement abstenus + in-scope correctement répondus
    oos_ok = sum(1 for r in oos if r["abstained"])
    ins_answered = sum(1 for r in ins if not r["abstained"])

    L = []
    L.append("# Évaluation du RAG agentique — récupération & génération\n")
    L.append(f"> Jeu de référence : **{len(ins)} questions réglementaires** + "
             f"**{len(oos)} questions hors-champ** (test d'abstention) · top_k = {top_k}\n")
    L.append("> Complète `rapport_eval.md` (qui mesure la DÉCISION). Ici on mesure "
             "la qualité de la **récupération** et de la **génération**.\n")

    L.append("\n## 1. Synthèse\n")
    L.append("| Dimension | Métrique | Valeur |")
    L.append("|---|---|---|")
    L.append(f"| Récupération | **Hit-Rate@{top_k}** | {_pct(hit)} |")
    L.append(f"| Récupération | **MRR** | {mrr if mrr is not None else '—'} |")
    L.append(f"| Récupération | **Context Precision@{top_k}** (LLM-juge) | {_pct(ctxp)} |")
    L.append(f"| Génération | **Faithfulness** (fidélité/ancrage) | {_pct(faith)} |")
    L.append(f"| Génération | **Answer Relevancy** | {_pct(relv)} |")
    L.append(f"| Sûreté | **Abstention correcte (hors-champ)** | {oos_ok}/{len(oos)} |")
    L.append(f"| Sûreté | **Réponse fournie (dans le champ)** | {ins_answered}/{len(ins)} |")
    L.append(f"| Performance | Latence moyenne récupération | {lat} ms |")

    L.append("\n## 2. Récupération — détail par question\n")
    L.append(f"| ID | Hit@{top_k} | MRR | Ctx.Prec | Latence | Source top | Voies |")
    L.append("|---|---|---|---|---|---|---|")
    for r in ins:
        L.append(f"| {r['id']} | {_yn(r['hit@k'])} | {r['mrr']} | "
                 f"{_pct(r['context_precision'])} | {int(r['latency_ms'])} ms | "
                 f"{r['top_source']} | {r['paths']} |")

    L.append("\n## 3. Génération — fidélité & pertinence\n")
    L.append("| ID | Faithfulness | Answer Relevancy | Abstention |")
    L.append("|---|---|---|---|")
    for r in ins:
        L.append(f"| {r['id']} | {_pct(r['faithfulness'])} | "
                 f"{_pct(r['answer_relevancy'])} | {'oui ⚠️' if r['abstained'] else 'non'} |")

    L.append("\n## 4. Sûreté — « savoir ne pas répondre »\n")
    L.append("Un bon système RAG **échoue en silence** avec des réponses confiantes. "
             "On teste donc l'abstention sur des questions **hors-champ** :\n")
    L.append("| ID (hors-champ) | Abstention attendue | Obtenu |")
    L.append("|---|---|---|")
    for r in oos:
        L.append(f"| {r['id']} | oui | {'✅ abstention' if r['abstained'] else '❌ a répondu'} |")

    # ── Le point clé des bonnes pratiques : ne pas s'arrêter à la moyenne ──
    L.append("\n## 5. Diagnostic — les pires lignes (action, pas moyenne)\n")
    worst = sorted([r for r in ins if r["context_precision"] is not None],
                   key=lambda r: (r["context_precision"], r["faithfulness"] or 1))[:3]
    if worst:
        L.append("| ID | Ctx.Prec | Faithfulness | Hypothèse de cause |")
        L.append("|---|---|---|---|")
        for r in worst:
            cause = _diagnose(r)
            L.append(f"| {r['id']} | {_pct(r['context_precision'])} | "
                     f"{_pct(r['faithfulness'])} | {cause} |")
    else:
        L.append("*(LLM-juge désactivé — relancer sans `--no-judge` pour le diagnostic.)*")

    L.append("\n## 6. Méthodologie\n")
    L.append("- **Récupération** exécutée par le VRAI pipeline hybride "
             "(BM25 + vecteur → fusion RRF → re-ranking cross-encoder → cache graphe).")
    L.append("- **Génération** avec le prompt ancré du système (réponse **ou** "
             "abstention explicite).")
    L.append("- **LLM-juge** (RAGAS-like) via le client Groq du projet, température 0.")
    L.append("- **Hit-Rate/MRR** : la source attendue apparaît-elle dans le top-k, "
             "et à quel rang.")
    L.append("- **Faithfulness ≠ exactitude** : mesure l'ancrage dans les extraits, "
             "pas la vérité absolue (limite assumée).\n")
    return "\n".join(L)


def _pct(x):
    return f"{round(x * 100, 1)} %" if isinstance(x, (int, float)) else "—"


def _yn(x):
    return "✅" if x == 1 else ("❌" if x == 0 else "—")


def _diagnose(row: dict) -> str:
    if row["context_precision"] is not None and row["context_precision"] < 0.5:
        return "extraits peu ciblés — reformuler la requête ou affiner le pré-filtre graphe"
    if row["faithfulness"] is not None and row["faithfulness"] < 0.8:
        return "réponse partiellement non ancrée — resserrer le prompt de synthèse"
    if row["hit@k"] == 0:
        return "source attendue absente du top-k — chunking / embeddings à revoir"
    return "cas limite — inspecter les extraits manuellement"


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="Évaluation RAG (RAGAS-like) du projet AML/KYC")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--sample", type=int, default=0, help="limiter à N questions (test rapide)")
    ap.add_argument("--no-judge", action="store_true", help="récupération seule (sans LLM-juge)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=== Évaluation RAG agentique ===")
    try:
        from retrieval_agent import RetrievalPipeline
        retriever = RetrievalPipeline(verbose=False)
    except Exception as e:
        print(f"❌ RetrievalPipeline indisponible ({e}).")
        print("   Vérifie Weaviate + Neo4j (et `. .\\cloud_env.ps1` si cloud).")
        return 1

    llm = None
    if not args.no_judge:
        try:
            from llm_client import get_llm
            llm = get_llm()
        except Exception as e:
            print(f"⚠️ LLM-juge indisponible ({e}) — bascule en récupération seule.")
    use_judge = llm is not None

    golden = GOLDEN[:args.sample] if args.sample else GOLDEN
    rows = []
    for i, item in enumerate(golden, 1):
        print(f"  [{i}/{len(golden)}] {item['id']} …", flush=True)
        try:
            rows.append(evaluate_query(retriever, llm, item, args.top_k, use_judge))
        except Exception as e:
            print(f"     ⚠️ échec {item['id']} : {str(e)[:100]}")
    try:
        retriever.close()
    except Exception:
        pass

    (BASE / "resultats_rag.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    report = build_report(rows, args.top_k)
    (BASE / "rapport_rag.md").write_text(report, encoding="utf-8")

    print("\n" + report.split("## 2.")[0])   # aperçu : synthèse
    print("✅ Écrit : rapport_rag.md  +  resultats_rag.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
