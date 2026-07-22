# -*- coding: utf-8 -*-
"""
generate_test_documents.py — Pack de justificatifs de DÉMO (jury)
==================================================================
Génère dans test_documents/ des documents COHÉRENTS avec les transactions
du guide de démo (GUIDE_DEMO_JURY.md) :

  1. facture_export_12000_usd.pdf        — facture avec TABLEAU (SWIFT 12 000 USD)
  2. contrat_prestation_15000_tnd.docx   — contrat Word avec TABLEAU échéancier
  3. attestation_origine_fonds_9000_tnd.pdf — attestation (débloque un dossier EDD)
  4. attestation_travail.docx            — document NON PROBANT (aucun montant)
  5. facture_perimee_2023.pdf            — cas limite : montant OK mais date > 1 an
  6. recu_paiement_2500_tnd.txt          — reçu texte simple

Zéro dépendance : PDF écrits en syntaxe PDF native (Helvetica WinAnsi),
DOCX construits en XML Office Open (vrais tableaux <w:tbl> → parsés par docling).

Usage :  python generate_test_documents.py
"""
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "test_documents"
OUT.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
#  PDF minimal (une page A4, Helvetica, WinAnsiEncoding → accents OK)
# ═══════════════════════════════════════════════════════════════════════

def _pdf_escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def write_pdf(path: Path, lines: list):
    """lines = [(taille, gras: bool, texte)] — rend un PDF texte simple."""
    content = ["BT", "14 TL", "50 800 Td"]
    for size, bold, text in lines:
        font = "/F2" if bold else "/F1"
        content.append(f"{font} {size} Tf")
        content.append(f"{max(size + 4, 14)} TL")
        content.append(f"({_pdf_escape(text)}) Tj T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1", errors="replace")

    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>")
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                b"/Encoding /WinAnsiEncoding >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                b"/Encoding /WinAnsiEncoding >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF").encode()
    path.write_bytes(bytes(out))
    print(f"  PDF  {path.name}")


