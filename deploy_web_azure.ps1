# ============================================================================
#  deploy_web_azure.ps1 — Construit et déploie le frontend sur Container Apps.
#
#  Principe : le front appelle l'API directement (VITE_API_BASE_URL = URL de
#  l'API Azure, figée dans le bundle au build). CORS est activé sur l'ingress
#  de l'API — aucune reconstruction de l'image API (892 Mo) nécessaire.
#
#  --provenance=false : produit une IMAGE SIMPLE, sans manifeste d'attestation
#  (provenance/SBOM). Ce manifeste supplémentaire faisait échouer le push sur
#  connexion instable (EOF) et laissait le tag 'latest' incomplet dans l'ACR.
#
#  Prérequis : API 'aml-api' déjà déployée · Docker démarré · az login.
#  Usage :  .\deploy_web_azure.ps1
#  Si le push coupe (EOF) : relance le script — build en cache, push repris.
# ============================================================================
$ErrorActionPreference = "Stop"

$RG   = "aml-kyc-rg"
$ACR  = "amlkyclouhichi2026"
$ENVN = "aml-env"

# ── 1. Récupérer l'URL publique de l'API déjà déployée ─────────────────────
$apiFqdn = (az containerapp show --name aml-api --resource-group $RG --query "properties.configuration.ingress.fqdn" -o tsv).Trim()
$apiUrl  = "https://$apiFqdn"
Write-Output "API cible (figée dans le bundle) : $apiUrl"

# ── 2. Construire l'image web (SANS attestation) ───────────────────────────
$webImage = "$ACR.azurecr.io/aml-web:latest"
Write-Output "Construction de l'image web (image simple, sans attestation)..."
docker build --provenance=false --build-arg VITE_API_BASE_URL=$apiUrl -t $webImage ./project
if ($LASTEXITCODE -ne 0) { throw "Build echoue." }

# ── 3. Pousser vers l'ACR (avec reprise automatique) ───────────────────────
#  Sur connexion instable, le push casse ('broken pipe'). Chaque reprise saute
#  les couches deja presentes ('Layer already exists') : on boucle jusqu'a ce
#  que tout soit passe. Astuce complementaire : Docker Desktop > Settings >
#  Docker Engine > "max-concurrent-uploads": 1  (une couche a la fois).
az acr login --name $ACR
$pushed = $false
for ($i = 1; $i -le 20; $i++) {
    Write-Output "--- Push tentative $i/20 ---"
    docker push $webImage
    if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
    Write-Output "  Coupure. Reprise dans 5s (les couches deja envoyees sont conservees)..."
    Start-Sleep -Seconds 5
}
if (-not $pushed) {
    throw "Push non termine apres 20 tentatives. Regle 'max-concurrent-uploads: 1' dans Docker Desktop, ou on passe par GitHub Actions."
}

# ── 4. Supprimer une eventuelle app en echec, puis creer ───────────────────
Write-Output "Nettoyage d'une eventuelle app aml-web en echec..."
az containerapp delete --name aml-web --resource-group $RG --yes 2>$null
$global:LASTEXITCODE = 0   # delete peut echouer si l'app n'existe pas : sans importance

$acrPwd = (az acr credential show --name $ACR --query "passwords[0].value" -o tsv).Trim()
az containerapp create `
  --name aml-web `
  --resource-group $RG `
  --environment $ENVN `
  --image $webImage `
  --registry-server "$ACR.azurecr.io" `
  --registry-username $ACR `
  --registry-password $acrPwd `
  --target-port 80 `
  --ingress external `
  --cpu 0.5 --memory 1.0Gi `
  --min-replicas 1 --max-replicas 1
if ($LASTEXITCODE -ne 0) { throw "Creation de l'app echouee." }

# ── 5. Activer CORS sur l'API (idempotent) ─────────────────────────────────
az containerapp ingress cors enable --name aml-api --resource-group $RG --allowed-origins "*" --allowed-methods "*" --allowed-headers "*"

# ── 6. Afficher l'URL du site ──────────────────────────────────────────────
$webFqdn = (az containerapp show --name aml-web --resource-group $RG --query "properties.configuration.ingress.fqdn" -o tsv).Trim()
Write-Output ""
Write-Output "==================================================================="
Write-Output "  PLATEFORME EN LIGNE (a donner au jury) :"
Write-Output "     https://$webFqdn"
Write-Output ""
Write-Output "  API :  $apiUrl"
Write-Output "==================================================================="
