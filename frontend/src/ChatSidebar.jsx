import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from './api'
import { useChat } from './chat'

const Icon = ({ d }) => (
  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
)

/** "New chat": clears the thread and goes to the home page, which is the chat. */
export function NewChat() {
  const { reset } = useChat()
  const navigate = useNavigate()
  return (
    <button type="button" className="side-new" onClick={() => { reset(); navigate('/') }}>
      <Icon d="M12 5v14M5 12h14" /> New chat
    </button>
  )
}

/** The viewer's past questions, newest first. Clicking one starts a new chat and asks it again (the history keeps the question,
 *  status and the one-line answer, not the full cited answer, so the full answer is produced again).
 *  Each entry has a delete button, and "Clear all" removes the whole list; both delete only this user's own entries. */
export function RecentList() {
  const { hist, ask, reset } = useChat()
  const navigate = useNavigate()
  const [failed, setFailed] = useState(null)
  const runs = hist.data?.runs
  const pick = (r) => {
    reset()
    navigate('/')
    ask(r.question)
  }
  const run = async (fn) => {
    setFailed(null)
    try {
      await fn()
    } catch (e) {
      setFailed(e.message)
    }
    hist.reload()
  }
  const clearAll = () => {
    if (window.confirm('Delete all your recent questions? This cannot be undone.')) run(() => api.l3ClearHistory())
  }
  return (
    <section className="side-recent" aria-label="Recent questions">
      <div className="side-recent-head">
        <div className="nav-section">Recent</div>
        {runs?.length > 0 && <button type="button" className="side-clear" onClick={clearAll}>Clear all</button>}
      </div>
      {hist.error && <p className="side-note">Could not load history: {hist.error.message}</p>}
      {failed && <p className="side-note" role="alert">Could not delete: {failed}</p>}
      {!hist.error && hist.loading && !runs && <p className="side-note">Loading...</p>}
      {runs && runs.length === 0 && <p className="side-note">Your questions will be listed here.</p>}
      <ul className="side-recent-list">
        {runs?.map((r) => (
          <li key={r.id} className="side-recent-row">
            <button type="button" className="side-recent-item" onClick={() => pick(r)} title={`${r.question}\n${String(r.status).replace(/_/g, ' ')}${r.short_answer ? ` · ${r.short_answer}` : ''}\n${new Date(r.time).toLocaleString()}\nClick to ask it again`}>
              {r.question}
            </button>
            <button type="button" className="side-recent-del" onClick={() => run(() => api.l3DeleteRun(r.id))} aria-label={`Delete: ${r.question}`} title="Delete this question">
              <Icon d="M6 6l12 12M18 6L6 18" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
