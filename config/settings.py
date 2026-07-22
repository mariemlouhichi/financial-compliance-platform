"""
settings.py — Configuration centralisée du pipeline de chunking AML/KYC.

MODIFICATIONS v2 :
  - ROLE_KEYWORDS : English-only (tous les docs sont traduits en anglais)
  - 8 rôles au lieu de 5 : SANCTION, OBLIGATION, PROCEDURE, THRESHOLD,
    DEFINITION, RED_FLAG, REPORTING, EXEMPTION
  - THRESHOLD_PATTERNS : couverture élargie (devises internationales)
  - EMBEDDING_MODEL : BAAI/bge-base-en-v1.5 (768 dims, optimisé retrieval)
"""

import os

# ═══════════════════════════════════════════════════════════════
# 1. CHEMINS
# ═══════════════════════════════════════════════════════════════
INPUT_FILE = os.getenv(
    "INPUT_FILE",
    os.path.join(os.path.dirname(__file__), "..", "data", "translated_content_v2_concatenated.json")
)
OUTPUT_DIR = os.getenv(
    "OUTPUT_DIR",
    os.path.join(os.path.dirname(__file__), "..", "output")
)

# ═══════════════════════════════════════════════════════════════
# 2. PROFILER — Score de Structure
# ═══════════════════════════════════════════════════════════════
STRUCTURE_THRESHOLD = float(os.getenv("STRUCTURE_THRESHOLD", "3.0"))

# ═══════════════════════════════════════════════════════════════
# 3. PATTERNS D'ANCRES
# ═══════════════════════════════════════════════════════════════
ANCHOR_PATTERNS = [
    ("Article",        r"(?i)\b(?:article|art\.?)\s*(\d+)"),
    ("Recommendation", r"(?i)\b(?:recommendation|recommandation)\s*(\d+)"),
    ("Section",        r"(?i)\b(?:section)\s*(\d+)"),
    ("Chapter",        r"(?i)\b(?:chapitre|chapter)\s*(\d+)"),
    ("Paragraph",      r"(?i)\b(?:paragraphe?|paragraph|§)\s*(\d+)"),
    ("Numbered",       r"(?m)^\s*(\d+)\.\s+[A-ZÀ-Ü]"),
]

# ═══════════════════════════════════════════════════════════════
# 4. BRANCHE A — Pattern-Based Chunking
# ═══════════════════════════════════════════════════════════════
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", "1500"))

# ═══════════════════════════════════════════════════════════════
# 5. BRANCHE B — Semantic Chunking
# ═══════════════════════════════════════════════════════════════
# BAAI/bge-base-en-v1.5 : 768 dims, top MTEB, optimisé pour le retrieval
# Compatible sentence-transformers, entraîné sur du contrastive learning
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")

SEMANTIC_SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.45"))
MIN_CHUNK_CHARS = int(os.getenv("MIN_CHUNK_CHARS", "200"))

# ═══════════════════════════════════════════════════════════════
# 6. THRESHOLD_PATTERNS — Détection de seuils financiers
# ═══════════════════════════════════════════════════════════════
# Couverture : TND, EUR, USD, GBP, MAD, DZD, EGP, SAR, AED, CHF, JPY, CNY...
# + pourcentages + ordres de grandeur
THRESHOLD_PATTERNS = [
    # Montants + devise/nom (number THEN currency)
    r"\d[\d\s.,]*(?:\s?(?:k|K|m|M))?\s*(?:"
    r"TND|TDN|DT|dinars?|dinar(?:\s+tunisien)?s?|"
    r"EUR|€|euros?|"
    r"USD|\$|dollars?|"
    r"GBP|£|pounds?|sterling|"
    r"MAD|DHS?|dirhams?|"
    r"DZD|dinars?\s+alg[ée]riens?|"
    r"EGP|livres?\s+[ée]gyptiennes?|"
    r"SAR|riyals?|"
    r"AED|dirhams?\s+(?:des\s+[ée]mirats|uae)|"
    r"QAR|KWD|BHD|OMR|CHF|"
    r"JPY|yen|CNY|RMB|yuan"
    r")\b",
    # Currency symbol BEFORE number ($10,000 / €5000 / £200)
    r"[\$€£]\s*\d[\d\s.,]*",
    # Pourcentages
    r"\d[\d\s.,]*(?:\s?(?:k|K|m|M))?\s*%",
    # Ordres de grandeur (sans devise explicite)
    r"\d[\d\s.,]*(?:\s?(?:k|K|m|M))?\s*"
    r"(?:mille|millions?|milliards?|thousand|millions?|billions?)\b",
]

