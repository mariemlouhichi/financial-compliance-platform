// ============================================================================
// Routeur hash minimaliste — évite d'installer react-router (zéro dépendance).
// URL de la forme  #/pipeline/TX-20260707-0001  →  { path: 'pipeline', param }
// ============================================================================
import { useEffect, useState, type ReactNode } from 'react';

export interface Route {
  path: string;    // premier segment ('' = dashboard)
  param?: string;  // second segment éventuel (ex: tx_id)
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '');
  const [path = '', param] = raw.split('/');
  return { path, param };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(parseHash());
  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}

export function navigate(to: string) {
  window.location.hash = to.startsWith('/') ? `#${to}` : `#/${to}`;
}

export function Link({ to, className, children }: {
  to: string; className?: string; children: ReactNode;
}) {
  return (
    <a href={`#/${to.replace(/^\//, '')}`} className={className}>
      {children}
    </a>
  );
}
