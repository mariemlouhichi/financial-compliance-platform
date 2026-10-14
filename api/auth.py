"""
api/auth.py — Authentification maison (comptes + confirmation email + token)
==============================================================================
Choix PFE : implémentation légère SANS dépendance nouvelle.
  • Mots de passe : PBKDF2-SHA256 (hashlib, 200k itérations, sel aléatoire)
  • Confirmation  : code 6 chiffres "envoyé par email" — SMTP réel si les
    variables d'env SMTP_HOST/SMTP_USER/SMTP_PASSWORD sont définies, sinon
    le code est affiché dans la console serveur (mode démo).
  • Session       : token signé HMAC-SHA256 (payload base64 + signature),
    expiration 12h. Équivalent JWT sans lib externe.
  • Stockage      : SQLite (platform.db, table users).

Keycloak (OAuth2/SSO) = PERSPECTIVE documentée — cette couche expose la même
interface (register/login/roles) et serait remplaçable sans toucher au front.

RÔLES : USER (agent bancaire, espace utilisateur) · ADMIN ·
        COMPLIANCE_OFFICER (espace admin).
"""

import base64
import hashlib
import hmac
import json
import os
import random
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "platform.db"
SECRET_PATH = BASE / "output" / "api_secret.key"

ROLES = {"USER", "ADMIN", "COMPLIANCE_OFFICER"}
TOKEN_TTL = 12 * 3600  # 12h

_db_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════
#  SECRET DE SIGNATURE (persisté — les tokens survivent au redémarrage)
# ═══════════════════════════════════════════════════════════════════════

def _secret() -> bytes:
    SECRET_PATH.parent.mkdir(exist_ok=True)
    if SECRET_PATH.exists():
        return SECRET_PATH.read_bytes()
    s = secrets.token_bytes(32)
    SECRET_PATH.write_bytes(s)
    return s

SECRET = _secret()


# ═══════════════════════════════════════════════════════════════════════
#  BASE UTILISATEURS (SQLite)
# ═══════════════════════════════════════════════════════════════════════

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _db_lock, _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username    TEXT PRIMARY KEY,
                email       TEXT UNIQUE NOT NULL,
                pw_hash     TEXT NOT NULL,
                role        TEXT NOT NULL,
                verified    INTEGER DEFAULT 0,
                verify_code TEXT,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
        # Migrations idempotentes (ADD COLUMN échoue si la colonne existe déjà).
        for col in ("reset_code TEXT",
                    "full_name TEXT",       # nom affiché de l'utilisateur
                    "profile TEXT"):        # coordonnées KYC (JSON) → client scoring
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
#  MOTS DE PASSE (PBKDF2) & TOKENS (HMAC)
# ═══════════════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, expected = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(dk.hex(), expected)
    except Exception:
        return False


