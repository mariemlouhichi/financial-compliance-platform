"""
decision_workflow.py — v1.0
==============================================================================
COUCHE POST-DÉCISION (human-in-the-loop) — Point 1 du backlog PFE.

POSITIONNEMENT DANS L'ARCHITECTURE
──────────────────────────────────
Le ComplianceAgent produit une DÉCISION (PASS/REVIEW/EDD/STR/BLOCK). Mais une
décision n'est pas une opération : il faut ensuite EXÉCUTER un workflow.

  ┌──────────────────┐   ComplianceDecision    ┌────────────────────────┐
  │ ComplianceAgent  │ ──────────────────────► │   DecisionWorkflow     │
  │ (que décider ?)  │                          │   (que FAIRE ?)        │
  └──────────────────┘                          └───────────┬────────────┘
                                                            │
                    ┌───────────────────────────────────────┼──────────────┐
                    ▼                    ▼                    ▼              ▼
              APPROVED              PENDING_DOCUMENTS    PENDING_REVIEW   BLOCKED
            (auto-exécutée)        (notif. au client     (file            (auto-
                                    → fournir justif.)    superviseur)     rejetée)

CE QUE CE MODULE AJOUTE (n'existait pas avant)
──────────────────────────────────────────────
  1. MACHINE À ÉTATS de la transaction (cycle de vie, pas juste une décision).
  2. MOTEUR DE JUSTIFICATIFS REQUIS : déterministe, chaque document cité avec
     sa source réglementaire (FATF/BCT) → auditable comme le reste du système.
  3. NOTIFICATION à l'utilisateur (abstraction NotificationSink → branchable
     plus tard sur email / Kafka / dashboard, sans changer ce code).
  4. FILE SUPERVISEUR + capture de décision (Approuver/Refuser/Annoter) +
     ÉVÉNEMENT DE FEEDBACK (graine du RLHF — Point 6).

PRINCIPE DE CONCEPTION
──────────────────────
  Ce module CONSOMME la ComplianceDecision (duck-typed) sans modifier le
  ComplianceAgent. Séparation stricte : « quoi décider » (agent) vs « quoi
  faire de la décision » (workflow). Aucune dépendance Docker/LLM → testable
  hors-ligne.

RÉFÉRENCES
──────────
  FATF R10 (CDD / objet économique), R12 (PEP), R16 (travel rule), R24 (BO),
  BCT circulaire 2018-09 (déclaration), loi 2015-26 (KYC). FATCA/CRS = FISCAL,
  marqués séparément (jamais mélangés au risque AML).
"""

import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("decision_workflow")

__version__ = "1.0"


# ═══════════════════════════════════════════════════════════════════════
#  MACHINE À ÉTATS — cycle de vie d'une transaction
# ═══════════════════════════════════════════════════════════════════════

class TxState:
    """États du cycle de vie d'une transaction (≠ décision du compliance agent)."""
    APPROVED          = "APPROVED"           # PASS → exécutée automatiquement
    BLOCKED           = "BLOCKED"            # BLOCK → rejetée automatiquement (absolu)
    PENDING_DOCUMENTS = "PENDING_DOCUMENTS"  # EN COURS — en attente de justificatifs du client
    PENDING_REVIEW    = "PENDING_REVIEW"     # EN COURS — en file d'attente superviseur
    # États terminaux après action superviseur (Point 6)
    APPROVED_BY_SUPERVISOR = "APPROVED_BY_SUPERVISOR"
    REJECTED_BY_SUPERVISOR = "REJECTED_BY_SUPERVISOR"

    # « en cours » = ni exécutée ni rejetée définitivement
    PENDING_STATES = {PENDING_DOCUMENTS, PENDING_REVIEW}


# Décision compliance → comportement de workflow par défaut
#   auto_execute : exécutée sans intervention humaine
#   needs_docs   : peut requérir des justificatifs avant exécution
#   needs_review : passe par un superviseur
DECISION_POLICY = {
    "PASS":   {"auto_execute": True,  "needs_docs": False, "needs_review": False},
    "REVIEW": {"auto_execute": False, "needs_docs": True,  "needs_review": True},
    "EDD":    {"auto_execute": False, "needs_docs": True,  "needs_review": True},
    "STR":    {"auto_execute": False, "needs_docs": False, "needs_review": True},  # STR = déclaration + sign-off
    "BLOCK":  {"auto_execute": False, "needs_docs": False, "needs_review": False}, # blocage absolu
}


