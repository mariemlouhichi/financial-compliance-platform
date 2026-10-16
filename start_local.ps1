# ============================================================================
#  start_local.ps1 — Démarre TOUTE la plateforme AML/KYC en LOCAL.
#
#  Objectif : ne JAMAIS dépendre du cloud (Weaviate Cloud / Neo4j Aura /
#  Upstash) pour la démo. Le cloud sert uniquement au redéploiement Azure
#  de septembre (config conservée dans .env.cloud).
#
#  Usage :   .\start_local.ps1
#  Arrêt :   Ctrl+C  (les bases Docker restent up ; pour tout arrêter :
#            docker compose stop)
# ============================================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "`n=== 1. Bases de donnees LOCALES (Docker) ===" -ForegroundColor Cyan
# Libere le port 8001 : on stoppe le conteneur api perime (code 3 semaines +
# branche au cloud). On garde SEULEMENT les bases locales.
docker compose stop api web 2>$null | Out-Null
docker compose up -d weaviate neo4j redis postgres
docker compose ps

Write-Host "`n=== 2. Attente que Weaviate local soit pret (localhost:8088) ===" -ForegroundColor Cyan
$ready = $false
foreach ($i in 1..20) {
    try {
        Invoke-RestMethod "http://localhost:8088/v1/.well-known/ready" -TimeoutSec 2 | Out-Null
        $ready = $true; break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $ready) { Write-Host "  ! Weaviate ne repond pas encore — verifie 'docker compose logs weaviate'" -ForegroundColor Yellow }
else {
    try {
        $body = '{"query":"{Aggregate{AMLChunk{meta{count}}}}"}'
        $r = Invoke-RestMethod "http://localhost:8088/v1/graphql" -Method Post -ContentType "application/json" -Body $body
        $n = $r.data.Aggregate.AMLChunk[0].meta.count
        Write-Host "  OK Weaviate local : $n chunks dans AMLChunk" -ForegroundColor Green
        if ($n -eq 0) { Write-Host "  ! Collection vide — relance : .\venv\Scripts\python.exe scripts/embed_to_weaviate.py --fresh" -ForegroundColor Yellow }
    } catch { Write-Host "  ! Comptage impossible : $($_.Exception.Message)" -ForegroundColor Yellow }
}

Write-Host "`n=== 3. Force l'environnement LOCAL (ecrase tout cloud_env.ps1) ===" -ForegroundColor Cyan
# Weaviate : WEAVIATE_URL vide => le code bascule en mode LOCAL (localhost:8088)
$env:WEAVIATE_URL       = ""
$env:WEAVIATE_API_KEY   = ""
$env:WEAVIATE_HOST      = "localhost"
$env:WEAVIATE_HTTP_PORT = "8088"
$env:WEAVIATE_GRPC_PORT = "50051"
# Neo4j local (docker-compose : neo4j / amlkyc2025)
$env:NEO4J_URI          = "bolt://localhost:7687"
$env:NEO4J_USER         = "neo4j"
$env:NEO4J_PASSWORD     = "amlkyc2025"
# Redis + Postgres locaux
$env:REDIS_URL          = "redis://localhost:6379/0"
$env:DATABASE_URL       = "postgresql://aml:amlkyc2025@localhost:5432/amlkyc"
Write-Host "  WEAVIATE_URL vide -> mode LOCAL (localhost:8088)" -ForegroundColor Green
Write-Host "  NEO4J_URI = bolt://localhost:7687" -ForegroundColor Green

Write-Host "`n=== 4. Lancement de l'API (code a jour, venv) sur http://localhost:8001 ===" -ForegroundColor Cyan
Write-Host "  Attendu au demarrage : 'OK Weaviate connecte (localhost:8088)'" -ForegroundColor DarkGray
Write-Host "  (surtout PAS 'Weaviate Cloud connecte')`n" -ForegroundColor DarkGray
.\venv\Scripts\python.exe -m uvicorn api.main:app --port 8001 --reload