# ═══════════════════════════════════════════════════════════════
# 7. ROLE_KEYWORDS — ENGLISH ONLY (8 rôles AML/KYC)
# ═══════════════════════════════════════════════════════════════
# Tous les documents sont traduits en anglais → pas besoin du français.
# 8 rôles couvrant l'intégralité du spectre réglementaire AML/KYC :
#   SANCTION, OBLIGATION, PROCEDURE, THRESHOLD,
#   DEFINITION, RED_FLAG, REPORTING, EXEMPTION

ROLE_KEYWORDS = {

    # ═══ 1. SANCTION (Penalty / Consequence) ═══
    "SANCTION": [
        r"\bpenalt(?:y|ies)\b", r"\bfine[sd]?\b", r"\bfining\b",
        r"\bviolation\b", r"\binfringement\b", r"\boffence\b", r"\boffense\b",
        r"\bbreached?\b", r"\bbreach\b",
        r"\bprohibit(?:ed|ion)?\b",
        r"\bsuspend\b", r"\bsuspension\b",
        r"\bimprison(?:ment)?\b", r"\bjail\b",
        r"\bprosecute?\b", r"\bprosecution\b",
        r"\brevok(?:e|ation)\b", r"\bwithdraw\b",
        r"\bclosure\b", r"\blicence\s+withdrawal\b",
        r"\bliability\b", r"\bsanction(?:s|ed)?\b",
        r"\bpunish(?:ment)?\b",
        r"\benforce(?:ment|d)?\b",
        r"\badministrative\s+fine\b",
        r"\bdebarment\b", r"\bblacklist\b", r"\blisted\b",
    ],

    # ═══ 2. OBLIGATION (Duty / Must / Shall) ═══
    "OBLIGATION": [
        r"\bshall\b", r"\bshall\s+not\b",
        r"\bmust\b", r"\bmust\s+not\b",
        r"\brequire[sd]?\b",
        r"\bmandatory\b", r"\bobligat(?:ory|ion|ions)\b",
        r"\bcompl(?:y|iance)\b",
        r"\bensure[sd]?\b",
        r"\bmandate[sd]?\b",
        r"\bresponsib(?:le|ility)\b",
        r"\bdut(?:y|ies)\b",
        r"\binstitution.*shall\b",
        r"\brequired\s+to\b",
        r"\bmust.*(?:apply|have|maintain)\b",
        r"\bneeds?\s+to\b",
        r"\bmay\s+not\b",
    ],

    # ═══ 3. PROCEDURE (Process / Steps / How-to) ═══
    "PROCEDURE": [
        r"\bprocedures?\b",
        r"\bprocess(?:es)?\b",
        r"\bsteps?\b", r"\bstages?\b", r"\bphases?\b",
        r"\bworkflow\b",
        r"\bverif(?:y|ied|ication)\b",
        r"\bcontrol(?:s|ling)?\b",
        r"\bcheck(?:s|ing)?\b",
        r"\bexamin(?:e|ation|ing)\b",
        r"\bidentif(?:y|ied|ication|ying)\b",
        r"\binspect(?:ion)?\b",
        r"\baudit(?:s|ing|or)?\b",
        r"\bdocument(?:ation|s)?\b",
        r"\brecord(?:s|ing)?\b",
        r"\bsubmi(?:t|ssion)\b",
        r"\bapprov(?:e|al)\b",
        r"\bvalidat(?:e|ion)\b",
        r"\bdue\s+diligence\b",
        r"\bKYC\b", r"\bCDD\b", r"\bEDD\b",
        r"\bstep\s+by\s+step\b",
    ],

    # ═══ 4. THRESHOLD (Amount / Limit / Deadline) ═══
    "THRESHOLD": [
        r"\bthresholds?\b",
        r"\blimits?\b",
        r"\bamounts?\b",
        r"\bexceed(?:s|ing)?\b",
        r"\bminim(?:um|a)\b", r"\bmaxim(?:um|a)\b",
        r"\bgreater\s+than\b", r"\bless\s+than\b",
        r"\bdeadline\b", r"\bdue\s+date\b",
        r"\bimmediately\b", r"\bwithout\s+delay\b",
        r"\bwithin\s+\d+\s+(?:day|week|month|year)",
        r"\bperiods?\b", r"\btime\s+frame\b",
        r"\bcosts?\b", r"\bfees?\b",
        r"\brat(?:e|io)\b", r"\bpercentage\b",
        r"\$\s*\d+", r"€\s*\d+",
        r"\d+\s*%",
        r"\d+\s+(?:dollars?|euros?|pounds?)",
        r"\bsignificant\s+amount\b",
    ],

    # ═══ 5. DEFINITION (Means / Refers to) ═══
    "DEFINITION": [
        r"\bdefined\s+as\b", r"\bdefinitions?\b",
        r"\bmeans\b", r"\bmeaning\b",
        r"\bshall\s+mean\b",
        r"\brefers?\s+to\b",
        r"\bconsidered\s+to\b", r"\bdeemed\b",
        r"\bwithin\s+the\s+meaning\b",
        r"\bfor\s+purposes?\s+of\b",
        r"\binterpret(?:ation)?\b",
        r"\bterminology\b", r"\bterms?\b",
        r"\binclude[sd]?\b", r"\bincluding\b",
        r"\bcomprise[sd]?\b",
        r"\bsuch\s+as\b",
        r"\bdenotes?\b",
        r"\bis\s+understood\b",
    ],

    # ═══ 6. RED_FLAG (Risk Indicator / Suspicious) ═══
    "RED_FLAG": [
        r"\bred\s+flags?\b",
        r"\brisks?\b", r"\brisky\b",
        r"\bsuspect\b", r"\bsuspicious\b", r"\bsuspicion\b",
        r"\balerts?\b",
        r"\banomal(?:ous|y)\b",
        r"\bunusual\b", r"\babnormal\b", r"\batypical\b",
        r"\bscrutiny\b",
        r"\bstructuring\b", r"\bsmurfing\b",
        r"\bfraud(?:ulent)?\b", r"\bscam\b",
        r"\bindicat(?:ion|or)\b",
        r"\bwarnings?\b",
        r"\bshell\s+company\b",
        r"\bmoney\s+laundering\b",
        r"\bterrorist\s+financing\b",
        r"\bfinancial\s+crime\b",
        r"\bhigh[\s-]risk\b",
        r"\bpolitically\s+exposed\b",
    ],

    # ═══ 7. REPORTING (SAR / Notification / Disclosure) ═══
    "REPORTING": [
        r"\breport(?:s|ing)?\b",
        r"\bsuspicious\s+activity\s+report\b", r"\bSAR\b",
        r"\bdeclar(?:e|ation)\b",
        r"\bdisclos(?:e|ure)\b",
        r"\bnotif(?:y|ication)\b",
        r"\bcommunicat(?:e|ion)\b",
        r"\bFIU\b", r"\bFIC\b",
        r"\bregulatory\s+authority\b",
        r"\blaw\s+enforcement\b",
        r"\bauthorities\b",
        r"\bpromptly\b",
        r"\bfile\s+a\s+report\b",
        r"\bshall.*report\b",
        r"\bmust.*report\b",
        r"\brequired.*to.*report\b",
    ],

    # ═══ 8. EXEMPTION (Exception / Derogation) ═══
    "EXEMPTION": [
        r"\bexempt(?:ion|ions|ed)?\b",
        r"\bexcept(?:ion|ed)?\b",
        r"\bunless\b",
        r"\bnot\s+applicable\b",
        r"\bdispens(?:ed?|ation)\b",
        r"\bderogation\b",
        r"\bnotwithstanding\b",
        r"\bprovided\s+that\b",
        r"\bnot\s+required\b",
        r"\brelieved?\s+of\b",
        r"\bcase[\s-]by[\s-]case\b",
        r"\bnot\s+subject\s+to\b",
        r"\bexclud(?:e|ed|sion)\b",
    ],
}

