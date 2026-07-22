# 18 — Notifications temps réel (SSE)

> Remplacer l'interrogation périodique par une notification poussée par le
> serveur — et le faire depuis un pipeline qui tourne dans un thread.

---

## 1. Le problème

L'interface interrogeait le serveur toutes les 10 secondes (`setInterval`) pour
savoir si quelque chose avait changé. Ce **polling** a trois défauts :

- **latence** — un utilisateur peut attendre jusqu'à 10 secondes avant de voir
  qu'un superviseur a validé son dossier ;
- **gaspillage** — la quasi-totalité des requêtes ne rapporte aucun changement ;
- **mauvaise mise à l'échelle** — 50 utilisateurs connectés produisent 300
  requêtes par minute, dont l'immense majorité pour rien.

L'objectif : que le **serveur prévienne** les clients quand un état change, au
lieu que les clients demandent en boucle.

---

## 2. Pourquoi SSE plutôt que WebSocket

Les deux permettent le temps réel. Le choix s'est porté sur **SSE**
(*Server-Sent Events*) pour des raisons précises :

| Critère | SSE | WebSocket |
|---|---|---|
| Sens de communication | Serveur → client | Bidirectionnel |
| Notre besoin réel | ✅ exactement ça | Surdimensionné |
| Dépendances | **Aucune** — natif FastAPI et navigateur | Souvent une couche supplémentaire |
| Reconnexion | **Automatique** (navigateur) | À implémenter |
| Traversée des proxys | Simple HTTP, passe partout | Nécessite une négociation de mise à niveau |
| Complexité du code | Faible | Moyenne |

Notre besoin est strictement **unidirectionnel** : le client n'envoie rien sur le
canal temps réel, il reçoit des notifications et refait ensuite ses appels REST
habituels. WebSocket n'aurait apporté que de la complexité.

SSE repose sur du HTTP simple avec `Content-Type: text/event-stream`, ce qui est
également un avantage pour un futur déploiement derrière un proxy Azure.

---

## 3. La vraie difficulté : franchir la frontière thread → asyncio

C'est le point technique intéressant du composant.

**Le contexte.** Le pipeline d'analyse tourne dans un **thread** (pour ne pas
bloquer l'API pendant les ~55 secondes de retrieval). Le flux SSE, lui, vit dans
la **boucle asyncio** d'uvicorn. Or les primitives `asyncio` ne sont **pas**
thread-safe : appeler `queue.put_nowait()` depuis un thread extérieur produit un
comportement indéfini.

**La solution.** Le bus capture la boucle événementielle au moment de
l'abonnement, puis toute publication depuis un thread passe par
`loop.call_soon_threadsafe()` — la seule méthode d'`asyncio` explicitement
conçue pour être appelée depuis un autre thread :

```
   Thread du pipeline                    Boucle asyncio (uvicorn)
   ──────────────────                    ────────────────────────
   BUS.publish(event)
        │
        └──▶ loop.call_soon_threadsafe(_bus_put, q, event)
                                              │
                                              ▼
                                        q.put_nowait(event)
                                              │
                                              ▼
                                     le flux SSE émet l'événement
                                              │
                                              ▼
                                        EventSource (navigateur)
```

Le bus applique aussi deux règles de robustesse :

- `publish()` **ne lève jamais** — un problème de notification ne doit jamais
  faire échouer une analyse ou une décision ;
- si la file d'un abonné est pleine (client lent, limite 100), l'événement est
  **abandonné** plutôt que de bloquer le serveur.

---

## 4. L'endpoint

```
GET /api/events?token=…
```

Réponse : un flux `text/event-stream` maintenu ouvert.

### L'authentification par paramètre d'URL

`EventSource`, l'API navigateur pour SSE, **ne permet pas d'ajouter d'en-tête
HTTP**. Impossible donc d'envoyer `Authorization: Bearer …`.

La solution standard, retenue ici : passer le jeton en **paramètre de requête**,
validé côté serveur exactement comme un en-tête (401 sinon).

Le compromis est acceptable parce que la charge utile des événements est
**minimale** : identifiant du dossier, statut, décision, état. Aucune donnée
sensible ne transite par le flux — le client reçoit un signal, puis refait ses
appels REST authentifiés, qui restent filtrés par propriétaire.