# ═══════════════════════════════════════════════════════════════════════
#  CATALOGUE DES JUSTIFICATIFS (chaque document est SOURCÉ)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RequiredDocument:
    """Un justificatif demandé au client — toujours rattaché à sa source."""
    code:       str
    label:      str
    reason:     str
    fatf_rec:   str  = ""
    source:     str  = ""        # référence réglementaire (BCT/loi/FATF)
    category:   str  = "AML"     # AML | FISCAL (FATCA/CRS séparés)
    mandatory:  bool = True

    def to_dict(self) -> dict:
        return asdict(self)


# Catalogue de référence : code → (label, fatf_rec, source, category).
# Chaque entrée est justifiable au jury (source réglementaire explicite).
_DOC_CATALOG = {
    "ECONOMIC_JUSTIFICATION": (
        "Justificatif économique de l'opération (facture, contrat, bon de commande)",
        "R10", "BCT 2018-09 — objet économique de l'opération", "AML"),
    "KYC_IDENTITY": (
        "Pièce d'identité valide + justificatif de domicile (mise à jour KYC)",
        "R10", "Loi 2015-26 art. CDD — identification du client", "AML"),
    "SOURCE_OF_FUNDS": (
        "Justificatif d'origine des fonds (relevés, acte de vente, attestation)",
        "R10", "Diligence renforcée EDD — EBA GL 2021/02", "AML"),
    "PEP_DECLARATION": (
        "Déclaration de patrimoine + approbation senior management (dossier PEP)",
        "R12", "FATF R12 — personnes politiquement exposées", "AML"),
    "BENEFICIAL_OWNER": (
        "Identification du bénéficiaire effectif (registre BO, organigramme)",
        "R24", "FATF R24 — transparence des bénéficiaires effectifs", "AML"),
    "BUSINESS_RELATIONSHIP": (
        "Documentation de la relation d'affaires (contrat-cadre, objet commercial)",
        "R16", "FATF R16 — informations sur le donneur d'ordre/bénéficiaire", "AML"),
    "INTERMEDIARY_BANK_DD": (
        "Questionnaire de diligence correspondant bancaire (Wolfsberg CBDDQ)",
        "R13", "FATF R13 — relations de correspondance bancaire", "AML"),
    # ── Volet FISCAL (séparé de l'AML — ne contribue jamais au risque blanchiment)
    "FATCA_W9": (
        "Formulaire W-9 (auto-certification US person — FATCA)",
        "", "FATCA (HIRE Act 2010) — obligation FISCALE, ≠ AML", "FISCAL"),
    "CRS_SELF_CERT": (
        "Auto-certification de résidence fiscale (CRS / OCDE)",
        "", "CRS (OCDE) — obligation FISCALE, ≠ AML", "FISCAL"),
}


def _make_doc(code: str, mandatory: bool = True) -> RequiredDocument:
    label, rec, src, cat = _DOC_CATALOG[code]
    return RequiredDocument(code=code, label=label, reason=label,
                            fatf_rec=rec, source=src, category=cat,
                            mandatory=mandatory)


# ═══════════════════════════════════════════════════════════════════════
#  MOTEUR DE JUSTIFICATIFS REQUIS (déterministe, sourcé)
# ═══════════════════════════════════════════════════════════════════════

