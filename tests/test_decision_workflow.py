"""
test_decision_workflow.py — tests de la couche post-décision (Point 1)
======================================================================
Tests rapides, hors-ligne (aucun Docker / LLM). Couvrent la machine à états,
le moteur de justificatifs sourcés, la file superviseur et le feedback RLHF.

Lancer :  venv/Scripts/python.exe -m pytest test_decision_workflow.py -q
"""

# Ce script vit dans un sous-dossier : on expose la racine du projet aux imports.
import sys as _sys, pathlib as _pathlib
_RACINE = _pathlib.Path(__file__).resolve().parents[1]
if str(_RACINE) not in _sys.path:
    _sys.path.insert(0, str(_RACINE))


import json
import pytest

from decision_workflow import (
    DecisionWorkflow, RequiredDocumentEngine, SupervisorQueue,
    JsonlNotificationSink, TxState,
)


# ── Mocks duck-typés d'une ComplianceDecision ─────────────────────────
class _Rule:
    def __init__(self, rec): self.fatf_rec = rec

class _Dec:
    def __init__(self, decision, score=0.0, violated=None, client=None):
        self.decision = decision
        self.risk_score = score
        self.violated_rules = [_Rule(r) for r in (violated or [])]
        self.client_context = client or {}


@pytest.fixture
def wf(tmp_path):
    """Workflow isolé : notifications + feedback dans un dossier temporaire."""
    sink = JsonlNotificationSink(path=str(tmp_path / "notif.jsonl"))
    queue = SupervisorQueue(feedback_path=str(tmp_path / "feedback.jsonl"))
    return DecisionWorkflow(notifier=sink, queue=queue, verbose=False)


# ══════════════════════════════════════════════════════════════════════
#  MACHINE À ÉTATS
# ══════════════════════════════════════════════════════════════════════

def test_pass_auto_approved(wf):
    out = wf.process({"tx_type": "VIREMENT_LOCAL", "amount": 1500,
                      "has_justification": True, "kyc_complete": True},
                     _Dec("PASS", 0))
    assert out.state == TxState.APPROVED
    assert out.required_documents == []
    assert out.needs_supervisor is False


def test_block_auto_blocked_no_docs(wf):
    out = wf.process({"tx_type": "SWIFT", "amount": 21800},
                     _Dec("BLOCK", 100, violated=["R6", "R19"]))
    assert out.state == TxState.BLOCKED
    assert out.required_documents == []        # blocage absolu : pas de régularisation
    assert out.needs_supervisor is False


def test_edd_missing_docs_pending_documents(wf):
    out = wf.process({"tx_type": "SWIFT", "amount": 9000,
                      "has_justification": False, "kyc_complete": True},
                     _Dec("EDD", 45, violated=["R10"]))
    assert out.state == TxState.PENDING_DOCUMENTS
    assert out.needs_supervisor is True
    codes = {d["code"] for d in out.required_documents}
    assert "ECONOMIC_JUSTIFICATION" in codes
    assert "SOURCE_OF_FUNDS" in codes          # EDD → origine des fonds systématique


def test_str_goes_to_supervisor_review(wf):
    out = wf.process({"tx_type": "DEPOT_ESPECES", "amount": 4700,
                      "has_justification": False, "kyc_complete": True},
                     _Dec("STR", 70, violated=["R20"]))
    assert out.state == TxState.PENDING_REVIEW
    assert out.str_required is True
    assert out.needs_supervisor is True


# ══════════════════════════════════════════════════════════════════════
#  MOTEUR DE JUSTIFICATIFS (sourcé)
# ══════════════════════════════════════════════════════════════════════

def test_pep_requires_r12_declaration(wf):
    out = wf.process({"tx_type": "VIREMENT_LOCAL", "amount": 4000,
                      "has_justification": True, "kyc_complete": True},
                     _Dec("EDD", 45, client={"level": "ÉLEVÉ", "is_pep": True}))
    codes = {d["code"] for d in out.required_documents}
    assert "PEP_DECLARATION" in codes
    pep_doc = next(d for d in out.required_documents if d["code"] == "PEP_DECLARATION")
    assert pep_doc["fatf_rec"] == "R12"
    assert pep_doc["source"]                    # source réglementaire renseignée


def test_beneficial_owner_from_violated_r24(wf):
    out = wf.process({"tx_type": "SWIFT", "amount": 9000,
                      "has_justification": True, "kyc_complete": True},
                     _Dec("EDD", 45, violated=["R24"]))
    codes = {d["code"] for d in out.required_documents}
    assert "BENEFICIAL_OWNER" in codes


def test_fatca_is_fiscal_separated_from_aml(wf):
    out = wf.process({"tx_type": "SWIFT", "amount": 5000,
                      "has_justification": True, "kyc_complete": True},
                     _Dec("EDD", 30, client={"level": "MOYEN",
                                             "fatca_obligation": "COLLECT_W9"}))
    fatca = [d for d in out.required_documents if d["code"] == "FATCA_W9"]
    assert len(fatca) == 1
    assert fatca[0]["category"] == "FISCAL"     # JAMAIS classé AML
    # un doc fiscal ne doit pas être marqué comme une obligation AML
    assert all(d["category"] in ("AML", "FISCAL") for d in out.required_documents)


def test_required_documents_are_deduplicated():
    eng = RequiredDocumentEngine()
    # R10 violé + EDD + has_justification False → SOURCE_OF_FUNDS demandé une seule fois
    docs = eng.required("EDD", {"has_justification": False, "kyc_complete": True},
                        {"R10"}, {"level": "ÉLEVÉ"})
    codes = [d.code for d in docs]
    assert len(codes) == len(set(codes))


# ══════════════════════════════════════════════════════════════════════
#  FILE SUPERVISEUR + FEEDBACK (graine RLHF)
# ══════════════════════════════════════════════════════════════════════

def test_pending_enqueued_and_supervisor_decision(wf, tmp_path):
    out = wf.process({"tx_type": "SWIFT", "amount": 9000,
                      "has_justification": False, "kyc_complete": True},
                     _Dec("EDD", 45), tx_id="TX-1")
    assert any(o.tx_id == "TX-1" for o in wf.queue.pending())

    evt = wf.queue.record_decision("TX-1", "APPROVE", supervisor_label="CORRECT",
                                   supervisor_id="officer_01")
    # retirée de la file après décision
    assert all(o.tx_id != "TX-1" for o in wf.queue.pending())
    assert evt.supervisor_action == "APPROVE"
    assert evt.agent_decision == "EDD"
    # feedback persisté (graine RLHF)
    fb = (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(fb)["supervisor_label"] == "CORRECT"


def test_notification_emitted_to_client_on_pending_docs(wf, tmp_path):
    wf.process({"tx_type": "SWIFT", "amount": 9000, "has_justification": False,
                "kyc_complete": False}, _Dec("EDD", 45), tx_id="TX-N")
    notifs = (tmp_path / "notif.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(notifs) >= 1
    payload = json.loads(notifs[0])
    assert payload["recipient"] == "CLIENT"
    assert payload["required_documents"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