def make_token(username: str, role: str) -> str:
    payload = json.dumps({"u": username, "r": role,
                          "exp": int(time.time()) + TOKEN_TTL}).encode()
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(SECRET, b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def decode_token(token: str) -> Optional[dict]:
    try:
        b64, sig = token.split(".")
        expected = hmac.new(SECRET, b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        pad = "=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(b64 + pad))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def current_user(authorization: Optional[str]) -> Optional[dict]:
    """Extrait {u, r} depuis 'Authorization: Bearer <token>' (None si absent)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return decode_token(authorization[7:])


# ═══════════════════════════════════════════════════════════════════════
#  "EMAIL" — SMTP réel si configuré, sinon console (démo)
# ═══════════════════════════════════════════════════════════════════════
#  Deux façons de configurer (par ordre de priorité) :
#    1. fichier smtp_config.json à la racine du projet — GITIGNORÉ, même
#       convention que groq_keys.json → AUCUNE manipulation terminal requise.
#       PRIORITAIRE : d'anciennes variables $env:SMTP_* oubliées dans un
#       terminal ne peuvent plus écraser la config (piège vécu).
#    2. variables d'environnement SMTP_* (repli si le fichier est absent).
#  Révoquer le mot de passe d'application après la soutenance.

SMTP_FILE = BASE / "smtp_config.json"


def _smtp_config() -> dict:
    """Config SMTP fusionnée : smtp_config.json (PRIORITAIRE) puis env."""
    cfg = {}
    if SMTP_FILE.exists():
        try:
            cfg = json.loads(SMTP_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [AUTH] ⚠️ smtp_config.json illisible ({e}) — ignoré")

    def get(env_key: str, default: str = "") -> str:
        return (str(cfg.get(env_key.lower(), "")).strip()
                or (os.getenv(env_key) or "").strip() or default)

    return {
        "host": get("SMTP_HOST"),
        "port": int(get("SMTP_PORT", "587") or 587),
        "user": get("SMTP_USER"),
        # les mots de passe d'application Gmail se collent souvent AVEC des
        # espaces → on les retire systématiquement (piège récurrent)
        "password": get("SMTP_PASSWORD").replace(" ", ""),
        "from": get("SMTP_FROM") or get("SMTP_USER") or "noreply@amlkyc.local",
    }


def _send_email(email: str, subject: str, body: str,
                console_label: str, code: str):
    """Envoie un email via SMTP si configuré, sinon affiche le code en console
    (mode démo). Générique : réutilisé par la confirmation ET la réinitialisation."""
    smtp = _smtp_config()
    if smtp["host"]:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.utils import formataddr

            msg = MIMEText(body, _charset="utf-8")
            msg["Subject"] = subject
            # FROM séparé : certains relais (Brevo…) ont un login SMTP
            # qui n'est pas une adresse email — l'expéditeur doit en être une.
            msg["From"] = formataddr(("AML/KYC Platform", smtp["from"]))
            msg["To"] = email
            # timeout : ne jamais bloquer la requête si le SMTP ne répond pas
            with smtplib.SMTP(smtp["host"], smtp["port"], timeout=15) as s:
                s.starttls()
                s.login(smtp["user"], smtp["password"])
                s.send_message(msg)
            print(f"  [AUTH] 📧 {console_label} envoyé par email à {email}")
            return
        except Exception as e:
            print(f"  [AUTH] ⚠️ SMTP indisponible ({e}) — repli console")
    # Mode démo : le code est visible côté serveur
    print(f"  [AUTH] 📧 (démo) {console_label} pour {email} : {code}")


def send_verification_email(email: str, code: str):
    body = (
        "Bonjour,\n\n"
        "Voici votre code de confirmation pour activer votre compte "
        "sur la plateforme AML/KYC :\n\n"
        f"        {code}\n\n"
        "Ce code est à usage unique et n'expire qu'à sa première "
        "utilisation. Si vous n'êtes pas à l'origine de cette demande, "
        "ignorez cet email.\n\n"
        "— AML/KYC Platform (ne pas répondre à cet email)"
    )
    _send_email(email, "Votre code de confirmation — AML/KYC Platform",
                body, "Code de confirmation", code)


def send_decision_email(email: str, tx_id: str, approved: bool,
                        tx: dict = None, annotation: str = ""):
    """Email HTML au client quand le superviseur tranche son dossier :
    bandeau VERT (approuvé) ou ROUGE (refusé), récapitulatif de l'opération.
    Repli texte + repli console — l'envoi n'est jamais bloquant."""
    tx = tx or {}
    color = "#059669" if approved else "#DC2626"
    color_soft = "#ECFDF5" if approved else "#FEF2F2"
    icon = "✓" if approved else "✕"
    titre = "Transaction approuvée" if approved else "Transaction refusée"
    message = ("Votre opération a été validée par la conformité et peut être "
               "exécutée." if approved else
               "Votre opération a été refusée par la conformité. Contactez "
               "votre agence pour plus d'informations.")
    montant = f"{tx.get('amount', 0):,} {tx.get('currency', '')}".replace(",", " ")

    html = f"""\
<!DOCTYPE html>
<html lang="fr"><body style="margin:0;padding:0;background:#F1F5F9;
  font-family:'Segoe UI',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="background:#F1F5F9;padding:32px 12px;"><tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0"
  style="background:#FFFFFF;border-radius:16px;overflow:hidden;
  box-shadow:0 4px 16px rgba(15,23,42,.08);">
  <!-- Bandeau de statut -->
  <tr><td style="background:{color};padding:28px 32px;text-align:center;">
    <div style="display:inline-block;width:52px;height:52px;line-height:52px;
      border-radius:50%;background:rgba(255,255,255,.2);color:#FFFFFF;
      font-size:26px;font-weight:bold;">{icon}</div>
    <p style="margin:14px 0 0;color:#FFFFFF;font-size:20px;font-weight:700;">
      {titre}</p>
    <p style="margin:6px 0 0;color:rgba(255,255,255,.85);font-size:13px;">
      Dossier {tx_id}</p>
  </td></tr>
  <!-- Corps -->
  <tr><td style="padding:28px 32px;">
    <p style="margin:0 0 18px;color:#334155;font-size:14px;line-height:1.6;">
      Bonjour,<br>{message}</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
      style="background:{color_soft};border-radius:10px;">
      <tr><td style="padding:16px 20px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
          style="font-size:13px;color:#334155;">
          <tr><td style="padding:4px 0;color:#64748B;">Référence</td>
              <td align="right" style="padding:4px 0;font-weight:600;
                font-family:Consolas,monospace;">{tx_id}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Type d'opération</td>
              <td align="right" style="padding:4px 0;font-weight:600;">
                {tx.get('tx_type', '—')}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Montant</td>
              <td align="right" style="padding:4px 0;font-weight:700;">
                {montant}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Bénéficiaire</td>
              <td align="right" style="padding:4px 0;font-weight:600;">
                {tx.get('beneficiary_name', '—')}</td></tr>
          <tr><td style="padding:4px 0;color:#64748B;">Statut</td>
              <td align="right" style="padding:4px 0;font-weight:700;
                color:{color};">{titre.upper()}</td></tr>
        </table>
      </td></tr>
    </table>
    {f'<p style="margin:18px 0 0;padding:12px 16px;background:#F8FAFC;border-left:3px solid {color};border-radius:0 8px 8px 0;color:#475569;font-size:13px;font-style:italic;">Note de la conformité : {annotation}</p>' if annotation else ''}
    <p style="margin:22px 0 0;color:#94A3B8;font-size:12px;line-height:1.5;">
      Retrouvez le détail complet de l'analyse dans votre espace
      « Mes dossiers » sur la plateforme.</p>
  </td></tr>
  <!-- Pied -->
  <tr><td style="background:#0F172A;padding:18px 32px;text-align:center;">
    <p style="margin:0;color:#94A3B8;font-size:11px;">
      Financial Compliance Platform — Surveillance Multi-Agent</p>
  </td></tr>
</table></td></tr></table></body></html>"""

    text = (f"{titre} — Dossier {tx_id}\n\n{message}\n\n"
            f"Type : {tx.get('tx_type', '-')} | Montant : {montant} | "
            f"Bénéficiaire : {tx.get('beneficiary_name', '-')}\n"
            + (f"Note : {annotation}\n" if annotation else "")
            + "\n— Financial Compliance Platform")

    smtp = _smtp_config()
    label = f"Décision ({'APPROUVÉE' if approved else 'REFUSÉE'}) {tx_id}"
    if smtp["host"]:
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.utils import formataddr
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"{'✅' if approved else '❌'} {titre} — {tx_id}"
            msg["From"] = formataddr(("Financial Compliance Platform", smtp["from"]))
            msg["To"] = email
            msg.attach(MIMEText(text, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
            with smtplib.SMTP(smtp["host"], smtp["port"], timeout=15) as s:
                s.starttls()
                s.login(smtp["user"], smtp["password"])
                s.send_message(msg)
            print(f"  [AUTH] 📧 {label} envoyé par email à {email}")
            return
        except Exception as e:
            print(f"  [AUTH] ⚠️ SMTP indisponible ({e}) — décision non emailée")
    print(f"  [AUTH] 📧 (démo) {label} pour {email}")


def send_weekly_report_email(email: str, stats: dict):
    """Rapport hebdomadaire HTML au Compliance Officer / Admin.

    stats attendu : total, legit, flagged, blocked, pending, by_decision (dict),
    top_pattern (str), period (str). Repli console si SMTP indisponible."""
    total = stats.get("total", 0)
    legit = stats.get("legit", 0)
    flagged = stats.get("flagged", 0)
    blocked = stats.get("blocked", 0)
    pending = stats.get("pending", 0)
    period = stats.get("period", "cette semaine")
    by_dec = stats.get("by_decision", {})
    top_pattern = stats.get("top_pattern", "—")
    pct_flag = round(flagged / total * 100, 1) if total else 0

    def bar(label, value, color):
        w = round(value / max(total, 1) * 100)
        return f"""
        <tr><td style="padding:6px 0;font-size:13px;color:#334155;width:120px;">{label}</td>
        <td style="padding:6px 0;"><div style="background:#F1F5F9;border-radius:6px;height:22px;">
          <div style="background:{color};height:22px;border-radius:6px;width:{max(w,3)}%;
            min-width:24px;text-align:right;color:#fff;font-size:11px;font-weight:700;
            line-height:22px;padding-right:6px;">{value}</div></div></td></tr>"""

    dec_colors = {"PASS": "#10b981", "REVIEW": "#f59e0b", "KYC": "#0ea5e9",
                  "RECORD": "#94a3b8", "EDD": "#f97316", "STR": "#ef4444",
                  "BLOCK": "#b91c1c"}
    dec_rows = "".join(bar(d, by_dec[d], dec_colors.get(d, "#94a3b8"))
                       for d in ["PASS", "REVIEW", "EDD", "STR", "BLOCK"]
                       if by_dec.get(d))

    html = f"""\
<!DOCTYPE html><html lang="fr"><body style="margin:0;background:#F1F5F9;
  font-family:'Segoe UI',Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="padding:32px 12px;"><tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
  style="background:#FFFFFF;border-radius:16px;overflow:hidden;
  box-shadow:0 4px 16px rgba(15,23,42,.08);">
  <tr><td style="background:linear-gradient(135deg,#1e293b,#312e81);padding:26px 32px;">
    <p style="margin:0;color:#a5b4fc;font-size:11px;font-weight:700;
      letter-spacing:2px;text-transform:uppercase;">Rapport hebdomadaire</p>
    <p style="margin:6px 0 0;color:#fff;font-size:20px;font-weight:700;">
      Surveillance des opérations — {period}</p>
  </td></tr>
  <tr><td style="padding:24px 32px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="25%" align="center" style="padding:8px;">
        <p style="margin:0;font-size:28px;font-weight:800;color:#1e293b;">{total}</p>
        <p style="margin:2px 0 0;font-size:11px;color:#64748b;">analysées</p></td>
      <td width="25%" align="center" style="padding:8px;">
        <p style="margin:0;font-size:28px;font-weight:800;color:#10b981;">{legit}</p>
        <p style="margin:2px 0 0;font-size:11px;color:#64748b;">légitimes</p></td>
      <td width="25%" align="center" style="padding:8px;">
        <p style="margin:0;font-size:28px;font-weight:800;color:#f97316;">{flagged}</p>
        <p style="margin:2px 0 0;font-size:11px;color:#64748b;">signalées (EDD/STR)</p></td>
      <td width="25%" align="center" style="padding:8px;">
        <p style="margin:0;font-size:28px;font-weight:800;color:#b91c1c;">{blocked}</p>
        <p style="margin:2px 0 0;font-size:11px;color:#64748b;">bloquées</p></td>
    </tr></table>
    <p style="margin:18px 0 8px;font-size:12px;font-weight:700;color:#334155;
      text-transform:uppercase;letter-spacing:.5px;">Répartition des décisions</p>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{dec_rows or '<tr><td style="font-size:13px;color:#94a3b8;padding:8px 0;">Aucune transaction sur la période.</td></tr>'}</table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
      style="margin-top:18px;background:#F8FAFC;border-radius:10px;">
      <tr><td style="padding:14px 18px;font-size:13px;color:#334155;">
        <b>Taux de signalement :</b> {pct_flag}% &nbsp;·&nbsp;
        <b>En attente superviseur :</b> {pending} &nbsp;·&nbsp;
        <b>Pattern dominant :</b> {top_pattern}
      </td></tr>
    </table>
  </td></tr>
  <tr><td style="background:#0f172a;padding:16px 32px;text-align:center;">
    <p style="margin:0;color:#94a3b8;font-size:11px;">
      🛡️ Financial Compliance Platform — Surveillance Multi-Agent<br>
      Rapport automatique · ne pas répondre à cet email.</p>
  </td></tr>
</table></td></tr></table></body></html>"""

    text = (f"Rapport hebdomadaire — {period}\n\n"
            f"Analysées : {total} | Légitimes : {legit} | "
            f"Signalées : {flagged} | Bloquées : {blocked}\n"
            f"Taux de signalement : {pct_flag}% | En attente : {pending} | "
            f"Pattern dominant : {top_pattern}\n\n— Financial Compliance Platform")

    smtp = _smtp_config()
    if smtp["host"]:
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.utils import formataddr
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"📊 Rapport hebdomadaire de conformité — {period}"
            msg["From"] = formataddr(("Financial Compliance Platform", smtp["from"]))
            msg["To"] = email
            msg.attach(MIMEText(text, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
            with smtplib.SMTP(smtp["host"], smtp["port"], timeout=15) as s:
                s.starttls()
                s.login(smtp["user"], smtp["password"])
                s.send_message(msg)
            print(f"  [AUTH] 📧 Rapport hebdomadaire envoyé à {email}")
            return True
        except Exception as e:
            print(f"  [AUTH] ⚠️ SMTP indisponible ({e}) — rapport non emailé")
            return False
    print(f"  [AUTH] 📧 (démo) Rapport hebdomadaire pour {email}")
    return False


def send_reset_email(email: str, code: str):
    body = (
        "Bonjour,\n\n"
        "Vous avez demandé la réinitialisation de votre mot de passe sur la "
        "plateforme AML/KYC. Voici votre code de réinitialisation :\n\n"
        f"        {code}\n\n"
        "Saisissez ce code puis choisissez un nouveau mot de passe. "
        "Si vous n'êtes pas à l'origine de cette demande, ignorez cet email : "
        "votre mot de passe restera inchangé.\n\n"
        "— AML/KYC Platform (ne pas répondre à cet email)"
    )
    _send_email(email, "Réinitialisation de votre mot de passe — AML/KYC Platform",
                body, "Code de réinitialisation", code)


# ═══════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    # L'identité est l'EMAIL (connexion par email) ; username = email en interne
    # (clé primaire + propriétaire des dossiers). full_name = nom affiché.
    email: str = Field(..., min_length=5)
    password: str = Field(..., min_length=6)
    full_name: str = Field("", max_length=80)
    role: str = Field("USER", description="USER | ADMIN | COMPLIANCE_OFFICER")


class VerifyIn(BaseModel):
    email: str
    code: str


class LoginIn(BaseModel):
    email: str
    password: str


@auth_router.post("/register")
def register(body: RegisterIn):
    """Crée le compte (non vérifié) et envoie le code de confirmation par email."""
    if body.role not in ROLES:
        raise HTTPException(400, f"Rôle invalide (attendu : {sorted(ROLES)})")
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Email invalide")
    full_name = body.full_name.strip() or email.split("@")[0]
    code = f"{random.randint(0, 999999):06d}"
    with _db_lock, _db() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, email, pw_hash, role, "
                "verify_code, full_name) VALUES (?,?,?,?,?,?)",
                # username = email : identité unique par email
                (email, email, hash_password(body.password), body.role,
                 code, full_name))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Un compte existe déjà pour cet email")
    send_verification_email(email, code)
    return {"registered": True, "email": email,
            "message": "Compte créé — saisis le code reçu par email pour l'activer."}


class ResendIn(BaseModel):
    email: str


@auth_router.post("/resend")
def resend(body: ResendIn):
    """Regénère et renvoie le code de confirmation (compte non encore activé).

    Indispensable si le premier code est perdu : register refuse un email
    déjà utilisé (409), donc sans cet endpoint l'utilisateur serait bloqué.
    """
    email = body.email.strip().lower()
    code = f"{random.randint(0, 999999):06d}"
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?",
                           (email,)).fetchone()
        if not row:
            raise HTTPException(404, "Email inconnu — crée d'abord un compte")
        if row["verified"]:
            return {"resent": False, "message": "Compte déjà activé — connecte-toi."}
        conn.execute("UPDATE users SET verify_code=? WHERE email=?",
                     (code, email))
        conn.commit()
    send_verification_email(email, code)
    return {"resent": True,
            "message": "Nouveau code envoyé (console serveur en mode démo)."}


@auth_router.post("/verify")
def verify(body: VerifyIn):
    """Active le compte avec le code à 6 chiffres."""
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?",
                           (body.email.strip().lower(),)).fetchone()
        if not row:
            raise HTTPException(404, "Email inconnu")
        if row["verified"]:
            return {"verified": True, "message": "Compte déjà activé."}
        if row["verify_code"] != body.code.strip():
            raise HTTPException(400, "Code incorrect")
        conn.execute("UPDATE users SET verified=1, verify_code=NULL WHERE email=?",
                     (body.email.strip().lower(),))
        conn.commit()
    return {"verified": True, "message": "Compte activé — tu peux te connecter."}


@auth_router.post("/login")
def login(body: LoginIn):
    """Connexion par EMAIL + mot de passe → token de session (12h)."""
    email = body.email.strip().lower()
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?",
                           (email,)).fetchone()
    if not row or not check_password(body.password, row["pw_hash"]):
        raise HTTPException(401, "Email ou mot de passe incorrect")
    if not row["verified"]:
        raise HTTPException(403, "Compte non activé — confirme ton email d'abord")
    return {"token": make_token(row["username"], row["role"]),
            "username": row["username"], "role": row["role"],
            "name": row["full_name"] or row["username"]}


