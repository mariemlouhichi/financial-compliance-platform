# ============================================================================
#  Identifiants des services cloud — À COPIER en cloud_env.ps1 (gitignoré)
#
#  Usage (PowerShell, depuis la racine du projet) :
#      . .\cloud_env.ps1        <-- le point suivi d'un espace est OBLIGATOIRE
#
#  Il charge les variables dans la session courante. Les scripts de migration
#  et l'API les lisent automatiquement — aucun identifiant dans le code.
#
#  ⚠️ NE JAMAIS committer cloud_env.ps1 ni partager son contenu.
# ============================================================================

# ── Neo4j Aura ──────────────────────────────────────────────────────────
#  URI visible dans la console Aura → bouton « Connect »
#  L'utilisateur est TOUJOURS « neo4j » sur Aura.
$env:NEO4J_URI      = "neo4j+s://REMPLACER.databases.neo4j.io"
$env:NEO4J_USER     = "neo4j"
$env:NEO4J_PASSWORD = "REMPLACER"

# ── Weaviate Cloud ──────────────────────────────────────────────────────
#  URL = REST Endpoint du cluster, PRÉFIXÉ de https://
#  Clé = API key de rôle « admin »
$env:WEAVIATE_URL     = "https://REMPLACER.c0.eu-central-1.aws.weaviate.cloud"
$env:WEAVIATE_API_KEY = "REMPLACER"

# ── Redis (Upstash) ─────────────────────────────────────────────────────
#  URL complète au format rediss:// (deux « s » = TLS), onglet Details.
$env:REDIS_URL             = "rediss://default:REMPLACER@REMPLACER.upstash.io:6379"
$env:REDIS_CONNECT_TIMEOUT = "10"

Write-Output "Identifiants cloud chargés dans la session :"
Write-Output ("  Neo4j    : " + $env:NEO4J_URI)
Write-Output ("  Weaviate : " + $env:WEAVIATE_URL)
Write-Output ("  Redis    : " + ($env:REDIS_URL -replace ':[^:@]+@', ':****@'))