### Le maintien de connexion

Une connexion HTTP inactive est coupée par les proxys au bout de quelques
dizaines de secondes. Le flux émet donc un commentaire SSE (`: keep-alive`)
toutes les **25 secondes** en l'absence d'événement, ce qui maintient le canal
ouvert sans polluer le client.

---

## 5. Les événements publiés

Deux moments déclenchent une notification :

| Type | Moment | Signification |
|---|---|---|
| `job` | Fin de pipeline | Une analyse vient de se terminer |
| `decision` | Après validation | Un superviseur a tranché un dossier |

Charge utile :

```json
{
  "type": "decision",
  "tx_id": "TX-20260722-0002",
  "owner": "louhichiii",
  "status": "DONE",
  "decision": "STR",
  "state": "REJECTED_BY_SUPERVISOR",
  "at": "2026-07-22T00:31:12"
}
```

---

## 6. Côté interface

Un utilitaire unique, `subscribeEvents(callback)`, ouvre l'`EventSource` et
renvoie une fonction de fermeture. La reconnexion en cas de coupure est gérée
**par le navigateur**, sans code supplémentaire.

Il est branché sur les trois surfaces où le temps réel a du sens :

| Page | Ce qui se met à jour instantanément |
|---|---|
| **Mes dossiers** | État d'un dossier, notification de décision |
| **Dashboard** | Indicateurs et répartitions |
| **File superviseur** | Arrivée d'un nouveau dossier, disparition d'un dossier tranché |

### Un filet de sécurité conservé

Le polling n'a pas été supprimé, il a été **ralenti de 10 à 45 secondes**.

Ce n'est pas une demi-mesure mais un choix d'ingénierie : SSE assure la
réactivité, un rafraîchissement lent assure la **cohérence** si le flux tombe sans
que le navigateur s'en aperçoive. C'est le schéma classique
« push rapide + réconciliation lente ».

La page **Pipeline Live** conserve son propre sondage rapide : elle affiche la
progression **pendant** l'analyse (étape par étape), or SSE ne signale que la fin.

---

## 7. Vérification

Le pont thread → asyncio a été testé en isolation : un abonné asynchrone reçoit
bien un événement publié depuis un thread séparé.

En conditions réelles, le comportement observé est net : une transaction soumise
dans un onglet apparaît dans la file superviseur d'un autre onglet avec la
mention **« en attente 1 min »**, sans rechargement ni attente.

Vérification visuelle dans le navigateur : onglet **Réseau**, une requête
`events` de type *eventsource* reste **ouverte** en permanence.

---

## 8. Limite connue — importante pour Azure

Le bus est **local au processus**. Avec plusieurs *workers* uvicorn ou plusieurs
instances Azure, un événement publié par l'instance A n'atteindrait pas les
clients connectés à l'instance B.

La solution, si le passage à l'échelle devient nécessaire : un **broker partagé**
(Redis pub/sub) entre le bus local et les instances. Redis est déjà présent dans
l'infrastructure du projet, ce serait un ajout limité.

En mono-instance — le cas de la démonstration et du déploiement prévu — le
fonctionnement est correct.

---

## 9. Autres limites

- **Pas de rejeu** : un client déconnecté pendant un événement ne le récupère
  pas. Le rafraîchissement lent compense.
- **Diffusion générale** : tous les clients authentifiés reçoivent tous les
  événements. Sans risque ici (charge utile minimale, données refetchées de façon
  filtrée), mais un filtrage par propriétaire serait souhaitable en production.

---

## 10. Fichiers concernés

| Fichier | Rôle |
|---|---|
| `api/main.py` | `EventBus`, `_publish_job_event`, `GET /api/events` |
| `project/src/api/client.ts` | `subscribeEvents`, type `RealtimeEvent` |
| `project/src/pages/MyFiles.tsx` | Abonnement + rafraîchissement lent |
| `project/src/pages/Dashboard.tsx` | Abonnement + rafraîchissement lent |
| `project/src/pages/Supervisor.tsx` | Abonnement (file + historique) |
