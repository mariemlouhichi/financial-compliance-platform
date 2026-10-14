// ============================================================================
// Authentification réelle (api/auth.py) — écrans dans une page :
//   login → connexion (email + mot de passe)
//   register → création de compte (rôle + email + mot de passe)
//   verify → saisie du code 6 chiffres reçu par email
//   forgot → mot de passe oublié : demande d'un code par email
//   reset → saisie du code + nouveau mot de passe
// Mode démo : sans SMTP configuré, le code s'affiche dans la CONSOLE du
// serveur backend (uvicorn) — indiqué à l'écran.
// ============================================================================
import { useState } from 'react';
import { Mail, LockKeyhole, UserPlus, LogIn, KeyRound } from 'lucide-react';
import { api } from '../api/client';
import { saveSession, type Role, ROLE_LABELS, type Session } from '../auth';

const ROLES: Role[] = ['USER', 'ADMIN', 'COMPLIANCE_OFFICER'];
type Mode = 'login' | 'register' | 'verify' | 'forgot' | 'reset';

export function LoginPage({ onLogin }: { onLogin: (s: Session) => void }) {
  const [mode, setMode] = useState<Mode>('login');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [role, setRole] = useState<Role>('USER');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(''); setInfo('');
    try { await fn(); } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  const doLogin = () => run(async () => {
    const r = await api.login({ email: email.trim(), password });
    onLogin(saveSession({ token: r.token, username: r.username, role: r.role, name: r.name }));
  });

  const doRegister = () => run(async () => {
    const r = await api.register({
      email: email.trim(), password, full_name: fullName.trim(), role,
    });
    setInfo(`${r.message} 💡 Mode démo : le code s'affiche dans la console du serveur backend.`);
    setMode('verify');
  });

  const doVerify = () => run(async () => {
    const r = await api.verifyEmail({ email: email.trim(), code: code.trim() });
    setInfo(`${r.message} Connecte-toi maintenant.`);
    setMode('login');
  });

  const doResend = () => run(async () => {
    const r = await api.resendCode(email.trim());
    setInfo(`${r.message}`);
  });

  const doForgot = () => run(async () => {
    const r = await api.forgotPassword(email.trim());
    setInfo(`${r.message} 💡 Mode démo : le code s'affiche dans la console du serveur backend.`);
    setMode('reset');
  });

  const doReset = () => run(async () => {
    const r = await api.resetPassword({
      email: email.trim(), code: code.trim(), new_password: newPassword,
    });
    setInfo(`${r.message}`);
    setPassword(''); setNewPassword(''); setCode('');
    setMode('login');
  });

  const inputCls =
    'w-full rounded-xl border border-slate-300 bg-slate-50/70 px-3.5 py-2.5 text-sm transition-colors focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100';
  const labelCls = 'mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500';

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#152c4d] px-4">
      <div className="relative w-full max-w-md rounded-2xl bg-white p-8 shadow-2xl">
        <div className="mb-6 flex flex-col items-center text-center">
          {/* La carte de connexion est blanche : le logo s'y intègre directement. */}
          <img src="/lexiq-logo.png" alt="LexIQ" className="mb-3 h-11 w-auto" />
          <p className="mt-1 text-xs text-slate-500">
            Plateforme multi-agents pour la surveillance intelligente
            des opérations financières
          </p>
          <p className="mt-2 text-[11px] font-semibold tracking-wide text-slate-500">
            VERMEG
          </p>
        </div>

        {/* Onglets connexion / création */}
        {(mode === 'login' || mode === 'register') && (
          <div className="mb-5 grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1">
            {(['login', 'register'] as Mode[]).map(m => (
              <button key={m} type="button" onClick={() => { setMode(m); setError(''); }}
                className={`flex items-center justify-center gap-1.5 rounded-lg py-2 text-xs font-semibold transition-colors ${
                  mode === m ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
                }`}>
                {m === 'login' ? <LogIn size={13} /> : <UserPlus size={13} />}
                {m === 'login' ? 'Se connecter' : 'Créer un compte'}
              </button>
            ))}
          </div>
        )}

        {error && (
          <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </p>
        )}
        {info && (
          <p className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
            {info}
          </p>
        )}

        {/* ── CONNEXION ─────────────────────────────────────────────── */}
        {mode === 'login' && (
          <form onSubmit={e => { e.preventDefault(); doLogin(); }} className="space-y-4">
            <div>
              <label className={labelCls}>Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="prenom.nom@email.com" className={inputCls} autoFocus />
            </div>
            <div>
              <label className={labelCls}>Mot de passe</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                placeholder="••••••••" className={inputCls} />
            </div>
            <button type="submit" disabled={busy || !email.includes('@') || !password}
              className="w-full rounded-lg bg-slate-900 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40">
              {busy ? 'Connexion…' : 'Se connecter'}
            </button>
            <div className="flex items-center justify-between">
              <button type="button" onClick={() => { setMode('forgot'); setError(''); setInfo(''); }}
                className="text-[11px] font-semibold text-indigo-600 hover:text-indigo-800">
                Mot de passe oublié ?
              </button>
              <button type="button" onClick={() => setMode('verify')}
                className="text-[11px] text-slate-400 hover:text-slate-600">
                Activer un compte
              </button>
            </div>
          </form>
        )}

        {/* ── MOT DE PASSE OUBLIÉ : demande du code ─────────────────── */}
        {mode === 'forgot' && (
          <form onSubmit={e => { e.preventDefault(); doForgot(); }} className="space-y-4">
            <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2.5">
              <KeyRound size={16} className="shrink-0 text-slate-400" />
              <p className="text-xs text-slate-600">
                Saisis l'email de ton compte : nous t'enverrons un code pour
                choisir un nouveau mot de passe.
              </p>
            </div>
            <div>
              <label className={labelCls}>Email du compte</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="prenom.nom@banque.tn" className={inputCls} autoFocus />
            </div>
            <button type="submit" disabled={busy || !email.includes('@')}
              className="w-full rounded-lg bg-slate-900 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40">
              {busy ? 'Envoi…' : 'Envoyer le code de réinitialisation'}
            </button>
            <button type="button" onClick={() => setMode('login')}
              className="w-full text-center text-[11px] text-slate-400 hover:text-slate-600">
              ← Retour à la connexion
            </button>
          </form>
        )}

        {/* ── MOT DE PASSE OUBLIÉ : code + nouveau mot de passe ─────── */}
        {mode === 'reset' && (
          <form onSubmit={e => { e.preventDefault(); doReset(); }} className="space-y-4">
            <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2.5">
              <Mail size={16} className="shrink-0 text-slate-400" />
              <p className="text-xs text-slate-600">
                Saisis le code reçu par email et choisis ton nouveau mot de passe.
              </p>
            </div>
            <div>
              <label className={labelCls}>Email du compte</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="prenom.nom@banque.tn" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Code de réinitialisation</label>
              <input value={code} onChange={e => setCode(e.target.value)}
                placeholder="000000" maxLength={6} inputMode="numeric" autoFocus
                className={`${inputCls} text-center font-mono text-lg tracking-[0.5em]`} />
            </div>
            <div>
              <label className={labelCls}>Nouveau mot de passe (6 caractères minimum)</label>
              <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)}
                placeholder="••••••••" className={inputCls} />
            </div>
            <button type="submit"
              disabled={busy || code.trim().length !== 6 || !email.includes('@') || newPassword.length < 6}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-slate-900 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40">
              <LockKeyhole size={14} /> {busy ? 'Réinitialisation…' : 'Réinitialiser le mot de passe'}
            </button>
            <div className="flex items-center justify-between">
              <button type="button" onClick={() => setMode('forgot')}
                className="text-[11px] text-slate-400 hover:text-slate-600">
                ← Renvoyer un code
              </button>
              <button type="button" onClick={() => setMode('login')}
                className="text-[11px] text-slate-400 hover:text-slate-600">
                Retour connexion
              </button>
            </div>
          </form>
        )}

        {/* ── CRÉATION DE COMPTE ────────────────────────────────────── */}
        {mode === 'register' && (
          <form onSubmit={e => { e.preventDefault(); doRegister(); }} className="space-y-4">
            <div>
              <label className={labelCls}>Nom complet</label>
              <input value={fullName} onChange={e => setFullName(e.target.value)}
                placeholder="ex : Mariem Louhichi" className={inputCls} autoFocus />
            </div>
            <div>
              <label className={labelCls}>Email (identifiant + code de confirmation)</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="prenom.nom@email.com" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Mot de passe (6 caractères minimum)</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                placeholder="••••••••" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Rôle</label>
              <div className="grid grid-cols-2 gap-2">
                {ROLES.map(r => (
                  <button type="button" key={r} onClick={() => setRole(r)}
                    className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${
                      role === r
                        ? 'border-slate-900 bg-slate-900 text-white'
                        : 'border-slate-300 text-slate-600 hover:border-slate-400'
                    }`}>
                    {ROLE_LABELS[r]}
                  </button>
                ))}
              </div>
            </div>
            <button type="submit"
              disabled={busy || fullName.trim().length < 2 || !email.includes('@') || password.length < 6}
              className="w-full rounded-lg bg-slate-900 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40">
              {busy ? 'Création…' : 'Créer le compte'}
            </button>
          </form>
        )}

        {/* ── CONFIRMATION EMAIL ────────────────────────────────────── */}
        {mode === 'verify' && (
          <form onSubmit={e => { e.preventDefault(); doVerify(); }} className="space-y-4">
            <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2.5">
              <Mail size={16} className="shrink-0 text-slate-400" />
              <p className="text-xs text-slate-600">
                Un code à 6 chiffres a été envoyé à l'email du compte.
                <span className="mt-0.5 block text-slate-400">
                  Sans SMTP configuré, il s'affiche dans la <b>console du serveur backend</b>.
                </span>
              </p>
            </div>
            <div>
              <label className={labelCls}>Email du compte</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="prenom.nom@banque.tn" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>Code de confirmation</label>
              <input value={code} onChange={e => setCode(e.target.value)}
                placeholder="000000" maxLength={6} inputMode="numeric" autoFocus
                className={`${inputCls} text-center font-mono text-lg tracking-[0.5em]`} />
            </div>
            <button type="submit" disabled={busy || code.trim().length !== 6 || !email.includes('@')}
              className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-slate-900 py-2.5 text-sm font-semibold text-white transition-opacity disabled:opacity-40">
              <LockKeyhole size={14} /> {busy ? 'Vérification…' : 'Activer mon compte'}
            </button>
            <div className="flex items-center justify-between">
              <button type="button" onClick={() => setMode('register')}
                className="text-[11px] text-slate-400 hover:text-slate-600">
                ← Création de compte
              </button>
              <button type="button" onClick={doResend}
                disabled={busy || !email.includes('@')}
                className="text-[11px] font-semibold text-indigo-600 hover:text-indigo-800 disabled:opacity-40">
                Code non reçu ? Renvoyer le code
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