@auth_router.get("/me")
def me(authorization: Optional[str] = Header(None)):
    user = current_user(authorization)
    if not user:
        raise HTTPException(401, "Token absent ou invalide")
    return {"username": user["u"], "role": user["r"]}


# ═══════════════════════════════════════════════════════════════════════
#  PROFIL / COORDONNÉES KYC (alimentent le Client Scoring du pipeline)
# ═══════════════════════════════════════════════════════════════════════
#  Stocké en JSON dans users.profile, au SCHÉMA « raw » de clients_db_loader
#  → le pipeline peut le passer directement à ClientDBLoader._to_profile().

class ProfileIn(BaseModel):
    # ── Les champs correspondent 1-à-1 aux FACTEURS du RiskFactorScorer
    #    (client_scoring_agent.py) — renseigner le profil alimente le scoring. ──
    full_name: str = ""
    nationality: str = ""            # facteur géographique (high_risk/grey_country)
    country_of_residence: str = ""   # facteur géographique (résidence)
    business_type: str = ""          # facteur high_risk_profession
    account_type: str = "INDIVIDUAL" # INDIVIDUAL|BUSINESS|CORRESPONDENT|TRUST
    rib: str = ""                    # RIB UNIQUE de l'utilisateur (auto-rempli au formulaire)
    # ── Informations complémentaires (dossier KYC — informatives) ──
    address: str = ""                # adresse postale
    phone: str = ""                  # téléphone de contact
    marital_status: str = ""         # état civil (personne physique)
    employer: str = ""               # employeur / organisme (personne physique)
    account_opened: str = ""         # date ISO → ancienneté (facteur new_relationship)
    previous_strs: int = 0           # facteur prior_strs (STR antérieures)
    beneficial_owner_identified: bool = True  # facteur no_beneficial_owner (sociétés)
    is_pep: bool = False             # facteur pep_domestic / pep_foreign (R12)
    pep_type: str = ""               # foreign / domestic (si PEP)
    fatca_us_person: bool = False    # « US person » (obligation fiscale FATCA)
    foreign_tax_residence: str = ""  # code pays résidence fiscale étrangère (CRS)
    birth_date: str = ""             # ISO, optionnel


