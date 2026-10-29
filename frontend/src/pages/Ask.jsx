import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Badge, ErrorBox, Notice, SensitivityBadge, useAsync } from '../ui'

const STEP_TITLES = {
  1: 'Question received', 2: 'Access check', 3: 'Entities and route', 4: 'SPARQL by the local LLM',
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
    case 'question': return `${d.user?.user} (${d.user?.role}) · mode ${d.mode} · model ${d.llm?.model}${d.llm?.available ? '' : ' (NOT available)'}`
    case 'access': return ev.status === 'running' ? 'Cutting out every document you may not read...' : `${d.documents_readable} readable, ${d.documents_withheld} withheld · graph ${d.triples_before} → ${d.triples_after} triples`
    case 'understand': return `${d.entities?.length || 0} entities · route: ${d.route}`
    case 'sparql': return ev.status === 'running' ? 'The model is writing the query...' : ev.status === 'skipped' ? d.reason : `${d.status} · ${d.row_count ?? 0} rows · ${d.attempts?.length || 0} attempt(s)`
    case 'mapping': return ev.status === 'skipped' ? d.reason : `${d.mapped?.length || 0} mapped · ${d.query_path?.length || 0} hop(s) in the query path${d.query_entities_not_in_question?.length ? ` · ${d.query_entities_not_in_question.length} extra in query` : ''}`
    case 'evidence': return ev.status === 'running' ? 'Searching your passages and tracing lineage...' : `${d.items?.length || 0} evidence items · ${d.lineage?.length || 0} documents traced`
    case 'answer': return ev.status === 'running' ? 'The model is writing from the evidence...' : `${d.mode}${d.seconds ? ` · ${d.seconds}s` : ''}${d.output_tokens ? ` · ${d.output_tokens} tokens` : ''}`
    case 'verify': return `${d.counts?.supported || 0} supported · ${d.counts?.weak || 0} weak · ${d.counts?.unsupported || 0} removed · risk ${d.risk}`
    default: return ''
  }
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
          <div><Badge tone="ok">{d.route}</Badge></div>
          <ul className="plain-list">{d.reasons?.map((r) => <li key={r}>{r}</li>)}</ul>
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

