/**
 * Impression / export PDF sans fenêtre surgissante.
 *
 * Pourquoi ce fichier existe :
 *   `printSar` et `printMarkdown` utilisaient `window.open(...)`. Quand le
 *   navigateur bloque les fenêtres surgissantes — comportement par défaut de
 *   Chrome dès que l'appel n'est pas jugé consécutif à un geste utilisateur —
 *   `window.open` renvoie `null`. Le code faisait alors `if (!w) return;`,
 *   donc RIEN ne se passait et aucun message n'était affiché : le bouton
 *   « Télécharger PDF » paraissait mort.
 *
 *   Une iframe masquée n'est jamais soumise au bloqueur. On y écrit le
 *   document, on imprime, puis on la retire.
 */

/** Écrit `html` dans une iframe masquée et déclenche l'impression. */
export function printHtml(html: string): void {
  const frame = document.createElement('iframe');
  frame.setAttribute('aria-hidden', 'true');
  frame.style.position = 'fixed';
  frame.style.right = '0';
  frame.style.bottom = '0';
  frame.style.width = '0';
  frame.style.height = '0';
  frame.style.border = '0';
  document.body.appendChild(frame);

  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  if (!doc || !win) {
    document.body.removeChild(frame);
    return;
  }

  doc.open();
  doc.write(html);
  doc.close();

  const launch = () => {
    try {
      win.focus();
      win.print();
    } finally {
      // on laisse le temps au navigateur d'ouvrir sa boîte d'impression
      // avant de détruire l'iframe (sinon le document disparaît trop tôt)
      window.setTimeout(() => {
        if (frame.parentNode) frame.parentNode.removeChild(frame);
      }, 1000);
    }
  };

  // les images/polices éventuelles doivent être chargées avant l'impression
  if (doc.readyState === 'complete') window.setTimeout(launch, 120);
  else win.addEventListener('load', () => window.setTimeout(launch, 120));
}
