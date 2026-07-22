"""
generate_dashboard.py — Génère un dashboard HTML interactif pour visualiser
la collection AMLChunk dans Weaviate.

Le script :
  1. Se connecte à Weaviate (localhost:8088)
  2. Récupère toutes les statistiques et un échantillon de chunks
  3. Génère un fichier HTML autonome avec les données pré-intégrées
  4. Ouvre le dashboard dans le navigateur

USAGE :
  python generate_dashboard.py

RÉSULTAT :
  → output/weaviate_dashboard.html (s'ouvre automatiquement)
"""

import weaviate
import json
import os
import webbrowser
from collections import defaultdict

WEAVIATE_PORT = 8088
COLLECTION_NAME = "AMLChunk"
OUTPUT_HTML = os.path.join(os.path.dirname(__file__), "output", "weaviate_dashboard.html")


def collect_data():
    """Récupère toutes les données depuis Weaviate en une seule passe."""
    print("[INFO] Connecting to Weaviate...")
    client = weaviate.connect_to_local(port=WEAVIATE_PORT)

    try:
        collection = client.collections.get(COLLECTION_NAME)
        total = collection.aggregate.over_all(total_count=True)

        role_counts = defaultdict(int)
        jurisdiction_counts = defaultdict(int)
        doc_counts = defaultdict(int)
        sizes = []
        threshold_count = 0
        samples = []
        all_chunks_preview = []

        print("[INFO] Collecting data...")
        for obj in collection.iterator():
            p = obj.properties
            role = p.get("chunk_role", "UNKNOWN")
            jur = p.get("jurisdiction", "Unknown")
            doc = p.get("source_doc", "Unknown")
            size = p.get("char_count", 0)

            role_counts[role] += 1
            jurisdiction_counts[jur] += 1
            doc_counts[doc] += 1
            sizes.append(size)

            if p.get("has_threshold", False):
                threshold_count += 1

            if len(samples) < 20:
                samples.append({
                    "text": p.get("text", "")[:500],
                    "source_doc": doc,
                    "article_ref": p.get("article_ref", ""),
                    "jurisdiction": jur,
                    "chunk_role": role,
                    "has_threshold": p.get("has_threshold", False),
                    "char_count": size,
                    "chunk_index": p.get("chunk_index", 0),
                })

            all_chunks_preview.append({
                "source_doc": doc,
                "article_ref": p.get("article_ref", ""),
                "jurisdiction": jur,
                "chunk_role": role,
                "has_threshold": p.get("has_threshold", False),
                "char_count": size,
                "text": p.get("text", "")[:300],
            })

        sizes.sort()
        stats = {
            "total": total.total_count,
            "roles": dict(sorted(role_counts.items(), key=lambda x: x[1], reverse=True)),
            "jurisdictions": dict(jurisdiction_counts),
            "documents": dict(sorted(doc_counts.items(), key=lambda x: x[1], reverse=True)),
            "thresholds": threshold_count,
            "size_stats": {
                "min": min(sizes) if sizes else 0,
                "max": max(sizes) if sizes else 0,
                "avg": sum(sizes) // len(sizes) if sizes else 0,
                "median": sizes[len(sizes) // 2] if sizes else 0,
                "p10": sizes[int(len(sizes) * 0.1)] if sizes else 0,
                "p90": sizes[int(len(sizes) * 0.9)] if sizes else 0,
            },
            "size_brackets": {
                "< 200": sum(1 for s in sizes if s < 200),
                "200-500": sum(1 for s in sizes if 200 <= s < 500),
                "500-1K": sum(1 for s in sizes if 500 <= s < 1000),
                "1K-1.5K": sum(1 for s in sizes if 1000 <= s < 1500),
                "> 1.5K": sum(1 for s in sizes if s >= 1500),
            },
            "samples": samples,
            "all_chunks": all_chunks_preview,
        }

        print(f"   OK: {total.total_count} chunks collected")
        return stats

    finally:
        client.close()


def generate_html(stats):
    """Génère le fichier HTML avec les données intégrées."""

    roles_json = json.dumps(stats["roles"])
    jurisdictions_json = json.dumps(stats["jurisdictions"])
    documents_json = json.dumps(stats["documents"])
    size_brackets_json = json.dumps(stats["size_brackets"])
    samples_json = json.dumps(stats["samples"], ensure_ascii=False)
    all_chunks_json = json.dumps(stats["all_chunks"], ensure_ascii=False)
    size_stats = stats["size_stats"]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AMLChunk Collection — Weaviate Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'DM Sans', sans-serif;
    background: #0a0e17;
    color: #c8cdd8;
    min-height: 100vh;
}}
.header {{
    background: linear-gradient(135deg, #0f1629 0%, #1a1f3a 100%);
    border-bottom: 1px solid #1e2a4a;
    padding: 24px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}}
.header h1 {{
    font-size: 22px;
    font-weight: 600;
    color: #e8ecf4;
    display: flex;
    align-items: center;
    gap: 12px;
}}
.header .logo {{
    width: 32px; height: 32px;
    background: linear-gradient(135deg, #4ade80, #22d3ee);
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; color: #0a0e17; font-size: 16px;
}}
.header .meta {{
    font-size: 13px;
    color: #6b7394;
}}
.header .meta span {{
    color: #4ade80;
    font-weight: 600;
}}

.tabs {{
    background: #0f1629;
    border-bottom: 1px solid #1e2a4a;
    padding: 0 32px;
    display: flex;
    gap: 0;
}}
.tab {{
    padding: 14px 24px;
    font-size: 14px;
    font-weight: 500;
    color: #6b7394;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;
}}
.tab:hover {{ color: #c8cdd8; }}
.tab.active {{
    color: #4ade80;
    border-bottom-color: #4ade80;
}}

.container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px 32px;
}}

.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

/* Overview cards */
.stats-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 24px;
}}
.stat-card {{
    background: #111827;
    border: 1px solid #1e2a4a;
    border-radius: 12px;
    padding: 20px;
}}
.stat-card .label {{
    font-size: 12px;
    color: #6b7394;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
}}
.stat-card .value {{
    font-size: 28px;
    font-weight: 700;
    color: #e8ecf4;
    font-family: 'JetBrains Mono', monospace;
}}
.stat-card .sub {{
    font-size: 12px;
    color: #6b7394;
    margin-top: 4px;
}}

