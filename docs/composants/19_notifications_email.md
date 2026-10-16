# 19 — Notifications par email

> Faire sortir l'information de la plateforme : prévenir le client d'une
> décision, et fournir au responsable conformité son rapport hebdomadaire.

---

## 1. Objectif

Une plateforme de conformité ne peut pas supposer que ses utilisateurs sont
connectés en permanence. Deux besoins concrets :

- **Le client** doit apprendre que son opération a été approuvée ou refusée —
  et, en cas de refus, **pourquoi**.
- **Le responsable conformité** doit disposer d'une vue périodique de l'activité
  sans avoir à ouvrir le tableau de bord.

Le module couvre quatre types d'envois : code de vérification, code de
réinitialisation, notification de décision, rapport hebdomadaire.

---

## 2. Configuration SMTP

Un problème récurrent en développement : les identifiants SMTP passés par
variables d'environnement se perdent à chaque nouvelle session de terminal.

La solution retenue suit la convention déjà utilisée pour les clés LLM : un
fichier **`smtp_config.json`** à la racine, **ignoré par Git**.

```
Priorité : variables d'environnement  >  smtp_config.json  >  mode démo
```

Deux détails qui ont coûté du temps et méritent d'être notés :

- **Les espaces du mot de passe d'application sont retirés automatiquement.**
  Google affiche les mots de passe d'application par blocs de quatre
  (`oivz gpxm utzs cotm`) ; collés tels quels, ils échouent. Le nettoyage est
  fait dans le code plutôt que laissé à l'utilisateur.
- **Le mot de passe d'application devra être révoqué après la soutenance.**

### Mode démo

Si aucun SMTP n'est configuré, le système **n'échoue pas** : il affiche le code
de vérification dans la console du serveur. La démonstration reste possible sans
compte mail.

---

## 3. Principe commun : jamais bloquant

Tous les envois partent dans un **thread séparé**. Un serveur SMTP lent ou
injoignable ne doit jamais retarder — ni faire échouer — une décision de
superviseur ou une analyse.

L'envoi est donc « au mieux » : il est tenté, journalisé, et l'échec éventuel
n'interrompt pas le flux métier.

Chaque message est construit en **`MIMEMultipart('alternative')`** : une version
texte brut et une version HTML. Les clients mail qui bloquent le HTML affichent
la version texte, l'information passe quand même.

---

## 4. L'email de décision

C'est le plus important sur le plan métier.

### Déclenchement

Il part automatiquement quand un Compliance Officer approuve ou refuse un
dossier, vers le **propriétaire** de ce dossier.

### Contenu

| Élément | Approbation | Refus |
|---|---|---|
| Bandeau | **Vert** | **Rouge** |
| Titre | Opération approuvée | Opération refusée |
| Tableau récapitulatif | Type, montant, bénéficiaire, statut | idem |
| **Motif** | Annotation du superviseur | **Annotation = la raison du refus** |
| Renvoi | « Retrouvez le détail dans Mes dossiers » | idem |

Le point clé : **l'annotation obligatoire du superviseur devient le motif
communiqué au client**. C'est ce qui relie l'exigence d'audit interne (justifier
sa décision) à l'exigence de transparence externe (expliquer au client).

### Le bug qui empêchait tout envoi

Un cas réel mérite d'être documenté, car il illustre une classe d'erreurs
silencieuses.

**Symptôme.** Un superviseur refuse une transaction, l'API répond `200 OK`,
l'état du dossier change correctement — mais aucun email n'arrive, et aucune
erreur n'apparaît.

**Cause.** Le propriétaire d'un dossier est un **username**. L'envoi n'était
déclenché que si ce username contenait un `@` — une heuristique qui fonctionnait
pour les comptes récents (`username = email`) mais **jamais** pour les comptes
plus anciens comme `user1`. La condition étant fausse, le code d'envoi n'était
tout simplement pas atteint.

**Correction.** Une fonction `get_user_email(username)` résout l'adresse réelle
dans la table `users`, et le résultat est **journalisé explicitement** :