function Lineage({ l }) {
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
function Recent({ runs, loading, error, onPick, disabled }) {
  return (
    <aside className="card stack recent" aria-label="Recent questions">
      <h2 style={{ margin: 0 }}>Recent</h2>
      {error && <p className="muted small">Could not load history: {error.message}</p>}
      {!error && loading && !runs && <p className="muted small">Loading...</p>}
      {runs && runs.length === 0 && <p className="muted small">No questions yet. Ask one and it will be listed here.</p>}
      <ul className="recent-list">
        {runs?.map((r, i) => (
          <li key={`${r.time}-${i}`}>
            <button type="button" className="recent-item" disabled={disabled} onClick={() => onPick(r)} title="Put this question back in the box">
              <span className="recent-q">{r.question}</span>
              <span className="row small muted">
                <Badge tone={RUN_TONE[r.status] || 'neutral'}>{String(r.status).replace(/_/g, ' ')}</Badge>
                <span>{r.mode === 'plain' ? 'plain' : 'semantic'}</span>
                {r.sparql && <span>SPARQL {r.sparql}</span>}
                <span>{r.seconds}s</span>
                <span>{new Date(r.time).toLocaleString()}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}

function Step({ n, ev }) {
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
        <Badge tone="neutral">{r.mode === 'plain' ? 'plain RAG' : 'semantic RAG'}</Badge>
        <Badge tone={r.risk === 'low' ? 'ok' : r.risk === 'none' ? 'neutral' : 'warn'}>hallucination risk: {r.risk}</Badge>
        <span className="muted small">{(r.timings_ms.total / 1000).toFixed(1)}s</span>
      </div>
      {r.message && <Notice tone="warn">{r.message}</Notice>}
      {r.short_answer && (
        <div className="answer-short"><span className="muted small">In one line</span><p>{r.short_answer.text} {r.short_answer.cites.map((c) => <Cite key={c} id={c} sources={sources} />)}</p></div>
      )}
      {r.lines.length > 0 && (
        <div className="stack">
          <span className="muted small">In plain English, every line with its source</span>
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

function Compare({ plain, semantic }) {
  const col = (title, run) => {
    const r = run?.final
    const docs = r ? [...new Set(r.sources.flatMap((s) => s.documents))] : []
    return (
      <div className="card stack">
        <h3 style={{ margin: 0 }}>{title}</h3>
        {!r ? <span className="muted">{run?.busy ? 'Running...' : 'Not run'}</span> : (
          <>
            <div className="row"><Badge tone={r.status === 'answered' ? 'ok' : 'warn'}>{r.status.replace(/_/g, ' ')}</Badge><span className="muted small">{(r.timings_ms.total / 1000).toFixed(1)}s · risk {r.risk}</span></div>
            <p>{r.short_answer ? r.short_answer.text : <span className="muted">No supported answer.</span>}</p>
            <ul className="plain-list small">
              <li>{r.lines.length} supported lines, {r.removed.length} removed</li>
              <li>Cited documents: {docs.length ? docs.join(', ') : 'none'}</li>
              <li>Graph used: {r.sparql ? `yes (SPARQL ${r.sparql.status})` : 'no'}</li>
            </ul>
          </>
        )}
      </div>
    )
  }
  return <div className="two-col">{col('Plain RAG (vector search only)', plain)}{col('Semantic RAG (graph + vectors)', semantic)}</div>
}

export default function Ask() {
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState('semantic')
  const [runs, setRuns] = useState({})
  const [error, setError] = useState(null)
  const abort = useRef(null)
  const { data: status } = useAsync(() => api.l3Status(), [])
  const { data: hist, error: histError, loading: histLoading, reload: reloadHistory } = useAsync(() => api.l3History(), [])

  const runOne = async (m, q) => {
    setRuns((r) => ({ ...r, [m]: { steps: {}, final: null, busy: true, question: q } }))
    await api.l3Ask(q, m, (ev) => {
      setRuns((r) => {
        const cur = r[m] || { steps: {} }
        if (ev.step === 'final') return { ...r, [m]: { ...cur, final: ev.result, busy: false } }
        if (ev.step === 'error') return { ...r, [m]: { ...cur, busy: false, failure: ev.message } }
        return { ...r, [m]: { ...cur, steps: { ...cur.steps, [ev.step]: ev } } }
      })
    }, abort.current?.signal)
    setRuns((r) => ({ ...r, [m]: { ...r[m], busy: false } }))
  }

  const submit = async (e) => {
    e?.preventDefault()
    const q = question.trim()
    if (!q) return
    setError(null)
    abort.current = new AbortController()
    setRuns({})
    try {
      if (mode === 'compare') {
        await runOne('plain', q)
        await runOne('semantic', q)
      } else {
        await runOne(mode, q)
      }
    } catch (err) {
      if (err.name !== 'AbortError') setError(err)
    }
    reloadHistory()
  }
  const pick = (r) => {
    setQuestion(r.question)
    if (['semantic', 'plain'].includes(r.mode)) setMode(r.mode)
  }
  const busy = Object.values(runs).some((r) => r.busy)
  const shown = mode === 'compare' ? 'semantic' : mode
  const run = runs[shown]
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
          <fieldset className="mode-set" disabled={busy}>
            <legend className="sr-only">Retrieval mode</legend>
            {[['semantic', 'Semantic RAG'], ['plain', 'Plain RAG'], ['compare', 'Compare both']].map(([v, l]) => (
              <label key={v}><input type="radio" name="mode" value={v} checked={mode === v} onChange={() => setMode(v)} /> {l}</label>
            ))}
          </fieldset>
          {status?.llm && <span className="muted small">model: <span className="mono">{status.llm.model}</span></span>}
        </div>
      </form>
      {error && <ErrorBox error={error} />}
      {run?.failure && <Notice tone="warn" title="A step failed">{run.failure}</Notice>}
      {run && (
        <>
          <div className="stack">
            <h2>What happened, step by step</h2>
            <p className="muted small">Question: “{run.question}”{mode === 'compare' ? ' · showing the semantic run; the plain run is in the comparison below' : ''}</p>
            {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <Step key={n} n={n} ev={run.steps[n]} />)}
          </div>
          {run.final && <Answer r={run.final} />}
        </>
      )}
      {mode === 'compare' && (runs.plain || runs.semantic) && <Compare plain={runs.plain} semantic={runs.semantic} />}
    </div>
    <Recent runs={hist?.runs} loading={histLoading} error={histError} onPick={pick} disabled={busy} />
    </div>
  )
}
