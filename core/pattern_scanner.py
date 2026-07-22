"""
pattern_scanner.py — Étape 1 : Le Profiler

Avant tout découpage, on scanne le document pour évaluer son niveau
d'organisation. Le résultat détermine quelle branche utiliser :

  Score > seuil → Branche A (Pattern Chunking)  = document structuré
  Score ≤ seuil → Branche B (Semantic Chunking)  = document narratif

═══════════════════════════════════════════════════════════════════
LES TROIS PILIERS DU SCORE :
═══════════════════════════════════════════════════════════════════

  1. FRÉQUENCE (F)
     Nombre total d'occurrences du pattern dans le texte.
     Ex : "Article 1", "Article 2", ..., "Article 45" → F = 45

  2. INCRÉMENTATION (I)
     Les numéros se suivent-ils ? (1→2→3 = parfait, 1→5→12 = mauvais)
     Ratio de 0 à 1 : proportion d'incréments consécutifs (+1).

  3. RÉGULARITÉ (R)
     Les ancres sont-elles espacées uniformément dans le texte ?
     Mesuré via le Coefficient de Variation (CV) des écarts en caractères.
     R = 1 / (1 + CV)  →  écarts réguliers = R proche de 1

═══════════════════════════════════════════════════════════════════
FORMULE FINALE :
  SCORE = F × I × (0.5 + R × 0.5)
═══════════════════════════════════════════════════════════════════
"""

import re
import sys
import os

# Ajouter le dossier parent au path pour les imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import ANCHOR_PATTERNS, STRUCTURE_THRESHOLD


class ProfileResult:
    """
    Contient le résultat complet du profilage d'un document.
    Utilisé par pipeline.py pour décider quelle branche appliquer.
    """
    def __init__(self, doc_name, is_structured, best_pattern_name,
                 best_pattern_regex, score, frequency, detail):
        self.doc_name = doc_name
        self.is_structured = is_structured      # True → Branche A
        self.best_pattern_name = best_pattern_name  # Ex: "Article"
        self.best_pattern_regex = best_pattern_regex
        self.score = score
        self.frequency = frequency
        self.detail = detail  # dict avec les 3 critères détaillés

    def __repr__(self):
        branch = "A (Structure)" if self.is_structured else "B (Sémantique)"
        return (
            f"[{self.doc_name}] Score={self.score} | "
            f"Pattern={self.best_pattern_name} (×{self.frequency}) | "
            f"→ Branche {branch}"
        )


def _compute_incrementality(numbers: list) -> float:
    """
    Critère 2 : Incrémentation.

    Vérifie si les numéros extraits forment une séquence qui s'incrémente
    de 1 en 1 (1→2→3→4).

    Retourne un ratio entre 0.0 et 1.0 :
      - 1.0 = séquence parfaite (1, 2, 3, 4, 5)
      - 0.0 = aucune incrémentation régulière (1, 7, 23, 100)

    Exemple :
      [1, 2, 3, 5, 6] → incréments [1, 1, 2, 1]
      → 3 incréments de 1 sur 4 → 0.75
    """
    if len(numbers) < 2:
        return 0.0

    # Dédupliquer et trier pour analyser la séquence
    sorted_nums = sorted(set(numbers))
    if len(sorted_nums) < 2:
        return 0.0

    # Calculer les écarts entre numéros consécutifs
    increments = [sorted_nums[i + 1] - sorted_nums[i]
                  for i in range(len(sorted_nums) - 1)]

    # Proportion d'incréments de exactement 1
    consecutive_count = sum(1 for inc in increments if inc == 1)
    return consecutive_count / len(increments)


def _compute_regularity(positions: list) -> float:
    """
    Critère 3 : Régularité.

    Mesure si les ancres sont espacées de manière uniforme dans le texte.
    Utilise le Coefficient de Variation (CV) = écart-type / moyenne.

    Un CV faible = espacement régulier = document bien structuré.

    Retourne un score entre 0.0 et 1.0 :
      R = 1 / (1 + CV)
      - R ≈ 1.0 → espacement très régulier
      - R ≈ 0.0 → espacement chaotique

    Exemple :
      positions = [0, 500, 1000, 1500] → gaps = [500, 500, 500]
      → CV = 0 → R = 1.0 (parfait)
    """
    if len(positions) < 3:
        return 0.5  # Pas assez de données → score neutre

    # Calculer les écarts (en caractères) entre chaque ancre
    gaps = [positions[i + 1] - positions[i]
            for i in range(len(positions) - 1)]

    mean_gap = sum(gaps) / len(gaps)
    if mean_gap == 0:
        return 0.0

    # Écart-type manuel (pas besoin de numpy pour ça)
    variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
    std_gap = variance ** 0.5

    cv = std_gap / mean_gap  # Coefficient de Variation
    return 1.0 / (1.0 + cv)


def profile_document(doc_name: str, text: str) -> ProfileResult:
    """
    Point d'entrée principal du Profiler.

    Teste chaque pattern d'ancre défini dans settings.py.
    Calcule le score pour chacun et retient le meilleur.

    Args:
        doc_name: Nom du fichier (ex: "bct_circulaire_2017_08_aml.pdf")
        text: Contenu textuel complet du document

    Returns:
        ProfileResult avec is_structured=True/False et le score détaillé
    """
    best_score = 0.0
    best_result = None

    for pattern_name, regex in ANCHOR_PATTERNS:
        # ── Trouver toutes les occurrences ──
        matches = list(re.finditer(regex, text))
        frequency = len(matches)

        # Besoin d'au moins 2 ancres pour évaluer
        if frequency < 2:
            continue

        # ── Extraire les numéros et les positions ──
        numbers = [int(m.group(1)) for m in matches]
        positions = [m.start() for m in matches]

        # ── Calculer les 3 critères ──
        incrementality = _compute_incrementality(numbers)
        regularity = _compute_regularity(positions)

        # ── Formule finale ──
        score = frequency * incrementality * (0.5 + regularity * 0.5)

        if score > best_score:
            best_score = score
            best_result = ProfileResult(
                doc_name=doc_name,
                is_structured=(score > STRUCTURE_THRESHOLD),
                best_pattern_name=pattern_name,
                best_pattern_regex=regex,
                score=round(score, 2),
                frequency=frequency,
                detail={
                    "frequency": frequency,
                    "incrementality": round(incrementality, 3),
                    "regularity": round(regularity, 3),
                },
            )

    # ── Aucun pattern trouvé → document narratif par défaut ──
    if best_result is None:
        best_result = ProfileResult(
            doc_name=doc_name,
            is_structured=False,
            best_pattern_name="Aucun",
            best_pattern_regex="",
            score=0.0,
            frequency=0,
            detail={"frequency": 0, "incrementality": 0, "regularity": 0},
        )

    return best_result