```
[API] 📧 email de décision → agent@demo.local (REFUSÉ)
[API] ⚠️ email de décision NON envoyé : owner='user1' sans adresse email résolue
```

La leçon : un envoi silencieusement ignoré est pire qu'un envoi en erreur. La
journalisation explicite du **cas négatif** rend le problème visible immédiatement.

### Une correction de forme

Le pied de page contenait « Message automatique, merci de ne pas répondre » et un
pictogramme. Les deux ont été retirés : un message de notification bancaire gagne
à ressembler à une communication professionnelle, pas à un envoi robotisé.

---

## 5. Le rapport hebdomadaire

Destiné au Compliance Officer et à l'administrateur.

### Contenu

Un email HTML soigné comprenant :

- un **en-tête** avec la période couverte ;
- **quatre indicateurs** : total des transactions, légitimes, signalées,
  bloquées — chacun dans sa couleur de statut ;
- une **répartition par décision** sous forme de barres proportionnelles ;
- le **pattern AML dominant** de la semaine ;
- le nombre de dossiers **en attente** de décision.

### Calcul

La fonction `_weekly_stats(days=7)` agrège les dossiers des sept derniers jours :

| Catégorie | Décisions incluses |
|---|---|
| **Légitimes** | `PASS`, `REVIEW`, `KYC`, `RECORD` |
| **Signalées** | `EDD`, `STR` |
| **Bloquées** | `BLOCK` |
| **En attente** | État `PENDING*` sans décision superviseur |

### Déclenchement

L'endpoint `POST /api/reports/weekly` est réservé aux rôles
`COMPLIANCE_OFFICER` et `ADMIN`. Il est aujourd'hui déclenché par un **bouton**
dans la page Rapports.

⚠️ **Limite à assumer** : l'envoi automatique chaque vendredi n'est **pas encore
branché**. Il nécessite un planificateur (APScheduler ou une tâche cron), qui est
identifié comme la prochaine brique — le code de l'API le mentionne
explicitement. En l'état, le rapport existe et fonctionne, mais son
déclenchement est manuel.

---

## 6. Emails d'authentification

| Email | Contenu | Particularité |
|---|---|---|
| **Vérification** | Code à 6 chiffres | Active le compte |
| **Réinitialisation** | Code à 6 chiffres | Vaut également activation du compte |

Le second point est une décision de conception : si l'utilisateur reçoit et
saisit correctement un code envoyé à son adresse, il prouve qu'il en est
propriétaire — la même preuve que pour une activation.

---

## 7. Vérification

Les envois ont été testés **en conditions réelles** vers une adresse Gmail, via
un script isolé qui a envoyé les trois emails métier : approbation (bandeau vert),
refus (bandeau rouge avec motif) et rapport hebdomadaire. Les trois ont été reçus.

Le test bout-en-bout complet — connexion, soumission d'une transaction, refus par
le superviseur, réception de l'email avec le motif — a ensuite été validé dans
l'application.

---

## 8. Limites assumées

- **Pas de planification automatique** du rapport hebdomadaire (brique suivante).
- **Pas de file de reprise** : un email perdu à cause d'un SMTP indisponible n'est
  pas réessayé.
- **Pas de préférences de notification** : l'utilisateur ne peut pas choisir ce
  qu'il reçoit.
- **Un seul fournisseur testé** (Gmail avec mot de passe d'application).

---

## 9. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `api/auth.py` | `_smtp_config`, `_send_email`, `send_verification_email`, `send_reset_email`, `send_decision_email`, `send_weekly_report_email`, `get_user_email` |
| `api/main.py` | Envoi à la validation, `_weekly_stats`, `POST /api/reports/weekly` |
| `smtp_config.json` | Identifiants SMTP (ignoré par Git) |
| `test_smtp.py` | Test d'envoi isolé |
| `project/src/pages/Reports.tsx` | Bouton « Rapport hebdo » |
