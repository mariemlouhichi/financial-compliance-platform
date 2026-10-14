# ============================================================================
#  deploy_api_azure.ps1 — Déploie l'API AML/KYC sur Azure Container Apps.
#
#  Les secrets sont lus depuis les fichiers LOCAUX gitignorés :
#     cloud_env.ps1 (Neo4j/Weaviate/Redis + DSN PostgreSQL Azure),
#     groq_keys.json (clés LLM), smtp_config.json (email).
#  Ils sont injectés comme SECRETS Container Apps (chiffrés au repos) — jamais
#  dans l'image ni dans git. Ce script ne contient aucun secret en dur.
#
#  Prérequis : être connecté (az login) + être dans le dossier 02_chunking_v2.
#  Usage :  .\deploy_api_azure.ps1
# ============================================================================
$ErrorActionPreference = "Stop"

# ── 1. Charger les identifiants cloud dans la session ──────────────────────
. .\cloud_env.ps1

# ── 2. Assembler les secrets depuis les fichiers locaux ────────────────────
$groq  = ((Get-Content groq_keys.json  -Raw | ConvertFrom-Json).keys) -join ","
$smtp  =  (Get-Content smtp_config.json -Raw | ConvertFrom-Json)
$dburl =  $env:AZURE_DATABASE_URL

# ── 3. Mot de passe du registre ACR (pour que Container Apps tire l'image) ──
$acrPwd = (az acr credential show --name amlkyclouhichi2026 --query "passwords[0].value" -o tsv).Trim()

# ── 4. Secrets (noms en minuscules) + variables d'environnement ────────────
$secrets = @(
  "neo4j-password=$env:NEO4J_PASSWORD"
  "weaviate-key=$env:WEAVIATE_API_KEY"
  "redis-url=$env:REDIS_URL"
  "groq-keys=$groq"
  "smtp-password=$($smtp.smtp_password)"
)
$envs = @(
  "NEO4J_URI=$env:NEO4J_URI"
  "NEO4J_USER=$env:NEO4J_USER"
  "NEO4J_PASSWORD=secretref:neo4j-password"
  "WEAVIATE_URL=$env:WEAVIATE_URL"
  "WEAVIATE_API_KEY=secretref:weaviate-key"
  "REDIS_URL=secretref:redis-url"
  "REDIS_CONNECT_TIMEOUT=10"
  "GROQ_API_KEYS=secretref:groq-keys"
  "SMTP_HOST=$($smtp.smtp_host)"
  "SMTP_PORT=$($smtp.smtp_port)"
  "SMTP_USER=$($smtp.smtp_user)"
  "SMTP_PASSWORD=secretref:smtp-password"
  "SMTP_FROM=$($smtp.smtp_user)"
)

# ── 4bis. PostgreSQL : optionnel ───────────────────────────────────────────
#  Sans AZURE_DATABASE_URL, api/store.py bascule seul sur la persistance
#  fichiers (message « pas de DATABASE_URL → persistance fichiers JSON »).
#  Les dossiers ne survivent alors pas au redémarrage du conteneur.
if ($dburl) {
    $secrets += "database-url=$dburl"
    $envs    += "DATABASE_URL=secretref:database-url"
    Write-Output "PostgreSQL : actif (persistance durable)."
} else {
    Write-Output "PostgreSQL : absent -> persistance fichiers (dossiers perdus au redemarrage)."
}

# ── 5. Créer la Container App ──────────────────────────────────────────────
Write-Output "Création de l'application aml-api (l'image de 892 Mo est tirée depuis l'ACR par Azure)..."
az containerapp create `
  --name aml-api `
  --resource-group aml-kyc-rg `
  --environment aml-env `
  --image amlkyclouhichi2026.azurecr.io/aml-api:latest `
  --registry-server amlkyclouhichi2026.azurecr.io `
  --registry-username amlkyclouhichi2026 `
  --registry-password $acrPwd `
  --target-port 8001 `
  --ingress external `
  --cpu 2.0 --memory 4.0Gi `
  --min-replicas 1 --max-replicas 1 `
  --secrets $secrets `
  --env-vars $envs

# ── 6. Afficher l'URL publique ─────────────────────────────────────────────
$fqdn = (az containerapp show --name aml-api --resource-group aml-kyc-rg --query "properties.configuration.ingress.fqdn" -o tsv).Trim()
Write-Output ""
Write-Output "==================================================================="
Write-Output "  API deployee."
Write-Output "  URL    :  https://$fqdn"
Write-Output "  Sante  :  https://$fqdn/api/health"
Write-Output "  Docs   :  https://$fqdn/docs"
Write-Output "==================================================================="
Write-Output "  Suivre le demarrage (chargement PEP/sanctions ~30-120s) :"
Write-Output "  az containerapp logs show --name aml-api --resource-group aml-kyc-rg --follow"
Write-Output "==================================================================="
