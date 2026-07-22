// ============================================================================
// Session + rôles — AUTH RÉELLE branchée sur api/auth.py :
//   inscription (mot de passe PBKDF2) → code de confirmation email →
//   login → token signé HMAC (12h) stocké ici et envoyé en Bearer.
// Keycloak (SSO/OAuth2) = perspective : cette couche expose la même interface.
// Le contrôle d'accès réel doit TOUJOURS être ré-appliqué côté API.
// ============================================================================

export type Role = 'USER' | 'ADMIN' | 'COMPLIANCE_OFFICER' | 'AUDITOR';

export interface Session {
  token: string;
  username: string;   // = email (identité)
  role: Role;
  name?: string;      // nom affiché
}

const KEY = 'amlkyc_session';

export const ROLE_LABELS: Record<Role, string> = {
  USER: 'Utilisateur',
  ADMIN: 'Administrateur',
  COMPLIANCE_OFFICER: 'Compliance Officer',
  AUDITOR: 'Auditeur',
};

// ── Matrice des espaces (SÉPARATION DES DEVOIRS) ─────────────────────────
// UTILISATEUR : soumet SES transactions et suit SES dossiers. Pas de SAR ni
//   de corpus réglementaire (documents internes conformité).
// COMPLIANCE_OFFICER (métier) : SEUL rôle qui instruit la file superviseur
//   (approuver/refuser) — garde équivalente côté API (/api/validate).
// ADMIN (technique DSI) : pilotage plateforme (simulateur, dataset, monitoring)
//   + lecture conformité, mais NE PEUT PAS valider un dossier réglementaire.
// AUDITOR : espace d'INVESTIGATION dédié (page audit : recherche transversale,
//   piste d'audit des décisions, statistiques de qualité) + lecture seule.
export const ROLE_PAGES: Record<Role, string[]> = {
  USER:               ['submit', 'myfiles', 'profile', 'pipeline'],
  COMPLIANCE_OFFICER: ['', 'supervisor', 'reports', 'rules', 'graph',
                       'regulations', 'pipeline'],
  ADMIN:              ['', 'submit', 'myfiles', 'profile',
                       'reports', 'rules', 'graph', 'regulations',
                       'simulator', 'pipeline'],
  AUDITOR:            ['', 'audit', 'reports', 'rules', 'graph', 'regulations',
                       'pipeline'],
};

/** Page d'accueil selon le rôle (l'agent bancaire n'a pas le dashboard admin). */
export function homeFor(role: Role): string {
  return role === 'USER' ? 'myfiles' : '';
}

export function isAdminRole(role: Role): boolean {
  return role !== 'USER';
}

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    // ancienne session démo (sans token) → invalide, forcer re-login
    return s.token ? s : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session): Session {
  localStorage.setItem(KEY, JSON.stringify(s));
  return s;
}

export function logout() {
  localStorage.removeItem(KEY);
}

export function canAccess(role: Role, page: string): boolean {
  return ROLE_PAGES[role].includes(page);
}