@auth_router.get("/profile")
def get_profile(authorization: Optional[str] = Header(None)):
    user = current_user(authorization)
    if not user:
        raise HTTPException(401, "Token absent ou invalide")
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT profile FROM users WHERE username=?",
                           (user["u"],)).fetchone()
    prof = json.loads(row["profile"]) if row and row["profile"] else {}
    # Le stockage est au schéma « raw » (clients_db) ; on renvoie une vue alignée
    # sur les champs du formulaire pour que la page « Mon profil » se re-remplisse.
    tax = prof.get("tax_residences") or []
    view = {
        "full_name": prof.get("full_name", ""),
        "nationality": prof.get("nationality", ""),
        "country_of_residence": prof.get("country_of_residence", ""),
        "business_type": prof.get("business_type", ""),
        "account_type": prof.get("account_type", "INDIVIDUAL"),
        "rib": prof.get("rib", ""),
        "address": prof.get("address", ""),
        "phone": prof.get("phone", ""),
        "marital_status": prof.get("marital_status", ""),
        "employer": prof.get("employer", ""),
        "account_opened": prof.get("account_open_date", ""),
        "previous_strs": int(prof.get("prior_strs", 0) or 0),
        "beneficial_owner_identified": bool(prof.get("beneficial_owner_identified", True)),
        "is_pep": bool(prof.get("is_pep", False)),
        "pep_type": prof.get("pep_type", ""),
        "fatca_us_person": prof.get("fatca_status") == "US_PERSON",
        "foreign_tax_residence": tax[0] if tax else "",
        "birth_date": prof.get("birth_date", ""),
    }
    return {"profile": view, "complete": bool(prof)}


