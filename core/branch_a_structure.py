"""
branch_a_structure.py — Branche A : Découpage par Patterns

Pour les documents STRUCTURÉS : circulaires BCT, lois, recommandations GAFI.

═══════════════════════════════════════════════════════════════════
LOGIQUE EN 3 ÉTAPES :
═══════════════════════════════════════════════════════════════════

  1. SEGMENTATION PAR UNITÉ DE LOI
     Le texte est découpé selon les ancres détectées par le Profiler.
     Chaque "Article X" ou "Recommendation X" devient un chunk.
     Le texte AVANT la première ancre est conservé comme "Préambule".

  2. EXTRACTION DE RÉFÉRENCE
     Le titre de l'ancre (ex: "Article 15") est capturé automatiquement
     et stocké dans la métadonnée article_ref.

  3. SUB-CHUNKING SÉMANTIQUE
     Si un article est trop long (> MAX_CHUNK_CHARS = 1500 caractères),
     le système utilise les embeddings pour le subdiviser intelligemment
     sans couper les phrases ni les règles logiques.
     → Les sous-chunks sont suffixés : "Article 15-a", "Article 15-b"...
═══════════════════════════════════════════════════════════════════
"""

import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import MAX_CHUNK_CHARS


class RawChunk:
    """
    Chunk brut (avant enrichissement des métadonnées).
    Sert de format intermédiaire entre le chunker et le role_tagger.
    """
    def __init__(self, text: str, article_ref: str, chunk_index: int):
        self.text = text
        self.article_ref = article_ref
        self.chunk_index = chunk_index


def split_into_sentences(text: str) -> list:
    """
    Découpe un texte en phrases en utilisant la ponctuation.

    Regex : on coupe après [. ! ? ;] suivi d'un espace et d'une majuscule.
    Cela évite de couper sur "n°2017-08" ou "Mr. Smith".
    """
    sentences = re.split(r'(?<=[.!?;])\s+(?=[A-ZÀ-Ü0-9])', text)
    return [s.strip() for s in sentences if s.strip()]


def split_by_pattern(text: str, pattern_name: str, regex: str) -> list:
    """
    Étape 1+2 : Découpe le texte selon les ancres et extrait les références.

    Args:
        text: Texte complet du document
        pattern_name: Nom du pattern gagnant (ex: "Article")
        regex: Expression régulière du pattern

    Returns:
        Liste de RawChunk, un par ancre trouvée + un préambule éventuel

    Exemple :
        "Préambule... Article 1 blabla Article 2 blabla"
        → [RawChunk("Préambule...", "Préambule", 0),
           RawChunk("Article 1 blabla", "Article 1", 1),
           RawChunk("Article 2 blabla", "Article 2", 2)]
    """
    matches = list(re.finditer(regex, text))

    # Aucune ancre trouvée → le document entier est un seul chunk
    if not matches:
        return [RawChunk(
            text=text.strip(),
            article_ref="Document Complet",
            chunk_index=0
        )]

    chunks = []
    idx = 0

    # ── Texte avant la première ancre = Préambule ──
    preamble = text[:matches[0].start()].strip()
    if len(preamble) > 100:
        # On ne garde le préambule que s'il est substantiel (> 100 chars)
        # Sinon c'est probablement juste un en-tête sans valeur
        chunks.append(RawChunk(
            text=preamble,
            article_ref="Préambule",
            chunk_index=idx
        ))
        idx += 1

    # ── Découpe entre chaque ancre ──
    for i, match in enumerate(matches):
        # Début = position de cette ancre
        start = match.start()
        # Fin = position de l'ancre suivante (ou fin du texte)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        chunk_text = text[start:end].strip()
        if not chunk_text:
            continue

        # Construire la référence : "Article 15", "Recommendation 3"...
        number = match.group(1)
        article_ref = f"{pattern_name} {number}"

        chunks.append(RawChunk(
            text=chunk_text,
            article_ref=article_ref,
            chunk_index=idx
        ))
        idx += 1

    return chunks


