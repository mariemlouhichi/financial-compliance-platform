"""
test_pipeline.py — Unit tests for the chunking pipeline v2.

Run with :  python -m pytest tests/test_pipeline.py -v
Or :        python tests/test_pipeline.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.pattern_scanner import profile_document, _compute_incrementality, _compute_regularity
from core.branch_a_structure import split_by_pattern, split_into_sentences
from core.branch_b_semantic import _cosine_similarity, _merge_small_chunks, RawChunk
from core.role_tagger import classify_role, detect_threshold, get_jurisdiction


# ═══ PROFILER TESTS ═══

def test_incrementality_perfect():
    assert _compute_incrementality([1, 2, 3, 4]) == 1.0

def test_incrementality_gaps():
    assert _compute_incrementality([1, 3, 5]) == 0.0

def test_incrementality_empty():
    assert _compute_incrementality([]) == 0.0

def test_regularity_uniform():
    assert _compute_regularity([0, 500, 1000, 1500, 2000]) > 0.9

def test_regularity_chaotic():
    assert _compute_regularity([0, 10, 5000, 5010, 20000]) < 0.5

def test_profiler_structured():
    text = "\n".join([f"Article {i} : Content for article {i}." for i in range(1, 8)])
    profile = profile_document("test.pdf", text)
    assert profile.is_structured is True

def test_profiler_narrative():
    text = "This guide presents best practices for risk management in banking institutions."
    profile = profile_document("test.pdf", text)
    assert profile.is_structured is False


# ═══ BRANCH A TESTS ═══

def test_split_by_pattern():
    text = "Short intro. Article 1 Content one. Article 2 Content two."
    chunks = split_by_pattern(text, "Article", r"(?i)\b(?:article)\s*(\d+)")
    assert len(chunks) == 2
    assert chunks[0].article_ref == "Article 1"

def test_sentence_splitting():
    text = "Sentence one. Sentence two. Article 3 continues."
    assert len(split_into_sentences(text)) == 3


# ═══ BRANCH B TESTS ═══

def test_cosine_identical():
    assert abs(_cosine_similarity([1, 2, 3], [1, 2, 3]) - 1.0) < 0.001

def test_cosine_orthogonal():
    assert abs(_cosine_similarity([1, 0], [0, 1])) < 0.001

def test_merge_small():
    chunks = [RawChunk("Tiny", "S1", 0), RawChunk("A" * 300, "S2", 1)]
    assert len(_merge_small_chunks(chunks, min_chars=200)) == 1


# ═══ ROLE TAGGER TESTS (8 ROLES) ═══

def test_role_sanction():
    assert classify_role("A penalty of 50000 dollars for the violation") == "SANCTION"

def test_role_obligation():
    assert classify_role("Banks shall ensure compliance with these requirements") == "OBLIGATION"

def test_role_procedure():
    assert classify_role("The verification procedure includes the following steps for CDD") == "PROCEDURE"

def test_role_threshold():
    assert classify_role("Transactions exceeding the threshold amount of 15000") == "THRESHOLD"

def test_role_definition():
    assert classify_role("Beneficial owner is defined as any natural person") == "DEFINITION"

def test_role_red_flag():
    assert classify_role("Unusual transactions and suspicious activity involving shell companies") == "RED_FLAG"

def test_role_reporting():
    assert classify_role("File a suspicious activity report SAR to the FIU immediately") == "REPORTING"

def test_role_exemption():
    assert classify_role("This requirement is not applicable unless the institution") == "EXEMPTION"

def test_role_information():
    assert classify_role("The annual conference took place in Geneva in 2024") == "INFORMATION"


# ═══ THRESHOLD DETECTION ═══

def test_threshold_dinars():
    assert detect_threshold("Amount of 50 000 dinars") is True

def test_threshold_usd():
    assert detect_threshold("Exceeding $10,000 in value") is True

def test_threshold_percent():
    assert detect_threshold("A rate of 10%") is True

def test_threshold_euro():
    assert detect_threshold("Payment of 15000 EUR") is True

def test_threshold_none():
    assert detect_threshold("No financial amount mentioned here") is False

def test_jurisdiction():
    assert get_jurisdiction("tunisia") == "Tunisia"
    assert get_jurisdiction("international") == "International"


# ═══ RUNNER ═══

def run_all_tests():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = failed = 0
    for test_fn in tests:
        try:
            test_fn()
            print(f"  ✅ {test_fn.__name__}")
            passed += 1
        except (AssertionError, AssertionError) as e:
            print(f"  ❌ {test_fn.__name__} : {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {test_fn.__name__} : {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'=' * 40}")
    print(f"Results : {passed} passed, {failed} failed out of {passed + failed}")
    print(f"{'=' * 40}")
    return failed == 0

if __name__ == "__main__":
    print("🧪 Running unit tests\n")
    success = run_all_tests()
    sys.exit(0 if success else 1)