class RequiredDocumentEngine:
    """
    Détermine les justificatifs à demander, à partir de TROIS sources d'info :
      1. les LACUNES de la transaction (champs manquants : justif., KYC…),
      2. les RECOMMANDATIONS FATF des règles violées/applicables (R12, R24…),
      3. le CONTEXTE CLIENT (PEP, FATCA/CRS — fiscal séparé).
    Déterministe et auditable : aucun LLM, chaque doc cite sa source.
    """

    @staticmethod
    def _truthy(v) -> bool:
        return v is True or (isinstance(v, str) and v.strip().lower() in
                             ("true", "yes", "oui", "1"))

    @staticmethod
    def _is_false(v) -> bool:
        """False explicite OU absent → considéré manquant (principe prudentiel)."""
        if v is None:
            return True
        return v is False or (isinstance(v, str) and v.strip().lower() in
                              ("false", "no", "non", "0", ""))

    def required(self, decision: str, transaction: dict,
                 violated_recs: set, client_context: dict = None) -> list:
        """Retourne la liste dédupliquée des RequiredDocument."""
        docs = {}   # code → RequiredDocument

        def add(code, mandatory=True):
            if code not in docs:
                docs[code] = _make_doc(code, mandatory)

        tx = transaction or {}
        cc = client_context or {}
        recs = violated_recs or set()
        needs_docs = DECISION_POLICY.get(decision, {}).get("needs_docs", False)

        # ── 1. LACUNES de la transaction (uniquement si décision non-PASS) ──
        if needs_docs or decision in ("STR",):
            if self._is_false(tx.get("has_justification")):
                add("ECONOMIC_JUSTIFICATION")
            if self._is_false(tx.get("kyc_complete")):
                add("KYC_IDENTITY")
            # EDD → origine des fonds systématiquement
            if decision == "EDD":
                add("SOURCE_OF_FUNDS")
            # Correspondant bancaire (banque intermédiaire renseignée)
            if tx.get("intermediary_bank"):
                add("INTERMEDIARY_BANK_DD")
            # SWIFT gros montant → documentation de la relation d'affaires
            if (tx.get("tx_type") == "SWIFT") and float(tx.get("amount") or 0) >= 10000:
                add("BUSINESS_RELATIONSHIP")

        # ── 2. OBLIGATIONS issues des règles (recommandations FATF) ──────
        if "R12" in recs:
            add("PEP_DECLARATION")
        if "R24" in recs:
            add("BENEFICIAL_OWNER")
        if "R13" in recs:
            add("INTERMEDIARY_BANK_DD")
        if "R10" in recs and decision in ("EDD", "STR"):
            add("SOURCE_OF_FUNDS")

        # ── 3. CONTEXTE CLIENT (PEP + BO) ────────────────────────────────
        if cc.get("level") == "ÉLEVÉ":
            add("SOURCE_OF_FUNDS")
        if cc.get("is_pep") or cc.get("pep"):
            add("PEP_DECLARATION")

        # ── 4. VOLET FISCAL (séparé — n'entre jamais dans le risque AML) ──
        fatca = (cc.get("fatca_obligation") or "")
        if fatca in ("COLLECT_W9",):
            add("FATCA_W9")
        if cc.get("crs_reportable"):
            add("CRS_SELF_CERT")

        # Tri : AML d'abord, puis FISCAL ; obligatoires d'abord
        ordered = sorted(docs.values(),
                         key=lambda d: (d.category != "AML", not d.mandatory, d.code))
        return ordered


# ═══════════════════════════════════════════════════════════════════════
#  NOTIFICATION (abstraction branchable : console / email / Kafka / UI)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Notification:
    """Message structuré envoyé à un destinataire (client ou superviseur)."""
    notif_id:    str
    tx_id:       str
    recipient:   str                # "CLIENT" | "SUPERVISOR" | "COMPLIANCE_OFFICER"
    channel:     str                # "console" | "email" | "kafka" | "dashboard"
    subject:     str
    body:        str
    required_documents: list = field(default_factory=list)  # list[dict]
    deadline:    Optional[str] = None
    created_at:  str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


class NotificationSink:
    """Interface de sortie des notifications (Strategy pattern)."""
    def send(self, notif: Notification) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleNotificationSink(NotificationSink):
    """Sortie console lisible (défaut). En prod : remplacer par email/Kafka/UI."""
    def send(self, notif: Notification) -> None:
        print(f"\n  ┌─ 📨 NOTIFICATION [{notif.recipient}] ─ {notif.channel}")
        print(f"  │  TX {notif.tx_id} · {notif.subject}")
        for line in notif.body.splitlines():
            print(f"  │  {line}")
        if notif.required_documents:
            print(f"  │  Justificatifs demandés :")
            for d in notif.required_documents:
                tag = "FISCAL" if d.get("category") == "FISCAL" else d.get("fatf_rec", "")
                print(f"  │    • [{tag:>6}] {d['label']}")
                print(f"  │             source : {d['source']}")
        if notif.deadline:
            print(f"  │  Échéance : {notif.deadline}")
        print(f"  └─────────────────────────────────────────────")