def sub_chunk_semantic(chunk: RawChunk, embed_fn) -> list:
    """
    Étape 3 : Sub-chunking sémantique pour les chunks trop longs.

    Si un chunk dépasse MAX_CHUNK_CHARS, on le subdivise en utilisant
    les embeddings pour trouver les points de rupture naturels.

    Algorithme :
      1. Découper le chunk en phrases
      2. Encoder chaque phrase (embedding)
      3. Calculer la similarité cosinus entre phrases consécutives
      4. Trouver les points où la similarité chute (mean - 1×std)
      5. Couper à ces points → sous-chunks suffixés (a, b, c...)

    Args:
        chunk: Le RawChunk trop long à subdiviser
        embed_fn: Fonction (list[str]) → ndarray d'embeddings

    Returns:
        Liste de RawChunks avec article_ref suffixé
        Ex: "Article 15" → ["Article 15-a", "Article 15-b", "Article 15-c"]
    """
    # Pas besoin de sub-chunker si le chunk est assez court
    if len(chunk.text) <= MAX_CHUNK_CHARS:
        return [chunk]

    sentences = split_into_sentences(chunk.text)
    if len(sentences) <= 2:
        return [chunk]  # Trop peu de phrases pour subdiviser

    # ── Calculer les embeddings ──
    embeddings = embed_fn(sentences)

    # ── Similarité cosinus entre phrases consécutives ──
    similarities = []
    for i in range(len(embeddings) - 1):
        # Produit scalaire / (norme1 × norme2)
        dot = sum(a * b for a, b in zip(embeddings[i], embeddings[i + 1]))
        norm_a = sum(a ** 2 for a in embeddings[i]) ** 0.5
        norm_b = sum(b ** 2 for b in embeddings[i + 1]) ** 0.5
        cos_sim = dot / (norm_a * norm_b + 1e-8)
        similarities.append(cos_sim)

    if not similarities:
        return [chunk]

    # ── Seuil adaptatif : moyenne - 1 écart-type ──
    mean_sim = sum(similarities) / len(similarities)
    variance = sum((s - mean_sim) ** 2 for s in similarities) / len(similarities)
    std_sim = variance ** 0.5
    threshold = mean_sim - std_sim

    # ── Identifier les points de rupture ──
    breakpoints = []
    for i, sim in enumerate(similarities):
        if sim < threshold:
            breakpoints.append(i + 1)  # Couper APRÈS la phrase i

    if not breakpoints:
        return [chunk]  # Pas de rupture significative

    # ── Construire les sous-chunks ──
    sub_chunks = []
    prev = 0
    suffix = ord('a')  # a, b, c, d...

    all_cuts = breakpoints + [len(sentences)]
    for bp in all_cuts:
        sub_text = " ".join(sentences[prev:bp]).strip()
        if sub_text:
            sub_chunks.append(RawChunk(
                text=sub_text,
                article_ref=f"{chunk.article_ref}-{chr(suffix)}",
                chunk_index=chunk.chunk_index,
            ))
            suffix += 1
        prev = bp

    return sub_chunks if sub_chunks else [chunk]


def chunk_branch_a(text: str, pattern_name: str, regex: str, embed_fn) -> list:
    """
    Point d'entrée de la Branche A.

    Combine les 3 étapes :
      1. Segmentation par ancres
      2. Extraction des références
      3. Sub-chunking des chunks trop longs

    Args:
        text: Texte complet du document
        pattern_name: Nom du pattern (du Profiler)
        regex: Regex du pattern (du Profiler)
        embed_fn: Fonction d'embedding

    Returns:
        Liste finale de RawChunks prêts pour l'enrichissement
    """
    # Étapes 1+2 : découpage par ancres
    raw_chunks = split_by_pattern(text, pattern_name, regex)

    # Étape 3 : sub-chunking si nécessaire
    final_chunks = []
    for chunk in raw_chunks:
        if len(chunk.text) > MAX_CHUNK_CHARS:
            sub_chunks = sub_chunk_semantic(chunk, embed_fn)
            final_chunks.extend(sub_chunks)
        else:
            final_chunks.append(chunk)

    # Renuméroter les index
    for i, chunk in enumerate(final_chunks):
        chunk.chunk_index = i

    return final_chunks
