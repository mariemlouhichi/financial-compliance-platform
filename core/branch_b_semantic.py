"""
branch_b_semantic.py — Branche B : Semantic Chunking (Refonte v3)

Pour les documents NON STRUCTURÉS : guides, rapports narratifs, mémos.

═══════════════════════════════════════════════════════════════════
PROBLÈMES DE LA VERSION PRÉCÉDENTE :
═══════════════════════════════════════════════════════════════════

  1. Le sentence splitter (regex ponctuation) échouait sur les documents
     sans ponctuation propre → des "phrases" de 3000+ caractères.

  2. Le seuil fixe (0.45) ne s'adaptait pas au modèle d'embedding.
     BGE produit des similarités dans la plage 0.6-0.95 pour du texte
     juridique cohérent → le seuil n'était jamais atteint.

═══════════════════════════════════════════════════════════════════
NOUVELLE APPROCHE : 3 TECHNIQUES COMBINÉES
═══════════════════════════════════════════════════════════════════

  TECHNIQUE 1 — Multi-Level Sentence Splitting
     Au lieu d'une seule regex, on découpe en 2 passes :
       Passe 1 : Couper par double saut de ligne (paragraphes)
       Passe 2 : Si un paragraphe dépasse 500 chars, le subdiviser
                  par ponctuation (phrases)
     Résultat : des unités de 50-500 chars au lieu de 50-3000 chars.

  TECHNIQUE 2 — Adaptive Percentile Threshold
     Au lieu d'un seuil fixe (0.45), on calcule la distribution
     des similarités POUR CE DOCUMENT et on coupe au percentile 25.
     → Si le doc a des sims entre 0.7-0.95, le seuil sera ~0.78
     → Si le doc a des sims entre 0.1-0.5, le seuil sera ~0.20
     Le seuil s'adapte automatiquement au modèle ET au document.

  TECHNIQUE 3 — Local Minima Detection (vallées)
     Au lieu de couper dès que sim < seuil, on cherche les VALLÉES
     dans la courbe de similarité : les points plus bas que leurs
     deux voisins. Ces vallées sont les frontières naturelles de
     sujet. On ne garde que les vallées sous le seuil adaptatif.
     Un lissage (moyenne mobile, fenêtre=3) élimine le bruit.

  + FILET DE SÉCURITÉ — Si un chunk dépasse quand même MAX_CHUNK_CHARS
    (texte extrêmement homogène), on le subdivise par accumulation
    de phrases sans dépasser la taille max.
═══════════════════════════════════════════════════════════════════
"""

import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import SEMANTIC_SIMILARITY_THRESHOLD, MIN_CHUNK_CHARS, MAX_CHUNK_CHARS


class RawChunk:
    """
    Même structure que dans branch_a pour compatibilité.
    Pour la Branche B, article_ref = "Segment N".
    """
    def __init__(self, text: str, article_ref: str, chunk_index: int):
        self.text = text
        self.article_ref = article_ref
        self.chunk_index = chunk_index


# ═══════════════════════════════════════════════════════════════
# TECHNIQUE 1 : Multi-Level Sentence Splitting
# ═══════════════════════════════════════════════════════════════

def _split_by_punctuation(text: str) -> list:
    """Découpe par ponctuation (. ! ? ;) suivie d'espace + majuscule."""
    sentences = re.split(r'(?<=[.!?;])\s+(?=[A-ZÀ-Ü0-9])', text)
    return [s.strip() for s in sentences if s.strip()]