# Rôle par défaut quand aucun mot-clé ne matche
DEFAULT_ROLE = "INFORMATION"

# ═══════════════════════════════════════════════════════════════
# 8. TYPES DE TRANSACTION — détection par mots-clés
#    Utilisé par le builder Neo4j pour tagger les LegalUnits
#    quand les chunks ne portent pas de champ transaction_types.
# ═══════════════════════════════════════════════════════════════
TRANSACTION_TYPE_KEYWORDS: dict[str, list[str]] = {

    "VIREMENT_LOCAL": [
        r"\bdomestic\s+(?:bank\s+)?transfer\b",
        r"\blocal\s+(?:bank\s+)?transfer\b",
        r"\bnational\s+(?:payment|transfer)\b",
        r"\bintra[-\s]?bank\b",
        r"\bdomestic\s+payment\b",
        r"\binterbank\s+transfer\b",
        r"\btransfer\s+within\s+(?:the\s+)?country\b",
        r"\bdomestic\s+(?:wire|funds?\s+transfer)\b",
    ],

    "SWIFT": [
        r"\bSWIFT\b",
        r"\bcorrespondent\s+bank(?:ing)?\b",
        r"\bBIC\b",
        r"\bIBAN\b",
        r"\bcross[-\s]?border\s+(?:payment|transfer)\b",
        r"\bwire\s+transfer\b",
        r"\btravel\s+rule\b",
        r"\binternational\s+(?:wire\s+|funds?\s+)?transfer\b",
        r"\bforeign\s+(?:currency\s+)?transfer\b",
        r"\btransfer\s+of\s+funds\b.*\binternational\b",
        r"\bpayment\s+service\b.*\bcross[-\s]?border\b",
    ],

    "MANDAT": [
        r"\bmoney\s+(?:transfer|order)\b",
        r"\bMVTS\b",
        r"\bmoney\s+(?:or\s+value\s+)?transfer\s+services?\b",
        r"\bWestern\s+Union\b",
        r"\bMoneyGram\b",
        r"\bhawala\b",
        r"\bremittance\b",
        r"\bmoney\s+service\s+business(?:es)?\b",
        r"\bMSB\b",
        r"\binformal\s+(?:value\s+)?transfer\b",
        r"\bcash\s+courier\b",
        r"\bvalue\s+transfer\s+services?\b",
    ],

    "DEPOT_ESPECES": [
        r"\bcash\s+deposit\b",
        r"\bcash\s+transaction(?:s)?\b",
        r"\bcurrency\s+transaction\s+report\b",
        r"\bCTR\b",
        r"\bstructuring\b",
        r"\bsmurfing\b",
        r"\bcash\s+threshold\b",
        r"\bphysical\s+currency\b",
        r"\bbanknotes?\b",
        r"\bcash[-\s]?intensive\b",
        r"\bsource\s+of\s+(?:the\s+)?funds?\b",
        r"\bcash\s+handling\b",
        r"\bdépôts?\s+(?:en\s+)?espèces\b",
        r"\bversement\s+(?:en\s+)?espèces\b",
        r"\bnuméraire\b",
    ],
}

# ═══════════════════════════════════════════════════════════════
# 9. JURIDICTIONS
# ═══════════════════════════════════════════════════════════════
JURISDICTION_MAP = {
    "tunisia":       "Tunisia",
    "international": "International",
    "france":        "France",
    "eu":            "European Union",
}
