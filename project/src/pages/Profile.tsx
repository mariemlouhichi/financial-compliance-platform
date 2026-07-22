// ============================================================================
// ESPACE UTILISATEUR — « Mon profil » : dossier KYC en 5 SECTIONS.
//   1 · Informations personnelles   2 · Coordonnées de contact
//   3 · Relation bancaire           4 · Situation professionnelle & état civil
//   5 · Déclarations réglementaires
// Chaque champ marqué <Factor> alimente DIRECTEMENT le RiskFactorScorer
// (client_scoring_agent.py). Profil ENTREPRISE : les champs « personne
// physique » (naissance, état civil, employeur) sont masqués et les libellés
// adaptés (raison sociale, pays d'immatriculation, siège social).
// ============================================================================
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  UserCircle, Save, CheckCircle2, Info, Phone, Landmark,
  Briefcase, ShieldCheck, Contact,
} from 'lucide-react';
import { api } from '../api/client';
import type { KycProfile } from '../types/api';
import type { Session } from '../auth';
import { PageHeader } from '../components/Layout';
import { Card, Spinner } from '../components/ui';

const EMPTY: KycProfile = {
  full_name: '', nationality: '', country_of_residence: '',
  business_type: '', account_type: 'INDIVIDUAL', account_opened: '', rib: '',
  address: '', phone: '', marital_status: '', employer: '',
  previous_strs: 0, beneficial_owner_identified: true,
  is_pep: false, pep_type: '', fatca_us_person: false,
  foreign_tax_residence: '', birth_date: '',
};

// Pays gérés par le Level 1 / le RiskCountryChecker (liste noire/grise FATF).
const COUNTRIES = [
  'Tunisia', 'France', 'Germany', 'Italy', 'Spain', 'Morocco', 'Algeria',
  'United States', 'Turkey', 'United Arab Emirates', 'Nigeria', 'Congo',
  'Iran', 'Syria', 'North Korea', 'Other',
];

const PROFESSIONS: { v: string; label: string; risky?: boolean }[] = [
  { v: 'salary_account', label: 'Salarié (compte de paie)' },
  { v: 'retail', label: 'Commerce de détail' },
  { v: 'consulting', label: 'Conseil / Services' },
  { v: 'manufacturing', label: 'Industrie / Fabrication' },
  { v: 'wholesale', label: 'Commerce de gros' },
  { v: 'agriculture', label: 'Agriculture' },
  { v: 'import_export', label: 'Import / Export', risky: true },
  { v: 'real_estate', label: 'Immobilier', risky: true },
  { v: 'money_exchange', label: 'Bureau de change', risky: true },
  { v: 'precious_metals', label: 'Métaux précieux', risky: true },
  { v: 'art_dealer', label: "Marchand d'art / antiquités", risky: true },
  { v: 'crypto', label: 'Crypto-actifs', risky: true },
  { v: 'casino', label: 'Jeux / Casino', risky: true },
  { v: 'other', label: 'Autre' },
];

const ACCOUNT_TYPES: { v: string; label: string; hint: string }[] = [
  { v: 'INDIVIDUAL', label: 'Particulier', hint: 'personne physique' },
  { v: 'BUSINESS', label: 'Entreprise', hint: 'bénéficiaire effectif requis (R24)' },
  { v: 'CORRESPONDENT', label: 'Correspondant bancaire', hint: 'risque R13 (+15)' },
  { v: 'TRUST', label: 'Fiducie / Trust', hint: 'structure opaque (+10)' },
];

const MARITAL: { v: string; label: string }[] = [
  { v: 'single', label: 'Célibataire' },
  { v: 'married', label: 'Marié(e)' },
  { v: 'divorced', label: 'Divorcé(e)' },
  { v: 'widowed', label: 'Veuf / Veuve' },
];

/** Étiquette « quel facteur de scoring ce champ pilote ». */
function Factor({ children }: { children: ReactNode }) {
  return (
    <span className="ml-1 inline-flex items-center gap-1 rounded bg-indigo-50 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-indigo-500">
      <Info size={9} /> {children}
    </span>
  );
}

/** En-tête de section numérotée (design unifié des 5 blocs). */
function SectionTitle({ n, Icon, children }: {
  n: number; Icon: typeof UserCircle; children: ReactNode;
}) {
  return (
    <span className="flex items-center gap-2.5">
      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-[11px] font-bold text-white">
        {n}
      </span>
      <Icon size={15} className="text-slate-400" />
      {children}
    </span>
  );
}

