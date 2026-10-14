// ============================================================================
// Layout enterprise : sidebar sombre scindée en ESPACE UTILISATEUR (agent
// bancaire) / ESPACE ADMIN (pilotage), filtrée par rôle + header de page.
// ============================================================================
import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  LayoutDashboard, Send, Workflow, ScanEye, FileText,
  PlayCircle, LogOut, FolderOpen,
  BookMarked, GitFork, Library, UserCircle,
  Sun, Moon,
} from 'lucide-react';
import { Link, useRoute } from '../router';
import { type Session, ROLE_LABELS, ROLE_PAGES, logout } from '../auth';
import { api } from '../api/client';
import { getTheme, toggleTheme, type Theme } from '../theme';

// Chaque section ne s'affiche que si le rôle a accès à ≥1 de ses pages
// (ROLE_PAGES filtre) — l'agent bancaire n'y voit que son propre espace.
const NAV_SECTIONS = [
  {
    label: 'Espace utilisateur',
    items: [
      { page: 'submit',  label: 'Nouvelle transaction', Icon: Send },
      { page: 'myfiles', label: 'Mes dossiers',         Icon: FolderOpen },
      { page: 'profile', label: 'Mon profil',           Icon: UserCircle },
    ],
  },
  {
    label: 'Conformité & connaissance',
    items: [
      { page: 'supervisor',  label: 'File superviseur',       Icon: ScanEye },
      { page: 'reports',     label: 'Rapports & SAR',         Icon: FileText },
      { page: 'rules',       label: 'Règles extraites',       Icon: BookMarked },
      { page: 'graph',       label: 'Graphe de connaissances', Icon: GitFork },
      { page: 'regulations', label: 'Corpus réglementaire',   Icon: Library },
    ],
  },
  {
    label: 'Espace admin',
    items: [
      { page: '',          label: 'Dashboard',       Icon: LayoutDashboard },
      { page: 'simulator', label: 'Flux en direct',  Icon: PlayCircle },
    ],
  },
  {
    label: 'Analyse',
    items: [
      { page: 'pipeline', label: 'Pipeline Live', Icon: Workflow },
    ],
  },
];

