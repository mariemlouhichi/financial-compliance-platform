# ============================================================================
#  Image du frontend AML/KYC — build Vite puis service statique par nginx.
#
#  Construction en 2 étapes : l'image finale ne contient QUE les fichiers
#  statiques compilés (~2 Mo) — ni Node, ni node_modules (139 Mo).
#
#  VITE_API_BASE_URL vaut "" par défaut : le frontend appelle /api/... en
#  chemin RELATIF, et nginx relaie vers le conteneur de l'API. Un seul point
#  d'entrée public, donc aucun problème de CORS.
# ============================================================================

# ── Étape 1 : compilation ──────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# Dépendances d'abord : cette couche est mise en cache tant que les
# fichiers de verrouillage ne changent pas.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Injecté au moment du BUILD (Vite fige les variables dans le bundle).
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Le typage doit passer avant de produire un artefact de production.
RUN npm run typecheck && npm run build

# ── Étape 2 : service ──────────────────────────────────────────────────────
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
