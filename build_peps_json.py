"""
build_peps_json.py — Construit data/peps.json depuis data/entities.ftm.json
============================================================================
Diagnostic : le warning "PEPs non trouves" (level1_rules/sanctions_screening.py)
vient de l'ABSENCE de data/peps.json — mais la donnée PEP existe déjà dans le
projet, dans data/entities.ftm.json (format NDJSON OpenSanctions/FollowTheMoney,
1.8M lignes, dataset "wd_peps" = Wikidata Politically Exposed Persons).

Ce fichier mélange TOUS les types d'entités (Occupancy, Person, Company...) de
plusieurs datasets. Il faut filtrer :
  schema == "Person"  ET  "role.pep" in properties.topics

→ 669 096 personnes correspondent. sanctions_screening.py attend un format
précis (liste JSON, schema/id/properties.name/properties.country) — ce script
fait la conversion. Le champ FtM pour un individu est "citizenship" (pas
"country" comme lu par le loader) → mappé ici pour rester compatible SANS
modifier sanctions_screening.py.

USAGE (une seule fois, ~1-2 min, fichier source 1.8M lignes en streaming) :
  python build_peps_json.py
"""

import json
from pathlib import Path

SRC = Path(__file__).parent / "data" / "entities.ftm.json"
OUT = Path(__file__).parent / "data" / "peps.json"


def main():
    if not SRC.exists():
        raise SystemExit(f"❌ Source introuvable : {SRC}")

    print(f"📖 Lecture NDJSON en streaming : {SRC.name}")

    peps = []
    total_lines = 0
    kept = 0

    with open(SRC, encoding="utf-8") as f:
        for line in f:
            total_lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                ent = json.loads(line)
            except json.JSONDecodeError:
                continue

            if ent.get("schema") != "Person":
                continue
            props = ent.get("properties", {})
            topics = props.get("topics", [])
            if "role.pep" not in topics:
                continue

            names = props.get("name", [])
            if not names:
                continue

            # FtM utilise "citizenship" pour les personnes ; "country" en repli
            countries = (props.get("country")
                         or props.get("citizenship")
                         or props.get("nationality")
                         or [])

            peps.append({
                "schema": "Person",
                "id": ent.get("id", ""),
                "properties": {
                    "name": names,
                    "country": countries,
                },
            })
            kept += 1

            if total_lines % 200_000 == 0:
                print(f"  … {total_lines:,} lignes lues, {kept:,} PEPs retenus")

    OUT.write_text(json.dumps(peps, ensure_ascii=False), encoding="utf-8")

    print(f"\n✅ {total_lines:,} lignes lues")
    print(f"✅ {kept:,} PEPs extraits (schema=Person, topics contient role.pep)")
    print(f"✅ Écrit : {OUT} ({OUT.stat().st_size / 1e6:.1f} Mo)")
    print("→ Relance le backend : le warning 'PEPs non trouves' doit disparaître")
    print("  et afficher 'OK PEPs: {n:,} entrees' à la place.")


if __name__ == "__main__":
    main()