export function Layout({ session, onLogout, children }: {
  session: Session; onLogout: () => void; children: ReactNode;
}) {
  const route = useRoute();
  const allowed = ROLE_PAGES[session.role];

  // ── État des services (poll /api/health toutes les 30 s) ─────────
  const [health, setHealth] = useState<{ ok: boolean; neo4j: boolean } | null>(null);
  useEffect(() => {
    const check = () => api.health()
      .then(h => setHealth({ ok: h.components_ready, neo4j: h.neo4j }))
      .catch(() => setHealth({ ok: false, neo4j: false }));
    check();
    const id = setInterval(check, 30_000);
    return () => clearInterval(id);
  }, []);

  // ── Badge « Mes dossiers » + TOASTS de changement d'état (poll 15 s) ──
  //  Le même sondage sert deux usages : le compteur rouge dans le menu, et
  //  la détection des TRANSITIONS (PENDING_* → APPROUVÉ/REFUSÉ…) qui
  //  déclenchent un toast furtif en bas à droite, où que soit l'utilisateur.
  const [notifCount, setNotifCount] = useState(0);
  const [toasts, setToasts] = useState<{ id: number; tone: 'success' | 'danger' | 'warning'; text: string }[]>([]);
  const prevStates = useRef<Map<string, string> | null>(null);
  const pushToast = (tone: 'success' | 'danger' | 'warning', text: string) => {
    const id = Date.now() + Math.random();
    setToasts(t => [...t, { id, tone, text }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 6000);
  };
  useEffect(() => {
    if (!allowed.includes('myfiles')) return;
    const load = () => api.transactions({ mine: true })
      .then(txs => {
        setNotifCount(txs.filter(t =>
          (t.summary?.state ?? '').startsWith('PENDING')).length);
        const cur = new Map(txs.map(t => [t.tx_id, t.summary?.state ?? '']));
        const prev = prevStates.current;
        if (prev) {   // pas de toasts au premier chargement
          for (const [txId, state] of cur) {
            const before = prev.get(txId);
            if (before === state || before === undefined) continue;
            if (state === 'APPROVED_BY_SUPERVISOR') {
              pushToast('success', `✅ Dossier ${txId} approuvé par le superviseur`);
            } else if (state === 'REJECTED_BY_SUPERVISOR') {
              pushToast('danger', `❌ Dossier ${txId} refusé par le superviseur`);
            } else if (state === 'PENDING_DOCUMENTS') {
              pushToast('warning', `📎 Dossier ${txId} — justificatifs demandés`);
            } else if (state === 'PENDING_REVIEW' && before === 'PENDING_DOCUMENTS') {
              pushToast('success', `📤 Dossier ${txId} transmis à la revue superviseur`);
            }
          }
        }
        prevStates.current = cur;
      })
      .catch(() => { /* silencieux */ });
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, [allowed]);

  return (
    <div className="flex min-h-screen bg-slate-100">
      {/* ── Sidebar ─────────────────────────────────────────────────── */}
      <aside className="fixed inset-y-0 left-0 z-20 flex w-64 flex-col bg-[#152c4d] text-slate-300">
        <div className="flex items-center gap-2.5 border-b border-white/10 bg-[#0f1f36] px-5 py-4">
          {/* Logo LexIQ — le fichier est un texte bleu marine sur fond blanc :
              on le pose sur une pastille claire pour qu'il reste lisible sur la
              barre latérale sombre. */}
          <span className="flex items-center justify-center rounded-lg bg-white px-2.5 py-1.5">
            <img src="/lexiq-logo.png" alt="LexIQ" className="h-5 w-auto" />
          </span>
          <div className="min-w-0">
            <p className="text-xs font-semibold leading-snug text-white">
              Financial Compliance Platform
            </p>
          </div>
        </div>

        <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-4">
          {NAV_SECTIONS.map(section => {
            const items = section.items.filter(i => allowed.includes(i.page));
            if (items.length === 0) return null;
            return (
              <div key={section.label}>
                <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                  {section.label}
                </p>
                <div className="space-y-0.5">
                  {items.map(({ page, label, Icon }) => {
                    const active = route.path === page;
                    return (
                      <Link
                        key={page || 'dash'}
                        to={page || ''}
                        className={`relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                          active
                            ? 'bg-indigo-500/15 font-semibold text-indigo-100'
                            : 'text-slate-400 hover:bg-white/10 hover:text-white'
                        }`}
                      >
                        {active && (
                          <span className="absolute inset-y-1.5 left-0 w-1 rounded-r-full bg-indigo-400" />
                        )}
                        <Icon size={16} strokeWidth={2}
                          className={active ? 'text-indigo-300' : undefined} />
                        {label}
                        {page === 'myfiles' && notifCount > 0 && (
                          <span className="ml-auto flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1.5 text-[10px] font-bold text-white"
                            title={`${notifCount} dossier(s) en attente (documents ou superviseur)`}>
                            {notifCount}
                          </span>
                        )}
                      </Link>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </nav>

        {/* ── État des services (indicateur discret) ──────────────── */}
        <div className="border-t border-white/10 px-4 py-2.5">
          <span className="flex items-center gap-1.5 text-[10px] text-slate-400"
            title={health
              ? `API ${health.ok ? 'en ligne' : 'initialisation…'} · Neo4j ${health.neo4j ? 'connecté' : 'hors ligne'}`
              : 'Vérification…'}>
            <span className={`h-2 w-2 rounded-full ${
              health === null ? 'bg-slate-500'
              : health.ok ? 'animate-pulse-soft bg-emerald-400' : 'bg-red-400'}`} />
            {health === null ? 'Services…'
              : health.ok ? `En ligne · Neo4j ${health.neo4j ? '✓' : '✗'}`
              : 'API hors ligne'}
          </span>
        </div>

        <div className="border-t border-white/10 px-4 py-3">
          <div className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-emerald-500 text-xs font-bold text-white">
              {(session.name || session.username).split(/[\s@.]+/).filter(Boolean)
                .slice(0, 2).map(w => w[0]?.toUpperCase() ?? '').join('')}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-white">
                {session.name || session.username}
              </p>
              <p className="text-xs text-slate-400">{ROLE_LABELS[session.role]}</p>
            </div>
          </div>
          <button
            onClick={() => { logout(); onLogout(); }}
            className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:bg-white/10"
          >
            <LogOut size={13} /> Se déconnecter
          </button>
        </div>

        {/* Mention du partenaire industriel — discrète, toujours visible. */}
        <div className="border-t border-white/10 px-4 py-2 text-center">
          <p className="text-[10px] font-semibold leading-tight tracking-wider text-slate-500">
            VERMEG
          </p>
        </div>
      </aside>

      {/* ── Contenu ─────────────────────────────────────────────────── */}
      <main className="ml-64 flex-1 px-8 py-6">{children}</main>

      {/* ── Toasts de notification (bas droite, furtifs 6 s) ─────────── */}
      <div className="fixed bottom-5 right-5 z-50 flex w-80 flex-col gap-2">
        {toasts.map(t => (
          <div key={t.id}
            className={`animate-slide-up rounded-xl border px-4 py-3 text-sm font-medium shadow-lg backdrop-blur ${
              t.tone === 'success' ? 'border-emerald-300 bg-emerald-50/95 text-emerald-800'
              : t.tone === 'danger' ? 'border-red-300 bg-red-50/95 text-red-800'
              : 'border-amber-300 bg-amber-50/95 text-amber-800'}`}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: {
  title: ReactNode; subtitle?: string; actions?: ReactNode;
}) {
  // Bascule thème clair/sombre — placée à côté des actions de page
  // (donc juste à côté du bouton « Actualiser » sur les pages qui en ont un).
  const [theme, setTheme] = useState<Theme>(getTheme());
  return (
    <div className="mb-6 flex items-start justify-between gap-4 border-b border-slate-200 pb-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-500">{subtitle}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {actions}
        <button
          onClick={() => setTheme(toggleTheme())}
          title={theme === 'dark' ? 'Passer en mode clair' : 'Passer en mode sombre'}
          aria-label="Changer de thème"
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-300 bg-white text-slate-600 shadow-sm transition-colors hover:text-indigo-600">
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </div>
  );
}