class JsonlNotificationSink(NotificationSink):
    """Persiste les notifications en JSON-lines (audit / rejouable)."""
    def __init__(self, path="output/notifications.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(exist_ok=True)

    def send(self, notif: Notification) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(notif.to_dict(), ensure_ascii=False) + "\n")


class MultiSink(NotificationSink):
    """Diffuse vers plusieurs sinks (ex: console + jsonl)."""
    def __init__(self, *sinks):
        self.sinks = sinks

    def send(self, notif: Notification) -> None:
        for s in self.sinks:
            try:
                s.send(notif)
            except Exception as e:
                logger.warning(f"Sink {s.__class__.__name__} a échoué : {e}")


# ═══════════════════════════════════════════════════════════════════════
#  FILE SUPERVISEUR + FEEDBACK (graine du RLHF — Point 6)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class WorkflowOutcome:
    """Résultat du traitement post-décision d'une transaction."""
    tx_id:               str
    agent_decision:      str                       # décision du ComplianceAgent
    state:               str                       # TxState
    risk_score:          float = 0.0
    required_documents:  list  = field(default_factory=list)  # list[dict]
    notifications:       list  = field(default_factory=list)  # list[dict]
    needs_supervisor:    bool  = False
    str_required:        bool  = False
    summary:             str   = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FeedbackEvent:
    """
    Annotation du superviseur → signal d'amélioration (RLHF léger).
    Alimentera (Point 6) : ré-entraînement ML (faux positifs), maj des few-shot
    prompts (corrections de règles), révision des seuils par segment.
    """
    tx_id:               str
    agent_decision:      str
    supervisor_action:   str          # APPROVE | REJECT | ANNOTATE
    supervisor_label:    str = ""      # ex: FALSE_POSITIVE | TRUE_FRAUD | CORRECT
    annotation:          str = ""
    supervisor_id:       str = ""
    created_at:          str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


class SupervisorQueue:
    """
    File des transactions « en cours » à valider par un superviseur.
    In-memory ici ; la persistance SQLite + le dashboard sont le Point 6.
    Le seam d'amélioration continue est posé : record_decision() émet un
    FeedbackEvent (persisté en JSON-lines) pour le futur RLHF.
    """
    def __init__(self, feedback_path="output/supervisor_feedback.jsonl"):
        self._pending: dict = {}   # tx_id → WorkflowOutcome
        self.feedback_path = Path(feedback_path)
        self.feedback_path.parent.mkdir(exist_ok=True)

    def enqueue(self, outcome: WorkflowOutcome) -> None:
        self._pending[outcome.tx_id] = outcome

    def pending(self) -> list:
        return list(self._pending.values())

    def get(self, tx_id: str) -> Optional[WorkflowOutcome]:
        return self._pending.get(tx_id)

    def record_decision(self, tx_id: str, supervisor_action: str,
                        supervisor_label: str = "", annotation: str = "",
                        supervisor_id: str = "") -> FeedbackEvent:
        """
        Enregistre la décision du superviseur, retire la transaction de la file,
        et émet un FeedbackEvent (graine RLHF). supervisor_action ∈
        {APPROVE, REJECT, ANNOTATE}.
        """
        outcome = self._pending.pop(tx_id, None)
        agent_decision = outcome.agent_decision if outcome else "?"
        if outcome:
            outcome.state = (TxState.APPROVED_BY_SUPERVISOR
                             if supervisor_action == "APPROVE"
                             else TxState.REJECTED_BY_SUPERVISOR
                             if supervisor_action == "REJECT"
                             else outcome.state)
        evt = FeedbackEvent(
            tx_id=tx_id, agent_decision=agent_decision,
            supervisor_action=supervisor_action, supervisor_label=supervisor_label,
            annotation=annotation, supervisor_id=supervisor_id)
        with open(self.feedback_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt.to_dict(), ensure_ascii=False) + "\n")
        return evt


# ═══════════════════════════════════════════════════════════════════════
#  ORCHESTRATEUR DU WORKFLOW
# ═══════════════════════════════════════════════════════════════════════

