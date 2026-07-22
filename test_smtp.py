# -*- coding: utf-8 -*-
"""
test_smtp.py — Teste la connexion SMTP de smtp_config.json en 5 secondes.
==========================================================================
Usage :
  python test_smtp.py                       → teste le login (sans envoyer)
  python test_smtp.py mon@email.com         → login + envoie un email de test

Si BadCredentials : le mot de passe d'application n'appartient PAS au compte
smtp_user. Sur https://myaccount.google.com/apppasswords, vérifie EN HAUT À
DROITE quel compte Google est connecté AVANT de générer — c'est pour CE
compte-là que le mot de passe est créé.
"""
import json
import smtplib
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

cfg = json.loads((Path(__file__).parent / "smtp_config.json")
                 .read_text(encoding="utf-8"))
host = cfg.get("smtp_host", "smtp.gmail.com")
port = int(cfg.get("smtp_port", 587))
user = cfg.get("smtp_user", "")
password = str(cfg.get("smtp_password", "")).replace(" ", "")

print(f"\n  Test SMTP : {user} @ {host}:{port} (mot de passe : {len(password)} caractères)")

try:
    with smtplib.SMTP(host, port, timeout=15) as s:
        s.starttls()
        s.login(user, password)
        print("  ✅ LOGIN ACCEPTÉ — ce couple compte/mot de passe fonctionne !")
        if len(sys.argv) > 1:
            to = sys.argv[1]
            from email.mime.text import MIMEText
            from email.utils import formataddr
            msg = MIMEText("Test d'envoi réussi — AML/KYC Platform.", _charset="utf-8")
            msg["Subject"] = "Test SMTP — AML/KYC Platform"
            msg["From"] = formataddr(("AML/KYC Platform", user))
            msg["To"] = to
            s.send_message(msg)
            print(f"  ✅ Email de test envoyé à {to} (regarde aussi le spam)")
except smtplib.SMTPAuthenticationError as e:
    print(f"  ❌ REFUSÉ ({e.smtp_code}) : le mot de passe n'appartient pas à «{user}».")
    print("     → Le mot de passe d'application est lié au compte Google CONNECTÉ")
    print("       au moment de la génération (vérifie l'avatar en haut à droite")
    print("       sur myaccount.google.com/apppasswords).")
    print("     → Corrige smtp_user OU régénère depuis le bon compte, puis relance ce test.")
except Exception as e:
    print(f"  ❌ Erreur réseau/SMTP : {e}")
