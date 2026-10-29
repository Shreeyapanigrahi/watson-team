import { useState } from 'react'
import { api } from '../api'
import { Badge, Empty, ErrorBox, Loading, Notice, useAsync } from '../ui'

function ResultTable({ columns, rows }) {
  if (!rows.length) return <Empty title="No rows">The query ran and matched nothing.</Empty>
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => <tr key={i}>{columns.map((c) => <td key={c} className="mono">{r[c] === null ? '' : String(r[c])}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  )
}

function Runner({ q }) {
  const names = Object.keys(q.params || {})
  const [params, setParams] = useState(q.params || {})
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [save, setSave] = useState(false)

  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      setRes(await api.l2RunQuery(q.id, params, save))
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="stack">
      <p>{q.question}</p>
      <details>
        <summary style={{ cursor: 'pointer', fontWeight: 600 }}>SPARQL text</summary>
        <pre className="pre">{q.sparql}</pre>
      </details>
      {q.reasoning === 'owlrl' && <Notice tone="info">This query relies on inference (OWL RL). The result is shown with and without it, so you can see what the reasoner added.</Notice>}
      {q.data === 'telemetry' && <Notice tone="info">This query reads the log of what searches returned, joined to the graph.</Notice>}
      {names.length > 0 && (
        <div className="row">
          {names.map((n) => (
            <div className="field" key={n}>
              <label htmlFor={`${q.id}${n}`}>{n}</label>
              <input id={`${q.id}${n}`} value={params[n]} onChange={(e) => setParams({ ...params, [n]: e.target.value })} />
            </div>
          ))}
        </div>
      )}
      <div className="row">
        <button className="btn btn-primary" onClick={run} disabled={busy}>{busy ? 'Running...' : 'Run'}</button>
        <label className="check"><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> Save the result to results/query_results</label>
      </div>
      <ErrorBox error={err} />
      {res && (
        <div className="stack">
          <p className="doc-sub">{res.count} row{res.count === 1 ? '' : 's'} in {res.seconds}s · {res.triples_in_graph?.toLocaleString()} triples{res.triples_after_inference ? ` (${res.triples_after_inference.toLocaleString()} after inference)` : ''}{res.telemetry_events !== undefined ? ` · ${res.telemetry_events} logged retrievals` : ''}</p>
          {res.rows_without_inference && (
            <div>
              <h3>Without inference ({res.rows_without_inference.length} rows)</h3>
              <ResultTable columns={res.columns} rows={res.rows_without_inference} />
              <h3>With inference ({res.rows.length} rows)</h3>
            </div>
          )}
          <ResultTable columns={res.columns} rows={res.rows} />
        </div>
      )}
    </div>
  )
}

function Custom() {
  const [text, setText] = useState('PREFIX Watson: <http://watson.local/pharma#>\nSELECT ?brand (COUNT(?doc) AS ?documents)\nWHERE { ?doc Watson:mentionsBrand ?brand }\nGROUP BY ?brand\nORDER BY DESC(?documents)\nLIMIT 10')
  const [res, setRes] = useState(null)
  const [err, setErr] = useState(null)
  const run = async () => {
    setErr(null)
    try {
      setRes(await api.l2CustomQuery(text))
    } catch (e) {
      setErr(e)
      setRes(null)
    }
  }
  return (
    <div className="card stack">
      <h3>Your own query</h3>
      <p className="muted">Read-only SELECT or ASK. Updates are refused.</p>
      <textarea aria-label="SPARQL query" className="mono" rows={9} value={text} onChange={(e) => setText(e.target.value)} style={{ width: '100%' }} />
      <div><button className="btn btn-primary" onClick={run}>Run</button></div>
      <ErrorBox error={err} />
      {res && <><p className="doc-sub">{res.count} rows{res.truncated ? ' (truncated)' : ''}</p><ResultTable columns={res.columns} rows={res.rows} /></>}
    </div>
  )
}

export default function Queries() {
  const qs = useAsync(() => api.l2Queries(), [])
  const [open, setOpen] = useState(null)
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>SPARQL queries</h1>
          <p>The questions the knowledge graph can answer. Results here are not filtered by who is asking, which is why only administrators can run them.</p>
        </div>
      </div>
      <ErrorBox error={qs.error} onRetry={qs.reload} />
      {qs.loading && !qs.data && <Loading />}
      {qs.data && qs.data.queries.map((q) => (
        <div className="card" key={q.id}>
          <div className="row spread">
            <div>
              <Badge tone="info">{q.id}</Badge> <strong>{q.title}</strong>
            </div>
            <button className="btn btn-small" onClick={() => setOpen(open === q.id ? null : q.id)} aria-expanded={open === q.id}>{open === q.id ? 'Close' : 'Open'}</button>
          </div>
          {open === q.id && <div style={{ marginTop: 10 }}><Runner q={q} /></div>}
        </div>
      ))}
      <Custom />
    </div>
  )
}
