import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, Empty, ErrorBox, Loading, Notice, Tabs, fmtDateTime, useAsync } from '../ui'

const CHECK_HELP = {
  '1': 'Every document has a lifecycle state, a sensitivity level and an expiry date',
  '2': 'Every chunk has one parent document and its access attributes, and a vector in the index',
  '3': 'No entity is ungrounded or left without a document',
  '4': 'Every chunk traces back to a lineage record that names a reviewer',
}
const STATE_TONE = { COMPLETE: 'ok', FAILED: 'bad', RUNNING: 'info', QUEUED: 'info', NOT_ENRICHED: 'warn' }

export default function GraphHealth() {
  const [tab, setTab] = useState('validation')
  const status = useAsync(() => api.l2Status(), [])
  const pending = status.data?.provisional_pending

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Graph health</h1>
          <p>Check that the knowledge graph is sound, see how each document was enriched, and decide on names the catalog did not recognise.</p>
        </div>
      </div>
      <ErrorBox error={status.error} onRetry={status.reload} />
      {status.data && (
        <div className="tiles">
          <div className="tile"><div className="tile-value">{(status.data.triples ?? 0).toLocaleString()}</div><div className="tile-label">triples in the graph</div></div>
          <div className="tile"><div className="tile-value">{status.data.vectors}</div><div className="tile-label">vectors in the index</div><div className="doc-sub">{status.data.embedding_model}</div></div>
          <div className="tile"><div className="tile-value">{status.data.readable_documents}</div><div className="tile-label">enriched documents</div></div>
          <div className="tile"><div className="tile-value">{pending}</div><div className="tile-label">names awaiting a decision</div></div>
        </div>
      )}
      <Tabs
        tabs={[{ id: 'validation', label: 'SHACL validation' }, { id: 'enrichment', label: 'Enrichment' },
          { id: 'provisional', label: 'Unrecognised names', count: pending }, { id: 'taxonomy', label: 'Taxonomies' }]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'validation' && <Validation onRan={status.reload} />}
      {tab === 'enrichment' && <Enrichment />}
      {tab === 'provisional' && <Provisional onChanged={status.reload} />}
      {tab === 'taxonomy' && <Taxonomy />}
    </div>
  )
}

