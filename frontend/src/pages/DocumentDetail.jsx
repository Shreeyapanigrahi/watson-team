import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import {
  Badge, Empty, ErrorBox, KV, Loading, Notice, ScoreBar, SensitivityBadge, StateBadge, Tabs, fmtDate, fmtDateTime, useAsync,
} from '../ui'

export default function DocumentDetail() {
  const { uri } = useParams()
  const { can } = useAuth()
  const doc = useAsync(() => api.document(uri), [uri])
  const [tab, setTab] = useState('overview')
  const [flash, setFlash] = useState(null)

  useEffect(() => {
    setTab('overview')
    setFlash(null)
  }, [uri])

  if (doc.loading && !doc.data) return <Loading />
  if (doc.error) return (
    <div className="stack">
      <Link to="/documents">Back to documents</Link>
      <ErrorBox error={doc.error} onRetry={doc.reload} />
    </div>
  )
  const d = doc.data
  if (d.view_level === 'summary') return <Summary d={d} />

  const title = d.metadata.extracted_title || d.source_filename
  const done = (msg) => {
    setFlash(msg)
    doc.reload()
  }

  return (
    <div className="stack">
      <div>
        <Link to="/documents">Documents</Link> <span className="muted">/ {d.document_uri}</span>
      </div>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <div className="row">
            <h1>{title}</h1>
            <StateBadge state={d.state} />
            {d.access && <SensitivityBadge level={d.access.sensitivity_level} />}
          </div>
          <p className="doc-sub">
            {d.document_uri} · {d.source_filename} · uploaded {fmtDateTime(d.created_at)} by {d.uploaded_by}
          </p>
        </div>
        <Lifecycle d={d} can={can} onDone={done} />
      </div>

      {flash && <Notice tone="ok">{flash}</Notice>}
      {d.state === 'REJECTED' && <Notice tone="bad" title="Rejected">{d.rejection_reason}</Notice>}
      {d.reviewer_notes.length > 0 && d.state !== 'ACTIVE' && (
        <Notice tone="warn" title="Notes for the reviewer">
          <ul className="plain">{d.reviewer_notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </Notice>
      )}

      {d.state === 'UNDER_REVIEW' && can('review') && <ReviewPanel d={d} onDone={done} />}

      <Tabs
        tabs={[
          { id: 'overview', label: 'Overview' },
          { id: 'checks', label: 'Checks' },
          { id: 'content', label: 'Content' },
          { id: 'history', label: 'History' },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'overview' && <Overview d={d} />}
      {tab === 'checks' && <Checks d={d} />}
      {tab === 'content' && <Content uri={d.document_uri} />}
      {tab === 'history' && <History d={d} can={can} />}
    </div>
  )
}

function Summary({ d }) {
  return (
    <div className="stack" style={{ maxWidth: 760 }}>
      <Link to="/documents">Documents</Link>
      <div className="row">
        <h1>{d.source_filename}</h1>
        <StateBadge state={d.state} />
      </div>
      <Notice tone="info" title="Status only">
        You uploaded this file. Its content and metadata are visible to reviewers and to users whose access attributes permit it, once it
        is approved.
      </Notice>
      {d.state === 'REJECTED' && <Notice tone="bad" title="Rejected">{d.rejection_reason}</Notice>}
      {d.state === 'UNDER_REVIEW' && <Notice tone="warn" title="Waiting for review">A reviewer will approve or reject this document.</Notice>}
      <div className="card">
        <KV
          rows={[
            ['Document', d.document_uri],
            ['Uploaded', fmtDateTime(d.created_at)],
            ['Duplicate check', d.assessment.dedup?.verdict?.replace('_', ' ')],
            ['PII scan', d.assessment.pii ? (d.assessment.pii.pii_detected ? `flagged: ${d.assessment.pii.pii_types.join(', ')}` : 'none found') : null],
            ['Parse warnings', d.assessment.parse_warnings.length ? d.assessment.parse_warnings.join(' | ') : 'none'],
          ]}
        />
      </div>
    </div>
  )
}

function Lifecycle({ d, can, onDone }) {
  const [confirm, setConfirm] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const act = async (kind) => {
    setBusy(true)
    setError(null)
    try {
      await (kind === 'archive' ? api.archive(d.document_uri) : api.reinstate(d.document_uri))
      setConfirm(null)
      onDone(kind === 'archive' ? 'Document archived. It is no longer current.' : 'Document reinstated and ACTIVE again.')
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  const canArchive = d.state === 'ACTIVE' && can('archive')
  const canReinstate = d.state === 'ARCHIVED' && can('reinstate')
  if (!canArchive && !canReinstate) return null
  const kind = canArchive ? 'archive' : 'reinstate'
  return (
    <div className="stack" style={{ gap: 8, justifyItems: 'end' }}>
      {confirm ? (
        <div className="row">
          <span className="muted">{kind === 'archive' ? 'Archive this document?' : 'Reinstate this document?'}</span>
          <button className="btn btn-danger btn-small" disabled={busy} onClick={() => act(kind)}>Confirm</button>
          <button className="btn btn-small" onClick={() => setConfirm(null)}>Cancel</button>
        </div>
      ) : (
        <button className="btn" onClick={() => setConfirm(kind)}>{kind === 'archive' ? 'Archive' : 'Reinstate'}</button>
      )}
      <ErrorBox error={error} />
    </div>
  )
}

/* ---------------------------------------------------------------- review */
function ReviewPanel({ d, onDone }) {
  const { meta } = useAuth()
  const m = d.metadata
  const dedup = d.assessment.dedup
  const pii = d.assessment.pii
  const [mode, setMode] = useState('approve')
  const [sens, setSens] = useState('')
  const [tas, setTas] = useState(m.therapeutic_area ? [m.therapeutic_area] : [])
  const [geos, setGeos] = useState(m.geography && m.geography !== 'GLOBAL' ? [m.geography] : [])
  const [studyType, setStudyType] = useState(m.study_type || '')
  const [ta, setTa] = useState(m.therapeutic_area || '')
  const [geography, setGeography] = useState(m.geography || '')
  const [title, setTitle] = useState(m.extracted_title || '')
  const [notes, setNotes] = useState('')
  const [expiry, setExpiry] = useState('')
  const [piiOverride, setPiiOverride] = useState(false)
  const [piiReason, setPiiReason] = useState('')
  const [archiveOld, setArchiveOld] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const toggle = (list, set, v) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  const retention = meta.study_types.find((s) => s.name === studyType)?.retention_days
  const noText = m.word_count === 0

  const approve = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.review(d.document_uri, {
        approved: true,
        sensitivity_level: sens || null,
        allowed_therapeutic_areas: tas,
        allowed_geographies: geos,
        metadata_updates: { study_type: studyType, therapeutic_area: ta, geography, extracted_title: title },
        notes: notes || null,
        expiry_date: expiry || null,
        pii_override: piiOverride,
        pii_override_reason: piiOverride ? piiReason : null,
        archive_superseded: archiveOld,
      })
      onDone('Approved. The document is ACTIVE, with its access attributes, expiry date and quality score written.')
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  const reject = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.review(d.document_uri, { approved: false, reason, notes: notes || null })
      onDone('Rejected. The document will never be indexed.')
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card stack">
      <div className="row spread">
        <h2>Review decision</h2>
        <div className="row">
          <button className={`btn btn-small ${mode === 'approve' ? 'btn-primary' : ''}`} onClick={() => setMode('approve')}>Approve</button>
          <button className={`btn btn-small ${mode === 'reject' ? 'btn-danger' : ''}`} onClick={() => setMode('reject')}>Reject</button>
        </div>
      </div>

      {dedup.verdict === 'near_duplicate' && (
        <Notice tone="warn" title="Possible near duplicate">
          {Math.round(dedup.similarity * 1000) / 10}% similar to <Link to={`/documents/${encodeURIComponent(dedup.original_uri)}`}>{dedup.original_uri}</Link>. Reject this
          one if it adds nothing.
        </Notice>
      )}
      {dedup.verdict === 'new_version' && (
        <Notice tone="info" title="Possible new version">
          Same title and author as <Link to={`/documents/${encodeURIComponent(dedup.original_uri)}`}>{dedup.original_uri}</Link>, with a later date.
        </Notice>
      )}
      {pii?.pii_detected && (
        <Notice tone="bad" title="PII detected">
          Found {pii.pii_types.join(', ')} in {pii.flagged_chunks} chunk(s). See the Checks tab. The safe choice is to reject. To approve
          anyway you must tick the override and give a reason.
        </Notice>
      )}
      {noText && <Notice tone="bad" title="No extractable text">This file has no text layer (likely a scan), so it cannot be approved. Reject it, or upload a text version.</Notice>}

      {mode === 'approve' ? (
        <div className="form">
          <div className="field-row">
            <div className="field">
              <label htmlFor="sens">Sensitivity level <span className="hint">Who may read it. Required.</span></label>
              <select id="sens" value={sens} onChange={(e) => setSens(e.target.value)}>
                <option value="">Choose...</option>
                {meta.sensitivity_levels.map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="stype">Study type <span className="hint">Suggested by the classifier</span></label>
              <select id="stype" value={studyType} onChange={(e) => setStudyType(e.target.value)}>
                <option value="">Choose...</option>
                {meta.study_types.map((s) => <option key={s.name}>{s.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ta">Therapeutic area</label>
              <select id="ta" value={ta} onChange={(e) => { setTa(e.target.value); setTas(e.target.value ? [e.target.value] : []) }}>
                <option value="">Choose...</option>
                {meta.therapeutic_areas.map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="geo">Region</label>
              <select id="geo" value={geography} onChange={(e) => { setGeography(e.target.value); setGeos(e.target.value && e.target.value !== 'GLOBAL' ? [e.target.value] : []) }}>
                <option value="">Choose...</option>
                {meta.geography_values.map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <span className="label">Readable by these areas <span className="hint">None ticked means all areas</span></span>
              <div className="checks">
                {meta.therapeutic_areas.map((t) => (
                  <label key={t} className="check"><input type="checkbox" checked={tas.includes(t)} onChange={() => toggle(tas, setTas, t)} />{t}</label>
                ))}
              </div>
            </div>
            <div className="field">
              <span className="label">Readable in these regions <span className="hint">None ticked means all regions</span></span>
              <div className="checks">
                {meta.regions.map((t) => (
                  <label key={t} className="check"><input type="checkbox" checked={geos.includes(t)} onChange={() => toggle(geos, setGeos, t)} />{t}</label>
                ))}
              </div>
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label htmlFor="title">Title {!m.extracted_title && <span className="hint">None found. Please set one.</span>}</label>
              <input id="title" type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="expiry">Expiry date <span className="hint">{retention ? `Leave empty for the default: ingest date + ${retention} days` : 'Leave empty for the default'}</span></label>
              <input id="expiry" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
            </div>
          </div>

          {dedup.verdict === 'new_version' && (
            <label className="check"><input type="checkbox" checked={archiveOld} onChange={(e) => setArchiveOld(e.target.checked)} />Archive {dedup.original_uri} when this one is approved</label>
          )}
          {pii?.pii_detected && (
            <div className="field">
              <label className="check"><input type="checkbox" checked={piiOverride} onChange={(e) => setPiiOverride(e.target.checked)} /><strong>Override the PII flag</strong></label>
              {piiOverride && <input type="text" placeholder="Why is this safe to approve? (required)" value={piiReason} onChange={(e) => setPiiReason(e.target.value)} aria-label="Override reason" />}
            </div>
          )}
          <div className="field">
            <label htmlFor="notes">Reviewer notes</label>
            <textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="e.g. Verified. No PII." />
          </div>
          <ErrorBox error={error} />
          <div className="sticky-actions">
            <button className="btn btn-primary" disabled={busy || noText || !sens} onClick={approve}>
              {busy ? 'Saving...' : 'Approve and activate'}
            </button>
            {!sens && <span className="muted" style={{ marginLeft: 10 }}>Choose a sensitivity level first.</span>}
          </div>
        </div>
      ) : (
        <div className="form">
          <div className="field">
            <label htmlFor="reason">Reason for rejection <span className="hint">Required. Recorded in the audit log.</span></label>
            <textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. PII detected - named patient identified" />
          </div>
          <ErrorBox error={error} />
          <div><button className="btn btn-danger" disabled={busy || !reason.trim()} onClick={reject}>{busy ? 'Saving...' : 'Reject document'}</button></div>
        </div>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- tabs */
function Overview({ d }) {
  const m = d.metadata
  const src = d.metadata_sources || {}
  const q = d.quality
  return (
    <div className="grid-2">
      <div className="card">
        <h2>Metadata record</h2>
        <KV
          rows={[
            ['Title', m.extracted_title, src.extracted_title],
            ['Author', m.extracted_author, src.extracted_author],
            ['Study type', m.study_type, src.study_type],
            ['Therapeutic area', m.therapeutic_area, src.therapeutic_area],
            ['Region', m.geography, src.geography],
            ['Brands', m.brands_mentioned?.join(', '), 'NER'],
            ['Indications', m.indications_mentioned?.join(', '), 'NER'],
            ['Language', m.language, src.language],
            ['Format', m.format],
            ['Words', m.word_count],
            ['Ingested', fmtDateTime(m.ingest_timestamp)],
            ['Report date', fmtDate(d.extracted_date)],
          ]}
        />
      </div>
      <div className="stack">
        <div className="card">
          <h2>Access and retention</h2>
          {d.access ? (
            <KV
              rows={[
                ['Sensitivity', <SensitivityBadge level={d.access.sensitivity_level} />],
                ['Therapeutic areas', d.access.allowed_therapeutic_areas.length ? d.access.allowed_therapeutic_areas.join(', ') : 'All'],
                ['Regions', d.access.allowed_geographies.length ? d.access.allowed_geographies.join(', ') : 'All'],
                ['Expires', fmtDate(d.expiry_date)],
                ['Reviewed by', d.review?.reviewer],
                ['Review notes', d.review?.notes],
                ['PII override', d.review?.pii_override ? `Yes: ${d.review.pii_override_reason}` : 'No'],
              ]}
            />
          ) : (
            <p className="muted">Set at review. Not decided yet.</p>
          )}
        </div>
        <div className="card">
          <h2>Quality score {q && <Badge tone="info">{q.quality_score}</Badge>}</h2>
          {q ? (
            <>
              {Object.entries(q.components).map(([k, v]) => (
                <div className="comp" key={k}>
                  <span>{k.replace(/_/g, ' ')} <span className="doc-sub">x {q.weights[k]}</span></span>
                  <ScoreBar value={v} label={k} />
                  <span className="v">{v}</span>
                </div>
              ))}
              <p className="doc-sub" style={{ marginTop: 8 }}>
                Sample size found: {q.evidence.sample_size_found ?? 'none stated'} · unique clinical terms: {q.evidence.unique_clinical_terms}
              </p>
            </>
          ) : (
            <p className="muted">Calculated when the document is approved.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function Checks({ d }) {
  const a = d.assessment
  const dedup = a.dedup
  const pii = a.pii
  return (
    <div className="stack">
      <div className="grid-2">
        <div className="card">
          <h2>Duplicate check</h2>
          <KV
            rows={[
              ['Verdict', <Badge tone={dedup.verdict === 'unique' ? 'ok' : 'warn'}>{dedup.verdict.replace('_', ' ')}</Badge>],
              ['Compared with', dedup.original_uri ? <Link to={`/documents/${encodeURIComponent(dedup.original_uri)}`}>{dedup.original_uri}</Link> : null],
              ['Similarity', dedup.similarity],
              ['Reason', dedup.reason],
            ]}
          />
        </div>
        <div className="card">
          <h2>Parsing and chunking</h2>
          <KV
            rows={[
              ['Chunks', `${a.chunking.chunk_count} (${a.chunking.strategy})`],
              ['Warnings', a.parse_warnings.length ? '' : 'none'],
            ]}
          />
          {a.parse_warnings.length > 0 && <ul className="plain">{a.parse_warnings.map((w, i) => <li key={i} className="doc-sub">{w}</li>)}</ul>}
          {d.metadata_flags?.length > 0 && <p style={{ marginTop: 8 }}>{d.metadata_flags.map((f) => <Badge key={f} tone="warn">{f}</Badge>)}</p>}
        </div>
      </div>
      <div className="card card-flush">
        <div style={{ padding: '16px 18px 0' }}>
          <h2>PII scan {pii.pii_detected ? <Badge tone="bad">flagged</Badge> : <Badge tone="ok">clean</Badge>}</h2>
          <p className="doc-sub" style={{ margin: '4px 0 10px' }}>
            {pii.flagged_chunks} of {pii.total_chunks} chunks flagged. The detector flags; a human decides.
          </p>
        </div>
        {pii.spans.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Type</th><th>Text</th><th>Why</th><th>Chunk</th></tr></thead>
              <tbody>
                {pii.spans.map((s, i) => (
                  <tr key={i}>
                    <td><Badge tone="bad">{s.type}</Badge></td>
                    <td className="mono">{s.text}</td>
                    <td className="muted">{s.reason || 'direct identifier'}</td>
                    <td className="mono">{s.chunk_uri}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function Highlight({ text, spans = [] }) {
  const parts = []
  let pos = 0
  ;[...spans].sort((a, b) => a.start - b.start).forEach((s, i) => {
    if (s.start < pos) return
    parts.push(text.slice(pos, s.start))
    parts.push(<mark key={i} className="pii" title={s.type}>{text.slice(s.start, s.end)}</mark>)
    pos = s.end
  })
  parts.push(text.slice(pos))
  return <>{parts}</>
}

function Content({ uri }) {
  const c = useAsync(() => api.content(uri), [uri])
  if (c.loading && !c.data) return <Loading />
  if (c.error) return <ErrorBox error={c.error} onRetry={c.reload} />
  if (c.data.chunks.length === 0) return <Empty title="No text">This file produced no extractable text.</Empty>
  return (
    <div className="stack">
      <p className="doc-sub">
        {c.data.chunks.length} chunks. Fixed-size chunks overlap by 20%, so a sentence at a boundary appears in both neighbours. Detected PII is highlighted.
      </p>
      {c.data.chunks.map((ch) => (
        <div className="chunk" key={ch.chunk_uri}>
          <div className="chunk-head">
            <span className="mono">{ch.chunk_uri}</span>
            <span>{ch.token_count} tokens</span>
            <span>chars {ch.char_start}-{ch.char_end}</span>
            {ch.pii_detected && <Badge tone="bad">PII</Badge>}
          </div>
          <div className="chunk-text"><Highlight text={ch.text} spans={ch.pii_spans} /></div>
        </div>
      ))}
    </div>
  )
}

function History({ d, can }) {
  const audit = useAsync(() => (can('view_audit') ? api.audit({ document_uri: d.document_uri, last: 200 }) : Promise.resolve(null)), [d.document_uri, d.state])
  return (
    <div className="grid-2">
      <div className="card">
        <h2>State history</h2>
        <div className="timeline">
          {d.history.map((h, i) => (
            <div className="tl-item" key={i}>
              <span className="tl-dot" />
              <div>
                <div>
                  {h.from ? <>{h.from.replace('_', ' ')} to </> : null}<strong>{h.to.replace('_', ' ')}</strong>
                </div>
                <div className="doc-sub">{fmtDateTime(h.timestamp)} · {h.actor}{h.notes ? ` · ${h.notes}` : ''}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
      {can('view_audit') && (
        <div className="card">
          <h2>Audit events</h2>
          {audit.loading && !audit.data && <Loading />}
          <ErrorBox error={audit.error} />
          <div className="timeline">
            {audit.data?.events.map((e, i) => (
              <div className="tl-item" key={i}>
                <span className="tl-dot" />
                <div>
                  <Badge tone="neutral">{e.event_type}</Badge>
                  <div className="doc-sub">{fmtDateTime(e.timestamp)}</div>
                  <div className="doc-sub">{JSON.stringify(e.details).slice(0, 160)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
