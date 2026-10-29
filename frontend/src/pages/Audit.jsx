import { useState } from 'react'
import { api } from '../api'
import { Badge, Empty, ErrorBox, Loading, fmtDateTime, useAsync } from '../ui'

const TONE = {
  INGEST: 'info', PARSE_WARNING: 'warn', PII_FLAG: 'bad', DEDUP_HIT: 'warn', STATE_TRANSITION: 'neutral',
  LIFECYCLE_VIOLATION: 'bad', EXPIRY_TRIGGERED: 'warn', ABAC_DENIED: 'bad',
  LOGIN: 'info', LOGIN_FAILED: 'bad', LOGOUT: 'neutral', PASSWORD_CHANGED: 'warn', HISTORY_DELETED: 'warn',
}

export default function Audit() {
  const [type, setType] = useState('')
  const [doc, setDoc] = useState('')
  const [last, setLast] = useState(100)
  const [open, setOpen] = useState(null)
  const { data, error, loading, reload } = useAsync(
    () => api.audit({ event_type: type, document_uri: doc.trim(), last }),
    [type, doc, last],
  )

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Audit log</h1>
          <p>
            Append-only record of every ingestion, check, decision and denied access. Newest first. Layers 2 and 3 add enrichment and
            query events to the same log.
          </p>
        </div>
        <button className="btn" onClick={reload}>
          Refresh
        </button>
      </div>
      <div className="row">
        <select aria-label="Event type" value={type} onChange={(e) => setType(e.target.value)} style={{ width: 230 }}>
          <option value="">All event types</option>
          {(data?.event_types || []).map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <input type="search" placeholder="Document, e.g. Watson:Doc_001" aria-label="Document URI" value={doc} onChange={(e) => setDoc(e.target.value)} style={{ width: 260 }} />
        <select aria-label="How many" value={last} onChange={(e) => setLast(Number(e.target.value))} style={{ width: 140 }}>
          <option value={50}>Last 50</option>
          <option value={100}>Last 100</option>
          <option value={500}>Last 500</option>
        </select>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data && <Loading />}
      {data && data.events.length === 0 && <Empty title="No events">Nothing matches this filter.</Empty>}
      {data && data.events.length > 0 && (
        <div className="card card-flush">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Event</th>
                  <th>Document</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map((e, i) => (
                  <tr key={i} className="clickable" onClick={() => setOpen(open === i ? null : i)}>
                    <td className="doc-sub" style={{ whiteSpace: 'nowrap' }}>{fmtDateTime(e.timestamp)}</td>
                    <td>
                      <Badge tone={TONE[e.event_type] || 'neutral'}>{e.event_type}</Badge>
                    </td>
                    <td className="mono">{e.document_uri || '-'}</td>
                    <td>
                      {open === i ? (
                        <div className="pre">{JSON.stringify(e.details, null, 2)}</div>
                      ) : (
                        <span className="doc-sub">{summary(e.details)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function summary(d) {
  if (d.from !== undefined && d.to) return `${d.from ?? 'new'} -> ${d.to}${d.actor ? ` by ${d.actor}` : ''}${d.notes ? ` · ${d.notes}` : ''}`
  if (d.warning) return d.warning
  if (d.verdict) return `${d.verdict} of ${d.original_uri}${d.similarity ? ` (similarity ${d.similarity})` : ''}`
  if (d.types) return `PII types: ${d.types.join(', ')}`
  if (d.reason) return `${d.user_id ?? ''} ${d.action ?? ''}: ${d.reason}`
  return JSON.stringify(d).slice(0, 120)
}
