# Les 49 règles Ground Truth — visualisation présentation

> Extraites par LLM (Groq llama-3.3-70b) selon la méthode Compliance-to-Code,
> puis **validées manuellement** (origin = human_verified). Réfèrence absolue.

## 1. Distributions (pour graphiques)

**Par juridiction :**

| Juridiction | Nb | % |
|---|---|---|
| International | 25 | 51% |
| Tunisia | 15 | 31% |
| EU | 9 | 18% |

**Par rôle (type d'obligation) :**

| Rôle | Nb |
|---|---|
| OBLIGATION | 12 |
| KYC | 10 |
| THRESHOLD | 8 |
| SANCTIONS | 7 |
| REPORTING | 5 |
| RISK_ASSESSMENT | 4 |
| RED_FLAG | 3 |

**Par source réglementaire :**

| Source | Nb |
|---|---|
| FATF Rec. | 13 |
| EBA GL 2021/02 | 8 |
| BCT 2017-08 | 5 |
| FATF MVTS | 4 |
| FATF Virtual Assets | 4 |
| Loi 2015-26 | 3 |
| BCT 2024-02 | 2 |
| Régl. Bancaire 2024 | 2 |
| Basel d353 | 2 |
| BCT 2025-17 | 1 |
| BCT 2018-09 | 1 |
| EU AMLD5 | 1 |
| Basel d494 | 1 |
| FATF Corresp. | 1 |
| CTAF | 1 |

**Par famille de régulateur :**

| Famille | Nb | % |
|---|---|---|
| FATF | 22 | 45% |
| Tunisie (BCT/loi/CTAF) | 15 | 31% |
| UE/EBA | 9 | 18% |
| Basel | 3 | 6% |

## 2. Tableau complet des 49 règles

| # | ID | Source | Article | Juridiction | Rôle | Obligation (résumé) |
|---|----|--------|---------|-------------|------|---------------------|
| 1 | GT-OBL-01 | BCT 2017-08 | Article 4-a | Tunisia | OBLIGATION | take appropriate measures to identify, assess, and understand the risk |
| 2 | GT-OBL-02 | BCT 2017-08 | Article 14 | Tunisia | OBLIGATION | Must ensure correspondent is authorized, obtain sufficient information |
| 3 | GT-OBL-03 | BCT 2017-08 | Article 3 | Tunisia | OBLIGATION | Must describe the required diligences and rules, particularly in matte |
| 4 | GT-OBL-04 | BCT 2025-17 | Article 4-a | Tunisia | OBLIGATION | Must define and implement a system for identifying and assessing risks |
| 5 | GT-OBL-05 | EBA GL 2021/02 | Article 18-f | EU | OBLIGATION | Must ensure transactions are carried out through an account in the cus |
| 6 | GT-OBL-06 | EBA GL 2021/02 | Article 26 | EU | OBLIGATION | Must not rely on that third party, unless the intermediary is a branch |
| 7 | GT-OBL-07 | BCT 2024-02 | Préambule | Tunisia | OBLIGATION | Must comply with the requirements set out in the circular before and d |
| 8 | GT-OBL-08 | FATF MVTS | Numbered 54 | International | OBLIGATION | Must include relevant originator and beneficiary information on wire t |
| 9 | GT-OBL-09 | Régl. Bancaire 2024 | Article 11 | Tunisia | OBLIGATION | Must implement anti-money laundering and counter-terrorist financing p |
| 10 | GT-OBL-10 | Régl. Bancaire 2024 | Article 20 | Tunisia | OBLIGATION | Must implement a framework to prevent risks, equip themselves with ris |
| 11 | GT-OBL-11 | BCT 2018-09 | Article 57 | Tunisia | OBLIGATION | Must centralize and examine reports, examine unusual transactions, ens |
| 12 | GT-OBL-12 | BCT 2024-02 | Numbered 7-b | Tunisia | OBLIGATION | Must include specific internal control rules for financial products an |
| 13 | GT-KYC-13 | BCT 2017-08 | Article 6 | Tunisia | KYC | Must carry out due diligence regarding the client's identity and, if a |
| 14 | GT-KYC-14 | Loi 2015-26 | Article 110 | Tunisia | KYC | Must apply due diligence measures, have adequate risk management syste |
| 15 | GT-KYC-15 | EU AMLD5 | Paragraph 1-b | EU | KYC | Must ensure that the derogation provided is applied and that competent |
| 16 | GT-KYC-16 | EBA GL 2021/02 | Article 13 | EU | KYC | Must take adequate measures to be satisfied that the customer is who h |
| 17 | GT-KYC-17 | EBA GL 2021/02 | Article 19 | EU | KYC | Must apply specific Enhanced Due Diligence (EDD) measures in addition  |
| 18 | GT-KYC-18 | EBA GL 2021/02 | Article 12-b | EU | KYC | Must apply group-wide AML/CFT policies and monitor transactions for su |
| 19 | GT-KYC-19 | FATF Rec. | Numbered 18 | International | KYC | Must take into account risk variables relating to those risk categorie |
| 20 | GT-KYC-20 | FATF Rec. | Numbered 21 | International | KYC | Must implement simplified CDD measures proportionate to the lower risk |
| 21 | GT-KYC-21 | FATF Rec. | Numbered 1 | International | KYC | Must report suspicious transactions, implement freezing action, and co |
| 22 | GT-KYC-22 | BCT 2017-08 | Article 9 | Tunisia | KYC | Must obtain necessary information about the third party's vigilance me |
| 23 | GT-THR-23 | FATF Rec. | Numbered 3-a | International | THRESHOLD | Must submit a truthful declaration to the designated competent authori |
| 24 | GT-THR-24 | Basel d494 | Article 85-e | International | THRESHOLD | Must investigate possible connected counterparties on the basis of eco |
| 25 | GT-THR-25 | FATF Rec. | Numbered 1 | International | THRESHOLD | Must report financial transactions above the designated threshold |
| 26 | GT-THR-26 | FATF Rec. | Numbered 2 | International | THRESHOLD | Must file disclosures, including suspicious transaction reports, cash  |
| 27 | GT-THR-27 | FATF MVTS | Numbered 48-g | International | THRESHOLD | Must report transactions above the threshold |
| 28 | GT-THR-28 | EBA GL 2021/02 | Article 12-a | EU | THRESHOLD | Must set the threshold at a level that presents a low risk for both te |
| 29 | GT-THR-29 | FATF MVTS | Numbered 27 | International | THRESHOLD | Must liaise to address ML/TF concerns and require submission of STRs o |
| 30 | GT-THR-30 | EBA GL 2021/02 | Article 13-f | EU | THRESHOLD | Must have systems in place to detect when the threshold or time limit  |
| 31 | GT-SAN-31 | Loi 2015-26 | Article 116 | Tunisia | SANCTIONS | Must collect and analyze declarations, assist in program elaboration,  |
| 32 | GT-SAN-32 | EBA GL 2021/02 | Article 2-k | EU | SANCTIONS | Must take into account country or geographical risk factors and pay pa |
| 33 | GT-SAN-33 | FATF Rec. | Préambule-c | International | SANCTIONS | Must apply a risk-based approach and implement targeted financial sanc |
| 34 | GT-SAN-34 | FATF Rec. | Numbered 1-b | International | SANCTIONS | Must identify, assess, and understand the money laundering and terrori |
| 35 | GT-SAN-35 | FATF Rec. | Numbered 14 | International | SANCTIONS | Must be licensed or registered, and subject to effective systems for m |
| 36 | GT-SAN-36 | Loi 2015-26 | Article 91 | Tunisia | SANCTIONS | Are subject to a fine equal to five times the amount of the fine provi |
| 37 | GT-SAN-37 | FATF Corresp. | Numbered 87-w | International | SANCTIONS | Must comply with AML/CFT requirements and follow guidance on detecting |
| 38 | GT-REP-38 | FATF Rec. | Numbered 23 | International | REPORTING | Must report suspicious transactions |
| 39 | GT-REP-39 | Basel d353 | Numbered 57 | International | REPORTING | Must report the transaction and ensure confidentiality, swift investig |
| 40 | GT-REP-40 | FATF Rec. | Numbered 4 | International | REPORTING | Must provide and receive customer, account, and transaction informatio |
| 41 | GT-REP-41 | FATF Virtual Assets | Numbered 5 | International | REPORTING | Must file an STR or seek clarification on the transaction |
| 42 | GT-REP-42 | FATF Virtual Assets | Numbered 11-a | International | REPORTING | Must report suspicious transactions |
| 43 | GT-RED-43 | FATF Virtual Assets | Préambule-b | International | RED_FLAG | Must identify and report suspicious transactions and red flag indicato |
| 44 | GT-RED-44 | FATF Virtual Assets | Numbered 8 | International | RED_FLAG | Must monitor and examine further when a red flag indicator is present |
| 45 | GT-RED-46 | CTAF | Numbered 1-f | Tunisia | RED_FLAG | Must file a suspicious transaction report (STR) with the relevant auth |
| 46 | GT-RIS-47 | FATF Rec. | Numbered 15 | International | RISK_ASSESSMENT | Must identify and assess money laundering or terrorist financing risks |
| 47 | GT-RIS-48 | FATF Rec. | Numbered 12 | International | RISK_ASSESSMENT | Must identify and assess money laundering and terrorist financing risk |
| 48 | GT-RIS-49 | FATF MVTS | Numbered 84-a | International | RISK_ASSESSMENT | Provide guidelines and assistance to assess compliance program regime, |
| 49 | GT-RIS-50 | Basel d353 | Numbered 46 | International | RISK_ASSESSMENT | Must have systems in place to detect unusual or suspicious transaction |