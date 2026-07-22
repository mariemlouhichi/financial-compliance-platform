"""
role_tagger.py — Étape 5 : Enrichissement & Classification (Metadata Layer)

v2 CHANGES :
  - 8 roles instead of 5 : SANCTION, OBLIGATION, PROCEDURE, THRESHOLD,
    DEFINITION, RED_FLAG, REPORTING, EXEMPTION
  - English-only keywords (all docs are pre-translated to English)
  - DEFAULT_ROLE = "INFORMATION" when no keyword matches
  - has_threshold now uses expanded currency patterns (TND, EUR, USD, GBP,
    MAD, SAR, AED, CHF, JPY, CNY...)

Each chunk gets these metadata fields :
  source_doc    : e.g. "bct_circulaire_2017_08_aml.pdf"
  article_ref   : e.g. "Article 15"
  jurisdiction   : e.g. "Tunisia"
  chunk_role    : e.g. "OBLIGATION"
  has_threshold : true/false
"""

import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import (
    ROLE_KEYWORDS, THRESHOLD_PATTERNS, JURISDICTION_MAP, DEFAULT_ROLE,
    TRANSACTION_TYPE_KEYWORDS,
)


class EnrichedChunk:
    """Final chunk with all metadata. Serialized to JSON."""
    def __init__(self, text, source_doc, article_ref, jurisdiction,
                 chunk_role, has_threshold, chunk_index, char_count):
        self.text = text
        self.source_doc = source_doc
        self.article_ref = article_ref
        self.jurisdiction = jurisdiction
        self.chunk_role = chunk_role
        self.has_threshold = has_threshold
        self.chunk_index = chunk_index
        self.char_count = char_count

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "source_doc": self.source_doc,
            "article_ref": self.article_ref,
            "jurisdiction": self.jurisdiction,
            "chunk_role": self.chunk_role,
            "has_threshold": self.has_threshold,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
        }


def classify_role(text: str) -> str:
    """
    Role-Aware classification using keyword scoring.

    For each of the 8 roles, count how many of its keywords appear
    in the text. The role with the highest score wins.
    If no keyword matches at all → DEFAULT_ROLE ("INFORMATION").

    The 8 roles cover the full AML/KYC regulatory spectrum :
      SANCTION    → penalties, fines, violations, enforcement
      OBLIGATION  → shall, must, required, compliance duties
      PROCEDURE   → steps, verification, KYC/CDD/EDD processes
      THRESHOLD   → amounts, limits, deadlines, percentages
      DEFINITION  → "defined as", "means", "refers to"
      RED_FLAG    → suspicious, unusual, risk indicators, fraud
      REPORTING   → SAR, disclosure, notification to FIU
      EXEMPTION   → exceptions, derogations, "not applicable"
    """
    text_lower = text.lower()
    scores = {}

    for role, keywords in ROLE_KEYWORDS.items():
        score = sum(1 for kw in keywords if re.search(kw, text_lower))
        scores[role] = score

    best_role = max(scores, key=scores.get)

    if scores[best_role] == 0:
        return DEFAULT_ROLE

    return best_role


def detect_threshold(text: str) -> bool:
    """
    Detects financial thresholds in text.

    Covers :
      - Amounts with currency : "50,000 TND", "10 000 EUR", "$500"
      - Percentages : "10%", "5.5 %"
      - Magnitude words : "1 million", "500 thousand", "2 billion"
      - Abbreviated amounts : "5k USD", "2M EUR"
      - International currencies : TND, EUR, USD, GBP, MAD, SAR,
        AED, CHF, JPY, CNY, and more
    """
    for pattern in THRESHOLD_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def detect_transaction_types(text: str) -> list[str]:
    """
    Détecte les types de transaction dans un chunk par mots-clés.
    Retourne une liste parmi : VIREMENT_LOCAL, SWIFT, MANDAT, DEPOT_ESPECES.
    Utilisé pour enrichir les métadonnées lors du re-chunking ou de la validation.
    """
    text_lower = text.lower()
    detected = []
    for tx_type, patterns in TRANSACTION_TYPE_KEYWORDS.items():
        if any(re.search(p, text_lower, re.IGNORECASE) for p in patterns):
            detected.append(tx_type)
    return detected


def get_jurisdiction(country_key: str) -> str:
    """Map JSON country key to readable jurisdiction name."""
    return JURISDICTION_MAP.get(country_key.lower(), country_key)


def enrich_chunk(text: str, source_doc: str, article_ref: str,
                 country_key: str, chunk_index: int) -> EnrichedChunk:
    """
    Main entry point : takes a raw chunk and returns an enriched chunk
    with all metadata fields populated.
    """
    return EnrichedChunk(
        text=text,
        source_doc=source_doc,
        article_ref=article_ref,
        jurisdiction=get_jurisdiction(country_key),
        chunk_role=classify_role(text),
        has_threshold=detect_threshold(text),
        chunk_index=chunk_index,
        char_count=len(text),
    )
