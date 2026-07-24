# -*- coding: utf-8 -*-
"""
import_neo4j_creds.py — Injecte le mot de passe Neo4j Aura dans cloud_env.ps1
                        SANS aucune re-saisie manuelle (évite les erreurs de
                        lecture l/1/I/o/0).

Usage :
  1. Dans la console Aura → instance → menu ⋮ → « Recover Database Credentials »
     (ou le bouton « Download » de l'écran de création) → TÉLÉCHARGER le fichier.
  2. python import_neo4j_creds.py
     → cherche le fichier d'identifiants (dossier projet + Téléchargements),
       en extrait NEO4J_PASSWORD, et met à jour cloud_env.ps1.
  3. . .\\cloud_env.ps1  puis  python test_cloud.py
"""
import glob
import os
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = Path(__file__).resolve().parent
CLOUD_ENV = BASE / "cloud_env.ps1"


def dossiers_candidats():
    yield BASE
    dl = Path(os.path.expanduser("~")) / "Downloads"
    if dl.exists():
        yield dl
    dl2 = Path(os.path.expanduser("~")) / "Téléchargements"
    if dl2.exists():
        yield dl2


def trouver_fichiers():
    """Fichiers ressemblant à des identifiants Aura, les plus récents d'abord."""
    motifs = ["*eo4j*.txt", "*eo4j*.env", "*Neo4j*", "*aura*.txt",
              "*credentials*.txt", "*225b0a2e*"]
    vus = {}
    for d in dossiers_candidats():
        for m in motifs:
            for p in glob.glob(str(d / m)):
                pp = Path(p)
                if pp.is_file():
                    vus[str(pp)] = pp.stat().st_mtime
    return [Path(p) for p, _ in sorted(vus.items(), key=lambda x: -x[1])]


def _val(texte: str, *cles):
    for cle in cles:
        m = re.search(rf"{cle}\s*[=:]\s*[\"']?([^\s\"'\r\n]+)", texte)
        if m:
            return m.group(1)
    return None


def extraire_identifiants(texte: str):
    """Retourne (uri, user, password) depuis un fichier d'identifiants Aura."""
    return (
        _val(texte, "NEO4J_URI", "NEO4J_URL"),
        _val(texte, "NEO4J_USERNAME", "NEO4J_USER"),
        _val(texte, "NEO4J_PASSWORD", "PASSWORD", "Password"),
    )


def main():
    fichiers = trouver_fichiers()
    if not fichiers:
        print("Aucun fichier d'identifiants trouvé.")
        print("→ Télécharge le fichier depuis Aura (Recover Database Credentials)")
        print("  et place-le dans le dossier du projet ou dans Téléchargements,")
        print("  puis relance ce script.")
        return 1

    print("Fichiers examinés (du plus récent au plus ancien) :")
    uri = user = pw = None
    source = None
    for f in fichiers[:8]:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        u, us, p = extraire_identifiants(txt)
        marque = " → identifiants trouvés" if p else ""
        print(f"  - {f.name}{marque}")
        if p and pw is None:
            uri, user, pw, source = u, us, p, f

    if not pw:
        print("\nAucun NEO4J_PASSWORD dans ces fichiers.")
        print("Ouvre le fichier téléchargé et vérifie qu'il contient une ligne")
        print("du type  NEO4J_PASSWORD=...")
        return 1

    if not CLOUD_ENV.exists():
        print(f"\n{CLOUD_ENV.name} introuvable — copie d'abord cloud_env.example.ps1.")
        return 1

    contenu = CLOUD_ENV.read_text(encoding="utf-8")
    maj = []
    contenu, n = re.subn(r'(\$env:NEO4J_PASSWORD\s*=\s*)"[^"]*"',
                         rf'\g<1>"{pw}"', contenu)
    if n:
        maj.append(f"mot de passe ({len(pw)} car.)")
    # L'URI change si l'instance a été recréée → on la met à jour aussi.
    if uri:
        contenu, n2 = re.subn(r'(\$env:NEO4J_URI\s*=\s*)"[^"]*"',
                              rf'\g<1>"{uri}"', contenu)
        if n2:
            maj.append(f"URI ({uri})")
    if user:
        contenu = re.sub(r'(\$env:NEO4J_USER\s*=\s*)"[^"]*"',
                         rf'\g<1>"{user}"', contenu)

    CLOUD_ENV.write_text(contenu, encoding="utf-8")
    print(f"\n✅ Injecté depuis : {source.name}")
    for m in maj:
        print(f"   • {m}")
    print("\nÉtape suivante :")
    print("   . .\\cloud_env.ps1")
    print("   .\\venv\\Scripts\\python.exe test_cloud.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
