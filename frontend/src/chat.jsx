import { createContext, useContext, useRef, useState } from 'react'
import { api } from './api'
import { useAsync } from './ui'

const Ctx = createContext(null)
export const useChat = () => useContext(Ctx)

/** The viewer's conversation with Watson, shared by the sidebar (New chat, Recent) and the chat page.
 *  Each turn holds one question and the run(s) of the Layer 3 pipeline for it. Watson answers every question on its own,
 *  so a thread is a list of independent questions, not a dialogue with memory. */
export function ChatProvider({ children }) {
  const [thread, setThread] = useState([])
  const [mode, setMode] = useState('semantic')
  const abort = useRef(null)
  const hist = useAsync(() => api.l3History(), [])

  const patchRun = (id, m, fn) =>
    setThread((th) => th.map((t) => (t.id === id ? { ...t, runs: { ...t.runs, [m]: fn(t.runs[m] || { steps: {} }) } } : t)))

  const runOne = async (id, m, q) => {
    patchRun(id, m, () => ({ steps: {}, final: null, busy: true }))
    try {
      await api.l3Ask(q, m, (ev) => {
        patchRun(id, m, (cur) => {
          if (ev.step === 'final') return { ...cur, final: ev.result, busy: false }
          if (ev.step === 'error') return { ...cur, busy: false, failure: ev.message }
          return { ...cur, steps: { ...cur.steps, [ev.step]: ev } }
        })
      }, abort.current?.signal)
    } finally {
      patchRun(id, m, (cur) => ({ ...cur, busy: false }))
    }
  }

  const ask = async (question, useMode) => {
    const q = question.trim()
    const m = useMode || mode
    if (!q) return
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    abort.current = new AbortController()
    setThread((th) => [...th, { id, question: q, mode: m, runs: {}, error: null }])
    try {
      if (m === 'compare') {
        await runOne(id, 'plain', q)
        await runOne(id, 'semantic', q)
      } else {
        await runOne(id, m, q)
      }
    } catch (err) {
      if (err.name !== 'AbortError') setThread((th) => th.map((t) => (t.id === id ? { ...t, error: err } : t)))
    }
    hist.reload()
  }

  const reset = () => {
    abort.current?.abort()
    setThread([])
  }
  const stop = () => abort.current?.abort()
  const busy = thread.some((t) => Object.values(t.runs).some((r) => r.busy))

  return <Ctx.Provider value={{ thread, mode, setMode, busy, ask, reset, stop, hist }}>{children}</Ctx.Provider>
}
