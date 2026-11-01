import { useState } from 'react'
import { api } from './api'
import { ErrorBox } from './ui'

/** Permanent delete of one document (admin only: render it only when can('delete')). Two clicks, so it is never one slip.
 *  `large` gives the button the same size as the Archive button beside it on the document page; the list keeps the small one. */
export default function DeleteDocument({ uri, onDeleted, large = false }) {
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.deleteDocument(uri)
      onDeleted(uri)
    } catch (e) {
      setError(e)
      setBusy(false)
    }
  }
  // the control sits inside clickable table rows: a click here must not open the document
  return (
    <div className="stack" style={{ gap: 6, justifyItems: 'end', ...(confirm ? { minWidth: 230, maxWidth: 300 } : {}) }} onClick={(e) => e.stopPropagation()}>
      {confirm ? (
        <>
          <span className="muted small" style={{ textAlign: 'right' }}>
            Delete {uri} for good? Its file, text, graph facts and search vectors are removed. This cannot be undone.
          </span>
          <div className="row">
            <button className="btn btn-danger btn-small" disabled={busy} onClick={run}>{busy ? 'Deleting...' : 'Delete permanently'}</button>
            <button className="btn btn-small" disabled={busy} onClick={() => setConfirm(false)}>Cancel</button>
          </div>
        </>
      ) : (
        <button className={`btn btn-danger${large ? '' : ' btn-small'}`} aria-label={`Delete ${uri}`} onClick={() => setConfirm(true)}>Delete</button>
      )}
      <ErrorBox error={error} />
    </div>
  )
}
