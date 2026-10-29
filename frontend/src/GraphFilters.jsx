import { useEffect, useMemo, useRef, useState } from 'react'
import { KIND_STYLE } from './GraphView'

// One entry per filter the Knowledge space screen offers. `key` is also the query parameter of GET /api/l2/graph.
export const FILTERS = [
  { key: 'documents', label: 'Document' },
  { key: 'brand', label: 'Brand', kind: 'Brand' },
  { key: 'indication', label: 'Indication', kind: 'Indication' },
  { key: 'drug', label: 'Drug', kind: 'Drug' },
  { key: 'area', label: 'Therapeutic area', kind: 'TherapeuticArea' },
  { key: 'geography', label: 'Geography', kind: 'Geography' },
  { key: 'relation', label: 'Relationship' },
]
export const EMPTY_FILTERS = Object.fromEntries(FILTERS.map((f) => [f.key, []]))
export const FILTER_KEY_BY_KIND = Object.fromEntries(FILTERS.filter((f) => f.kind).map((f) => [f.kind, f.key]))
export const anyFilter = (filters) => FILTERS.some((f) => filters[f.key].length > 0)
/** Query parameters for the API: each filter as comma-separated ids (empty ones are left out by the client). */
export const filterParams = (filters) => Object.fromEntries(FILTERS.map((f) => [f.key, filters[f.key].join(',')]))

/** What the API offers for one filter, as {id, label, hint}. The API lists only what the signed-in user may read. */
function optionsFor(spec, data) {
  const rows = data?.[spec.key] || []
  if (spec.key === 'documents') return rows.map((d) => ({ id: d.id, label: d.title || d.id, hint: d.id.replace('Watson:', '') }))
  if (spec.key === 'relation') return rows.map((r) => ({ id: r.name, label: r.name, hint: `${r.edges} edges` }))
  return rows.map((e) => ({ id: e.id, label: e.label, hint: `${e.documents} doc${e.documents === 1 ? '' : 's'}` }))
}

function MultiPick({ spec, options, value, onChange }) {
  const [q, setQ] = useState('')
  const shown = options.filter((o) => !q || `${o.label} ${o.hint}`.toLowerCase().includes(q.toLowerCase()))
  const toggle = (id) => onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id])
  const box = useRef(null)
  const close = () => {
    if (box.current) box.current.open = false
    setQ('')
  }
  // an open picker closes on a click outside it or on Escape, so the choice does not stay in the way
  useEffect(() => {
    const away = (e) => { if (box.current?.open && !box.current.contains(e.target)) close() }
    const esc = (e) => { if (e.key === 'Escape' && box.current?.open) { close(); box.current.querySelector('summary')?.focus() } }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [])
  return (
    <details className="pick" name="graph-filter" ref={box}>
      <summary className={value.length ? 'pick-on' : ''}>
        {spec.label}
        {value.length ? ` (${value.length})` : ''}
      </summary>
      <div className="pick-body">
        <input type="search" aria-label={`Search ${spec.label}`} placeholder={`Search ${options.length}`} value={q} onChange={(e) => setQ(e.target.value)} />
        <ul className="pick-list">
          {shown.map((o) => (
            <li key={o.id}>
              <label className="check">
                <input type="checkbox" checked={value.includes(o.id)} onChange={() => toggle(o.id)} />
                <span>{o.label}</span> <span className="doc-sub">{o.hint}</span>
              </label>
            </li>
          ))}
          {shown.length === 0 && <li className="muted">{options.length ? 'Nothing matches' : 'Nothing to choose from'}</li>}
        </ul>
        <div className="row">
          <button type="button" className="btn btn-small" onClick={close}>Done</button>
          {value.length > 0 && (
            <button type="button" className="btn btn-small" onClick={() => onChange([])}>
              Clear {spec.label.toLowerCase()}
            </button>
          )}
        </div>
      </div>
    </details>
  )
}

/** The filter bar of the Knowledge space screen: one picker per filter, the chosen values as removable chips, and a clear button. */
export function FilterBar({ options, filters, setFilters }) {
  const lookup = useMemo(() => {
    const out = {}
    FILTERS.forEach((spec) => {
      out[spec.key] = Object.fromEntries(optionsFor(spec, options).map((o) => [o.id, o]))
    })
    return out
  }, [options])
  const set = (key, value) => setFilters((f) => ({ ...f, [key]: value }))
  const chips = FILTERS.flatMap((spec) => filters[spec.key].map((id) => ({ spec, id, label: lookup[spec.key][id]?.label || id })))
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="filter-bar" role="group" aria-label="Graph filters">
        <strong>Filter by</strong>
        {FILTERS.map((spec) => (
          <MultiPick key={spec.key} spec={spec} options={optionsFor(spec, options)} value={filters[spec.key]} onChange={(v) => set(spec.key, v)} />
        ))}
        {chips.length > 0 && (
          <button type="button" className="btn btn-small" onClick={() => setFilters(EMPTY_FILTERS)}>
            Clear all filters
          </button>
        )}
      </div>
      {chips.length > 0 && (
        <div className="filter-chips">
          {chips.map((c) => (
            <span key={`${c.spec.key}:${c.id}`} className="badge badge-info">
              {c.spec.label}: {c.label}{' '}
              <button type="button" className="chip-x" aria-label={`Remove ${c.spec.label} ${c.label}`} onClick={() => set(c.spec.key, filters[c.spec.key].filter((v) => v !== c.id))}>
                x
              </button>
            </span>
          ))}
        </div>
      )}
      <p className="doc-sub" style={{ margin: 0 }}>
        Values inside one filter are alternatives (brand A or brand B); different filters all have to hold (brand A and area X). The options are only
        what the documents you may read contain.
      </p>
    </div>
  )
}

export const kindLabel = (kind) => KIND_STYLE[kind]?.label || kind || ''
