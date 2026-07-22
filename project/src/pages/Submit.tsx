import { useEffect, useMemo, useState } from 'react';
import { Search, Send, UserCheck, UserX, AlertTriangle, ShieldAlert,
  Landmark, Globe, Banknote } from 'lucide-react';
import { api } from '../api/client';
import type { TransactionIn, TxType, ClientProfile } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, KV, Spinner } from '../components/ui';
import { navigate } from '../router';

// Seuils STR (miroir de level1_rules/threshold_rules.py — affichage indicatif,
// la décision réelle reste côté backend)
const STR_THRESHOLDS: Record<string, { amount: number; currency: string }> = {
  SWIFT: { amount: 10_000, currency: 'USD' },
  VIREMENT_LOCAL: { amount: 10_000, currency: 'TND' },
  MANDAT: { amount: 1_000, currency: 'TND' },
  DEPOT_ESPECES: { amount: 5_000, currency: 'TND' },
};

// ── Règles RIB/IBAN par pays (validation dynamique) ──────────────────────
//  digits = RIB numérique national ; iban = préfixe pays + longueur totale.
const BANK_ID_RULES: Record<string, {
  hint: string; len: number; kind: 'digits' | 'iban'; prefix?: string;
}> = {
  tunisia: { hint: 'RIB tunisien : 20 chiffres', len: 20, kind: 'digits' },
  france: { hint: 'IBAN français : FR + 25 → 27 caractères', len: 27, kind: 'iban', prefix: 'FR' },
  germany: { hint: 'IBAN allemand : DE + 20 → 22 caractères', len: 22, kind: 'iban', prefix: 'DE' },
  italy: { hint: 'IBAN italien : IT + 25 → 27 caractères', len: 27, kind: 'iban', prefix: 'IT' },
  spain: { hint: 'IBAN espagnol : ES + 22 → 24 caractères', len: 24, kind: 'iban', prefix: 'ES' },
  morocco: { hint: 'RIB marocain : 24 chiffres', len: 24, kind: 'digits' },
  algeria: { hint: 'RIB algérien : 20 chiffres', len: 20, kind: 'digits' },
  turkey: { hint: 'IBAN turc : TR + 24 → 26 caractères', len: 26, kind: 'iban', prefix: 'TR' },
};

/** Valide un RIB/IBAN selon le pays. null = pas de règle connue (accepté). */
function checkBankId(country: string, value: string):
    { ok: boolean; msg: string } | null {
  const rule = BANK_ID_RULES[country.trim().toLowerCase()];
  const v = value.replace(/\s+/g, '').toUpperCase();
  if (!rule || !v) return null;
  if (rule.kind === 'digits') {
    if (!/^\d+$/.test(v)) return { ok: false, msg: `${rule.hint} — chiffres uniquement` };
    if (v.length !== rule.len)
      return { ok: false, msg: `${rule.hint} (saisi : ${v.length}/${rule.len})` };
  } else {
    if (rule.prefix && !v.startsWith(rule.prefix))
      return { ok: false, msg: `${rule.hint} — doit commencer par ${rule.prefix}` };
    if (v.length !== rule.len)
      return { ok: false, msg: `${rule.hint} (saisi : ${v.length}/${rule.len})` };
    if (!/^[A-Z]{2}[0-9A-Z]+$/.test(v))
      return { ok: false, msg: `${rule.hint} — format invalide` };
  }
  return { ok: true, msg: `✓ ${rule.hint}` };
}

const TX_TYPES: {
  value: TxType; label: string; desc: string; currency: string;
  Icon: typeof Landmark; tag: string;
}[] = [
  { value: 'VIREMENT_LOCAL', label: 'Virement local',  desc: 'Transfert domestique (TND)', currency: 'TND', Icon: Landmark, tag: 'BCT' },
  { value: 'SWIFT',          label: 'SWIFT',           desc: 'Virement international',     currency: 'USD', Icon: Globe,    tag: 'FATF R16' },
  { value: 'MANDAT',         label: 'Mandat',          desc: 'Western Union, MoneyGram…',  currency: 'TND', Icon: Send,     tag: 'MVTS' },
  { value: 'DEPOT_ESPECES',  label: 'Dépôt espèces',   desc: 'Dépôt cash en agence',       currency: 'TND', Icon: Banknote, tag: 'BCT 2006-19' },
];