.charts-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 24px;
}}
.chart-card {{
    background: #111827;
    border: 1px solid #1e2a4a;
    border-radius: 12px;
    padding: 20px;
}}
.chart-card h3 {{
    font-size: 14px;
    font-weight: 600;
    color: #e8ecf4;
    margin-bottom: 16px;
}}

/* Documents table */
.table-card {{
    background: #111827;
    border: 1px solid #1e2a4a;
    border-radius: 12px;
    overflow: hidden;
}}
.table-card h3 {{
    font-size: 14px;
    font-weight: 600;
    color: #e8ecf4;
    padding: 16px 20px;
    border-bottom: 1px solid #1e2a4a;
}}
table {{
    width: 100%;
    border-collapse: collapse;
}}
th {{
    text-align: left;
    padding: 10px 20px;
    font-size: 11px;
    font-weight: 600;
    color: #6b7394;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid #1e2a4a;
    background: #0d1322;
}}
td {{
    padding: 10px 20px;
    font-size: 13px;
    color: #c8cdd8;
    border-bottom: 1px solid #131b2e;
}}
tr:hover td {{ background: #131b2e; }}

/* Badges */
.badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 600;
    font-family: 'JetBrains Mono', monospace;
}}
.badge-sanction {{ background: #3b1320; color: #f87171; }}
.badge-obligation {{ background: #1a2744; color: #60a5fa; }}
.badge-procedure {{ background: #132b2a; color: #34d399; }}
.badge-threshold {{ background: #2a2210; color: #fbbf24; }}
.badge-definition {{ background: #2a1a3e; color: #a78bfa; }}
.badge-red_flag {{ background: #3b1320; color: #fb923c; }}
.badge-reporting {{ background: #1a2744; color: #38bdf8; }}
.badge-exemption {{ background: #1f2e1a; color: #86efac; }}
.badge-information {{ background: #1a1f2e; color: #94a3b8; }}

.badge-tunisia {{ background: #1a2744; color: #60a5fa; }}
.badge-international {{ background: #132b2a; color: #34d399; }}

/* Chunks explorer */
.search-bar {{
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
}}
.search-bar input, .search-bar select {{
    background: #111827;
    border: 1px solid #1e2a4a;
    border-radius: 8px;
    padding: 10px 16px;
    color: #e8ecf4;
    font-family: 'DM Sans', sans-serif;
    font-size: 13px;
    outline: none;
}}
.search-bar input:focus, .search-bar select:focus {{
    border-color: #4ade80;
}}
.search-bar input {{ flex: 1; }}
.search-bar select {{ min-width: 150px; }}

.chunk-card {{
    background: #111827;
    border: 1px solid #1e2a4a;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 10px;
    transition: border-color 0.2s;
}}
.chunk-card:hover {{ border-color: #2a3a5a; }}
.chunk-meta {{
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 8px;
}}
.chunk-meta .doc {{
    font-size: 13px;
    font-weight: 600;
    color: #e8ecf4;
}}
.chunk-meta .ref {{
    font-size: 12px;
    color: #6b7394;
    font-family: 'JetBrains Mono', monospace;
}}
.chunk-text {{
    font-size: 13px;
    color: #94a3b8;
    line-height: 1.6;
    max-height: 80px;
    overflow: hidden;
    position: relative;
}}
.chunk-text.expanded {{ max-height: none; }}
.chunk-footer {{
    display: flex;
    gap: 16px;
    margin-top: 8px;
    font-size: 11px;
    color: #4b5574;
}}

.pagination {{
    display: flex;
    justify-content: center;
    gap: 8px;
    margin-top: 16px;
}}
.pagination button {{
    background: #111827;
    border: 1px solid #1e2a4a;
    border-radius: 6px;
    padding: 6px 14px;
    color: #c8cdd8;
    font-size: 13px;
    cursor: pointer;
}}
.pagination button:hover {{ border-color: #4ade80; }}
.pagination button.active {{ background: #4ade80; color: #0a0e17; border-color: #4ade80; }}
.pagination .info {{ padding: 6px 14px; font-size: 13px; color: #6b7394; }}

/* Size stats */
.size-grid {{
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 12px;
    margin-bottom: 16px;
}}
.size-stat {{
    background: #0d1322;
    border-radius: 8px;
    padding: 12px;
    text-align: center;
}}
.size-stat .label {{ font-size: 11px; color: #6b7394; }}
.size-stat .val {{
    font-size: 18px; font-weight: 700; color: #e8ecf4;
    font-family: 'JetBrains Mono', monospace;
    margin-top: 4px;
}}
</style>
</head>
<body>

<div class="header">
    <h1>
        <div class="logo">W</div>
        AMLChunk Collection
    </h1>
    <div class="meta">
        Weaviate v1.28.4 — localhost:8088 — <span>{stats['total']}</span> objects — HNSW Cosine 768d
    </div>
</div>

<div class="tabs">
    <div class="tab active" onclick="switchTab('overview')">Overview</div>
    <div class="tab" onclick="switchTab('documents')">Documents</div>
    <div class="tab" onclick="switchTab('explorer')">Chunk Explorer</div>
</div>

<div class="container">

<!-- ══════ OVERVIEW TAB ══════ -->
<div id="tab-overview" class="tab-content active">
    <div class="stats-grid">
        <div class="stat-card">
            <div class="label">Total Chunks</div>
            <div class="value">{stats['total']:,}</div>
            <div class="sub">{len(stats['documents'])} documents indexed</div>
        </div>
        <div class="stat-card">
            <div class="label">Vector Dimensions</div>
            <div class="value">768</div>
            <div class="sub">BAAI/bge-base-en-v1.5</div>
        </div>
        <div class="stat-card">
            <div class="label">Financial Thresholds</div>
            <div class="value">{stats['thresholds']}</div>
            <div class="sub">{stats['thresholds']*100//stats['total']}% of chunks</div>
        </div>
        <div class="stat-card">
            <div class="label">Avg Chunk Size</div>
            <div class="value">{size_stats['avg']}</div>
            <div class="sub">chars (median: {size_stats['median']})</div>
        </div>
    </div>

    <div class="charts-grid">
        <div class="chart-card">
            <h3>Distribution by Role</h3>
            <canvas id="roleChart" height="260"></canvas>
        </div>
        <div class="chart-card">
            <h3>Distribution by Jurisdiction</h3>
            <canvas id="jurChart" height="130"></canvas>
            <div style="margin-top:16px">
                <h3>Chunk Size Distribution</h3>
                <canvas id="sizeChart" height="130"></canvas>
            </div>
        </div>
    </div>

    <div class="chart-card">
        <h3>Chunk Size Statistics</h3>
        <div class="size-grid">
            <div class="size-stat"><div class="label">Min</div><div class="val">{size_stats['min']}</div></div>
            <div class="size-stat"><div class="label">P10</div><div class="val">{size_stats['p10']}</div></div>
            <div class="size-stat"><div class="label">Median</div><div class="val">{size_stats['median']}</div></div>
            <div class="size-stat"><div class="label">Average</div><div class="val">{size_stats['avg']}</div></div>
            <div class="size-stat"><div class="label">P90</div><div class="val">{size_stats['p90']}</div></div>
            <div class="size-stat"><div class="label">Max</div><div class="val">{size_stats['max']}</div></div>
        </div>
    </div>
</div>

<!-- ══════ DOCUMENTS TAB ══════ -->
<div id="tab-documents" class="tab-content">
    <div class="table-card">
        <h3>All Documents ({len(stats['documents'])} sources)</h3>
        <table>
            <thead>
                <tr>
                    <th>Document</th>
                    <th>Chunks</th>
                    <th>% of Total</th>
                    <th>Bar</th>
                </tr>
            </thead>
            <tbody id="docTableBody"></tbody>
        </table>
    </div>
</div>

<!-- ══════ EXPLORER TAB ══════ -->
<div id="tab-explorer" class="tab-content">
    <div class="search-bar">
        <input type="text" id="searchInput" placeholder="Search in chunks..." oninput="filterChunks()">
        <select id="roleFilter" onchange="filterChunks()">
            <option value="">All Roles</option>
        </select>
        <select id="jurFilter" onchange="filterChunks()">
            <option value="">All Jurisdictions</option>
        </select>
        <select id="docFilter" onchange="filterChunks()">
            <option value="">All Documents</option>
        </select>
    </div>
    <div id="chunksList"></div>
    <div class="pagination" id="pagination"></div>
</div>

</div>

<script>
// ══════ DATA ══════
const roles = {roles_json};
const jurisdictions = {jurisdictions_json};
const documents = {documents_json};
const sizeBrackets = {size_brackets_json};
const samples = {samples_json};
const allChunks = {all_chunks_json};

// ══════ TAB SWITCHING ══════
function switchTab(name) {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelector('#tab-' + name).classList.add('active');
    event.target.classList.add('active');
}}

// ══════ CHARTS ══════
const roleColors = {{
    'OBLIGATION': '#60a5fa', 'INFORMATION': '#94a3b8', 'PROCEDURE': '#34d399',
    'THRESHOLD': '#fbbf24', 'RED_FLAG': '#fb923c', 'DEFINITION': '#a78bfa',
    'SANCTION': '#f87171', 'REPORTING': '#38bdf8', 'EXEMPTION': '#86efac'
}};

new Chart(document.getElementById('roleChart'), {{
    type: 'bar',
    data: {{
        labels: Object.keys(roles),
        datasets: [{{
            data: Object.values(roles),
            backgroundColor: Object.keys(roles).map(r => roleColors[r] || '#6b7394'),
            borderRadius: 6,
            barPercentage: 0.7,
        }}]
    }},
    options: {{
        indexAxis: 'y',
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ grid: {{ color: '#1e2a4a' }}, ticks: {{ color: '#6b7394' }} }},
            y: {{ grid: {{ display: false }}, ticks: {{ color: '#c8cdd8', font: {{ family: "'JetBrains Mono'", size: 11 }} }} }}
        }}
    }}
}});

new Chart(document.getElementById('jurChart'), {{
    type: 'doughnut',
    data: {{
        labels: Object.keys(jurisdictions),
        datasets: [{{
            data: Object.values(jurisdictions),
            backgroundColor: ['#60a5fa', '#34d399'],
            borderWidth: 0,
        }}]
    }},
    options: {{
        responsive: true,
        cutout: '65%',
        plugins: {{
            legend: {{ position: 'bottom', labels: {{ color: '#c8cdd8', padding: 16 }} }}
        }}
    }}
}});

new Chart(document.getElementById('sizeChart'), {{
    type: 'bar',
    data: {{
        labels: Object.keys(sizeBrackets),
        datasets: [{{
            data: Object.values(sizeBrackets),
            backgroundColor: '#4ade80',
            borderRadius: 4,
            barPercentage: 0.6,
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ grid: {{ display: false }}, ticks: {{ color: '#6b7394', font: {{ size: 10 }} }} }},
            y: {{ grid: {{ color: '#1e2a4a' }}, ticks: {{ color: '#6b7394' }} }}
        }}
    }}
}});

// ══════ DOCUMENTS TABLE ══════
const docBody = document.getElementById('docTableBody');
const maxChunks = Math.max(...Object.values(documents));
Object.entries(documents).forEach(([doc, count]) => {{
    const pct = (count / {stats['total']} * 100).toFixed(1);
    const barW = (count / maxChunks * 100).toFixed(0);
    docBody.innerHTML += '<tr>' +
        '<td style="font-family:JetBrains Mono;font-size:12px">' + doc + '</td>' +
        '<td style="font-family:JetBrains Mono;font-weight:600">' + count + '</td>' +
        '<td>' + pct + '%</td>' +
        '<td><div style="background:#1e2a4a;border-radius:4px;height:8px;width:100%">' +
        '<div style="background:#4ade80;border-radius:4px;height:8px;width:' + barW + '%"></div></div></td>' +
        '</tr>';
}});

// ══════ EXPLORER ══════
let filteredChunks = [...allChunks];
let currentPage = 0;
const perPage = 20;

// Populate filters
const roleFilter = document.getElementById('roleFilter');
Object.keys(roles).forEach(r => {{
    roleFilter.innerHTML += '<option value="' + r + '">' + r + ' (' + roles[r] + ')</option>';
}});
const jurFilter = document.getElementById('jurFilter');
Object.keys(jurisdictions).forEach(j => {{
    jurFilter.innerHTML += '<option value="' + j + '">' + j + '</option>';
}});
const docFilter = document.getElementById('docFilter');
Object.keys(documents).forEach(d => {{
    docFilter.innerHTML += '<option value="' + d + '">' + d.substring(0, 40) + '</option>';
}});

function getBadgeClass(role) {{
    return 'badge badge-' + role.toLowerCase();
}}
function getJurBadge(jur) {{
    return 'badge badge-' + jur.toLowerCase();
}}

function filterChunks() {{
    const search = document.getElementById('searchInput').value.toLowerCase();
    const role = document.getElementById('roleFilter').value;
    const jur = document.getElementById('jurFilter').value;
    const doc = document.getElementById('docFilter').value;

    filteredChunks = allChunks.filter(c => {{
        if (role && c.chunk_role !== role) return false;
        if (jur && c.jurisdiction !== jur) return false;
        if (doc && c.source_doc !== doc) return false;
        if (search && !c.source_doc.toLowerCase().includes(search) &&
            !c.article_ref.toLowerCase().includes(search) &&
            !c.chunk_role.toLowerCase().includes(search)) return false;
        return true;
    }});

    currentPage = 0;
    renderChunks();
}}

function renderChunks() {{
    const list = document.getElementById('chunksList');
    const start = currentPage * perPage;
    const page = filteredChunks.slice(start, start + perPage);

    list.innerHTML = '';
    page.forEach((c, i) => {{
        const textPreview = c.text || '';

        list.innerHTML += '<div class="chunk-card">' +
            '<div class="chunk-meta">' +
            '<span class="' + getBadgeClass(c.chunk_role) + '">' + c.chunk_role + '</span>' +
            '<span class="' + getJurBadge(c.jurisdiction) + '">' + c.jurisdiction + '</span>' +
            '<span class="doc">' + c.source_doc + '</span>' +
            '<span class="ref">' + c.article_ref + '</span>' +
            '</div>' +
            (textPreview ? '<div class="chunk-text">' + textPreview.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>' : '') +
            '<div class="chunk-footer">' +
            '<span>' + c.char_count + ' chars</span>' +
            '<span>threshold: ' + (c.has_threshold ? 'yes' : 'no') + '</span>' +
            '</div>' +
            '</div>';
    }});

    // Pagination
    const totalPages = Math.ceil(filteredChunks.length / perPage);
    const pag = document.getElementById('pagination');
    pag.innerHTML = '<div class="info">' + filteredChunks.length + ' chunks</div>';

    if (totalPages > 1) {{
        if (currentPage > 0)
            pag.innerHTML += '<button onclick="currentPage--;renderChunks()">Prev</button>';

        const startP = Math.max(0, currentPage - 3);
        const endP = Math.min(totalPages, currentPage + 4);
        for (let p = startP; p < endP; p++) {{
            pag.innerHTML += '<button class="' + (p === currentPage ? 'active' : '') +
                '" onclick="currentPage=' + p + ';renderChunks()">' + (p + 1) + '</button>';
        }}

        if (currentPage < totalPages - 1)
            pag.innerHTML += '<button onclick="currentPage++;renderChunks()">Next</button>';

        pag.innerHTML += '<div class="info">Page ' + (currentPage + 1) + ' / ' + totalPages + '</div>';
    }}
}}

filterChunks();
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] Dashboard generated: {OUTPUT_HTML}")
    return OUTPUT_HTML


def main():
    stats = collect_data()
    path = generate_html(stats)
    abs_path = os.path.abspath(path)
    print("[INFO] Opening in browser...")
    webbrowser.open(f"file:///{abs_path}")
    print("[OK] Done!")


if __name__ == "__main__":
    main()