@auth_router.put("/profile")
def update_profile(body: ProfileIn, authorization: Optional[str] = Header(None)):
    """Enregistre les coordonnées KYC de l'utilisateur. Renseigner son profil
    vaut KYC complété (kyc_complete=True) → pris en compte par le Client Scoring."""
    user = current_user(authorization)
    if not user:
        raise HTTPException(401, "Token absent ou invalide")
    account_type = (body.account_type or "INDIVIDUAL").strip().upper()
    if account_type not in ("INDIVIDUAL", "BUSINESS", "CORRESPONDENT", "TRUST"):
        account_type = "INDIVIDUAL"
    raw = {
        "client_id": user["u"],
        "full_name": body.full_name.strip(),
        "nationality": body.nationality.strip(),
        "country_of_residence": body.country_of_residence.strip(),
        "business_type": body.business_type.strip(),
        "account_type": account_type,
        "rib": body.rib.replace(" ", ""),
        "address": body.address.strip(),
        "phone": body.phone.strip(),
        "marital_status": body.marital_status.strip(),
        "employer": body.employer.strip(),
        "is_pep": bool(body.is_pep),
        "pep_type": body.pep_type.strip(),
        "prior_strs": max(0, int(body.previous_strs or 0)),
        "beneficial_owner_identified": bool(body.beneficial_owner_identified),
        "kyc_complete": True,
        "fatca_status": "US_PERSON" if body.fatca_us_person else "NONE",
    }
    if body.account_opened.strip():
        raw["account_open_date"] = body.account_opened.strip()
    if body.foreign_tax_residence.strip():
        raw["tax_residences"] = [body.foreign_tax_residence.strip().upper()[:2]]
    if body.birth_date.strip():
        raw["birth_date"] = body.birth_date.strip()
    with _db_lock, _db() as conn:
        conn.execute("UPDATE users SET profile=?, full_name=COALESCE(NULLIF(?,''), full_name) "
                     "WHERE username=?",
                     (json.dumps(raw, ensure_ascii=False),
                      body.full_name.strip(), user["u"]))
        conn.commit()

    # ── Upsert dans la BASE CLIENTS (clients_db.json) ────────────────────
    #  Tout utilisateur qui renseigne son profil devient un client CONNU de
    #  la banque : visible par « Vérifier client », le scoring et l'admin.
    #  (En production : PostgreSQL — même interface, cf. clients_db_loader.)
    try:
        from clients_db_loader import ClientDBLoader
        loader = ClientDBLoader(str(BASE / "clients_db.json"))
        loader.add_new_client(user["u"], raw, save=True)
        # le RIB sert aussi d'identifiant de compte → doublon volontaire pour
        # que « Vérifier client » fonctionne avec le RIB comme avec l'email
        if raw.get("rib"):
            loader.add_new_client(raw["rib"], {**raw, "client_id": raw["rib"]},
                                  save=True)
    except Exception as e:
        print(f"  [AUTH] ⚠️ upsert clients_db.json échoué : {e}")

    return {"saved": True, "profile": raw}