const input = 'w-full rounded-xl border border-slate-200 bg-slate-50/70 px-3.5 py-2.5 text-sm text-slate-800 transition-colors focus:border-indigo-400 focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-100';
const label = 'mb-1.5 block text-[11px] font-semibold uppercase tracking-wide text-slate-500';

export function SubmitPage() {
  const [txType, setTxType] = useState<TxType>('VIREMENT_LOCAL');
  const [form, setForm] = useState({
    amount: '', currency: 'TND', client_id: '',
    sender_country: 'Tunisia', beneficiary_name: '',
    destination_country: 'Tunisia', motive: '', has_justification: true,
    sender_rib: '', beneficiary_iban: '',
  });
  const [files, setFiles] = useState<File[]>([]);   // justificatifs joints
  const [client, setClient] = useState<ClientProfile | null>(null);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [fatf, setFatf] = useState<{ black: string[]; grey: string[] }>({ black: [], grey: [] });
  const [ribAuto, setRibAuto] = useState(false);   // RIB pré-rempli depuis le profil

  useEffect(() => {
    api.fatfLists()
      .then(r => setFatf({
        black: Object.values(r.black_names).map(n => n.toLowerCase()),
        grey: Object.values(r.grey_names).map(n => n.toLowerCase()),
      }))
      .catch(() => { /* alertes indisponibles — le backend décide de toute façon */ });

    // ── PRÉ-REMPLISSAGE : chaque utilisateur a UN SEUL RIB émetteur ──
    //  Le RIB (et le compte émetteur) viennent de « Mon profil » — plus de
    //  saisie manuelle, plus d'erreur possible sur son propre compte.
    api.getProfile()
      .then(r => {
        if (r.complete && r.profile.rib) {
          setForm(f => ({
            ...f,
            sender_rib: r.profile.rib,
            client_id: f.client_id || r.profile.rib,   // RIB = identifiant de compte
          }));
          setRibAuto(true);
        }
      })
      .catch(() => { /* non connecté / profil absent → saisie manuelle */ });
  }, []);

  // ── Alertes métier EN DIRECT (avant soumission) ──────────────────────
  const alerts = useMemo(() => {
    const out: { tone: 'red' | 'amber'; text: string }[] = [];
    const dest = form.destination_country.trim().toLowerCase();
    const send = form.sender_country.trim().toLowerCase();
    const inList = (list: string[], c: string) =>
      !!c && list.some(n => n.includes(c) || c.includes(n));
    if (inList(fatf.black, dest) || inList(fatf.black, send)) {
      out.push({ tone: 'red',
        text: 'Pays sur la LISTE NOIRE FATF — contre-mesures obligatoires : l\'opération sera BLOQUÉE par le Level 1.' });
    } else if (inList(fatf.grey, dest) || inList(fatf.grey, send)) {
      out.push({ tone: 'amber',
        text: 'Pays sur la liste grise FATF (surveillance renforcée) — une diligence renforcée (EDD) sera appliquée.' });
    }
    const thr = STR_THRESHOLDS[txType];
    const amount = parseFloat(form.amount);
    if (thr && amount > 0 && form.currency === thr.currency && amount > thr.amount) {
      out.push({ tone: 'amber',
        text: `Seuil ${txType === 'DEPOT_ESPECES' ? 'de déclaration' : 'STR'} dépassé (${thr.amount.toLocaleString('fr-FR')} ${thr.currency}) — ${form.has_justification ? 'le justificatif fourni sera vérifié' : 'sans justificatif, une déclaration de soupçon (STR) sera obligatoire'}.` });
    } else if (thr && amount > 0 && form.currency === thr.currency
               && amount >= thr.amount * 0.85 && amount < thr.amount) {
      out.push({ tone: 'amber',
        text: `Montant juste sous le seuil (${thr.amount.toLocaleString('fr-FR')} ${thr.currency}) — le système recherchera un contournement (structuring).` });
    }
    return out;
  }, [form.amount, form.currency, form.destination_country, form.sender_country,
      form.has_justification, txType, fatf]);

  const set = (k: string, v: unknown) => setForm(f => ({ ...f, [k]: v }));

  const pickType = (t: typeof TX_TYPES[number]) => {
    setTxType(t.value);
    set('currency', t.currency);
  };

  const checkClient = async () => {
    if (!form.client_id.trim()) return;
    setChecking(true);
    try {
      setClient(await api.client(form.client_id.trim()));
    } catch {
      setClient(null);
      setError('Client introuvable côté API');
    } finally {
      setChecking(false);
    }
  };

  // Validation RIB/IBAN en direct (selon le pays sélectionné)
  const ribCheck = checkBankId(form.sender_country, form.sender_rib);
  const ibanCheck = checkBankId(form.destination_country, form.beneficiary_iban);

  const submit = async () => {
    setError('');
    const amount = parseFloat(form.amount);
    if (!amount || amount <= 0) { setError('Montant invalide'); return; }
    if (ribCheck && !ribCheck.ok) {
      setError(`RIB émetteur invalide — ${ribCheck.msg}`); return;
    }
    if (ibanCheck && !ibanCheck.ok) {
      setError(`Coordonnées bénéficiaire invalides — ${ibanCheck.msg}`); return;
    }
    setSubmitting(true);
    try {
      // Les fichiers joints partent en base64 → analysés en étape 0 du pipeline
      const justificatifs = await Promise.all(files.map(f =>
        new Promise<{ filename: string; content_b64: string }>((res, rej) => {
          const r = new FileReader();
          r.onload = () => res({ filename: f.name, content_b64: String(r.result) });
          r.onerror = () => rej(new Error(`Lecture impossible : ${f.name}`));
          r.readAsDataURL(f);
        })));
      const tx: TransactionIn = {
        tx_type: txType,
        amount,
        currency: form.currency,
        sender_name: form.client_id ? `Account-${form.client_id}` : 'Client-Inconnu',
        sender_country: form.sender_country,
        beneficiary_name: form.beneficiary_name || 'Bénéficiaire',
        destination_country: form.destination_country,
        motive: form.motive,
        has_justification: form.has_justification,
        client_id: form.client_id || null,
        sender_rib: form.sender_rib.replace(/\s+/g, ''),
        beneficiary_iban: form.beneficiary_iban.replace(/\s+/g, ''),
        justificatifs: justificatifs.length ? justificatifs : undefined,
      };
      const { tx_id } = await api.analyze(tx);
      navigate(`pipeline/${tx_id}`);
    } catch (e) {
      setError(`Échec de l'analyse : ${e}`);
      setSubmitting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Nouvelle transaction"
        subtitle="La transaction traversera les 8 étapes du pipeline (Level 1 → SAR → Workflow)"
      />

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ── Formulaire ─────────────────────────────────────────────── */}
        <div className="space-y-5 lg:col-span-2">
          <Card title="1 · Type d'opération">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {TX_TYPES.map(t => {
                const on = txType === t.value;
                return (
                  <button key={t.value} onClick={() => pickType(t)}
                    className={`group rounded-xl border p-3 text-left transition-all duration-150 hover:-translate-y-0.5 ${
                      on
                        ? 'border-indigo-500 bg-indigo-50 ring-2 ring-indigo-500/20'
                        : 'border-slate-200 bg-white hover:border-indigo-300 hover:shadow-sm'
                    }`}>
                    <div className="mb-2 flex items-center justify-between">
                      <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${
                        on ? 'bg-indigo-500 text-white' : 'bg-slate-100 text-slate-500 group-hover:bg-indigo-100 group-hover:text-indigo-600'}`}>
                        <t.Icon size={16} strokeWidth={2} />
                      </span>
                      <span className={`rounded-full px-1.5 py-0.5 text-[9px] font-bold ${
                        on ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-400'}`}>
                        {t.tag}
                      </span>
                    </div>
                    <p className={`text-sm font-semibold ${on ? 'text-indigo-900' : 'text-slate-800'}`}>{t.label}</p>
                    <p className={`mt-0.5 text-[11px] ${on ? 'text-indigo-500' : 'text-slate-500'}`}>
                      {t.desc}
                    </p>
                  </button>
                );
              })}
            </div>
          </Card>

          <Card title="2 · Client émetteur">
            <div className="flex gap-2">
              <input className={input} placeholder="ex : 802559360"
                value={form.client_id}
                onChange={e => { set('client_id', e.target.value); setClient(null); }} />
              <button onClick={checkClient} disabled={checking || !form.client_id.trim()}
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-xs font-semibold text-white disabled:opacity-40">
                <Search size={14} /> Vérifier
              </button>
            </div>
            {checking && <div className="mt-3"><Spinner label="Recherche du profil…" /></div>}
            {ribAuto && (
              <p className="mt-2 text-[11px] font-medium text-emerald-600">
                ✓ RIB pré-rempli depuis votre profil
              </p>
            )}
          </Card>

          <Card title="3 · Détails de l'opération">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className={label}>Montant</label>
                <input className={input} type="number" min="0" step="0.01"
                  value={form.amount} onChange={e => set('amount', e.target.value)} />
              </div>
              <div>
                <label className={label}>Devise</label>
                <select className={input} value={form.currency}
                  onChange={e => set('currency', e.target.value)}>
                  {['TND', 'USD', 'EUR', 'GBP'].map(c => <option key={c}>{c}</option>)}
                </select>
              </div>
              <div>
                <label className={label}>Pays émetteur</label>
                <input className={input} value={form.sender_country}
                  onChange={e => set('sender_country', e.target.value)} />
              </div>
              <div>
                <label className={label}>Pays destination</label>
                <input className={input} value={form.destination_country}
                  onChange={e => set('destination_country', e.target.value)} />
              </div>
              <div>
                <label className={label}>Bénéficiaire</label>
                <input className={input} placeholder="Nom ou Account-XXXX"
                  value={form.beneficiary_name}
                  onChange={e => set('beneficiary_name', e.target.value)} />
              </div>
              <div>
                <label className={label}>Motif</label>
                <input className={input} placeholder="ex : Paiement facture"
                  value={form.motive} onChange={e => set('motive', e.target.value)} />
              </div>
              <div>
                <label className={label}>
                  RIB émetteur
                  {BANK_ID_RULES[form.sender_country.trim().toLowerCase()] && (
                    <span className="ml-1 font-normal text-slate-400">
                      ({BANK_ID_RULES[form.sender_country.trim().toLowerCase()].hint})
                    </span>
                  )}
                </label>
                <input className={`${input} ${ribCheck && !ribCheck.ok ? 'border-red-400' : ribCheck?.ok ? 'border-emerald-400' : ''}`}
                  placeholder="ex : 08 006 0123456789012 34"
                  value={form.sender_rib}
                  onChange={e => set('sender_rib', e.target.value)} />
                {ribCheck && (
                  <p className={`mt-0.5 text-[10px] ${ribCheck.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                    {ribCheck.msg}
                  </p>
                )}
              </div>
              <div>
                <label className={label}>
                  RIB / IBAN bénéficiaire
                  {BANK_ID_RULES[form.destination_country.trim().toLowerCase()] && (
                    <span className="ml-1 font-normal text-slate-400">
                      ({BANK_ID_RULES[form.destination_country.trim().toLowerCase()].hint})
                    </span>
                  )}
                </label>
                <input className={`${input} ${ibanCheck && !ibanCheck.ok ? 'border-red-400' : ibanCheck?.ok ? 'border-emerald-400' : ''}`}
                  placeholder="s'adapte au pays de destination"
                  value={form.beneficiary_iban}
                  onChange={e => set('beneficiary_iban', e.target.value)} />
                {ibanCheck && (
                  <p className={`mt-0.5 text-[10px] ${ibanCheck.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                    {ibanCheck.msg}
                  </p>
                )}
              </div>
            </div>

            {/* ── Justificatif : joint À LA SOUMISSION → analysé en étape 0
                   du pipeline ; son verdict remplace la case déclarative ── */}
            <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50/60 p-3">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={form.has_justification}
                  onChange={e => set('has_justification', e.target.checked)}
                  className="h-4 w-4 rounded border-slate-300" />
                Justificatif économique fourni (facture, contrat…)
              </label>
              {form.has_justification && (
                <div className="mt-2 pl-6">
                  <input type="file" accept=".pdf,.txt,.md,.docx" multiple
                    onChange={e => setFiles(Array.from(e.target.files ?? []))}
                    className="block text-xs text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-900 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-white hover:file:bg-slate-700" />
                  {files.length > 0 ? (
                    <p className="mt-1 text-[11px] font-medium text-emerald-600">
                      📎 {files.map(f => f.name).join(' · ')} — vérifié à l'analyse
                    </p>
                  ) : (
                    <p className="mt-1 text-[11px] text-amber-600">
                      Aucun fichier joint
                    </p>
                  )}
                </div>
              )}
            </div>
          </Card>

          {/* ── Alertes métier en direct (le backend reste décisionnaire) ── */}
          {alerts.map((a, i) => (
            <div key={i} className={`flex items-start gap-2.5 rounded-r-xl border-l-4 py-2.5 pl-3 pr-4 text-sm shadow-sm ${
              a.tone === 'red'
                ? 'border-red-500 bg-red-50 font-semibold text-red-800'
                : 'border-amber-500 bg-amber-50 text-amber-800'}`}>
              {a.tone === 'red'
                ? <ShieldAlert size={17} className="mt-0.5 shrink-0 animate-pulse" />
                : <AlertTriangle size={17} className="mt-0.5 shrink-0" />}
              <span>{a.text}</span>
            </div>
          ))}

          {error && (
            <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
              {error}
            </p>
          )}

          <button onClick={submit} disabled={submitting}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 py-3 text-sm font-bold text-white shadow-sm transition-opacity hover:bg-emerald-700 disabled:opacity-50">
            <Send size={16} />
            {submitting ? 'Lancement du pipeline…' : 'Analyser la transaction'}
          </button>
        </div>

        {/* ── Profil client ──────────────────────────────────────────── */}
        <div>
          <Card title="Profil KYC du client">
            {!client ? (
              <p className="text-sm text-slate-400">
                Saisis un identifiant puis « Vérifier » pour afficher le profil.
              </p>
            ) : (
              <>
                <div className={`mb-3 flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold ${
                  client.known ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'
                }`}>
                  {client.known ? <UserCheck size={16} /> : <UserX size={16} />}
                  {client.known ? 'Client connu' : 'Client inconnu → profil par défaut'}
                </div>
                <KV k="Nationalité" v={client.nationality || '—'} />
                <KV k="Résidence" v={client.country_residence || '—'} />
                <KV k="PEP" v={client.is_pep ? `OUI (${client.pep_type ?? '?'})` : 'non'} />
                <KV k="KYC complet" v={client.kyc_complete ? 'OUI' : 'NON'} />
                <KV k="STR antérieurs" v={client.previous_strs} />
                <KV k="Type de compte" v={client.account_type || '—'} />
                <div className="mt-3 border-t border-slate-100 pt-3">
                  <p className="mb-1 text-xs font-semibold text-slate-500">
                    Historique comportemental (30 j)
                  </p>
                  <KV k="Transactions" v={String(client.history?.transaction_count_30d ?? 0)} />
                  <KV k="Volume (TND)" v={Number(client.history?.total_volume_30d ?? 0).toLocaleString('fr-FR')} />
                  <KV k="Vélocité 24h" v={String(client.history?.velocity_24h ?? 0)} />
                </div>
              </>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