class DecisionWorkflow:
    """
    Transforme une ComplianceDecision en ACTIONS opérationnelles :
    état du cycle de vie + justificatifs requis + notifications + mise en file
    superviseur. Rend le système « productible » (exploitable en agence).

    Usage :
        wf = DecisionWorkflow()
        outcome = wf.process(transaction, decision, tx_id="TX-123")
        # ... plus tard, côté superviseur :
        wf.queue.record_decision("TX-123", "APPROVE", supervisor_id="agent007")
    """

    def __init__(self, notifier: NotificationSink = None,
                 queue: SupervisorQueue = None, verbose: bool = True):
        self.notifier = notifier or ConsoleNotificationSink()
        self.queue    = queue or SupervisorQueue()
        self.docs     = RequiredDocumentEngine()
        self.verbose  = verbose
        self._counter = 0

    # ── Extraction duck-typed depuis ComplianceDecision (objet OU dict) ──
    @staticmethod
    def _get(obj, attr, default=None):
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    def _violated_recs(self, decision) -> set:
        recs = set()
        for r in (self._get(decision, "violated_rules", []) or []):
            rec = self._get(r, "fatf_rec", "")
            if rec:
                recs.add(rec)
        return recs

    def process(self, transaction: dict, decision,
                tx_id: str = None, deadline: str = None) -> WorkflowOutcome:
        """
        decision : ComplianceDecision (ou dict avec .decision/.risk_score/
                   .violated_rules/.client_context). transaction : dict d'origine.
        """
        self._counter += 1
        tx_id = tx_id or f"TX-{self._counter:05d}"
        dec   = self._get(decision, "decision", "PASS")
        score = self._get(decision, "risk_score", 0.0) or 0.0
        cc    = self._get(decision, "client_context", {}) or {}
        recs  = self._violated_recs(decision)
        policy = DECISION_POLICY.get(dec, DECISION_POLICY["REVIEW"])

        # ── Justificatifs requis (déterministe, sourcé) ──────────────────
        required = self.docs.required(dec, transaction, recs, cc)
        required_dicts = [d.to_dict() for d in required]

        # ── Détermination de l'état du cycle de vie ──────────────────────
        notifications = []
        str_required  = (dec == "STR")

        if dec == "PASS":
            state = TxState.APPROVED
            summary = "Transaction conforme — exécutée automatiquement."
        elif dec == "BLOCK":
            state = TxState.BLOCKED
            summary = "Blocage réglementaire absolu — transaction rejetée."
            # Alerte interne (officier de conformité), pas le client
            notifications.append(self._notify(
                tx_id, "COMPLIANCE_OFFICER",
                "Transaction bloquée (gel réglementaire)",
                f"Décision {dec} (score {score:.0f}/100). Motifs : "
                f"{', '.join(sorted(recs)) or 'sanctions/pays liste noire'}.",
                [], deadline=None))
        else:
            # REVIEW / EDD / STR → EN COURS
            mandatory_missing = [d for d in required
                                 if d.mandatory and d.category == "AML"]
            if policy["needs_docs"] and mandatory_missing:
                state = TxState.PENDING_DOCUMENTS
                summary = (f"Décision {dec} : justificatifs manquants — "
                           f"transaction EN COURS, notification au client.")
                notifications.append(self._notify(
                    tx_id, "CLIENT",
                    f"Justificatifs requis pour votre opération ({tx_id})",
                    (f"Votre opération nécessite une vérification de conformité "
                     f"({dec}). Merci de fournir les pièces suivantes pour "
                     f"poursuivre le traitement."),
                    required_dicts, deadline=deadline))
            else:
                state = TxState.PENDING_REVIEW
                kind = "déclaration de soupçon (STR)" if str_required else "revue"
                summary = (f"Décision {dec} : transaction EN COURS — mise en "
                           f"file superviseur pour {kind}.")

        outcome = WorkflowOutcome(
            tx_id=tx_id, agent_decision=dec, state=state, risk_score=float(score),
            required_documents=required_dicts,
            notifications=[n.to_dict() for n in notifications],
            needs_supervisor=(state in TxState.PENDING_STATES),
            str_required=str_required, summary=summary)

        # ── Mise en file superviseur si « en cours » ─────────────────────
        if state in TxState.PENDING_STATES:
            self.queue.enqueue(outcome)

        if self.verbose:
            self._print(outcome)

        return outcome

    def _notify(self, tx_id, recipient, subject, body, docs, deadline) -> Notification:
        self._counter_n = getattr(self, "_counter_n", 0) + 1
        notif = Notification(
            notif_id=f"N-{self._counter_n:05d}", tx_id=tx_id, recipient=recipient,
            channel=self.notifier.__class__.__name__.replace("NotificationSink", "").lower() or "console",
            subject=subject, body=body, required_documents=docs, deadline=deadline)
        self.notifier.send(notif)
        return notif

    def _print(self, o: WorkflowOutcome):
        icon = {"APPROVED": "✅", "BLOCKED": "⛔",
                "PENDING_DOCUMENTS": "📨", "PENDING_REVIEW": "👨‍⚖️"}.get(o.state, "•")
        print(f"\n  {icon} WORKFLOW [{o.tx_id}] décision={o.agent_decision} "
              f"→ état={o.state}")
        print(f"     {o.summary}")
        if o.required_documents:
            aml = [d for d in o.required_documents if d['category'] == 'AML']
            fis = [d for d in o.required_documents if d['category'] == 'FISCAL']
            print(f"     Justificatifs : {len(aml)} AML"
                  + (f" + {len(fis)} FISCAL (séparés)" if fis else ""))
        if o.needs_supervisor:
            print(f"     → en file superviseur"
                  + (" (STR à déclarer)" if o.str_required else ""))