function Validation({ onRan }) {
  const last = useAsync(() => api.l2ValidateLast(), [])
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const shown = res || (last.data?.ran_at ? last.data : null)

  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      setRes(await api.l2Validate())
      onRan?.()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  const groups = {}
  ;(shown?.violations || []).forEach((v) => {
    ;(groups[v.shape || 'Other'] = groups[v.shape || 'Other'] || []).push(v)
  })

  return (
    <div className="stack">
      <div className="card">
        <div className="row spread">
          <div>
            <h3 style={{ margin: 0 }}>Validate the knowledge graph</h3>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              Runs the SHACL shapes over the whole graph, then checks that every chunk of an approved document has a vector in the index.
            </p>
          </div>
          <button className="btn btn-primary" onClick={run} disabled={busy}>{busy ? 'Running...' : 'Run validation'}</button>
        </div>
      </div>
      <ErrorBox error={err || last.error} />
      {last.loading && !shown && <Loading />}
      {!shown && !last.loading && <Empty title="Not run yet">Run the validation to see the result.</Empty>}
      {shown && (
        <div className="card">
          <h3>
            {shown.conforms ? <Badge tone="ok">Conforms</Badge> : <Badge tone="bad">{shown.violation_count} violation{shown.violation_count === 1 ? '' : 's'}</Badge>}{' '}
            <span className="doc-sub">
              {shown.shapes} shapes · {shown.triples_checked?.toLocaleString()} triples · {fmtDateTime(shown.ran_at)} by {shown.ran_by} · {shown.seconds}s
            </span>
          </h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>The brief's check</th><th>Result</th></tr></thead>
              <tbody>
                {Object.entries(CHECK_HELP).map(([k, text]) => {
                  const hits = Object.entries(shown.by_check || {}).filter(([name]) => name.startsWith(k)).reduce((a, [, c]) => a + c, 0)
                  const ib = k === '2' ? shown.index_binding : null
                  const bad = hits + (ib && !ib.ok ? ib.missing_count + ib.orphan_count : 0)
                  return (
                    <tr key={k}>
                      <td><strong>Check {k}.</strong> {text}</td>
                      <td><Badge tone={bad ? 'bad' : 'ok'}>{bad ? `${bad} problem${bad === 1 ? '' : 's'}` : 'pass'}</Badge></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {shown.index_binding && (
            <p className="doc-sub" style={{ marginTop: 8 }}>
              Index: {shown.index_binding.active_chunks} chunks of approved documents, {shown.index_binding.indexed_vectors} vectors,{' '}
              {shown.index_binding.missing_count} chunks without a vector, {shown.index_binding.orphan_count} vectors without an approved chunk.
            </p>
          )}
          {Object.keys(groups).length > 0 && (
            <>
              <h3>Violations</h3>
              {Object.entries(groups).map(([shape, vs]) => (
                <details key={shape} open>
                  <summary style={{ cursor: 'pointer', fontWeight: 600 }}>{shape} ({vs.length})</summary>
                  <div className="table-wrap">
                    <table>
                      <thead><tr><th>Node</th><th>Property</th><th>Problem</th></tr></thead>
                      <tbody>
                        {vs.slice(0, 50).map((v, i) => <tr key={i}><td className="mono">{v.focus}</td><td className="mono">{v.path}</td><td>{v.message}</td></tr>)}
                      </tbody>
                    </table>
                  </div>
                </details>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function Enrichment() {
  const rows = useAsync(() => api.l2Enrichment(), [])
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState(null)
  const retry = async (uri) => {
    setBusy(uri)
    setErr(null)
    try {
      await api.l2Enrich(uri)
      rows.reload()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(null)
    }
  }
  if (rows.loading && !rows.data) return <Loading />
  if (rows.error) return <ErrorBox error={rows.error} onRetry={rows.reload} />
  const stale = rows.data.documents.filter((d) => d.stale).length
  return (
    <div className="card">
      <ErrorBox error={err} />
      {stale > 0 && <Notice tone="warn" title="Catalog has changed">{stale} document{stale === 1 ? ' was' : 's were'} enriched with an older catalog. Re-run them to bring their entities up to date.</Notice>}
      {rows.data.documents.length === 0 ? <Empty title="No approved documents">Documents are enriched after a reviewer approves them.</Empty> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Document</th><th>State</th><th className="num">Chunks</th><th className="num">Mentions</th><th className="num">Sentiments</th><th className="num">Triples</th><th>Finished</th><th /></tr></thead>
            <tbody>
              {rows.data.documents.map((d) => (
                <tr key={d.document_uri}>
                  <td><Link to={`/documents/${encodeURIComponent(d.document_uri)}`}>{d.title || d.document_uri}</Link></td>
                  <td>
                    <Badge tone={STATE_TONE[d.state] || 'neutral'}>{d.state}</Badge>
                    {d.stale && <> <Badge tone="warn">catalog changed</Badge></>}
                    {d.error && <div className="doc-sub">{d.stage}: {d.error}</div>}
                  </td>
                  <td className="num">{d.chunks ?? '-'}</td>
                  <td className="num">{d.mentions ?? '-'}</td>
                  <td className="num">{d.sentiments ?? '-'}</td>
                  <td className="num">{d.triples ?? '-'}</td>
                  <td>{fmtDateTime(d.finished)}</td>
                  <td><button className="btn btn-small" disabled={busy === d.document_uri} onClick={() => retry(d.document_uri)}>{busy === d.document_uri ? 'Running...' : d.state === 'COMPLETE' ? 'Re-run' : 'Run'}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Provisional({ onChanged }) {
  const list = useAsync(() => api.l2Provisional(), [])
  const [open, setOpen] = useState(null)
  if (list.loading && !list.data) return <Loading />
  if (list.error) return <ErrorBox error={list.error} onRetry={list.reload} />
  const rows = list.data.entities
  const pending = rows.filter((r) => r.status === 'PENDING')
  const done = rows.filter((r) => r.status !== 'PENDING')
  const changed = () => { setOpen(null); list.reload(); onChanged?.() }

  return (
    <div className="stack">
      <Notice tone="info">
        When a document names a brand, drug, class or indication the catalog does not know, it is held here as a provisional name. A person decides:
        it is another spelling of something that exists (alias), a genuinely new entry, or a mistake. Nothing is added to the catalog without that decision.
      </Notice>
      {pending.length === 0 ? <Empty title="Nothing waiting">No unrecognised names are waiting for a decision.</Empty> : pending.map((p) => (
        <div className="card" key={p.id}>
          <div className="row spread">
            <div>
              <strong>{p.label}</strong> <Badge tone="info">{p.kind}</Badge>
              <div className="doc-sub">Seen in {p.documents.length} document{p.documents.length === 1 ? '' : 's'}: {p.documents.join(', ')}</div>
            </div>
            <button className="btn btn-small" onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? 'Close' : 'Decide'}</button>
          </div>
          {open === p.id && <DecisionForm p={p} onDone={changed} />}
        </div>
      ))}
      {done.length > 0 && (
        <div className="card">
          <h3>Decided</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Kind</th><th>Decision</th><th>By</th><th>When</th></tr></thead>
              <tbody>
                {done.map((p) => (
                  <tr key={p.id}><td>{p.label}</td><td>{p.kind}</td><td>{p.status}{p.resolved_to ? ` as ${p.resolved_to}` : ''}</td><td>{p.decided_by}</td><td>{fmtDateTime(p.decided_at)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Pick({ id, label, kind, value, onChange, multiple, required }) {
  const ents = useAsync(() => api.l2Entities({ kind }), [kind])
  return (
    <div className="field">
      <label htmlFor={id}>{label}{required ? '' : ' (optional)'}</label>
      <select id={id} multiple={multiple} value={value} onChange={(e) => onChange(multiple ? [...e.target.selectedOptions].map((o) => o.value) : e.target.value)}>
        {!multiple && <option value="">{required ? 'Choose...' : 'None'}</option>}
        {(ents.data?.entities || []).map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
      </select>
    </div>
  )
}

function DecisionForm({ p, onDone }) {
  const [mode, setMode] = useState('alias')
  const [target, setTarget] = useState('')
  const [fields, setFields] = useState({})
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const set = (k) => (v) => setFields((f) => ({ ...f, [k]: v }))
  const canNew = ['Brand', 'Drug', 'DrugClass', 'Indication'].includes(p.kind)

  const go = async (fn) => {
    setBusy(true)
    setErr(null)
    try {
      await fn()
      onDone()
    } catch (e) {
      setErr(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="stack" style={{ marginTop: 12 }}>
      <ErrorBox error={err} />
      <div className="row">
        <label className="check"><input type="radio" name={`m${p.id}`} checked={mode === 'alias'} onChange={() => setMode('alias')} /> Another spelling of an existing entry</label>
        {canNew && <label className="check"><input type="radio" name={`m${p.id}`} checked={mode === 'new'} onChange={() => setMode('new')} /> A new entry</label>}
      </div>
      {mode === 'alias' && <Pick id={`t${p.id}`} label={`Which ${p.kind}?`} kind={p.kind} value={target} onChange={setTarget} required />}
      {mode === 'new' && (
        <div className="stack">
          {p.kind === 'Brand' && <>
            <Pick id={`d${p.id}`} label="Active drug" kind="Drug" value={fields.drug || ''} onChange={set('drug')} required />
            <Pick id={`a${p.id}`} label="Therapeutic area" kind="TherapeuticArea" value={fields.therapeutic_area || ''} onChange={set('therapeutic_area')} required />
            <Pick id={`i${p.id}`} label="Indications (hold Ctrl to choose several)" kind="Indication" value={fields.indications || []} onChange={set('indications')} multiple required />
          </>}
          {p.kind === 'Drug' && <Pick id={`c${p.id}`} label="Drug class" kind="DrugClass" value={fields.drug_class || ''} onChange={set('drug_class')} />}
          {p.kind === 'Indication' && <>
            <Pick id={`a${p.id}`} label="Therapeutic area" kind="TherapeuticArea" value={fields.therapeutic_area || ''} onChange={set('therapeutic_area')} required />
            <Pick id={`p${p.id}`} label="Broader indication" kind="Indication" value={fields.parent || ''} onChange={set('parent')} />
          </>}
        </div>
      )}
      <div className="field">
        <label htmlFor={`n${p.id}`}>Note (optional)</label>
        <input id={`n${p.id}`} value={note} onChange={(e) => setNote(e.target.value)} />
      </div>
      <div className="row">
        <button className="btn btn-primary" disabled={busy || (mode === 'alias' && !target)}
          onClick={() => go(() => api.l2Approve(p.id, { mode, target_id: mode === 'alias' ? target : undefined, fields: mode === 'new' ? fields : undefined, note: note || undefined }))}>
          {busy ? 'Working...' : 'Approve'}
        </button>
        <button className="btn" disabled={busy} onClick={() => go(() => api.l2Reject(p.id, note || undefined))}>Reject as a mistake</button>
      </div>
      <p className="doc-sub">Approving rebuilds the catalog, adds the entry to the graph and re-runs the documents that used the name. Rejected names are not proposed again.</p>
    </div>
  )
}

function Taxonomy() {
  const { can } = useAuth()
  const tax = useAsync(() => api.l2Taxonomy(), [])
  const [drafts, setDrafts] = useState({})
  const [err, setErr] = useState(null)
  if (tax.loading && !tax.data) return <Loading />
  if (tax.error) return <ErrorBox error={tax.error} onRetry={tax.reload} />
  const add = async (name) => {
    setErr(null)
    try {
      await api.l2AddTerm(name, drafts[name])
      setDrafts((d) => ({ ...d, [name]: '' }))
      tax.reload()
    } catch (e) {
      setErr(e)
    }
  }
  return (
    <div className="stack">
      <Notice tone="info">A document can only be approved with values from these lists. A new value is added here first, so every term used in the graph has been agreed.</Notice>
      <ErrorBox error={err} />
      <div className="grid-2">
        {Object.entries(tax.data.taxonomies).map(([name, t]) => (
          <div className="card" key={name}>
            <h3>{name} <span className="doc-sub">version {t.version}</span></h3>
            <ul className="plain">
              {t.terms.map((x) => <li key={x.id}>{x.deprecated ? <s className="muted">{x.label}</s> : x.label} {x.deprecated && <Badge tone="neutral">deprecated</Badge>}</li>)}
            </ul>
            {can('edit_taxonomy') && (
              <div className="row">
                <input aria-label={`New ${name} term`} placeholder="New term" value={drafts[name] || ''} onChange={(e) => setDrafts((d) => ({ ...d, [name]: e.target.value }))} />
                <button className="btn btn-small" disabled={!drafts[name]} onClick={() => add(name)}>Add</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