def _multi_level_split(text: str, max_unit_chars: int = 500) -> list:
    """
    Découpage en 2 passes pour garantir des unités de taille raisonnable.

    Passe 1 : Couper par double saut de ligne (\\n\\n) → paragraphes.
              Les documents narratifs (guides, rapports) utilisent les
              sauts de ligne comme séparateurs de sections.

    Passe 2 : Pour chaque paragraphe qui dépasse max_unit_chars,
              le subdiviser par ponctuation (phrases classiques).

    Passe 3 : Pour les unités encore trop longues (pas de ponctuation),
              couper par saut de ligne simple (\\n).

    Résultat : des unités entre 50 et ~500 chars, exploitables
               par l'analyse de similarité.

    Args:
        text: Texte complet
        max_unit_chars: Taille au-delà de laquelle on subdivise (500)

    Returns:
        Liste de strings (unités textuelles)
    """
    # ── Passe 1 : Paragraphes (double saut de ligne) ──
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # Si pas de double saut de ligne → essayer simple saut de ligne
    if len(paragraphs) <= 1:
        paragraphs = re.split(r'\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # ── Passe 2 : Subdiviser les paragraphes longs par ponctuation ──
    units = []
    for para in paragraphs:
        if len(para) <= max_unit_chars:
            units.append(para)
        else:
            # Tenter le découpage par ponctuation
            sub_sentences = _split_by_punctuation(para)
            if len(sub_sentences) > 1:
                units.extend(sub_sentences)
            else:
                # ── Passe 3 : Couper par saut de ligne simple ──
                sub_lines = [l.strip() for l in para.split('\n') if l.strip()]
                if len(sub_lines) > 1:
                    units.extend(sub_lines)
                else:
                    units.append(para)

    return units


# ═══════════════════════════════════════════════════════════════
# TECHNIQUE 2 : Adaptive Percentile Threshold
# ═══════════════════════════════════════════════════════════════

def _adaptive_threshold(similarities: list, percentile: int = 25) -> float:
    """
    Calcule un seuil de rupture adapté à CE document.

    Au lieu d'un seuil fixe (0.45), on prend le percentile 25 de la
    distribution des similarités. Cela signifie : "les 25% de paires
    de phrases les MOINS similaires sont des points de rupture".

    Pourquoi le percentile 25 ?
      - P10 couperait trop peu (seulement les ruptures extrêmes)
      - P50 couperait trop (la moitié du document)
      - P25 est le sweet spot : ~75% du texte reste groupé,
        ~25% des transitions deviennent des points de coupure.

    Le résultat s'adapte automatiquement :
      - BGE avec texte juridique cohérent (sims 0.7-0.95) → seuil ~0.78
      - TF-IDF avec texte varié (sims 0.05-0.30) → seuil ~0.10
      - Texte mixte (sims 0.3-0.8) → seuil ~0.45

    Args:
        similarities: Liste des similarités consécutives
        percentile: Percentile à utiliser (défaut: 25)

    Returns:
        Seuil de rupture adapté au document
    """
    if not similarities:
        return SEMANTIC_SIMILARITY_THRESHOLD

    sorted_sims = sorted(similarities)
    idx = int(len(sorted_sims) * percentile / 100)
    idx = max(0, min(idx, len(sorted_sims) - 1))

    return sorted_sims[idx]


# ═══════════════════════════════════════════════════════════════
# TECHNIQUE 3 : Local Minima Detection (détection de vallées)
# ═══════════════════════════════════════════════════════════════

def _smooth_similarities(similarities: list, window: int = 3) -> list:
    """
    Lissage par moyenne mobile pour éliminer le bruit.

    Sans lissage, une seule phrase "technique" au milieu d'un
    paragraphe cohérent crée un faux pic vers le bas.
    La moyenne mobile sur 3 points absorbe ces fluctuations.

    Exemple :
      Raw:      [0.8, 0.3, 0.85, 0.9]   ← le 0.3 est du bruit
      Smoothed: [0.55, 0.65, 0.68, 0.875] ← le creux est atténué

    Args:
        similarities: Liste des similarités brutes
        window: Taille de la fenêtre (3 = chaque point = moyenne de lui-même + 2 voisins)
    """
    if len(similarities) <= window:
        return similarities[:]

    smoothed = []
    half_w = window // 2
    for i in range(len(similarities)):
        start = max(0, i - half_w)
        end = min(len(similarities), i + half_w + 1)
        avg = sum(similarities[start:end]) / (end - start)
        smoothed.append(avg)

    return smoothed


def _find_local_minima(similarities: list) -> list:
    """
    Trouve les vallées dans la courbe de similarité.

    Une vallée (local minimum) est un point dont la similarité est
    plus basse que ses deux voisins. Ce sont les frontières naturelles
    entre les sujets.

    Exemple :
      Similarities: [0.8, 0.75, 0.5, 0.4, 0.6, 0.9, 0.3, 0.85]
      Vallées :                       ↑ (index 3)      ↑ (index 6)
      → Breakpoints: [4, 7]  (on coupe APRÈS la vallée)

    Returns:
        Liste d'indices (positions des vallées dans la liste de similarités)
    """
    if len(similarities) < 3:
        return []

    minima = []
    for i in range(1, len(similarities) - 1):
        if similarities[i] < similarities[i - 1] and similarities[i] < similarities[i + 1]:
            minima.append(i)

    # Ajouter les extrémités si elles sont plus basses que leur voisin
    if len(similarities) >= 2:
        if similarities[0] < similarities[1]:
            minima.insert(0, 0)
        if similarities[-1] < similarities[-2]:
            minima.append(len(similarities) - 1)

    return sorted(set(minima))


# ═══════════════════════════════════════════════════════════════
# UTILITAIRES
# ═══════════════════════════════════════════════════════════════

def _cosine_similarity(vec_a, vec_b) -> float:
    """Similarité cosinus entre deux vecteurs."""
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a ** 2 for a in vec_a) ** 0.5
    norm_b = sum(b ** 2 for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _merge_small_chunks(chunks: list, min_chars: int = None) -> list:
    """Fusionne les chunks trop petits (< MIN_CHUNK_CHARS) avec leur voisin."""
    if min_chars is None:
        min_chars = MIN_CHUNK_CHARS

    if len(chunks) <= 1:
        return chunks

    merged = []
    buffer_text = ""

    for chunk in chunks:
        if buffer_text:
            combined = buffer_text + " " + chunk.text
            buffer_text = ""
            if len(combined) >= min_chars:
                merged.append(RawChunk(
                    text=combined.strip(),
                    article_ref=chunk.article_ref,
                    chunk_index=len(merged),
                ))
            else:
                buffer_text = combined
        elif len(chunk.text) < min_chars:
            buffer_text = chunk.text
        else:
            merged.append(RawChunk(
                text=chunk.text,
                article_ref=chunk.article_ref,
                chunk_index=len(merged),
            ))

    if buffer_text:
        if merged:
            last = merged[-1]
            merged[-1] = RawChunk(
                text=last.text + " " + buffer_text.strip(),
                article_ref=last.article_ref,
                chunk_index=last.chunk_index,
            )
        else:
            merged.append(RawChunk(
                text=buffer_text.strip(),
                article_ref="Segment 1",
                chunk_index=0
            ))

    return merged


def _enforce_max_size(chunks: list, units: list, embed_fn,
                      max_chars: int = None) -> list:
    """
    Filet de sécurité : subdivise les chunks qui dépassent encore MAX_CHUNK_CHARS.

    Cela arrive quand le texte est extrêmement homogène (aucune vallée
    significative). On accumule les phrases jusqu'à max_chars puis on coupe.

    C'est le dernier recours — la grande majorité des chunks est déjà
    correctement dimensionnée par les techniques 1-3.
    """
    if max_chars is None:
        max_chars = MAX_CHUNK_CHARS

    final = []
    for chunk in chunks:
        if len(chunk.text) <= max_chars:
            final.append(chunk)
            continue

        # Re-découper ce chunk en unités
        sub_units = _multi_level_split(chunk.text, max_unit_chars=300)
        if len(sub_units) <= 1:
            final.append(chunk)
            continue

        # Accumuler les unités jusqu'à max_chars
        current_text = ""
        suffix = ord('a')
        for unit in sub_units:
            if current_text and len(current_text) + len(unit) + 1 > max_chars:
                final.append(RawChunk(
                    text=current_text.strip(),
                    article_ref=f"{chunk.article_ref}-{chr(suffix)}",
                    chunk_index=0,
                ))
                suffix += 1
                current_text = unit
            else:
                current_text = (current_text + " " + unit).strip() if current_text else unit

        if current_text.strip():
            final.append(RawChunk(
                text=current_text.strip(),
                article_ref=f"{chunk.article_ref}-{chr(suffix)}",
                chunk_index=0,
            ))

    return final


# ═══════════════════════════════════════════════════════════════
# POINT D'ENTRÉE PRINCIPAL
# ═══════════════════════════════════════════════════════════════

def chunk_branch_b(text: str, embed_fn, threshold: float = None) -> list:
    """
    Semantic Chunking complet avec les 3 techniques combinées.

    Flux :
      1. Multi-level split → unités de 50-500 chars
      2. Embedding de chaque unité
      3. Similarité cosinus entre unités consécutives
      4. Lissage (moyenne mobile, fenêtre=3)
      5. Détection des vallées (local minima)
      6. Filtrage par seuil adaptatif (percentile 25)
      7. Construction des chunks aux points de rupture
      8. Fusion des micro-chunks (< MIN_CHUNK_CHARS)
      9. Filet de sécurité (MAX_CHUNK_CHARS)

    Args:
        text: Texte complet du document
        embed_fn: Fonction (list[str]) → ndarray d'embeddings
        threshold: Non utilisé (conservé pour compatibilité API)

    Returns:
        Liste de RawChunks prêts pour l'enrichissement
    """
    # ── Technique 1 : Multi-level splitting ──
    units = _multi_level_split(text)

    if len(units) <= 1:
        # Document trop court pour être découpé
        return [RawChunk(text=text.strip(), article_ref="Segment 1", chunk_index=0)]

    # Filtrer les unités vides ou trop courtes (< 20 chars = bruit)
    units = [u for u in units if len(u) >= 20]

    if len(units) <= 1:
        return [RawChunk(text=text.strip(), article_ref="Segment 1", chunk_index=0)]

    # ── Technique 2+3 : Embeddings → Similarités → Lissage → Vallées ──
    embeddings = embed_fn(units)

    # Calculer les similarités entre unités consécutives
    raw_similarities = []
    for i in range(len(embeddings) - 1):
        sim = _cosine_similarity(embeddings[i], embeddings[i + 1])
        raw_similarities.append(sim)

    if not raw_similarities:
        return [RawChunk(text=text.strip(), article_ref="Segment 1", chunk_index=0)]

    # Lissage (fenêtre=3) pour éliminer le bruit
    smoothed = _smooth_similarities(raw_similarities, window=3)

    # Seuil adaptatif basé sur le percentile 25 de CE document
    adaptive_thresh = _adaptive_threshold(smoothed, percentile=25)

    # Trouver les vallées dans la courbe lissée
    minima = _find_local_minima(smoothed)

    # Ne garder que les vallées sous le seuil adaptatif
    breakpoints = [m + 1 for m in minima if smoothed[m] < adaptive_thresh]

    # Si aucun breakpoint trouvé → utiliser le seuil adaptatif en mode simple
    if not breakpoints:
        breakpoints = [i + 1 for i, sim in enumerate(smoothed) if sim < adaptive_thresh]

    # Ajouter début et fin
    breakpoints = sorted(set([0] + breakpoints + [len(units)]))

    # ── Construire les chunks ──
    chunks = []
    for i in range(len(breakpoints) - 1):
        start = breakpoints[i]
        end = breakpoints[i + 1]
        chunk_text = " ".join(units[start:end]).strip()

        if not chunk_text:
            continue

        chunks.append(RawChunk(
            text=chunk_text,
            article_ref=f"Segment {len(chunks) + 1}",
            chunk_index=len(chunks),
        ))

    if not chunks:
        return [RawChunk(text=text.strip(), article_ref="Segment 1", chunk_index=0)]

    # ── Fusion des micro-chunks ──
    chunks = _merge_small_chunks(chunks)

    # ── Filet de sécurité : subdiviser les chunks > MAX_CHUNK_CHARS ──
    chunks = _enforce_max_size(chunks, units, embed_fn)

    # Renuméroter
    for i, chunk in enumerate(chunks):
        chunk.chunk_index = i

    return chunks