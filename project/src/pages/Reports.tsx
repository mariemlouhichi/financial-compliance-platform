import { useEffect, useState } from 'react';
import { Download, RefreshCw, Mail, FileText } from 'lucide-react';
import { api } from '../api/client';
import type { TxListItem } from '../types/api';
import { PageHeader } from '../components/Layout';
import { Card, DecisionBadge, RiskBadge, StateBadge, EmptyState, Spinner } from '../components/ui';
import { printMarkdown } from '../components/Markdown';
import { Link } from '../router';

export function ReportsPage() {
  const [txs, setTxs] = useState<TxListItem[] | null>(null);
  const [reportFlash, setReportFlash] = useState('');

  const load = async () => setTxs(await api.transactions());
  useEffect(() => { load(); }, []);

  const sendWeekly = async () => {
    setReportFlash('Envoi du rapport…');
    try {
      const r = await api.weeklyReport();
      setReportFlash(r.sent
        ? `✅ Rapport hebdomadaire envoyé à ${r.recipient} (${(r.stats as { total?: number }).total ?? 0} transactions sur 7 j).`
        : `Rapport généré (démo) — SMTP non configuré. Destinataire : ${r.recipient}.`);
    } catch (e) {
      setReportFlash(`Erreur : ${e instanceof Error ? e.message : String(e)}`);
    }
    setTimeout(() => setReportFlash(''), 8000);
  };

  const [sarBusy, setSarBusy] = useState('');

  // SAR → PDF structuré. La fenêtre est ouverte DANS le clic (geste utilisateur)
  // puis remplie après le fetch — sinon le navigateur bloque l'ouverture qui
  // suivrait le `await` (ce qui donnait une fenêtre vide).
  const openSarPdf = async (txId: string) => {
    const w = window.open('', '_blank', 'width=900,height=700');
    if (!w) {
      setReportFlash('Autorise les fenêtres pop-up pour ouvrir le rapport PDF.');
      setTimeout(() => setReportFlash(''), 6000);
      return;
    }
    w.document.write('<!doctype html><meta charset="utf-8">'
      + '<p style="font:14px sans-serif;color:#334155;padding:2rem">'
      + 'Génération du rapport SAR…</p>');
    setSarBusy(txId);
    try {
      const md = await api.sarMarkdown(txId);
      printMarkdown(md, `Rapport SAR — ${txId}`, w);
    } catch {
      w.close();
      setReportFlash('Aucun rapport SAR disponible pour cette transaction.');
      setTimeout(() => setReportFlash(''), 5000);
    }
    setSarBusy('');
  };

  const exportCsv = () => {
    if (!txs) return;
    const rows = [
      ['tx_id', 'date', 'type', 'montant', 'devise', 'decision', 'risque', 'etat', 'superviseur'],
      ...txs.map(t => [
        t.tx_id, t.created_at, t.transaction.tx_type ?? '',
        String(t.transaction.amount ?? ''), t.transaction.currency ?? '',
        t.summary?.ca_decision ?? '', t.summary?.risk_level ?? '',
        t.summary?.state ?? '', t.supervisor_decision?.action ?? '',
      ]),
    ];
    const csv = rows.map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(';')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([`﻿${csv}`], { type: 'text/csv' }));
    a.download = 'rapport_transactions.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <>
      <PageHeader
        title="Rapports & SAR"
        subtitle="Historique des analyses — consultation, export CSV et téléchargement des rapports"
        actions={
          <div className="flex gap-2">
            <button onClick={load}
              className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
              <RefreshCw size={13} /> Actualiser
            </button>
            <button onClick={sendWeekly}
              title="Envoie le rapport hebdomadaire par email (en production : automatique chaque vendredi)"
              className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500">
              <Mail size={13} /> Rapport hebdo
            </button>
            <button onClick={exportCsv}
              className="flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-700">
              <Download size={13} /> Export CSV
            </button>
          </div>
        }
      />

      {reportFlash && (
        <p className="mb-4 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2.5 text-sm text-indigo-800">
          {reportFlash}
        </p>
      )}

      <Card>
        {!txs ? <Spinner label="Chargement…" /> : txs.length === 0 ? (
          <EmptyState>Aucune analyse enregistrée dans cette session.</EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                  <th className="pb-2 pr-4">Transaction</th>
                  <th className="pb-2 pr-4">Date</th>
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4">Montant</th>
                  <th className="pb-2 pr-4">Décision</th>
                  <th className="pb-2 pr-4">Risque</th>
                  <th className="pb-2 pr-4">État</th>
                  <th className="pb-2 pr-4">Superviseur</th>
                  <th className="pb-2">SAR</th>
                </tr>
              </thead>
              <tbody>
                {txs.map(t => (
                  <tr key={t.tx_id} className="border-b border-slate-100 hover:bg-slate-50">
                    <td className="py-2.5 pr-4">
                      <Link to={`pipeline/${t.tx_id}`}
                        className="font-mono text-xs font-semibold text-indigo-700 hover:underline">
                        {t.tx_id}
                      </Link>
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-slate-500">
                      {new Date(t.created_at).toLocaleString('fr-FR')}
                    </td>
                    <td className="py-2.5 pr-4 text-xs text-slate-600">{t.transaction.tx_type}</td>
                    <td className="py-2.5 pr-4 tabular-nums">
                      {Number(t.transaction.amount ?? 0).toLocaleString('fr-FR')} {t.transaction.currency}
                    </td>
                    <td className="py-2.5 pr-4"><DecisionBadge value={t.summary?.ca_decision} /></td>
                    <td className="py-2.5 pr-4"><RiskBadge value={t.summary?.risk_level} /></td>
                    <td className="py-2.5 pr-4"><StateBadge value={t.summary?.state} /></td>
                    <td className="py-2.5 pr-4 text-xs text-slate-600">
                      {t.supervisor_decision
                        ? `${t.supervisor_decision.action} (${t.supervisor_decision.by})`
                        : '—'}
                    </td>
                    <td className="py-2.5">
                      {t.summary?.ca_decision === 'STR' || t.summary?.ca_decision === 'BLOCK' ? (
                        <button onClick={() => openSarPdf(t.tx_id)} disabled={sarBusy === t.tx_id}
                          title="Ouvrir le rapport SAR en PDF (sections imprimables)"
                          className="flex items-center gap-1 rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs font-semibold text-red-700 hover:bg-red-100 disabled:opacity-50">
                          <FileText size={13} /> {sarBusy === t.tx_id ? '…' : 'PDF'}
                        </button>
                      ) : (
                        <span className="text-xs text-slate-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
