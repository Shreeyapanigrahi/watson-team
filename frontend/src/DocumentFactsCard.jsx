import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from './api'
import { FILTER_KEY_BY_KIND, kindLabel } from './GraphFilters'
import { Badge, Empty, ErrorBox, Loading, useAsync } from './ui'

const SOURCES = [
  { id: 'all', label: 'All facts' },
  { id: 'document', label: 'Stated by the document' },
  { id: 'passages', label: 'Found in its passages' },
  { id: 'catalog', label: 'Reference data (catalog)' },
]

function from(f) {
  if (f.source === 'catalog') return 'catalog'
  if (f.source === 'document') return f.passages ? `document, and ${f.passages} passage${f.passages === 1 ? '' : 's'}` : 'document'
  return `${f.passages} passage${f.passages === 1 ? '' : 's'}`
}

function Entity({ e, onFilter }) {
  const key = FILTER_KEY_BY_KIND[e.kind]
  return (
    <span>
      <span className="muted">{kindLabel(e.kind)}</span> {e.label}
      {key && onFilter && (
        <>
          {' '}
          <button type="button" className="chip-btn" title={`Show only the documents about ${e.label}`} onClick={() => onFilter(key, e.id)}>
            filter graph
          </button>
        </>
      )}
    </span>
  )
}

/** One document's facts as statements (subject, relationship, object) and where each one comes from, plus its sentiment records.
 *  Read from GET /api/l2/documents/{uri}/facts, which refuses a document the user may not read. */
export default function DocumentFactsCard({ id, onFilter }) {
  const facts = useAsync(() => api.l2DocumentFacts(id), [id])
  const [source, setSource] = useState('all')
  const d = facts.data
  const rows = (d?.facts || []).filter((f) => source === 'all' || f.source === source)
  return (
    <div className="card">
      <div className="row spread">
        <div>
          <h3 style={{ margin: 0 }}>Facts in {d?.title || id.replace('Watson:', '')}</h3>
          <p className="doc-sub" style={{ margin: '2px 0 0' }}>
            <Link to={`/documents/${encodeURIComponent(id)}`}>{id.replace('Watson:', '')}</Link>
            {d?.enriched && ` · ${d.counts.entities} entities, ${d.counts.passages} passages`}
          </p>
        </div>
        <div className="row">
          <label className="check" htmlFor="facts-source" style={{ fontWeight: 600 }}>Show</label>
          <select id="facts-source" value={source} onChange={(e) => setSource(e.target.value)} style={{ width: 'auto' }}>
            {SOURCES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </div>
      </div>
      <ErrorBox error={facts.error} onRetry={facts.reload} />
      {facts.loading && !d && <Loading label="Reading the document's facts" />}
      {d && !d.enriched && <Empty title="Not enriched yet">This document has no knowledge-graph facts until enrichment has run.</Empty>}
      {d?.enriched && (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <caption className="sr-only">{rows.length} facts</caption>
            <thead><tr><th>Subject</th><th>Relationship</th><th>Object</th><th>From</th></tr></thead>
            <tbody>
              {rows.map((f, i) => (
                <tr key={`${f.subject.id}|${f.relation}|${f.object.id}|${f.source}|${i}`}>
                  <td>{f.subject.kind === 'Document' ? <span className="muted">This document</span> : <Entity e={f.subject} onFilter={onFilter} />}</td>
                  <td><code>{f.relation}</code></td>
                  <td><Entity e={f.object} onFilter={onFilter} /></td>
                  <td><Badge tone={f.source === 'catalog' ? 'muted' : f.source === 'document' ? 'ok' : 'info'}>{from(f)}</Badge></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={4} className="muted">No facts of this kind.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {d?.enriched && d.sentiment.length > 0 && (
        <>
          <h3 style={{ marginTop: 16 }}>Sentiment recorded in this document</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Brand</th><th>Geography</th><th>Indication</th><th className="num">Average score</th><th className="num">Assertions</th></tr></thead>
              <tbody>
                {d.sentiment.map((s) => (
                  <tr key={`${s.brand.id}|${s.geography.id}|${s.indication.id}`}>
                    <td>{s.brand.label}</td><td>{s.geography.label}</td><td>{s.indication.label}</td>
                    <td className="num">{s.average_score}</td><td className="num">{s.assertions}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
