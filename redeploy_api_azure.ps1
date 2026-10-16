# ============================================================================
#  redeploy_api_azure.ps1 — Reconstruit l'image API et met à jour la Container
#  App (sans toucher aux variables/secrets déjà configurés).
#
#  À utiliser après toute modif du backend ou des comptes semés. Les couches
#  lourdes (torch, deps) sont en cache → seule la partie modifiée est reconstruite
#  et poussée.
#
#  Prérequis : Docker démarré · az login · platform_seed.db généré
#              (`python scripts/seed_accounts.py`).
#  Usage :  .\redeploy_api_azure.ps1
# ============================================================================
$ErrorActionPreference = "Stop"

$RG  = "aml-kyc-rg"
$ACR = "amlkyclouhichi2026"
$img = "$ACR.azurecr.io/aml-api:latest"

# ── 0. Garde-fou : la base des comptes doit exister (le Dockerfile la copie) ─
if (-not (Test-Path ".\platform_seed.db")) {
    throw "platform_seed.db introuvable. Lance d'abord : python scripts/seed_accounts.py"
}

# ── 1. Build (image simple, couches lourdes en cache) ──────────────────────
Write-Output "Build de l'image API (torch/deps en cache)..."
docker build --provenance=false -t $img .
if ($LASTEXITCODE -ne 0) { throw "Build echoue." }

# ── 2. Push avec reprise automatique (couches inchangees deja dans l'ACR) ───
az acr login --name $ACR
$pushed = $false
for ($i = 1; $i -le 30; $i++) {
    Write-Output "--- Push tentative $i/30 ---"
    docker push $img
    if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
    Write-Output "  Coupure. Reprise dans 5s..."
    Start-Sleep -Seconds 5
}
if (-not $pushed) { throw "Push non termine apres 30 tentatives." }

# ── 3. Nouvelle revision (force le pull de l'image mise a jour) ─────────────
$suffix = "r" + (Get-Date -Format "MMddHHmm")
az containerapp update --name aml-api --resource-group $RG --image $img --revision-suffix $suffix
if ($LASTEXITCODE -ne 0) { throw "Mise a jour echouee." }

Write-Output ""
Write-Output "==================================================================="
Write-Output "  API mise a jour (revision $suffix)."
Write-Output "  Les comptes de demo sont maintenant PERMANENTS."
Write-Output "  Attends ~1-2 min (demarrage), puis connecte-toi avec un compte."
Write-Output "==================================================================="