# ═══════════════════════════════════════════════════════════════════════
#  DÉMONSTRATION STANDALONE (sans Docker / sans LLM)
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n  DECISION WORKFLOW v{__version__} — démonstration\n" + "=" * 64)

    # Mock minimal d'une ComplianceDecision (duck-typed)
    class _Rule:
        def __init__(self, rec): self.fatf_rec = rec
    class _Dec:
        def __init__(self, decision, score, violated=None, client=None):
            self.decision = decision
            self.risk_score = score
            self.violated_rules = [_Rule(r) for r in (violated or [])]
            self.client_context = client or {}

    wf = DecisionWorkflow(
        notifier=MultiSink(ConsoleNotificationSink(),
                           JsonlNotificationSink()))

    cases = [
        ("Virement local conforme", {
            "tx_type": "VIREMENT_LOCAL", "amount": 1500, "has_justification": True,
            "kyc_complete": True},
         _Dec("PASS", 0)),

        ("SWIFT EDD sans justificatif (PEP)", {
            "tx_type": "SWIFT", "amount": 9000, "currency": "USD",
            "has_justification": False, "kyc_complete": True},
         _Dec("EDD", 45, violated=["R12", "R10"],
              client={"level": "ÉLEVÉ", "is_pep": True,
                      "fatca_obligation": "COLLECT_W9"})),

        ("Dépôt espèces smurfing", {
            "tx_type": "DEPOT_ESPECES", "amount": 4700, "has_justification": False,
            "kyc_complete": True},
         _Dec("STR", 70, violated=["R20"])),

        ("SWIFT Iran sanctionné", {
            "tx_type": "SWIFT", "amount": 21800, "has_justification": False},
         _Dec("BLOCK", 100, violated=["R6", "R19"])),
    ]

    outcomes = []
    for title, tx, dec in cases:
        print(f"\n{'─'*64}\n  CAS : {title}\n{'─'*64}")
        outcomes.append(wf.process(tx, dec, tx_id=f"TX-{title[:6].upper()}"))

    # ── Simulation d'une décision superviseur (feedback / RLHF) ──────────
    print(f"\n{'═'*64}")
    print(f"  FILE SUPERVISEUR : {len(wf.queue.pending())} transaction(s) EN COURS")
    for o in wf.queue.pending():
        print(f"    • {o.tx_id} [{o.state}] décision={o.agent_decision}")

    # Le superviseur annote le STR comme faux positif → graine RLHF
    pend = wf.queue.pending()
    if pend:
        target = pend[0].tx_id
        evt = wf.queue.record_decision(
            target, supervisor_action="ANNOTATE",
            supervisor_label="FALSE_POSITIVE",
            annotation="Origine des fonds justifiée a posteriori — faux positif.",
            supervisor_id="officer_01")
        print(f"\n  ✍️  Feedback superviseur enregistré (graine RLHF) :")
        print(f"     {target} → {evt.supervisor_action}/{evt.supervisor_label}")
        print(f"     persisté dans output/supervisor_feedback.jsonl")

    print(f"\n{'═'*64}\n  ✅ Démonstration terminée.\n")
