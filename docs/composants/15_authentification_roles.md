# 15 — Authentification, rôles et séparation des devoirs

> Qui a le droit de faire quoi — et pourquoi c'est un sujet réglementaire, pas
> seulement technique.

---

## 1. Objectif

Dans une banque, la question « qui peut valider une déclaration de soupçon ? »
n'est pas une question d'ergonomie : c'est une exigence de contrôle interne. Un
administrateur système ne doit **jamais** pouvoir approuver une opération
réglementaire, même s'il a techniquement accès à tout.

Ce module implémente donc trois choses :

1. une authentification réelle (comptes, mots de passe, vérification par email) ;
2. quatre rôles métier avec des périmètres distincts ;
3. la **séparation des devoirs** (*segregation of duties*), contrôlée côté serveur.

---

## 2. Les comptes

### Stockage

Une base **SQLite** (`platform.db`) avec une table `users` :

| Colonne | Contenu |
|---|---|
| `username` | Clé primaire — vaut l'email pour les comptes récents |
| `email` | Adresse de connexion et de notification |
| `pw_hash` | Empreinte du mot de passe |
| `role` | `USER` / `ADMIN` / `COMPLIANCE_OFFICER` / `AUDITOR` |
| `full_name` | Nom affiché |
| `profile` | Profil KYC complet, stocké en JSON |
| `verified` | Compte activé ou non |
| `code`, `reset_code` | Codes de vérification et de réinitialisation |

Le champ `profile` mérite une remarque : il stocke le profil KYC **au format
attendu par le moteur de scoring client**. C'est ce qui permet à la page « Mon
profil » d'alimenter réellement l'analyse, sans couche de conversion.

### Mots de passe

Les mots de passe sont hachés avec **PBKDF2** (dérivation de clé avec sel et
nombreuses itérations). Jamais stockés en clair, jamais réversibles.

### Jetons de session

Le système utilise un **jeton signé HMAC avec expiration à 12 heures** —
l'équivalent fonctionnel d'un JWT, sans dépendance externe :

```
charge utile : { "u": username, "r": rôle, "exp": horodatage + 12h }
signature    : HMAC avec une clé secrète locale
```

À chaque requête, le jeton est vérifié : signature valide **et** non expiré. Le
frontend l'envoie en en-tête `Authorization: Bearer …`.

Le choix de ne pas ajouter de bibliothèque JWT est cohérent avec le reste du
projet : la logique tient en quelques lignes, elle est auditable, et elle évite
une dépendance de plus.

---

## 3. Le parcours d'inscription

```
1. Inscription     → nom complet, email, mot de passe, rôle
2. Code envoyé     → 6 chiffres par email (ou console en mode démo)
3. Activation      → saisie du code → compte vérifié
4. Connexion       → par EMAIL + mot de passe
5. Profil KYC      → l'utilisateur renseigne ses coordonnées
6. Utilisation     → ses opérations sont analysées avec son profil réel
```

### Mot de passe oublié

Deux endpoints, `/forgot` et `/reset`, avec une subtilité de sécurité :

**Anti-énumération de comptes.** La réponse de `/forgot` est **identique dans
tous les cas** — que l'email existe ou non. Sinon, un attaquant pourrait tester
des adresses pour découvrir lesquelles sont enregistrées.

**Le code de réinitialisation vaut activation.** Décision prise en cours de
projet sur un argument juste : si l'utilisateur reçoit et saisit correctement un
code envoyé à son adresse, il prouve qu'il en est propriétaire. Un compte non
activé peut donc être débloqué par ce chemin, ce qui a résolu un blocage réel
rencontré en test.

---

## 4. Les quatre rôles

| Rôle | Vocation | Périmètre |
|---|---|---|
| **USER** | Le client qui soumet ses opérations | Nouvelle transaction, Mes dossiers, Mon profil, Pipeline Live |
| **COMPLIANCE_OFFICER** | L'analyste conformité | File superviseur, Rapports & SAR, Règles, Graphe, Corpus |
| **ADMIN** | Le profil technique (DSI) | Dashboard, simulateur, supervision — **mais pas la validation** |
| **AUDITOR** | Le contrôle interne | Investigation, rapports, règles, graphe, corpus — **en lecture seule** |

