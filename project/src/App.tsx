// ============================================================================
// App — point d'entrée : session (auth réelle api/auth.py) + routeur hash.
// Deux espaces : UTILISATEUR (agent bancaire : soumettre + mes dossiers) et
// ADMIN (dashboard, superviseur, simulateur, rapports, réglementation, dataset).
// ============================================================================
import { useEffect, useState, type ReactNode } from 'react';
import { getSession, canAccess, homeFor, type Session } from './auth';
import { useRoute, navigate } from './router';
import { Layout } from './components/Layout';
import { EmptyState } from './components/ui';
import { LoginPage } from './pages/Login';
import { DashboardPage } from './pages/Dashboard';
import { SubmitPage } from './pages/Submit';
import { MyFilesPage } from './pages/MyFiles';
import { PipelineLivePage } from './pages/PipelineLive';
import { SupervisorPage } from './pages/Supervisor';
import { ReportsPage } from './pages/Reports';
import { RegulationsPage } from './pages/Regulations';
import { SimulatorPage } from './pages/Simulator';
import { DatasetPage } from './pages/Dataset';
import { RulesPage } from './pages/Rules';
import { GraphPage } from './pages/Graph';
import { ProfilePage } from './pages/Profile';
import { AuditPage } from './pages/Audit';

export default function App() {
  const [session, setSession] = useState<Session | null>(getSession());
  const route = useRoute();

  // L'agent bancaire n'a pas accès au dashboard admin : la racine ('')
  // le redirige vers l'accueil de SON espace (« Mes dossiers »).
  useEffect(() => {
    if (session && route.path === '' && !canAccess(session.role, '')) {
      navigate(homeFor(session.role));
    }
  }, [session, route.path]);

  if (!session) {
    return <LoginPage onLogin={s => { setSession(s); navigate(homeFor(s.role)); }} />;
  }

  const page = route.path;

  let content: ReactNode;
  if (!canAccess(session.role, page)) {
    content = (
      <EmptyState>
        Accès refusé — le rôle « {session.role} » n'a pas accès à cette page.
      </EmptyState>
    );
  } else {
    switch (page) {
      case '':            content = <DashboardPage />; break;
      case 'submit':      content = <SubmitPage />; break;
      case 'myfiles':     content = <MyFilesPage session={session} />; break;
      case 'profile':     content = <ProfilePage session={session} />; break;
      case 'pipeline':    content = <PipelineLivePage txId={route.param} session={session} />; break;
      case 'audit':       content = <AuditPage />; break;
      case 'supervisor':  content = <SupervisorPage session={session} />; break;
      case 'reports':     content = <ReportsPage />; break;
      case 'rules':       content = <RulesPage />; break;
      case 'graph':       content = <GraphPage />; break;
      case 'regulations': content = <RegulationsPage />; break;
      case 'simulator':   content = <SimulatorPage />; break;
      case 'dataset':     content = <DatasetPage />; break;
      default:            content = <EmptyState>Page introuvable.</EmptyState>;
    }
  }

  return (
    <Layout session={session} onLogout={() => setSession(null)}>
      {content}
    </Layout>
  );
}
