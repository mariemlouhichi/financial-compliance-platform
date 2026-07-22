// ============================================================================
// Rapport SAR STRUCTURÉ — rendu depuis sar_json (plus de markdown brut).
// Une seule source de vérité : sarHtml() produit le HTML stylé, utilisé
//   1. dans la modal (SarView, styles scopés)
//   2. pour le PDF (printSar → fenêtre d'impression → « Enregistrer en PDF »)
// ============================================================================

type Sar = Record<string, any>;

function esc(s: unknown): string {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const DECISION_THEME: Record<string, { bg: string; soft: string }> = {
  PASS: { bg: '#059669', soft: '#ECFDF5' },
  REVIEW: { bg: '#D97706', soft: '#FFFBEB' },
  EDD: { bg: '#EA580C', soft: '#FFF7ED' },
  STR: { bg: '#DC2626', soft: '#FEF2F2' },
  BLOCK: { bg: '#7F1D1D', soft: '#FEF2F2' },
};

function fmtAmount(v: unknown, cur: unknown): string {
  const n = Number(v ?? 0);
  return `${n.toLocaleString('fr-FR')} ${esc(cur)}`;
}

/** HTML complet du rapport (inline-friendly, sans dépendance). */
export function sarHtml(sar: Sar): string {
  const theme = DECISION_THEME[String(sar.decision_trigger)] ?? DECISION_THEME.REVIEW;
  const tx = sar.transaction ?? {};
  const cp = sar.client_profile ?? {};
  const violated: Sar[] = sar.violated_rules ?? [];
  const respected: Sar[] = sar.respected_rules ?? [];
  const shap: Sar[] = sar.ml_top_features ?? [];
  const docs: string[] = sar.required_documents ?? [];

  const scoreTile = (label: string, value: string, sub = '') => `
    <div class="tile">
      <p class="tile-label">${esc(label)}</p>
      <p class="tile-value">${esc(value)}</p>
      ${sub ? `<p class="tile-sub">${esc(sub)}</p>` : ''}
    </div>`;

  const covBar = (label: string, pct: number) => `
    <div class="cov">
      <span class="cov-label">${esc(label)}</span>
      <span class="cov-track"><span class="cov-fill" style="width:${Math.min(100, pct)}%"></span></span>
      <span class="cov-pct">${pct.toFixed(0)} %</span>
    </div>`;

  return `
  <div class="sar">
    <!-- ── Bandeau de décision ─────────────────────────────────────── -->
    <div class="band" style="background:${theme.bg}">
      <div>
        <p class="band-id">DÉCLARATION DE SOUPÇON · ${esc(sar.sar_id)}</p>
        <p class="band-decision">${esc(sar.decision_trigger)}
          <span class="band-risk">· risque ${esc(sar.risk_level)} · ${Number(sar.risk_score_combined ?? 0).toFixed(0)}/100</span></p>
      </div>
      <div class="band-right">
        <p>${esc(String(sar.generated_at ?? '').slice(0, 16).replace('T', ' '))}</p>
        <p>${esc(sar.declarant_bank)}</p>
      </div>
    </div>

    <!-- ── Recommandation ──────────────────────────────────────────── -->
    <div class="reco" style="background:${theme.soft};border-color:${theme.bg}">
      <span class="reco-icon" style="background:${theme.bg}">🎯</span>
      <p>${esc(sar.recommendation)}</p>
    </div>

    <!-- ── Synthèse ────────────────────────────────────────────────── -->
    <section>
      <h2>🧭 Synthèse</h2>
      <p class="summary">${esc(sar.executive_summary || sar.narrative || '—')}</p>
    </section>

    <!-- ── Scores ──────────────────────────────────────────────────── -->
    <section>
      <h2>📊 Scores</h2>
      <div class="tiles">
        ${scoreTile('Compliance (règles)', `${Number(sar.risk_score_compliance ?? 0).toFixed(0)}/100`, String(sar.decision_trigger ?? ''))}
        ${scoreTile('ML (indicatif)', sar.risk_score_ml ? `${Number(sar.risk_score_ml).toFixed(0)} %` : 'N/A',
          `${esc(sar.ml_decision ?? '')}${sar.ml_reliable === false ? ' · ignoré (profil nouveau)' : ''}`)}
        ${scoreTile('Client', String(sar.client_risk_level || '—'), cp.score != null ? `${Number(cp.score).toFixed(0)}/100` : '')}
        ${scoreTile('Score combiné', `${Number(sar.risk_score_combined ?? 0).toFixed(0)}/100`, String(sar.risk_level ?? ''))}
      </div>
      <p class="divergence"><b>Divergence RAG ↔ ML : ${esc(sar.divergence_flag)}</b> — ${esc(sar.divergence_action)}</p>
    </section>

    <!-- ── Transaction ─────────────────────────────────────────────── -->
    <section>
      <h2>💳 Opération</h2>
      <table class="kv">
        <tr><td>Type</td><td><b>${esc(tx.type)}</b></td>
            <td>Montant</td><td><b>${fmtAmount(tx.amount, tx.currency)}</b></td></tr>
        <tr><td>Émetteur</td><td>${esc(tx.sender)} (${esc(tx.sender_country)})</td>
            <td>Bénéficiaire</td><td>${esc(tx.beneficiary)} (${esc(tx.beneficiary_country)})</td></tr>
        <tr><td>Motif</td><td>${esc(tx.motive || '—')}</td>
            <td>Justificatif</td><td>${tx.has_justification ? '✅ fourni' : '❌ absent'}</td></tr>
      </table>
    </section>

    <!-- ── Violations ──────────────────────────────────────────────── -->
    <section>
      <h2>⚖️ Obligations violées (${violated.length})</h2>
      ${violated.length === 0 ? '<p class="ok">Aucune obligation violée.</p>' : `
      <table class="rules">
        <tr><th>Source</th><th>Article</th><th>FATF</th><th>Sévérité</th><th>Obligation</th></tr>
        ${violated.slice(0, 6).map(r => `
        <tr>
          <td>${r.is_bct_binding ? '⚖️' : '🌐'} ${esc(String(r.source ?? '').replace('.pdf', ''))}</td>
          <td>${esc(r.article_ref)}</td>
          <td>${esc(r.fatf_rec)}</td>
          <td><b class="sev">${esc(r.severity)}</b></td>
          <td class="rule-text">${esc(String(r.rule_text ?? '').slice(0, 110))}…</td>
        </tr>`).join('')}
      </table>`}
      <p class="conf">✅ ${respected.length} obligation(s) vérifiée(s) conforme(s)</p>
      <div class="covs">
        ${covBar('Couverture BCT', Number(sar.coverage_bct ?? 0))}
        ${covBar('Couverture FATF', Number(sar.coverage_fatf ?? 0))}
      </div>
    </section>

    <!-- ── Pattern + Client + ML ───────────────────────────────────── -->
    <section class="grid2">
      <div>
        <h2>🎯 Pattern AML</h2>
        <p><b>${esc(sar.aml_pattern)}</b> — ${esc(sar.aml_pattern_explanation)}</p>
      </div>
      <div>
        <h2>👤 Profil client</h2>
        <p><span class="chip">${esc(cp.client_id ?? '?')}</span>
           <span class="chip">risque ${esc(cp.level ?? '—')}</span>
           ${cp.is_pep ? '<span class="chip warn">🚨 PEP</span>' : ''}
           ${cp.kyc_complete === false ? '<span class="chip warn">KYC incomplet</span>' : ''}
           ${cp.prior_strs ? '<span class="chip warn">STR antérieurs</span>' : ''}
           ${sar.fatca_obligation && sar.fatca_obligation !== 'NONE'
             ? `<span class="chip">FATCA : ${esc(sar.fatca_obligation)}</span>` : ''}</p>
      </div>
    </section>

    ${shap.length ? `
    <section>
      <h2>🤖 Facteurs ML (SHAP)</h2>
      <table class="rules">
        <tr><th>Feature</th><th>Impact</th><th>Pousse vers</th><th>Interprétation</th></tr>
        ${shap.slice(0, 5).map(f => `
        <tr>
          <td><code>${esc(f.feature)}</code></td>
          <td>${Number(f.shap ?? 0) >= 0 ? '+' : ''}${Number(f.shap ?? 0).toFixed(2)}</td>
          <td>${f.pushes === 'FRAUDE' ? '🔴 FRAUDE' : '🟢 NORMAL'}</td>
          <td>${esc(f.interpretation ?? '—')}</td>
        </tr>`).join('')}
      </table>
    </section>` : ''}

    ${docs.length ? `
    <section>
      <h2>📎 Documents requis</h2>
      <ol>${docs.map(d => `<li>${esc(d)}</li>`).join('')}</ol>
    </section>` : ''}

    <footer>
      🛡️ Financial Compliance Platform — rapport généré automatiquement ·
      auditable (chaque règle cite sa source réglementaire)
    </footer>
  </div>`;
}

const SAR_CSS = `
  .sar { color:#1e293b; font-size:13px; line-height:1.55; }
  .sar section { margin-top:18px; }
  .sar h2 { font-size:13px; margin:0 0 8px; color:#0f172a; border-bottom:1px solid #e2e8f0; padding-bottom:5px; }
  .sar .band { display:flex; justify-content:space-between; align-items:center; gap:12px;
    border-radius:14px; padding:16px 20px; color:#fff; }
  .sar .band-id { margin:0; font-size:10px; letter-spacing:.08em; opacity:.85; }
  .sar .band-decision { margin:4px 0 0; font-size:22px; font-weight:800; }
  .sar .band-risk { font-size:12px; font-weight:500; opacity:.9; }
  .sar .band-right { text-align:right; font-size:11px; opacity:.85; }
  .sar .band-right p { margin:2px 0; }
  .sar .reco { display:flex; align-items:center; gap:10px; margin-top:12px;
    border:1px solid; border-radius:10px; padding:10px 14px; font-weight:600; }
  .sar .reco-icon { width:26px; height:26px; border-radius:50%; display:flex;
    align-items:center; justify-content:center; font-size:13px; flex:none; }
  .sar .reco p { margin:0; }
  .sar .summary { background:#f8fafc; border-left:3px solid #6366f1;
    border-radius:0 8px 8px 0; padding:10px 14px; margin:0; }
  .sar .tiles { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
  .sar .tile { border:1px solid #e2e8f0; border-radius:10px; padding:8px 12px; }
  .sar .tile-label { margin:0; font-size:9px; text-transform:uppercase;
    letter-spacing:.05em; color:#64748b; }
  .sar .tile-value { margin:2px 0 0; font-size:17px; font-weight:800; color:#0f172a; }
  .sar .tile-sub { margin:1px 0 0; font-size:10px; color:#64748b; }
  .sar .divergence { margin:10px 0 0; font-size:12px; background:#f8fafc;
    border-radius:8px; padding:8px 12px; }
  .sar table { border-collapse:collapse; width:100%; font-size:12px; }
  .sar .kv td { border:1px solid #e2e8f0; padding:6px 10px; }
  .sar .kv td:nth-child(odd) { background:#f8fafc; color:#64748b; font-size:10px;
    text-transform:uppercase; letter-spacing:.04em; width:12%; }
  .sar .rules th { background:#f1f5f9; color:#475569; font-size:10px;
    text-transform:uppercase; letter-spacing:.04em; text-align:left;
    padding:5px 8px; border:1px solid #e2e8f0; }
  .sar .rules td { border:1px solid #e2e8f0; padding:5px 8px; vertical-align:top; }
  .sar .rule-text { color:#475569; }
  .sar .sev { color:#b91c1c; }
  .sar .ok { color:#059669; font-weight:600; }
  .sar .conf { margin:8px 0 4px; font-size:12px; color:#059669; }
  .sar .covs { display:grid; gap:4px; }
  .sar .cov { display:flex; align-items:center; gap:8px; font-size:11px; }
  .sar .cov-label { width:110px; color:#64748b; }
  .sar .cov-track { flex:1; height:6px; border-radius:99px; background:#e2e8f0; overflow:hidden; }
  .sar .cov-fill { display:block; height:100%; border-radius:99px; background:#6366f1; }
  .sar .cov-pct { width:38px; text-align:right; font-weight:700; }
  .sar .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .sar .chip { display:inline-block; background:#f1f5f9; border-radius:99px;
    padding:2px 9px; font-size:11px; margin:0 3px 3px 0; }
  .sar .chip.warn { background:#fef2f2; color:#b91c1c; font-weight:600; }
  .sar code { background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:11px; }
  .sar ol { margin:4px 0; padding-left:20px; }
  .sar footer { margin-top:20px; padding-top:10px; border-top:1px solid #e2e8f0;
    font-size:10px; color:#94a3b8; text-align:center; }
  @media print { .sar .band { -webkit-print-color-adjust:exact; print-color-adjust:exact; } }
`;

/** Rendu dans l'app (modal Pipeline Live). */
export function SarView({ sar }: { sar: Sar }) {
  return (
    <div dangerouslySetInnerHTML={{
      __html: `<style>${SAR_CSS}</style>${sarHtml(sar)}` }} />
  );
}

/** Export PDF : fenêtre print-friendly → « Enregistrer au format PDF ». */
export function printSar(sar: Sar, title: string) {
  const w = window.open('', '_blank', 'noopener,width=920,height=720');
  if (!w) return;
  w.document.write(`<!doctype html><html><head><meta charset="utf-8">
    <title>${esc(title)}</title>
    <style>body{font-family:'Segoe UI',system-ui,sans-serif;max-width:820px;
      margin:0 auto;padding:24px;}${SAR_CSS}</style></head>
    <body>${sarHtml(sar)}
    <script>window.onload = () => setTimeout(() => window.print(), 300);</script>
    </body></html>`);
  w.document.close();
}
