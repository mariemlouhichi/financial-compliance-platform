# -*- coding: utf-8 -*-
"""
Génère `facture_export_7850_usd.pdf` — le justificatif manquant pour une
transaction qui DOIT PASSER.

Pourquoi ce fichier existe :
  `facture_export_12000_usd.pdf` ne peut pas servir à un cas PASS. La
  vérification du justificatif compare les montants à l'égalité stricte
  (api/main.py : abs(v - amount) < 0.01), donc le document n'est recevable
  que pour une transaction de 12 000 USD exactement — montant qui franchit
  le seuil SWIFT de 10 000 USD et impose une EDD.

  Cette facture à 7 850 USD reste sous le seuil, et le montant n'est pas
  un multiple de 500 : le signal AMOUNT_ROUND du Niveau 1 reste inactif : SWIFT 7 850 USD vers la
  France ne déclenche aucune règle de Niveau 1 → décision PASS, avec un
  justificatif néanmoins lu et jugé recevable.

Cohérence exigée par _deterministic_checks :
  • le total doit valoir EXACTEMENT le montant saisi          → 7 850,00 USD
  • le document doit citer une des parties                    → « Societe Lumiere SARL »
    (et le compte émetteur 802559360)
  • il doit porter une date de moins d'un an                  → 28/08/2026
"""

from pathlib import Path
from generate_test_documents import write_pdf, OUT

OUT.mkdir(exist_ok=True)

write_pdf(OUT / "facture_export_7850_usd.pdf", [
    (16, True,  "FACTURE COMMERCIALE No 2026-0613"),
    (10, False, ""),
    (11, False, "Date d'emission : 28/08/2026          Echeance : 28/09/2026"),
    (10, False, ""),
    (11, True,  "VENDEUR"),
    (11, False, "Societe Textile Ben Ali Export SARL - Tunis, Tunisie"),
    (11, False, "Compte bancaire no 802559360 - Banque de Tunisie"),
    (11, False, "Matricule fiscal : 1234567/A/M/000"),
    (10, False, ""),
    (11, True,  "CLIENT"),
    (11, False, "Societe Lumiere SARL - 14 rue de la Soie, 69002 Lyon, France"),
    (11, False, "TVA intracommunautaire : FR 40 123456789"),
    (10, False, ""),
    (11, True,  "DETAIL DES MARCHANDISES"),
    (10, False, "----------------------------------------------------------------------"),
    (10, False, "Designation                      Quantite   Prix unit.      Montant"),
    (10, False, "----------------------------------------------------------------------"),
    (10, False, "Tissu jacquard coton (rouleaux)      80       55,00 USD    4 400,00 USD"),
    (10, False, "Linge de maison brode (lots)         60       42,00 USD    2 520,00 USD"),
    (10, False, "Frais de conditionnement              1      930,00 USD      930,00 USD"),
    (10, False, "----------------------------------------------------------------------"),
    (12, True,  "TOTAL A PAYER : 7 850,00 USD"),
    (10, False, ""),
    (11, False, "Conditions : paiement par virement SWIFT a 30 jours."),
    (11, False, "Incoterm : FOB Rades. Marchandises conformes au contrat 2026-114."),
    (10, False, ""),
    (10, False, "Fait a Tunis, le 28/08/2026 - Cachet et signature du vendeur"),
])

print("  OK  ->", OUT / "facture_export_7850_usd.pdf")
