import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, Empty, ErrorBox, KV, Loading, Notice, ScoreBar, fmtDate, fmtDateTime, useAsync } from '../ui'

const METHOD_HELP = {
  exact: 'Matched a catalog name exactly',
  case_insensitive: 'Matched a catalog name, ignoring capitals',
  partial: 'Matched part of a longer catalog name',
  fuzzy: 'Close to a catalog name (similarity above the threshold)',
  provisional: 'Not in the catalog; held for a person to decide',
}
const STATE_TONE = { COMPLETE: 'ok', FAILED: 'bad', RUNNING: 'info', QUEUED: 'info' }

/** What the knowledge graph holds about one document. A reader who may not see the document gets an access error from the server. */
export default function DocumentKnowledge({ uri }) {
  const { can } = useAuth()
  const k = useAsync(() => api.l2Knowledge(uri), [uri])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const grouped = useMemo(() => {
    const by = {}
    ;(k.data?.mentions || []).forEach((m) => {
      const key = m.entity_id || m.text
      const g = (by[key] = by[key] || { key, id: m.entity_id, kind: m.kind, texts: new Set(), count: 0, min: 1, methods: new Set() })
      g.texts.add(m.text)
      g.count += 1
      g.min = Math.min(g.min, m.confidence)
      g.methods.add(m.method)
    })
    return Object.values(by).sort((a, b) => a.kind.localeCompare(b.kind) || b.count - a.count)
  }, [k.data])

  if (k.loading && !k.data) return <Loading />
  if (k.error) return <ErrorBox error={k.error} onRetry={k.reload} />
  const d = k.data
  const st = d.enrichment

  const rerun = async () => {
    setBusy(true)
    setErr(null)
    try {
      await api.l2Enrich(uri)
      k.reload()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <ErrorBox error={err} />
      <div className="card">
        <div className="row spread">
          <div>
            <h3 style={{ margin: 0 }}>Enrichment {st?.state ? <Badge tone={STATE_TONE[st.state] || 'neutral'}>{st.state}</Badge> : <Badge tone="warn">not enriched</Badge>}</h3>
            {st?.finished && <p className="doc-sub">Finished {fmtDateTime(st.finished)}</p>}
            {st?.error && <p className="doc-sub">Stopped at {st.stage}: {st.error}</p>}
          </div>
          {can('enrich') && <button className="btn btn-small" onClick={rerun} disabled={busy}>{busy ? 'Running...' : d.enriched ? 'Re-run enrichment' : 'Run enrichment'}</button>}
        </div>
        {d.stale && <Notice tone="warn" title="Catalog has changed since this document was enriched">Its entities were matched against catalog {d.lineage.catalog_version}; the catalog is now {d.catalog_version_now}. Re-run to update them.</Notice>}
      </div>

      {!d.enriched ? (
        <Empty title="Nothing in the graph yet">Only approved documents are enriched. Once a reviewer approves this one, its entities, sentiment and lineage appear here.</Empty>
      ) : (
        <>
          <div className="grid-2">
            <div className="card">
              <h3>In the graph</h3>
              <KV rows={[
                ['Knowledge spaces', d.spaces.length ? d.spaces.map((s) => s.name).join(', ') : null],
                ['Document type', d.type.join(' / ')],
                ['Chunks indexed', d.chunks.length],
                ['Triples written', d.lineage?.triples_written],
              ]} />
            </div>
            <div className="card">
              <h3>Lineage</h3>
              {d.lineage ? (
                <KV rows={[
                  ['Approved by', d.lineage.reviewed_by],
                  ['Processed', fmtDateTime(d.lineage.processed)],
                  ['Embedding model', d.lineage.model],
                  ['Catalog version', <span className="mono">{d.lineage.catalog_version}</span>],
                ]} />
              ) : <p className="muted">No lineage record.</p>}
            </div>
          </div>

          <div className="card">
            <h3>Entities found ({grouped.length})</h3>
            {grouped.length === 0 ? <p className="muted">No catalog entity was found in this document.</p> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Entity</th><th>Kind</th><th>Written as</th><th className="num">Times</th><th title="Lowest confidence among its mentions">Confidence</th><th>How it was matched</th></tr></thead>
                  <tbody>
                    {grouped.map((g) => (
                      <tr key={g.key}>
                        <td>{g.id && !/Provisional_/.test(g.id) ? <Link to={`/entities/${encodeURIComponent(g.id.replace(/^Watson:/, ''))}`}>{[...g.texts][0]}</Link> : <><span>{[...g.texts][0]}</span> <Badge tone="warn">provisional</Badge></>}</td>
                        <td>{g.kind}</td>
                        <td>{[...g.texts].join(', ')}</td>
                        <td className="num">{g.count}</td>
                        <td style={{ minWidth: 110 }}><ScoreBar value={g.min} label="Confidence" /><span className="doc-sub">{g.min}</span></td>
                        <td>{[...g.methods].map((m) => <div key={m} title={METHOD_HELP[m]}>{m.replace('_', ' ')}</div>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <h3>Sentiment statements ({d.sentiment.length})</h3>
            {d.sentiment.length === 0 ? <p className="muted">This document states no brand sentiment scores.</p> : (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Date</th><th>Brand</th><th>Indication</th><th>Region</th><th style={{ minWidth: 130 }}>Score</th><th>Sentence</th></tr></thead>
                  <tbody>
                    {d.sentiment.map((s, i) => (
                      <tr key={i}>
                        <td>{fmtDate(s.date)}</td><td>{s.brand}</td><td>{s.indication}</td><td>{s.geography}</td>
                        <td><ScoreBar value={s.score} label="Sentiment" /><span className="doc-sub">{s.score}</span></td>
                        <td>{s.sentence}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <h3>Entities by chunk</h3>
            <div className="table-wrap">
              <table>
                <thead><tr><th className="num">Chunk</th><th>Entities</th></tr></thead>
                <tbody>
                  {d.chunks.map((c) => (
                    <tr key={c.chunk_uri}>
                      <td className="num" title={c.chunk_uri}>{c.index}</td>
                      <td>{c.entities.length ? c.entities.map((e) => <Badge key={e.kind + e.id} tone={e.kind === 'Provisional' ? 'warn' : 'info'} title={e.kind}>{e.label}</Badge>) : <span className="muted">none</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