La matrice `ROLE_PAGES` définit, côté frontend, les pages accessibles par rôle.
Une section entière de la barre latérale disparaît si le rôle n'a accès à aucune
de ses pages — l'auditeur voit donc l'espace conformité **sans** la file
superviseur.

---

## 5. La séparation des devoirs

C'est le point réglementaire fort du module.

### La règle

> Seul un **Compliance Officer** peut approuver ou refuser un dossier.
> Un **administrateur** ne le peut pas, quel que soit son niveau d'accès technique.

### L'implémentation — côté serveur

```
POST /api/validate/{tx_id}
    → pas de jeton            → 401
    → rôle ≠ COMPLIANCE_OFFICER → 403 avec message explicite
```

Le contrôle est **dans l'API**, pas seulement dans l'interface. C'est essentiel :
masquer un bouton n'est pas un contrôle d'accès. N'importe qui peut appeler l'API
directement ; c'est là que la règle doit être appliquée.

L'interface affiche par ailleurs un bandeau explicite rappelant la règle, pour
que la contrainte soit **visible** et non subie.

### Pourquoi c'est important

En contrôle interne, on sépare celui qui **exploite le système** de celui qui
**prend la décision métier**. Sinon, la personne qui administre la base pourrait
approuver ses propres opérations. C'est un principe de base de l'audit bancaire,
et une question de jury très probable.

---

## 6. Portée des données

Au-delà des rôles, les données sont cloisonnées par **propriétaire** :

- `GET /api/transactions?mine=true` ne retourne que les dossiers de l'utilisateur
  connecté ;
- l'accès au détail d'un dossier vérifie la propriété ;
- le **SAR est masqué au rôle utilisateur** : c'est un document interne de
  conformité, pas une pièce destinée au client.

---

## 7. Un correctif révélateur : username ≠ email

Un bug rencontré en test illustre bien l'importance de ce module.

**Le symptôme.** Un superviseur refuse une transaction, mais le client ne reçoit
jamais l'email de notification.

**La cause.** Le propriétaire d'un dossier est stocké sous forme de **username**.
Pour les comptes récents, `username = email`, donc tout fonctionnait. Mais les
comptes plus anciens ont un username distinct (`user1`), et l'envoi n'était
déclenché que si le propriétaire contenait un `@`. Pour ces comptes, aucun email
ne partait — silencieusement.

**Le correctif.** Une fonction `get_user_email(username)` résout l'adresse réelle
depuis la table `users` avant tout envoi, avec journalisation explicite du
résultat (envoyé / non résolu). Le cas « username = email » reste géré sans
requête inutile.

La leçon est générale : dès qu'on a deux identifiants pour une même personne, il
faut une résolution explicite, pas une heuristique sur la présence d'un `@`.

---

## 8. Gestion des secrets

| Secret | Emplacement | Protection |
|---|---|---|
| Clés API LLM | `groq_keys.json` | Ignoré par Git |
| Identifiants SMTP | `smtp_config.json` | Ignoré par Git |
| Connexion PostgreSQL | `db_config.json` ou `DATABASE_URL` | Ignoré par Git |
| Clé de signature des jetons | `output/api_secret.key` | Ignoré par Git |
| Base des comptes | `platform.db` | Ignoré par Git |

Tous ces fichiers sont dans `.gitignore`. Le mot de passe d'application SMTP
devra être **révoqué après la soutenance**.

---

## 9. Limites assumées

- **Pas d'OAuth / Keycloak** : l'authentification est maison. Suffisante pour un
  PFE et auditable, mais une banque réelle utiliserait un fournisseur d'identité
  centralisé avec authentification multifacteur.
- **Pas de MFA**, pas de politique de complexité de mot de passe, pas de
  limitation de tentatives (*rate limiting*).
- **Pas de journal d'accès** : les décisions sont tracées, les consultations non.
- **Jeton non révocable** avant son expiration de 12 h (pas de liste noire).

---

## 10. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `api/auth.py` | Comptes, jetons, profils, codes, emails |
| `api/main.py` | Contrôles de rôle sur les endpoints sensibles |
| `project/src/auth.ts` | Session, `ROLE_PAGES`, libellés de rôle |
| `project/src/pages/Login.tsx` | Connexion, inscription, activation, réinitialisation |
| `project/src/pages/Profile.tsx` | Profil KYC en 5 sections |
| `platform.db` | Base des comptes (ignorée par Git) |