export function ProfilePage({ session }: { session: Session }) {
  const [form, setForm] = useState<KycProfile>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [complete, setComplete] = useState(false);
  const [flash, setFlash] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    api.getProfile()
      .then(r => {
        setComplete(r.complete);
        if (r.complete) {
          setForm({ ...EMPTY, ...r.profile });
        } else {
          setForm({ ...EMPTY, full_name: session.name ?? '' });
        }
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, [session.name]);

  const set = <K extends keyof KycProfile>(k: K, v: KycProfile[K]) =>
    setForm(f => ({ ...f, [k]: v }));

  const isBusiness = ['BUSINESS', 'CORRESPONDENT', 'TRUST'].includes(form.account_type);

  // Taux de complétion du dossier (jauge de l'en-tête)
  const completion = useMemo(() => {
    const fields = isBusiness
      ? [form.full_name, form.nationality, form.country_of_residence,
         form.address, form.phone, form.business_type, form.rib, form.account_opened]
      : [form.full_name, form.nationality, form.country_of_residence,
         form.address, form.phone, form.business_type, form.rib,
         form.account_opened, form.birth_date, form.marital_status];
    const filled = fields.filter(v => String(v ?? '').trim() !== '').length;
    return Math.round((filled / fields.length) * 100);
  }, [form, isBusiness]);

  const save = async () => {
    setSaving(true); setError(''); setFlash('');
    try {
      await api.updateProfile(form);
      setComplete(true);
      setFlash('✅ Profil enregistré — vos coordonnées alimentent le scoring client de vos prochaines transactions.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <Spinner label="Chargement de votre profil…" />;

  const inputCls =
    'w-full rounded-xl border border-slate-200 bg-slate-50/70 px-3.5 py-2.5 text-sm text-slate-800 transition-colors focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100';
  const labelCls = 'mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500';
  const initials = (form.full_name || session.username)
    .split(/[\s@.]+/).filter(Boolean).slice(0, 2)
    .map(w => w[0]?.toUpperCase() ?? '').join('');

  return (
    <>
      <PageHeader
        title="Mon profil"
        subtitle="Dossier KYC — vos informations alimentent le scoring client"
      />

      {flash && (
        <p className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">
          {flash}
        </p>
      )}
      {error && (
        <p className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
          {error}
        </p>
      )}

      {/* ── EN-TÊTE : avatar + identité + complétion ─────────────────── */}
      <div className="mb-6 flex flex-wrap items-center gap-5 rounded-2xl bg-slate-900 px-6 py-5 text-white shadow-sm">
        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-emerald-500 text-xl font-bold">
          {initials || '?'}
        </div>
        <div className="min-w-0">
          <p className="truncate text-lg font-bold">
            {form.full_name || session.name || session.username}
          </p>
          <p className="truncate text-xs text-slate-400">{session.username}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-300">
              {isBusiness ? '🏢 Entreprise' : '👤 Personne physique'}
            </span>
            {complete ? (
              <span className="flex items-center gap-1 rounded-full bg-emerald-500/20 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
                <CheckCircle2 size={11} /> KYC renseigné
              </span>
            ) : (
              <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-semibold text-amber-300">
                ⚠️ Profil incomplet — analysé avec un profil par défaut
              </span>
            )}
          </div>
        </div>
        <div className="ml-auto w-48">
          <div className="mb-1 flex items-baseline justify-between text-xs">
            <span className="text-slate-400">Complétion du dossier</span>
            <span className="font-bold tabular-nums">{completion}%</span>
          </div>
          <div className="h-2 w-full rounded-full bg-slate-700">
            <div className={`h-2 rounded-full transition-all duration-500 ${
              completion >= 80 ? 'bg-emerald-400' : completion >= 40 ? 'bg-amber-400' : 'bg-red-400'}`}
              style={{ width: `${completion}%` }} />
          </div>
        </div>
      </div>

      {/* ── LES 5 SECTIONS (grille large 2 colonnes) ─────────────────── */}
      <div className="grid gap-6 xl:grid-cols-2">

        {/* 1 · INFORMATIONS PERSONNELLES */}
        <Card title={<SectionTitle n={1} Icon={Contact}>
          {isBusiness ? 'Identité de l’entreprise' : 'Informations personnelles'}
        </SectionTitle>}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className={labelCls}>{isBusiness ? 'Raison sociale' : 'Nom et prénom'}</label>
              <input value={form.full_name} onChange={e => set('full_name', e.target.value)}
                placeholder={isBusiness ? 'ex : Société Textile Ben Ali SARL' : 'Prénom Nom'}
                className={inputCls} />
            </div>
            <div className="sm:col-span-2">
              <label className={labelCls}>Identifiant client</label>
              <input value={session.username} readOnly
                className={`${inputCls} cursor-not-allowed text-slate-500`} />
            </div>
            {!isBusiness && (
              <div>
                <label className={labelCls}>Date de naissance</label>
                <input type="date" value={form.birth_date ?? ''}
                  onChange={e => set('birth_date', e.target.value)} className={inputCls} />
              </div>
            )}
            <div className={isBusiness ? 'sm:col-span-2' : ''}>
              <label className={labelCls}>Adresse {isBusiness && 'du siège'}</label>
              <input value={form.address ?? ''} onChange={e => set('address', e.target.value)}
                placeholder="ex : 12 avenue Habib Bourguiba, Tunis" className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>
                {isBusiness ? 'Pays d’immatriculation' : 'Nationalité'} <Factor>géographie</Factor>
              </label>
              <select value={form.nationality} onChange={e => set('nationality', e.target.value)}
                className={inputCls}>
                <option value="">— choisir —</option>
                {COUNTRIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className={labelCls}>
                {isBusiness ? 'Pays du siège social' : 'Pays de résidence'} <Factor>géographie</Factor>
              </label>
              <select value={form.country_of_residence}
                onChange={e => set('country_of_residence', e.target.value)}
                className={inputCls}>
                <option value="">— choisir —</option>
                {COUNTRIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
        </Card>

        {/* 2 · COORDONNÉES DE CONTACT */}
        <Card title={<SectionTitle n={2} Icon={Phone}>Coordonnées de contact</SectionTitle>}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className={labelCls}>Email connecté</label>
              <input value={session.username} readOnly
                className={`${inputCls} cursor-not-allowed text-slate-500`} />
            </div>
            <div>
              <label className={labelCls}>Numéro de téléphone</label>
              <input value={form.phone ?? ''} onChange={e => set('phone', e.target.value)}
                placeholder="ex : +216 22 345 678" className={inputCls} />
            </div>
          </div>
        </Card>

        {/* 3 · RELATION BANCAIRE */}
        <Card title={<SectionTitle n={3} Icon={Landmark}>Relation bancaire</SectionTitle>}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label className={labelCls}>RIB du compte émetteur (20 chiffres)</label>
              <input value={form.rib}
                onChange={e => set('rib', e.target.value)}
                placeholder="ex : 08 006 0123456789012 34"
                className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>
                Type de compte <Factor>BO / R13</Factor>
              </label>
              <select value={form.account_type}
                onChange={e => set('account_type', e.target.value)}
                className={inputCls}>
                {ACCOUNT_TYPES.map(a => (
                  <option key={a.v} value={a.v}>{a.label} — {a.hint}</option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelCls}>
                Date d'ouverture du compte <Factor>ancienneté</Factor>
              </label>
              <input type="date" value={form.account_opened}
                onChange={e => set('account_opened', e.target.value)} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>
                Déclarations de soupçon antérieures <Factor>prior_strs</Factor>
              </label>
              <input type="number" min={0} value={form.previous_strs}
                onChange={e => set('previous_strs', Math.max(0, Number(e.target.value) || 0))}
                className={inputCls} />
            </div>
            {isBusiness && (
              <label className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 sm:col-span-2">
                <input type="checkbox" checked={form.beneficial_owner_identified}
                  onChange={e => set('beneficial_owner_identified', e.target.checked)}
                  className="mt-0.5" />
                <span className="text-sm text-slate-700">
                  <b>Bénéficiaire effectif identifié</b>
                  <Factor>R24</Factor>
                </span>
              </label>
            )}
          </div>
        </Card>

        {/* 4 · SITUATION PROFESSIONNELLE & ÉTAT CIVIL */}
        <Card title={<SectionTitle n={4} Icon={Briefcase}>
          {isBusiness ? 'Activité de l’entreprise' : 'Situation professionnelle & état civil'}
        </SectionTitle>}>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className={isBusiness ? 'sm:col-span-2' : ''}>
              <label className={labelCls}>
                {isBusiness ? 'Secteur d’activité' : 'Profession / activité'} <Factor>profession</Factor>
              </label>
              <select value={form.business_type}
                onChange={e => set('business_type', e.target.value)}
                className={inputCls}>
                <option value="">— choisir —</option>
                {PROFESSIONS.map(p => (
                  <option key={p.v} value={p.v}>
                    {p.label}{p.risky ? '  ⚠️ à risque' : ''}
                  </option>
                ))}
              </select>
            </div>
            {!isBusiness && (
              <>
                <div>
                  <label className={labelCls}>Employeur / organisme</label>
                  <input value={form.employer ?? ''} onChange={e => set('employer', e.target.value)}
                    placeholder="ex : STEG, Ministère, indépendant…" className={inputCls} />
                </div>
                <div>
                  <label className={labelCls}>État civil</label>
                  <select value={form.marital_status ?? ''}
                    onChange={e => set('marital_status', e.target.value)}
                    className={inputCls}>
                    <option value="">— choisir —</option>
                    {MARITAL.map(m => <option key={m.v} value={m.v}>{m.label}</option>)}
                  </select>
                </div>
              </>
            )}
          </div>
        </Card>

        {/* 5 · DÉCLARATIONS RÉGLEMENTAIRES (pleine largeur) */}
        <div className="xl:col-span-2">
          <Card title={<SectionTitle n={5} Icon={ShieldCheck}>Déclarations réglementaires</SectionTitle>}>
            <div className="grid gap-4 lg:grid-cols-3">
              <div>
                <label className="flex h-full items-start gap-3 rounded-lg border border-slate-200 p-3">
                  <input type="checkbox" checked={form.is_pep}
                    onChange={e => set('is_pep', e.target.checked)} className="mt-0.5" />
                  <span className="text-sm text-slate-700">
                    <b>Personne politiquement exposée (PEP)</b>
                    <Factor>R12</Factor>
                    <span className="block text-xs text-slate-400">
                      {isBusiness
                        ? 'Dirigeant ou bénéficiaire effectif exerçant une fonction publique.'
                        : 'Fonction publique importante (vous ou un proche).'}
                    </span>
                    {form.is_pep && (
                      <span className="mt-2 flex gap-2">
                        {(['domestic', 'foreign'] as const).map(t => (
                          <button key={t} type="button" onClick={() => set('pep_type', t)}
                            className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${
                              form.pep_type === t
                                ? 'border-slate-900 bg-slate-900 text-white'
                                : 'border-slate-300 text-slate-600 hover:border-slate-400'}`}>
                            {t === 'domestic' ? 'Nationale (+20)' : 'Étrangère (+35)'}
                          </button>
                        ))}
                      </span>
                    )}
                  </span>
                </label>
              </div>
              <div>
                <label className="flex h-full items-start gap-3 rounded-lg border border-slate-200 p-3">
                  <input type="checkbox" checked={form.fatca_us_person}
                    onChange={e => set('fatca_us_person', e.target.checked)} className="mt-0.5" />
                  <span className="text-sm text-slate-700">
                    <b>« US person » (FATCA)</b>
                    <span className="block text-xs text-slate-400">
                      {isBusiness
                        ? 'Entité américaine ou détenue par des US persons.'
                        : 'Citoyen ou résident fiscal américain.'}
                    </span>
                  </span>
                </label>
              </div>
              <div className="rounded-lg border border-slate-200 p-3">
                <label className={labelCls}>
                  Résidence fiscale étrangère (hors Tunisie) <Factor>CRS</Factor>
                </label>
                <select value={form.foreign_tax_residence}
                  onChange={e => set('foreign_tax_residence', e.target.value)}
                  className={inputCls}>
                  <option value="">— aucune —</option>
                  {['FR', 'DE', 'GB', 'IT', 'ES', 'BE', 'NL', 'CH', 'LU',
                    'CA', 'AE', 'SA', 'QA', 'MA', 'DZ', 'EG', 'TR'].map(c => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </div>
            </div>
          </Card>
        </div>
      </div>

      {/* ── BARRE D'ENREGISTREMENT (collée en bas) ───────────────────── */}
      <div className="sticky bottom-4 z-10 mt-6 flex items-center gap-3 rounded-2xl border border-slate-200 bg-white/95 px-5 py-3 shadow-lg backdrop-blur">
        <button onClick={save} disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-slate-900 px-5 py-2.5 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-40">
          <Save size={15} /> {saving ? 'Enregistrement…' : 'Enregistrer mon profil'}
        </button>
        <span className="text-xs text-slate-500">
          Complétion : <b className="tabular-nums">{completion}%</b>
          {complete && <span className="ml-2 text-emerald-600">· KYC renseigné ✓</span>}
        </span>
      </div>
    </>
  );
}
