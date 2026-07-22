"""
convert_un_sanctions.py
=======================
Convertit consolidated.xml (ONU) en JSON optimisé pour lookup O(1).

USAGE :
  python convert_un_sanctions.py --input consolidated.xml --output data/un_sanctions.json

SORTIE :
  {
    "individuals": {
      "AHMED_MOHAMMED": {
        "full_name": "Ahmed Mohammed Al-Qahtani",
        "aliases": ["Abu Mohammed", "..."],
        "nationality": "Saudi Arabia",
        "date_of_birth": "1985-03-12",
        "reason": "Al-Qaida affiliate",
        "listed_on": "2015-06-08"
      },
      ...
    },
    "entities": {
      "AL_NUSRA_FRONT": { ... }
    },
    "metadata": {
      "total_individuals": 1234,
      "total_entities": 567,
      "last_updated": "2025-05-15"
    }
  }
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# Fix encodage Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def normalize_name(name: str) -> str:
    """Normalise un nom pour indexation (majuscules, sans accents)."""
    import unicodedata
    name = name.upper()
    name = ''.join(
        c for c in unicodedata.normalize('NFD', name)
        if unicodedata.category(c) != 'Mn'
    )
    # Remplacer espaces/tirets par underscore
    name = name.replace(' ', '_').replace('-', '_')
    # Supprimer caractères spéciaux
    name = ''.join(c for c in name if c.isalnum() or c == '_')
    return name


def parse_un_xml(xml_path: Path) -> dict:
    """Parse le XML ONU (consolidated list) et extrait individus + entites.

    Structure reelle du fichier:
      CONSOLIDATED_LIST (pas de namespace)
        INDIVIDUALS / INDIVIDUAL
          FIRST_NAME, SECOND_NAME, LISTED_ON, COMMENTS1
          NATIONALITY/VALUE
          INDIVIDUAL_ALIAS/ALIAS_NAME
          INDIVIDUAL_DATE_OF_BIRTH/YEAR
        ENTITIES / ENTITY
          FIRST_NAME, LISTED_ON, COMMENTS1
          ENTITY_ALIAS/ALIAS_NAME
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    individuals = {}
    entities    = {}

    # ── Individus ─────────────────────────────────────────────────────────────
    for ind in root.findall('.//INDIVIDUAL'):
        try:
            first = (ind.findtext('FIRST_NAME')  or '').strip()
            last  = (ind.findtext('SECOND_NAME') or '').strip()
            full_name = f"{first} {last}".strip()
            if not full_name:
                continue

            key = normalize_name(full_name)

            # Aliases
            aliases = []
            for alias in ind.findall('INDIVIDUAL_ALIAS'):
                a = (alias.findtext('ALIAS_NAME') or '').strip()
                if a:
                    aliases.append(a)

            # Nationalite (imbriquee dans <NATIONALITY><VALUE>)
            nat_el = ind.find('NATIONALITY')
            nationality = ''
            if nat_el is not None:
                nationality = (nat_el.findtext('VALUE') or '').strip()

            # Date de naissance (annee dans <INDIVIDUAL_DATE_OF_BIRTH><YEAR>)
            dob_el = ind.find('INDIVIDUAL_DATE_OF_BIRTH')
            dob = ''
            if dob_el is not None:
                dob = (dob_el.findtext('YEAR') or '').strip()

            reason    = (ind.findtext('COMMENTS1') or '').strip()
            listed_on = (ind.findtext('LISTED_ON')  or '').strip()

            individuals[key] = {
                "full_name":     full_name,
                "aliases":       aliases,
                "nationality":   nationality,
                "date_of_birth": dob,
                "reason":        reason[:200],
                "listed_on":     listed_on,
            }
            # Indexer aussi chaque alias pour lookup rapide
            for a in aliases:
                ak = normalize_name(a)
                if ak and ak not in individuals:
                    individuals[ak] = {**individuals[key], "alias_of": full_name}

        except Exception as e:
            print(f"  WARN individu: {e}")
            continue

    # ── Entites ───────────────────────────────────────────────────────────────
    for ent in root.findall('.//ENTITY'):
        try:
            name = (ent.findtext('FIRST_NAME') or '').strip()
            if not name:
                continue

            key = normalize_name(name)

            aliases = []
            for alias in ent.findall('ENTITY_ALIAS'):
                a = (alias.findtext('ALIAS_NAME') or '').strip()
                if a:
                    aliases.append(a)

            reason    = (ent.findtext('COMMENTS1') or '').strip()
            listed_on = (ent.findtext('LISTED_ON')  or '').strip()

            entities[key] = {
                "name":      name,
                "aliases":   aliases,
                "reason":    reason[:200],
                "listed_on": listed_on,
            }
            for a in aliases:
                ak = normalize_name(a)
                if ak and ak not in entities:
                    entities[ak] = {**entities[key], "alias_of": name}

        except Exception:
            continue

    return {
        "individuals": individuals,
        "entities":    entities,
        "metadata": {
            "total_individuals": len(individuals),
            "total_entities":    len(entities),
            "last_updated":      datetime.now().isoformat(),
        }
    }


def main():
    parser = argparse.ArgumentParser(description="Convertir UN XML → JSON")
    parser.add_argument("--input",  required=True, help="Fichier consolidated.xml")
    parser.add_argument("--output", default="data/un_sanctions.json")
    args = parser.parse_args()
    
    input_path  = Path(args.input)
    output_path = Path(args.output)
    
    if not input_path.exists():
        print(f"❌ Fichier introuvable: {input_path}")
        return
    
    print(f"\n{'='*70}")
    print(f"  CONVERSION UN SANCTIONS XML -> JSON")
    print(f"{'='*70}")
    print(f"  Input  : {input_path} ({input_path.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"  Output : {output_path}")
    print()
    
    print("  Parsing XML...")
    data = parse_un_xml(input_path)
    
    print(f"  OK {data['metadata']['total_individuals']:,} individus")
    print(f"  OK {data['metadata']['total_entities']:,} entites")
    print()

    # Sauvegarder
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    size_mb = output_path.stat().st_size / 1024 / 1024
    print(f"  Sauvegarde: {output_path} ({size_mb:.1f} MB)")
    print(f"\n  Compression: {input_path.stat().st_size / output_path.stat().st_size:.1f}x")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
