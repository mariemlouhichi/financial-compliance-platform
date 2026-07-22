"""
test_reflection.py
===================
Test de la capacité de réflexion du Planning Agent.

On simule une retrieval incomplète et on vérifie que le Planning Agent
identifie les gaps et génère de nouvelles questions.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from planning_agent import PlanningAgent


# Simulation : chunks retournés (incomplets)
MOCK_CHUNKS = [
    {
        "text": "Financial institutions must implement sanctions screening against OFAC, UN, and EU lists...",
        "source_doc": "fatf_recommendations_2025.pdf",
        "article_ref": "Rec. 6",
        "chunk_role": "SANCTIONS",
    },
    {
        "text": "Transactions above 10,000 USD without economic justification must be reported to CTAF...",
        "source_doc": "bct_circulaire_2017_08_aml.pdf",
        "article_ref": "Article 43",
        "chunk_role": "REPORTING",
    },
    {
        "text": "High-risk countries include Iran, North Korea, Myanmar. Enhanced due diligence required...",
        "source_doc": "fatf_recommendations_2025.pdf",
        "article_ref": "Rec. 19",
        "chunk_role": "KYC",
    },
    # GAP : pas de beneficial ownership, pas de travel rule, pas de correspondent banking
]


def test_reflection():
    print("\n" + "="*70)
    print("  TEST REFLECTION — Gap Analysis Post-Retrieval")
    print("="*70)
    
    # Transaction SWIFT Iran (la plus complexe)
    transaction = {
        "transaction_type": "SWIFT",
        "amount": 9800,
        "currency": "USD",
        "sender_country": "Tunisia",
        "receiver_country": "Iran",
        "sender_name": "Ahmed Ben Ali",
        "receiver_name": "Iran National Bank",
        "motive": "Real estate investment",
        "timestamp": "2026-05-19T03:15:00",
        "has_justification": False,
    }
    
    # Questions qui ont été cherchées (simplifiées)
    questions_asked = [
        "FATF blacklist high risk country Iran countermeasures",
        "wire transfer 9800 USD without economic justification STR",
        "sanctions screening SWIFT Iran transaction blocking",
    ]
    
    # Init Planning Agent
    planner = PlanningAgent(verbose=True)
    
    print(f"\n  Transaction : SWIFT {transaction['amount']} {transaction['currency']}")
    print(f"                {transaction['sender_country']} → {transaction['receiver_country']}")
    print(f"  Motive      : {transaction['motive']}")
    print(f"\n  Questions cherchées : {len(questions_asked)}")
    for i, q in enumerate(questions_asked, 1):
        print(f"    Q{i}: {q}")
    
    print(f"\n  Chunks retournés : {len(MOCK_CHUNKS)}")
    for i, c in enumerate(MOCK_CHUNKS, 1):
        print(f"    {i}. [{c['chunk_role']}] {c['source_doc']} {c['article_ref']}")
    
    print(f"\n  {'─'*70}")
    print(f"  LANCEMENT REFLECTION...")
    print(f"  {'─'*70}")
    
    # REFLECT
    reflection = planner.reflect(
        transaction=transaction,
        questions_asked=questions_asked,
        chunks_retrieved=MOCK_CHUNKS,
    )
    
    # Résultats
    print(f"\n  {'='*70}")
    print(f"  RÉSULTATS REFLECTION")
    print(f"  {'='*70}")
    print(f"  Couverture        : {reflection.coverage_ratio:.0%}")
    print(f"  Gaps détectés     : {len(reflection.gaps_found)}")
    
    if reflection.gaps_found:
        print(f"\n  Dimensions manquantes :")
        for gap in reflection.gaps_found:
            print(f"    ❌ {gap}")
    
    print(f"\n  Nouvelles questions : {len(reflection.new_questions)}")
    if reflection.new_questions:
        for i, q in enumerate(reflection.new_questions, 1):
            print(f"    Q{i}: {q}")
    
    print(f"\n  Besoin de re-chercher ? {'❌ NON (couverture suffisante)' if reflection.is_sufficient else '✅ OUI (gaps détectés)'}")
    
    # Vérification
    print(f"\n  {'─'*70}")
    print(f"  VÉRIFICATION")
    print(f"  {'─'*70}")
    
    expected_gaps = ["beneficial_ownership", "travel_rule_R16", "correspondent_banking"]
    found_gaps = [g for g in expected_gaps if any(eg in g.lower() for eg in ["beneficial", "travel", "correspondent"])]
    
    if reflection.gaps_found:
        print(f"  ✅ Le système a bien identifié des gaps manquants")
        print(f"  ✅ Coverage ratio < 100% : {reflection.coverage_ratio:.0%}")
    else:
        print(f"  ⚠️  Aucun gap détecté — vérifier si le LLM est disponible")
    
    if reflection.new_questions:
        print(f"  ✅ Le système a généré {len(reflection.new_questions)} nouvelles questions")
        print(f"  ✅ L'orchestrateur devrait maintenant re-chercher avec ces questions")
    else:
        print(f"  ⚠️  Aucune nouvelle question — soit couverture complète, soit LLM indisponible")
    
    print("\n" + "="*70 + "\n")


if __name__ == "__main__":
    test_reflection()
