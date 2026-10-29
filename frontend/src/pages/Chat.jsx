import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useChat } from '../chat'
import { Badge, ErrorBox, Notice, useAsync } from '../ui'
import { Lineage, STEP_TITLES, Step } from './Ask'

const Chip = ({ id, sources }) => <span className="cite" title={sources?.find((s) => s.id === id)?.label || id}>{id}</span>

function RunAnswer({ run }) {
  const r = run.final
  const busy = run.busy
  const done = Object.keys(run.steps || {}).map(Number).filter(Number.isFinite)
  const current = done.length ? Math.max(...done) : 0
  return (
    <div className="stack" style={{ gap: 10 }}>
      {run.failure && <Notice tone="warn" title="A step failed">{run.failure}</Notice>}
      {busy && (
        <div className="msg-progress" role="status">
          <span className="spinner" aria-hidden="true" />
          <span>{current ? `Step ${current} of 8: ${STEP_TITLES[current]}` : 'Starting...'}</span>
        </div>
      )}
      {r && (
        <div className="stack" aria-live="polite" style={{ gap: 10 }}>
          {r.message && <Notice tone="warn">{r.message}</Notice>}
          {r.lines.map((l, i) => (
            <p key={i} className="msg-line">
              {l.text} {l.cites.map((c) => <Chip key={c} id={c} sources={r.sources} />)}
              {l.verdict === 'weak' && <Badge tone="warn" title={`score ${l.score}`}>weakly supported</Badge>}
              {l.attributed && <Badge tone="muted" title="The model gave no citation; the closest evidence was attached because it supports the sentence">source attached by checker</Badge>}
            </p>
          ))}
          {!r.lines.length && r.short_answer && (
            <p className="msg-line">{r.short_answer.text} {r.short_answer.cites.map((c) => <Chip key={c} id={c} sources={r.sources} />)}</p>
          )}
          {!r.lines.length && !r.short_answer && <p className="muted">Watson found no supported answer to this question in the documents you may read.</p>}
          <div className="row small muted">
            <Badge tone={r.status === 'answered' ? 'ok' : 'warn'}>{r.status.replace(/_/g, ' ')}</Badge>
            <span>{r.mode === 'plain' ? 'plain RAG' : 'semantic RAG'}</span>
            <span>hallucination risk: {r.risk}</span>
            <span>{(r.timings_ms.total / 1000).toFixed(1)}s</span>
          </div>
          {r.sources.length > 0 && (
            <details className="msg-more">
              <summary>Sources ({r.sources.length})</summary>
              <p className="muted small">Only evidence that a surviving line cites is listed. {r.withheld_documents} document(s) outside your access were never read.</p>
              {r.sources.map((s) => (
                <div key={s.id} className="src-card">
                  <div className="row"><Badge tone="info">{s.id}</Badge><Badge tone="neutral">{s.kind}</Badge><span className="mono small">{s.label}</span></div>
                  <p>{s.text}</p>
                </div>
              ))}
              {r.documents.map((l) => <Lineage key={l.document_uri} l={l} />)}
            </details>
          )}
          {r.removed.length > 0 && (
            <details className="msg-more">
              <summary>{r.removed.length} line(s) removed by the hallucination check</summary>
              {r.removed.map((x, i) => <div key={i} className="ev-item"><p className="strike">{x.text}</p><p className="muted small">Removed because {x.why}.</p></div>)}
            </details>
          )}
        </div>
      )}
      <details className="msg-more" open={busy}>
        <summary>How Watson worked this out</summary>
        <div className="stack" style={{ marginTop: 8 }}>{[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <Step key={n} n={n} ev={run.steps[n]} />)}</div>
      </details>
    </div>
  )
}

// The complete answer is every checked line with its sources; Compare mode shows the plain and the semantic answer in full, one after the other.
function Reply({ turn }) {
  const modes = turn.mode === 'compare' ? ['plain', 'semantic'] : [turn.mode]
  return (
    <div className="msg msg-bot">
      <span className="msg-avatar" aria-hidden="true">W</span>
      <div className="msg-body">
        {turn.error && <ErrorBox error={turn.error} />}
        {modes.map((m) => turn.runs[m] && (
          <div key={m} className="stack" style={{ gap: 10 }}>
            {modes.length > 1 && <strong>{m === 'plain' ? 'Plain RAG' : 'Semantic RAG'}</strong>}
            <RunAnswer run={turn.runs[m]} />
          </div>
        ))}
      </div>
    </div>
  )
}

const MODES = [['semantic', 'Semantic RAG'], ['plain', 'Plain RAG'], ['compare', 'Compare both']]

function Composer() {
  const { ask, busy, stop, mode, setMode } = useChat()
  const [text, setText] = useState('')
  const [menu, setMenu] = useState(false)
  const field = useRef(null)
  const box = useRef(null)
  const { data: status } = useAsync(() => api.l3Status(), [])

  useEffect(() => {
    const away = (e) => { if (menu && box.current && !box.current.contains(e.target)) setMenu(false) }
    const esc = (e) => { if (e.key === 'Escape') setMenu(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [menu])

  const grow = () => {
    const el = field.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }
  const send = (e) => {
    e?.preventDefault()
    if (!text.trim() || busy) return
    ask(text)
    setText('')
    requestAnimationFrame(grow)
  }

  return (
    <div className="chat-input-wrap">
      {status && !status.llm.available && (
        <Notice tone="warn" title="Local language model not ready">{status.llm.message} Answers are quoted from the evidence instead of written by the model.</Notice>
      )}
      <form className="chat-input" onSubmit={send}>
        <label htmlFor="chat-q" className="sr-only">Your question</label>
        <textarea
          id="chat-q" ref={field} rows={1} maxLength={600} value={text} placeholder="Ask Watson about your research"
          onChange={(e) => { setText(e.target.value); grow() }}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(e) }}
        />
        <div className="chat-input-tools" ref={box}>
          <button type="button" className="chat-tool" onClick={() => setMenu((m) => !m)} aria-haspopup="menu" aria-expanded={menu} aria-label="Answer settings" title="Answer settings" disabled={busy}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6" /></svg>
          </button>
          {menu && (
            <fieldset className="chat-menu">
              <legend>Retrieval mode</legend>
              {MODES.map(([v, l]) => (
                <label key={v}><input type="radio" name="chat-mode" value={v} checked={mode === v} onChange={() => { setMode(v); setMenu(false) }} /> {l}</label>
              ))}
            </fieldset>
          )}
          {busy ? (
            <button type="button" className="chat-send chat-stop" onClick={stop} aria-label="Stop" title="Stop">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>
            </button>
          ) : (
            <button type="submit" className="chat-send" disabled={!text.trim()} aria-label="Send" title="Send">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            </button>
          )}
        </div>
      </form>
      <p className="chat-note">Watson answers each question on its own, from the documents your role may read.</p>
    </div>
  )
}

export default function Chat() {
  const { thread } = useChat()
  const scroller = useRef(null)
  const stick = useRef(true)

  useEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [thread])

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scroller} onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120 }}>
        <div className="chat-col">
          {thread.length === 0 ? (
            <div className="chat-empty">
              <span className="chat-empty-mark" aria-hidden="true">W</span>
              <h1>What do you want to know?</h1>
              <p className="muted">Ask in plain English. Every line of the answer names the document it came from.</p>
            </div>
          ) : (
            thread.map((t) => (
              <div key={t.id} className="turn">
                <div className="msg msg-user"><p>{t.question}</p></div>
                <Reply turn={t} />
              </div>
            ))
          )}
        </div>
      </div>
      <Composer />
    </div>
  )
}
