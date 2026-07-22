// ============================================================================
// Thème clair / sombre — classe .dark sur <html>, persisté en localStorage.
// Le remapping des couleurs se fait dans index.css (bloc « MODE SOMBRE ») :
// les classes utilitaires récurrentes (bg-white, text-slate-900…) y sont
// redéfinies sous le scope .dark → aucune page à modifier individuellement.
// ============================================================================

const KEY = 'amlkyc_theme';

export type Theme = 'light' | 'dark';

export function getTheme(): Theme {
  return localStorage.getItem(KEY) === 'dark' ? 'dark' : 'light';
}

export function applyTheme(t: Theme) {
  document.documentElement.classList.toggle('dark', t === 'dark');
  localStorage.setItem(KEY, t);
}

/** À appeler au démarrage (main.tsx) — applique le thème sauvegardé. */
export function initTheme() {
  applyTheme(getTheme());
}

export function toggleTheme(): Theme {
  const next: Theme = getTheme() === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  return next;
}