def get_user_raw_profile(username: str) -> Optional[dict]:
    """Coordonnées KYC stockées (schéma clients_db) pour le pipeline, ou None."""
    if not username:
        return None
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT profile FROM users WHERE username=?",
                           (username,)).fetchone()
    if not row or not row["profile"]:
        return None
    try:
        return json.loads(row["profile"])
    except Exception:
        return None


def get_user_email(username: str) -> Optional[str]:
    """Adresse email associée à un username (propriétaire d'un dossier).

    Les comptes récents ont `username = email`, mais les anciens gardent un
    username distinct (ex. « user1 ») ; il faut alors résoudre l'email réel
    pour lui envoyer la notification de décision.
    """
    if not username:
        return None
    if "@" in username:
        return username
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT email FROM users WHERE username=?",
                           (username,)).fetchone()
    return row["email"] if row and row["email"] else None


# ═══════════════════════════════════════════════════════════════════════
#  MOT DE PASSE OUBLIÉ (code de réinitialisation par email)
# ═══════════════════════════════════════════════════════════════════════

class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    email: str
    code: str
    new_password: str = Field(..., min_length=6)


@auth_router.post("/forgot")
def forgot_password(body: ForgotIn):
    """Génère un code de réinitialisation et l'envoie par email.

    Réponse VOLONTAIREMENT identique que l'email existe ou non (anti-énumération
    de comptes) : on ne révèle jamais si une adresse est enregistrée.

    Fonctionne AUSSI sur un compte non encore activé : saisir le bon code
    prouve la propriété de l'email — c'est la MÊME preuve que l'activation
    (la réussite du /reset vaut donc activation, cf. reset_password).
    """
    email = body.email.strip().lower()
    generic = {"sent": True,
               "message": "Si un compte existe pour cet email, "
                          "un code de réinitialisation vient d'être envoyé."}
    code = f"{random.randint(0, 999999):06d}"
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT verified FROM users WHERE email=?",
                           (email,)).fetchone()
        if not row:
            # Email inconnu → réponse générique, aucun envoi
            return generic
        conn.execute("UPDATE users SET reset_code=? WHERE email=?", (code, email))
        conn.commit()
    send_reset_email(email, code)
    return generic


@auth_router.post("/reset")
def reset_password(body: ResetIn):
    """Vérifie le code de réinitialisation et remplace le mot de passe."""
    email = body.email.strip().lower()
    with _db_lock, _db() as conn:
        row = conn.execute("SELECT reset_code FROM users WHERE email=?",
                           (email,)).fetchone()
        if not row or not row["reset_code"]:
            raise HTTPException(400, "Aucune demande de réinitialisation en cours")
        if row["reset_code"] != body.code.strip():
            raise HTTPException(400, "Code incorrect")
        # Code OK → nouveau hash, code consommé (mis à NULL).
        # verified=1 : saisir le bon code reçu par email PROUVE la propriété
        # de l'adresse — c'est exactement la même preuve que l'activation,
        # donc un compte non encore activé est activé par la même occasion.
        conn.execute("UPDATE users SET pw_hash=?, reset_code=NULL, verified=1 "
                     "WHERE email=?",
                     (hash_password(body.new_password), email))
        conn.commit()
    return {"reset": True,
            "message": "Mot de passe réinitialisé — tu peux te connecter."}
