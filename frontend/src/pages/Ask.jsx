import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { Badge, ErrorBox, Notice, SensitivityBadge, useAsync } from '../ui'

export const STEP_TITLES = {
  1: 'Question received', 2: 'Access check', 3: 'Entities, route and search path', 4: 'SPARQL by the local LLM',
  5: 'Entity mapping', 6: 'Evidence and lineage', 7: 'Answer by the local LLM', 8: 'Hallucination check',
}
const TONE = { done: 'ok', warn: 'warn', error: 'bad', skipped: 'muted', running: 'info' }
const VERDICT = { supported: 'ok', weak: 'warn', unsupported: 'bad' }

const Code = ({ children }) => <pre className="code">{children}</pre>

function Cite({ id, sources }) {
  const s = sources?.find((x) => x.id === id)
  return (
    <a className="cite" href={`#src-${id}`} title={s ? s.label : id}>
      {id}
    </a>
  )
}

function Table({ columns, rows }) {
  if (!rows?.length) return <p className="muted">No rows.</p>
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{columns.map((c) => <td key={c} className="mono">{r[c] == null ? '' : String(r[c])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

// ---- one line of summary per step, always visible
function summary(ev) {
  const d = ev.data || {}
  switch (ev.key) {
    case 'question': return `${d.user?.user} (${d.user?.role}) · search chosen from the question · model ${d.llm?.model}${d.llm?.available ? '' : ' (NOT available)'}`
    case 'access': return ev.status === 'running' ? 'Cutting out every document you may not read...' : `${d.documents_readable} readable, ${d.documents_withheld} withheld · graph ${d.triples_before} → ${d.triples_after} triples`
    case 'understand': return `${d.entities?.length || 0} entities · ${d.search?.label || 'search'} · route: ${d.route}${d.terms?.available ? ` · ${(d.terms.relations_selected?.length || 0) + (d.terms.kinds_selected?.length || 0)} term(s) matched by meaning` : ''}`
    case 'sparql': return ev.status === 'running' ? 'The model is writing the query...' : ev.status === 'skipped' || d.status === 'no_relation' ? d.reason : `${d.status} · ${d.row_count ?? 0} rows · ${d.attempts?.length || 0} attempt(s)`
    case 'mapping': return ev.status === 'skipped' ? d.reason : `${d.mapped?.length || 0} mapped · ${d.query_path?.length || 0} hop(s) in the query path${d.query_entities_not_in_question?.length ? ` · ${d.query_entities_not_in_question.length} extra in query` : ''}`
    case 'evidence': return ev.status === 'running' ? 'Searching your passages and tracing lineage...' : `${d.items?.filter((i) => i.kind !== 'chunk').length || 0} graph fact(s) + ${d.items?.filter((i) => i.kind === 'chunk').length || 0} passage(s) · ${d.lineage?.length || 0} documents traced`
    case 'answer': return ev.status === 'running' ? 'The model is writing from the evidence...' : `${d.mode}${d.seconds ? ` · ${d.seconds}s` : ''}${d.output_tokens ? ` · ${d.output_tokens} tokens` : ''}`
    case 'verify': return `${d.counts?.supported || 0} supported · ${d.counts?.weak || 0} weak · ${d.counts?.unsupported || 0} removed · risk ${d.risk}`
    default: return ''
  }
}

// ---- your everyday words matched to the ontology's technical terms by meaning (layer3/semantic.py, glossary in layer3/glossary.json)
function Terms({ t }) {
  if (!t.available) return <Notice tone="warn" title="Matching by meaning is not available">{t.reason} The exact-word rules were used instead.</Notice>
  const rows = [...t.relations.map((x) => ({ ...x, type: 'relation' })), ...t.kinds.map((x) => ({ ...x, type: 'answer kind' }))]
    .filter((x) => x.selected || x.score >= 0.6)
  const none = !t.relations_selected.length && !t.kinds_selected.length
  return (
    <div className="stack">
      <b>Your words matched to ontology terms, by meaning</b>
      {none && <p className="muted small">Nothing matched clearly enough, so the model was free to choose the relation.</p>}
      {rows.length > 0 && (
        <div className="table-wrap"><table>
          <thead><tr><th>Your words</th><th>Ontology term</th><th>Type</th><th>Score</th><th>Closest glossary phrase</th><th>Narrowed the query</th></tr></thead>
          <tbody>{rows.map((x) => (
            <tr key={x.type + x.term}>
              <td>“{x.words}”</td><td className="mono">{x.term}</td><td>{x.type}</td><td>{x.score}</td><td>{x.phrase}</td>
              <td>{x.selected ? <Badge tone="ok">yes</Badge> : <span className="muted">no</span>}</td>
            </tr>))}</tbody>
        </table></div>
      )}
      <p className="muted small">
        A term narrows the model's first edge only at a score of {t.thresholds.relation} or more for a relation, {t.thresholds.kind} for an answer kind. Lower scores are shown, not used.
        The glossary ({t.glossary_status === 'DRAFT' ? 'still a draft, not yet reviewed' : 'reviewed'}) is layer3/glossary.json.
      </p>
    </div>
  )
}

function StepBody({ ev }) {
  const d = ev.data || {}
  if (ev.status === 'running') return null
  switch (ev.key) {
    case 'access':
      return (
        <div className="stack">
          <p className="muted">
            {d.attributes?.user} may read up to <b>{d.attributes?.max_sensitivity}</b>, therapeutic areas <b>{(d.attributes?.therapeutic_areas || []).join(', ')}</b>,
            geographies <b>{(d.attributes?.geographies || []).join(', ')}</b>. Documents outside that are removed from the graph copy used for every later step; they are counted, never named.
          </p>
          <details><summary>{d.documents?.length} documents the rest of the pipeline may use</summary>
            <ul className="plain-list">{d.documents?.map((x) => <li key={x.document_uri}><span className="mono">{x.document_uri}</span> {x.title} <SensitivityBadge level={x.sensitivity_level} /></li>)}</ul>
          </details>
        </div>
      )
    case 'understand':
      return (
        <div className="stack">
          <div className="row">
            {d.entities?.length ? d.entities.map((e) => <Badge key={e.entity_id} tone="info" title={`${e.method}, confidence ${e.confidence}`}>{e.text} → {e.label} ({e.kind})</Badge>) : <span className="muted">No catalog entity named.</span>}
          </div>
          {d.search && (
            <div className="stack" style={{ gap: 4 }}>
              <div className="row"><b>Planned search path</b><Badge tone="ok">{d.search.label}</Badge><span className="muted small">{d.search.chosen_by === 'router' ? 'chosen from your question; step 6 shows what was actually used' : 'chosen manually'}</span></div>
              <ul className="plain-list">{d.search.reasons?.map((r) => <li key={r}>{d.search.chosen_by === 'router' ? `Your question ${r}` : r}</li>)}</ul>
            </div>
          )}
          <div className="row"><b>Evidence route</b><Badge tone="neutral">{d.route}</Badge></div>
          <ul className="plain-list">{d.reasons?.map((r) => <li key={r}>{r}</li>)}</ul>
          {d.terms && <Terms t={d.terms} />}
        </div>
      )
    case 'sparql':
      return (
        <div className="stack">
          {d.reason && <Notice tone="warn" title="No usable query">{d.reason}</Notice>}
          {d.sparql && <Code>{d.sparql.replace(/^PREFIX .*\n/gm, '')}</Code>}
          {d.rows?.length > 0 && <Table columns={d.columns} rows={d.rows} />}
          <details><summary>Attempts ({d.attempts?.length})</summary>
            {d.attempts?.map((a) => (
              <div key={a.n} className="attempt">
                <b>Attempt {a.n}</b> {a.seconds}s · {a.error ? <span className="bad-text">{a.error}</span> : a.note ? <span>{a.note}</span> : <span className="ok-text">accepted</span>}
                <Code>{a.plan}</Code>
              </div>
            ))}
          </details>
          <p className="muted small">The model writes a plan (patterns, filters, aggregates) under a schema that only allows real ontology properties; it is rendered as the SPARQL above and checked against the ontology before it runs on your view of the graph.</p>
        </div>
      )
    case 'mapping':
      return ev.status === 'skipped' ? null : (
        <div className="stack">
          {d.mapped?.length ? (
            <div className="table-wrap"><table>
              <thead><tr><th>Question term</th><th>Catalog entity</th><th>Kind</th><th>Catalog columns</th><th>External IDs</th><th>Readable docs</th><th>Used by query</th></tr></thead>
              <tbody>{d.mapped.map((m) => (
                <tr key={m.entity_id}>
                  <td>{m.text}</td><td><Link to={`/entities/${m.entity_id}`}>{m.label}</Link></td><td>{m.kind}</td>
                  <td title={(m.grounding_examples || []).join('\n')}>{m.grounded_columns}</td>
                  <td className="mono">{Object.entries(m.alignment || {}).map(([k, v]) => `${k} ${v}`).join(', ') || '-'}</td>
                  <td>{m.readable_documents}</td><td>{m.used_in_query ? 'yes' : 'no'}</td>
                </tr>))}</tbody>
            </table></div>
          ) : <p className="muted">No catalog entity in the question to map.</p>}
          {d.query_path?.length > 0 && (
            <div className="stack">
              <b>Relationship path the query walks</b>
              <ol className="path-list">
                {d.query_path.map((h, i) => (
                  <li key={i}><span className="path-node">{h.from}</span><span className="path-rel">{h.relation}</span><span className="path-node">{h.to}</span></li>
                ))}
              </ol>
            </div>
          )}
          {d.mapped?.some((m) => m.relations?.length) && (
            <div className="stack">
              <b>Relationships of each entity in the graph</b>
              {d.mapped.filter((m) => m.relations?.length).map((m) => (
                <div key={m.entity_id}><span className="muted small">{m.label}</span>
                  <ul className="plain-list small">{m.relations.map((r) => <li key={r} className="mono">{r}</li>)}</ul>
                </div>
              ))}
            </div>
          )}
          {d.query_entities_not_in_question?.length > 0 && <Notice tone="warn" title="The query uses entities the question did not name">{d.query_entities_not_in_question.join(', ')}</Notice>}
          {d.query_ontology_terms?.length > 0 && <p className="muted small">Ontology terms in the query: <span className="mono">{d.query_ontology_terms.join(', ')}</span></p>}
        </div>
      )
    case 'evidence':
      return (
        <div className="stack">
          {d.notes?.map((n) => <Notice key={n} tone="warn">{n}</Notice>)}
          {d.items?.map((it) => (
            <div key={it.id} className="ev-item">
              <div className="row"><Badge tone="info">{it.id}</Badge><Badge tone="neutral">{it.kind}</Badge><span className="mono small">{it.source_label}</span>
                {it.score != null && <span className="muted small">score {it.score}{it.score_parts?.entity_overlap != null ? ` (cosine ${it.score_parts.cosine}, entity overlap ${it.score_parts.entity_overlap})` : ''}</span>}</div>
              <p>{it.text}</p>
            </div>
          ))}
          {d.lineage?.length > 0 && <details><summary>Lineage of {d.lineage.length} documents (before the answer is checked)</summary>{d.lineage.map((l) => <Lineage key={l.document_uri} l={l} />)}</details>}
        </div>
      )
    case 'answer':
      return (<div className="stack"><Code>{d.raw || '(empty)'}</Code>{d.note && <Notice tone="warn">{d.note}</Notice>}</div>)
    case 'verify':
      return (
        <div className="stack">
          <p className="muted small">{d.method}. Thresholds and limits are on the Design evidence page.</p>
          {d.sentences?.map((s, i) => (
            <div key={i} className="ev-item">
              <div className="row"><Badge tone={VERDICT[s.verdict]}>{s.verdict}</Badge><span className="muted small">score {s.score} · words {s.checks.words} · meaning {s.checks.meaning} · numbers {s.checks.numbers_in_evidence ? 'ok' : 'NOT in evidence'} · names {s.checks.names_in_evidence ? 'ok' : `missing: ${s.checks.names_missing.join(', ')}`}{s.attributed ? ' · had no citation' : ''}</span></div>
              <p>{s.text} {s.cites.map((c) => <span key={c} className="cite">{c}</span>)}</p>
            </div>
          ))}
        </div>
      )
    default: return null
  }
}

export function Lineage({ l }) {
  return (
    <div className="src-card">
      <div className="row"><Link to={`/documents/${encodeURIComponent(l.document_uri)}`} className="mono">{l.document_uri}</Link><SensitivityBadge level={l.sensitivity_level} /><Badge tone="neutral">{l.study_type}</Badge></div>
      <div><b>{l.title}</b></div>
      <ul className="plain-list small muted">
        <li>File {l.source_filename} · {l.therapeutic_area} · {l.geography} · quality {l.quality_score} · expires {l.expiry_date}</li>
        <li>Approved by <b>{l.reviewed_by}</b>{l.reviewed_at ? ` on ${new Date(l.reviewed_at).toLocaleDateString()}` : ''}</li>
        <li>Enriched by {l.enrichment.model} at {l.enrichment.at}, catalog {l.enrichment.catalog_version}, {l.enrichment.triples_written} triples</li>
        <li>Chunks used: {l.chunks_used.map((c) => `${c.chunk_uri.split(':')[1]} (vector #${c.vector_id ?? '?'})`).join(', ') || 'none directly'}</li>
      </ul>
    </div>
  )
}

const RUN_TONE = { answered: 'ok', no_supported_answer: 'warn', no_evidence: 'warn' }

// ---- the user's own past questions (an ADMIN sees everyone's); click one to put it back in the box
// Everyone can delete their own entries; the admin's list also shows other people's entries, which can be read but not deleted here.
function Recent({ runs, loading, error, onPick, disabled, me, onDelete, onClear, deleteError }) {
  const mine = runs?.filter((r) => r.user_id === me).length || 0
  return (
    <aside className="card stack recent" aria-label="Recent questions">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0 }}>Recent</h2>
        {mine > 0 && <button type="button" className="btn btn-small btn-ghost" disabled={disabled} onClick={onClear}>Clear {runs.length === mine ? 'all' : 'mine'}</button>}
      </div>
      {error && <p className="muted small">Could not load history: {error.message}</p>}
      {deleteError && <p className="muted small" role="alert">Could not delete: {deleteError}</p>}
      {!error && loading && !runs && <p className="muted small">Loading...</p>}
      {runs && runs.length === 0 && <p className="muted small">No questions yet. Ask one and it will be listed here.</p>}
      <ul className="recent-list">
        {runs?.map((r) => (
          <li key={r.id} className="recent-row">
            <button type="button" className="recent-item" disabled={disabled} onClick={() => onPick(r)} title="Put this question back in the box">
              <span className="recent-q">{r.question}</span>
              <span className="row small muted">
                <Badge tone={RUN_TONE[r.status] || 'neutral'}>{String(r.status).replace(/_/g, ' ')}</Badge>
                <span>{(r.path || (r.mode === 'plain' ? 'vector search only' : 'semantic search (graph + vector)')).replace('graph + vector search', 'semantic search (graph + vector)')}</span>
                {r.sparql && <span>SPARQL {r.sparql}</span>}
                <span>{r.seconds}s</span>
                <span>{new Date(r.time).toLocaleString()}</span>
                {r.user_id !== me && <span>by {r.user_id}</span>}
              </span>
            </button>
            {r.user_id === me && (
              <button type="button" className="btn btn-small btn-ghost recent-del" disabled={disabled} onClick={() => onDelete(r)} aria-label={`Delete: ${r.question}`} title="Delete this question">Delete</button>
            )}
          </li>
        ))}
      </ul>
    </aside>
  )
}

export function Step({ n, ev }) {
  const status = ev ? ev.status : 'pending'
  return (
    <details className={`step step-${status}`} open={status !== 'pending' && status !== 'skipped'}>
      <summary>
        <span className="step-num">{n}</span>
        <span className="step-title">{STEP_TITLES[n]}</span>
        {status === 'running' && <span className="spinner" aria-label="running" />}
        <span className="step-sum">{ev ? summary(ev) : <span className="muted">waiting</span>}</span>
        {ev?.ms != null && <span className="muted small">{ev.ms >= 1000 ? `${(ev.ms / 1000).toFixed(1)}s` : `${ev.ms}ms`}</span>}
        {ev && <Badge tone={TONE[status]}>{status}</Badge>}
      </summary>
      {ev && <div className="step-body"><StepBody ev={ev} /></div>}
    </details>
  )
}

function Answer({ r }) {
  const sources = r.sources
  return (
    <div className="card stack" aria-live="polite">
      <div className="row">
        <h2 style={{ margin: 0 }}>Answer</h2>
        <Badge tone={r.status === 'answered' ? 'ok' : 'warn'}>{r.status.replace(/_/g, ' ')}</Badge>
        <Badge tone="neutral" title={`Planned: ${r.search?.label || r.mode}. ${(r.search?.reasons || []).join('; ')}${r.evidence_path ? ` Used: ${r.evidence_path.graph_facts} graph fact(s), ${r.evidence_path.passages} passage(s).` : ''}`}>{r.evidence_path?.label || (r.mode === 'plain' ? 'vector search only' : 'semantic search (graph + vector)')}</Badge>
        <Badge tone={r.risk === 'low' ? 'ok' : r.risk === 'none' ? 'neutral' : 'warn'}>hallucination risk: {r.risk}</Badge>
        <span className="muted small">{(r.timings_ms.total / 1000).toFixed(1)}s</span>
      </div>
      {r.message && <Notice tone="warn">{r.message}</Notice>}
      {r.lines.length === 0 && r.short_answer && (
        <p className="answer-line">{r.short_answer.text} {r.short_answer.cites.map((c) => <Cite key={c} id={c} sources={sources} />)}</p>
      )}
      {r.lines.length > 0 && (
        <div className="stack">
          {r.lines.map((l, i) => (
            <p key={i} className="answer-line">
              {l.text} {l.cites.map((c) => <Cite key={c} id={c} sources={sources} />)}
              {l.verdict === 'weak' && <Badge tone="warn" title={`score ${l.score}`}>weakly supported</Badge>}
              {l.attributed && <Badge tone="muted" title="The model gave no citation; the closest evidence was attached because it supports the sentence">source attached by checker</Badge>}
            </p>
          ))}
        </div>
      )}
      {r.removed.length > 0 && (
        <details><summary>{r.removed.length} line(s) removed by the hallucination check</summary>
          {r.removed.map((x, i) => <div key={i} className="ev-item"><p className="strike">{x.text}</p><p className="muted small">Removed because {x.why}.</p></div>)}
        </details>
      )}
      {sources.length > 0 && (
        <div className="stack">
          <h3 style={{ margin: 0 }}>Sources cited in this answer</h3>
          <p className="muted small">Only evidence that a surviving line cites is listed. {r.withheld_documents} document(s) outside your access were never read.</p>
          {sources.map((s) => (
            <div key={s.id} id={`src-${s.id}`} className="src-card">
              <div className="row"><Badge tone="info">{s.id}</Badge><Badge tone="neutral">{s.kind}</Badge><span className="mono small">{s.label}</span></div>
              <p>{s.text}</p>
              {s.computed_over?.length > 0 && <p className="muted small">Computed over {s.computed_over.length} readable documents: {s.computed_over.join(', ')}</p>}
            </div>
          ))}
          {r.documents.map((l) => <Lineage key={l.document_uri} l={l} />)}
        </div>
      )}
    </div>
  )
}

export default function Ask() {
  const [question, setQuestion] = useState('')
  const [run, setRun] = useState(null)
  const [error, setError] = useState(null)
  const [deleteError, setDeleteError] = useState(null)
  const { user } = useAuth()
  const abort = useRef(null)
  const { data: status } = useAsync(() => api.l3Status(), [])
  const { data: hist, error: histError, loading: histLoading, reload: reloadHistory } = useAsync(() => api.l3History(), [])

  // The question chooses its own search: the pipeline's router decides between the graph (with vectors) and vector search alone (step 3).
  const submit = async (e) => {
    e?.preventDefault()
    const q = question.trim()
    if (!q) return
    setError(null)
    abort.current = new AbortController()
    setRun({ steps: {}, final: null, busy: true, question: q })
    try {
      await api.l3Ask(q, 'auto', (ev) => {
        setRun((cur) => {
          if (ev.step === 'final') return { ...cur, final: ev.result, busy: false }
          if (ev.step === 'error') return { ...cur, busy: false, failure: ev.message }
          return { ...cur, steps: { ...cur.steps, [ev.step]: ev } }
        })
      }, abort.current.signal)
    } catch (err) {
      if (err.name !== 'AbortError') setError(err)
    }
    setRun((cur) => ({ ...cur, busy: false }))
    reloadHistory()
  }
  const pick = (r) => setQuestion(r.question)
  const removeFromHistory = async (fn) => {
    setDeleteError(null)
    try {
      await fn()
    } catch (e) {
      setDeleteError(e.message)
    }
    reloadHistory()
  }
  const onDelete = (r) => removeFromHistory(() => api.l3DeleteRun(r.id))
  const onClear = () => {
    if (window.confirm('Delete all of your own recent questions? Other users\' questions are not touched. This cannot be undone.')) removeFromHistory(() => api.l3ClearHistory())
  }
  const busy = !!run?.busy
  const llmDown = status && !status.llm.available

  return (
    <div className="ask-layout">
    <div className="stack ask-main">
      <div>
        <h1>Ask Watson</h1>
        <p className="muted">Ask in plain English. You will see each step as it runs, then an answer in which every line names its source.</p>
      </div>
      {llmDown && <Notice tone="warn" title="Local language model not ready">{status.llm.message} The pipeline still runs; answers are then quoted from the evidence instead of written by the model.</Notice>}
      <form className="card stack" onSubmit={submit}>
        <label htmlFor="q" className="muted small">Your question</label>
        <textarea id="q" rows={2} value={question} maxLength={600} onChange={(e) => setQuestion(e.target.value)} placeholder="Type your question" onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) submit(e) }} />
        <div className="row">
          <button className="btn btn-primary" disabled={busy || !question.trim()} type="submit">{busy ? 'Working...' : 'Ask'}</button>
          {busy && <button type="button" className="btn" onClick={() => abort.current?.abort()}>Stop</button>}
          {status?.llm && <span className="muted small">model: <span className="mono">{status.llm.model}</span></span>}
        </div>
      </form>
      {error && <ErrorBox error={error} />}
      {run?.failure && <Notice tone="warn" title="A step failed">{run.failure}</Notice>}
      {run && (
        <>
          <div className="stack">
            <h2>What happened, step by step</h2>
            <p className="muted small">Question: “{run.question}”. Step 3 shows which search the question was sent through, and why.</p>
            {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <Step key={n} n={n} ev={run.steps[n]} />)}
          </div>
          {run.final && <Answer r={run.final} />}
        </>
      )}
    </div>
    <Recent runs={hist?.runs} loading={histLoading} error={histError} onPick={pick} disabled={busy} me={user.user_id} onDelete={onDelete} onClear={onClear} deleteError={deleteError} />
    </div>
  )
}
