// ============================================================================
// Mini-rendu Markdown maison (zéro dépendance) — couvre la syntaxe utilisée
// par les rapports SAR : titres, gras/italique/code, tableaux, listes,
// citations, règles horizontales. + export PDF via fenêtre d'impression
// (le navigateur propose « Enregistrer au format PDF »).
// ============================================================================

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Inline : `code`, **gras**, *italique* (après échappement HTML). */
function inline(s: string): string {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>');
}

export function mdToHtml(md: string): string {
  const lines = md.split('\n');
  const out: string[] = [];
  let i = 0;
  let inList = false;
  const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };

  while (i < lines.length) {
    const line = lines[i];

    // Tableau : ligne |...| suivie d'un séparateur |---|
    if (line.trim().startsWith('|') && i + 1 < lines.length
        && /^\s*\|[\s\-|:]+\|\s*$/.test(lines[i + 1])) {
      closeList();
      const cells = (l: string) => l.trim().replace(/^\||\|$/g, '').split('|');
      out.push('<table><thead><tr>');
      for (const c of cells(line)) out.push(`<th>${inline(c.trim())}</th>`);
      out.push('</tr></thead><tbody>');
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        out.push('<tr>');
        for (const c of cells(lines[i])) out.push(`<td>${inline(c.trim())}</td>`);
        out.push('</tr>');
        i++;
      }
      out.push('</tbody></table>');
      continue;
    }

    const t = line.trim();
    if (t === '---' || t === '***') { closeList(); out.push('<hr/>'); }
    else if (t.startsWith('### ')) { closeList(); out.push(`<h3>${inline(t.slice(4))}</h3>`); }
    else if (t.startsWith('## ')) { closeList(); out.push(`<h2>${inline(t.slice(3))}</h2>`); }
    else if (t.startsWith('# ')) { closeList(); out.push(`<h1>${inline(t.slice(2))}</h1>`); }
    else if (t.startsWith('> ')) { closeList(); out.push(`<blockquote>${inline(t.slice(2))}</blockquote>`); }
    else if (/^[-•]\s+/.test(t)) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${inline(t.replace(/^[-•]\s+/, ''))}</li>`);
    } else if (/^\d+\.\s+/.test(t)) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${inline(t.replace(/^\d+\.\s+/, ''))}</li>`);
    } else if (t === '') { closeList(); }
    else { closeList(); out.push(`<p>${inline(t)}</p>`); }
    i++;
  }
  closeList();
  return out.join('\n');
}

const MD_CSS = `
  * { box-sizing: border-box; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; color: #1e293b;
         line-height: 1.55; max-width: 820px; margin: 0 auto; padding: 28px;
         font-size: 13px; }
  h1 { font-size: 19px; border-bottom: 2px solid #0f172a; padding-bottom: 8px; }
  h2 { font-size: 15px; margin-top: 22px; color: #0f172a; }
  h3 { font-size: 13px; margin-top: 16px; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 12px; }
  th, td { border: 1px solid #cbd5e1; padding: 5px 8px; text-align: left; }
  th { background: #f1f5f9; font-size: 10px; text-transform: uppercase;
       letter-spacing: .04em; color: #475569; }
  blockquote { border-left: 3px solid #6366f1; background: #eef2ff;
               margin: 10px 0; padding: 8px 12px; border-radius: 0 6px 6px 0; }
  code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px;
         font-size: 11px; font-family: Consolas, monospace; }
  hr { border: none; border-top: 1px solid #e2e8f0; margin: 16px 0; }
  ul { padding-left: 20px; }
  @media print { body { padding: 0; } }
`;

// CSS scopé pour l'affichage DANS l'app (mêmes styles, préfixés .sar-md)
const VIEW_CSS = `
  .sar-md { color: #1e293b; line-height: 1.55; font-size: 13px; }
  .sar-md h1 { font-size: 17px; border-bottom: 2px solid #0f172a; padding-bottom: 8px; }
  .sar-md h2 { font-size: 15px; margin-top: 20px; color: #0f172a; }
  .sar-md h3 { font-size: 13px; margin-top: 14px; }
  .sar-md table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 12px; }
  .sar-md th, .sar-md td { border: 1px solid #cbd5e1; padding: 5px 8px; text-align: left; }
  .sar-md th { background: #f1f5f9; font-size: 10px; text-transform: uppercase;
               letter-spacing: .04em; color: #475569; }
  .sar-md blockquote { border-left: 3px solid #6366f1; background: #eef2ff;
                       margin: 10px 0; padding: 8px 12px; border-radius: 0 6px 6px 0; }
  .sar-md code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px;
                 font-size: 11px; font-family: Consolas, monospace; }
  .sar-md hr { border: none; border-top: 1px solid #e2e8f0; margin: 14px 0; }
  .sar-md ul { padding-left: 20px; }
  .sar-md p { margin: 6px 0; }
`;

/** Rendu du markdown dans l'app (modal SAR). */
export function MarkdownView({ md }: { md: string }) {
  return (
    <div className="sar-md" dangerouslySetInnerHTML={{
      __html: `<style>${VIEW_CSS}</style>${mdToHtml(md)}` }} />
  );
}

/** Export PDF : ouvre une fenêtre print-friendly → « Enregistrer en PDF ». */
export function printMarkdown(md: string, title: string, win?: Window | null) {
  // IMPORTANT : ne PAS passer 'noopener' à window.open — cela ferait renvoyer
  // `null` (fenêtre blanche non pilotable). On peut aussi recevoir une fenêtre
  // déjà ouverte (`win`) : indispensable quand l'appel suit un `await`, sinon
  // le navigateur bloque l'ouverture (hors geste utilisateur direct).
  const w = win ?? window.open('', '_blank', 'width=900,height=700');
  if (!w) return;
  w.document.open();
  w.document.write(`<!doctype html><html><head><meta charset="utf-8">
    <title>${esc(title)}</title><style>${MD_CSS}</style></head>
    <body>${mdToHtml(md)}
    <script>window.onload = () => setTimeout(() => window.print(), 300);</script>
    </body></html>`);
  w.document.close();
}