# ═══════════════════════════════════════════════════════════════════════
#  DOCX minimal (zip + XML Office Open) — avec VRAIS tableaux Word
# ═══════════════════════════════════════════════════════════════════════

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text: str, bold=False, size=22) -> str:
    b = "<w:b/>" if bold else ""
    return (f'<w:p><w:r><w:rPr>{b}<w:sz w:val="{size}"/></w:rPr>'
            f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p>')


def _table(rows: list) -> str:
    """rows[0] = en-têtes (gras). Vrai tableau Word <w:tbl>."""
    cells = []
    for ri, row in enumerate(rows):
        tcs = "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>'
            f'{_p(str(c), bold=(ri == 0), size=20)}</w:tc>' for c in row)
        cells.append(f"<w:tr>{tcs}</w:tr>")
    borders = ('<w:tblBorders>' + "".join(
        f'<w:{s} w:val="single" w:sz="4" w:color="000000"/>'
        for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + '</w:tblBorders>')
    return (f'<w:tbl><w:tblPr>{borders}</w:tblPr>'
            f'<w:tblGrid/>{" ".join(cells)}</w:tbl>')


def write_docx(path: Path, blocks: list):
    """blocks = chaînes XML (_p / _table) assemblées dans word/document.xml."""
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document {W}><w:body>{"".join(blocks)}'
                f'<w:sectPr/></w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    print(f"  DOCX {path.name}")


# ═══════════════════════════════════════════════════════════════════════
#  LES DOCUMENTS (cohérents avec GUIDE_DEMO_JURY.md)
# ═══════════════════════════════════════════════════════════════════════

# 1 ── Facture export 12 000 USD (PDF, avec tableau) — pour la SWIFT 12 000 USD
write_pdf(OUT / "facture_export_12000_usd.pdf", [
    (16, True,  "FACTURE COMMERCIALE No 2026-0457"),
    (10, False, ""),
    (11, False, "Date d'emission : 28/06/2026          Echeance : 28/07/2026"),
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
    (10, False, "Tissu jacquard coton (rouleaux)     120       55,00 USD    6 600,00 USD"),
    (10, False, "Linge de maison brode (lots)         90       42,00 USD    3 780,00 USD"),
    (10, False, "Frais de conditionnement              1    1 620,00 USD    1 620,00 USD"),
    (10, False, "----------------------------------------------------------------------"),
    (12, True,  "TOTAL A PAYER : 12 000,00 USD"),
    (10, False, ""),
    (11, False, "Conditions : paiement par virement SWIFT a 30 jours."),
    (11, False, "Incoterm : FOB Rades. Marchandises conformes au contrat 2026-114."),
    (10, False, ""),
    (10, False, "Fait a Tunis, le 28/06/2026 - Cachet et signature du vendeur"),
])

# 2 ── Contrat de prestation 15 000 TND (DOCX, tableau échéancier) — VIREMENT_LOCAL
write_docx(OUT / "contrat_prestation_15000_tnd.docx", [
    _p("CONTRAT DE PRESTATION DE SERVICES No 2026-114", bold=True, size=30),
    _p(""),
    _p("Entre les soussignes :", bold=True),
    _p("Le CLIENT : titulaire du compte no 802559360, Banque de Tunisie, Tunis."),
    _p("Le PRESTATAIRE : Bureau Etudes Manar SARL, 12 avenue Habib Bourguiba, Sfax."),
    _p(""),
    _p("Article 1 - Objet", bold=True),
    _p("Le prestataire realise une etude technique complete (genie civil) pour "
       "le projet d'extension de l'atelier du client."),
    _p(""),
    _p("Article 2 - Prix et modalites de paiement", bold=True),
    _p("Le prix global et forfaitaire est fixe a 15 000,00 TND (quinze mille "
       "dinars), payable par virement local selon l'echeancier suivant :"),
    _table([
        ["Echeance", "Livrable", "Date", "Montant"],
        ["Avance 40 %", "Note de cadrage", "05/07/2026", "6 000,00 TND"],
        ["Acompte 40 %", "Etude preliminaire", "05/08/2026", "6 000,00 TND"],
        ["Solde 20 %", "Rapport final", "05/09/2026", "3 000,00 TND"],
        ["TOTAL", "", "", "15 000,00 TND"],
    ]),
    _p(""),
    _p("Article 3 - Duree", bold=True),
    _p("Le present contrat prend effet le 02/07/2026 pour une duree de 3 mois."),
    _p(""),
    _p("Fait a Tunis en deux exemplaires, le 02/07/2026."),
    _p("Le Client                                    Le Prestataire"),
])

# 3 ── Attestation d'origine des fonds 9 000 TND (PDF) — débloque un dossier EDD
write_pdf(OUT / "attestation_origine_fonds_9000_tnd.pdf", [
    (16, True,  "ATTESTATION D'ORIGINE DES FONDS"),
    (10, False, ""),
    (11, False, "Je soussigne, titulaire du compte no 802559360 ouvert aupres de la"),
    (11, False, "Banque de Tunisie, atteste sur l'honneur que la somme de"),
    (13, True,  "9 000,00 TND (neuf mille dinars tunisiens)"),
    (11, False, "objet du virement au profit de Ste El Amen Distribution,"),
    (11, False, "provient de la vente de ma recolte d'olives de la saison 2025-2026,"),
    (11, False, "encaissee le 15/05/2026 (bordereau de vente no 774 - Cooperative"),
    (11, False, "Agricole de Zaghouan, joint en copie)."),
    (10, False, ""),
    (11, False, "Pieces justificatives disponibles :"),
    (11, False, "  - Bordereau de vente cooperative no 774 du 15/05/2026"),
    (11, False, "  - Releve de compte du mois de mai 2026"),
    (10, False, ""),
    (11, False, "Je reconnais avoir connaissance des sanctions prevues par la loi"),
    (11, False, "2015-26 relative a la lutte contre le blanchiment d'argent."),
    (10, False, ""),
    (11, False, "Fait a Tunis, le 08/07/2026"),
    (11, False, "Signature du titulaire du compte"),
])

# 4 ── Attestation de travail (DOCX) — document NON PROBANT (aucun montant)
write_docx(OUT / "attestation_travail.docx", [
    _p("ATTESTATION DE TRAVAIL", bold=True, size=30),
    _p(""),
    _p("Nous soussignes, Societe Informatique du Sud SARL, attestons que"),
    _p("Monsieur Ahmed Trabelsi est employe au sein de notre societe en"),
    _p("qualite de developpeur informatique depuis le 03/01/2022."),
    _p(""),
    _p("La presente attestation est delivree a l'interesse pour servir et"),
    _p("valoir ce que de droit."),
    _p(""),
    _p("Fait a Sfax, le 01/07/2026"),
    _p("La Direction des Ressources Humaines"),
])

# 5 ── Facture périmée (PDF) — cas limite : montant OK mais datée de 2023
write_pdf(OUT / "facture_perimee_2023.pdf", [
    (16, True,  "FACTURE No 2023-0089"),
    (10, False, ""),
    (11, False, "Date d'emission : 10/01/2023"),
    (10, False, ""),
    (11, False, "Vendeur : Societe Textile Ben Ali Export SARL (compte 802559360)"),
    (11, False, "Client : Societe Lumiere SARL - Lyon, France"),
    (10, False, ""),
    (12, True,  "TOTAL : 12 000,00 USD"),
    (10, False, ""),
    (11, False, "Paiement par virement SWIFT. Facture reglee le 15/02/2023."),
])

# 6 ── Reçu texte simple (TXT)
(OUT / "recu_paiement_2500_tnd.txt").write_text(
    "RECU DE PAIEMENT No 2026-221\n"
    "Date : 05/07/2026\n\n"
    "Recu du titulaire du compte no 802559360 la somme de 2 500,00 TND\n"
    "(deux mille cinq cents dinars) en reglement de la facture d'electricite\n"
    "et travaux de maintenance au profit de Ste Services Plus, Tunis.\n\n"
    "Cachet et signature\n", encoding="utf-8")
print("  TXT  recu_paiement_2500_tnd.txt")

print(f"\n  6 documents generes dans {OUT}")
