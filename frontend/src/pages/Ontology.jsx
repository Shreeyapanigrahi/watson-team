import { useMemo, useState } from 'react'
import { api } from '../api'
import { Badge, ErrorBox, Loading, Tabs, useAsync } from '../ui'

const TONE = { grounded: 'ok', partial: 'warn', extension: 'info', unknown: 'bad' }

const list = (v) => (Array.isArray(v) ? v.join(', ') : String(v))

function Tree({ classes }) {
  const children = useMemo(() => {
    const m = {}
    classes.forEach((c) => (c.parents.length ? c.parents : ['']).forEach((p) => (m[p] = [...(m[p] || []), c])))
    return m
  }, [classes])
  const ids = new Set(classes.map((c) => c.id))
  const roots = classes.filter((c) => !c.parents.some((p) => ids.has(p)))
  const render = (c, depth) => (
    <li key={c.id + depth}>
      <span title={c.comment}><strong>{c.label}</strong></span>{' '}
      <Badge tone={TONE[c.grounding_status]} title={c.grounding_note}>{c.grounding_status}</Badge>
      {c.equivalent.length > 0 && <span className="mono"> = {c.equivalent.join(', ')}</span>}
      {(children[c.id] || []).length > 0 && <ul className="plain" style={{ marginLeft: 18 }}>{children[c.id].map((k) => render(k, depth + 1))}</ul>}
    </li>
  )
  return <ul className="plain">{roots.map((c) => render(c, 0))}</ul>
}

function PropTable({ rows }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Property</th><th>Domain</th><th>Range</th><th>Characteristics</th><th>Grounding</th></tr></thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id}>
              <td title={p.comment}><strong>{p.label}</strong></td>
              <td>{p.domain.length ? p.domain.join(', ') : <span className="muted">any</span>}</td>
              <td>{p.range.length ? p.range.join(', ') : <span className="muted">any</span>}</td>
              <td>
                {[...p.characteristics, ...(p.inverse_of.length ? [`inverse of ${p.inverse_of.join(', ')}`] : []),
                  ...(p.sub_property_of.length ? [`sub-property of ${p.sub_property_of.join(', ')}`] : [])].join('; ') || <span className="muted">-</span>}
              </td>
              <td><Badge tone={TONE[p.grounding_status]} title={p.grounding_note}>{p.grounding_status}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Ontology() {
  const o = useAsync(() => api.l2Ontology(), [])
  const [tab, setTab] = useState('classes')
  if (o.loading && !o.data) return <Loading />
  if (o.error) return <ErrorBox error={o.error} onRetry={o.reload} />
  const d = o.data
  const gs = d.grounding_summary
  const failed = d.checks.filter((c) => !c.ok)

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Ontology</h1>
          <p>
            The schema the knowledge graph follows: {d.classes.length} classes, {d.object_properties.length} relationships and {d.datatype_properties.length} attributes.
            Each term shows whether the data catalog holds it. Hover a name for its definition.
          </p>
          <p className="doc-sub mono">{d.namespace} · catalog {d.catalog_version}</p>
        </div>
      </div>

      <div className="card">
        <h3>Checks against the brief's minimums {failed.length === 0 ? <Badge tone="ok">all {d.checks.length} pass</Badge> : <Badge tone="bad">{failed.length} fail</Badge>}</h3>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Requirement</th><th>Found</th><th /></tr></thead>
            <tbody>
              {d.checks.map((c) => (
                <tr key={c.check}>
                  <td>{c.check}</td>
                  <td>{Array.isArray(c.value) ? (c.value.length ? `${c.value.length} missing: ${list(c.value)}` : 'all present') : c.value}</td>
                  <td><Badge tone={c.ok ? 'ok' : 'bad'}>{c.ok ? 'pass' : 'fail'}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="doc-sub" style={{ marginTop: 8 }}>
          Grounding of all {d.terms} terms: {gs.grounded} grounded, {gs.partial} partial, {gs.extension} extension (the catalog has no counterpart), {gs.unknown} unknown.
          "Extension" is expected for documents, chunks, sentiment and lineage, which a pharma data model does not describe.
        </p>
      </div>

      <Tabs
        tabs={[{ id: 'classes', label: 'Classes', count: d.classes.length }, { id: 'objects', label: 'Relationships', count: d.object_properties.length },
          { id: 'data', label: 'Attributes', count: d.datatype_properties.length }, { id: 'align', label: 'Alignments' }]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'classes' && <div className="card"><Tree classes={d.classes} /></div>}
      {tab === 'objects' && (
        <div className="card">
          <p className="muted">Inverse pairs: {d.inverse_pairs.map((p) => p.join(' / ')).join('; ')}. Transitive: {d.transitive.join(', ')}.</p>
          <PropTable rows={d.object_properties} />
        </div>
      )}
      {tab === 'data' && <div className="card"><PropTable rows={d.datatype_properties} /></div>}
      {tab === 'align' && (
        <div className="card">
          <h3>Class level</h3>
          <ul className="plain">
            {d.alignments['owl:equivalentClass'].map((a) => <li key={a.term}><strong>{a.term}</strong> <span className="muted">owl:equivalentClass</span> <span className="mono">{a.target}</span></li>)}
          </ul>
          <h3>Individual level</h3>
          <ul className="plain">
            {Object.entries(d.alignments.individual_level).map(([k, v]) => <li key={k}>{k}: <strong>{v}</strong></li>)}
          </ul>
          <p className="muted">
            Disease IDs are exact matches because the label or a synonym is identical. Therapeutic areas are close matches because a business grouping
            such as "Oncology" overlaps a MeSH heading without being the same thing. Catalog grounding uses its own link (groundedIn), not an
            equivalence claim, because a catalog table is a storage structure, not the same concept.
          </p>
        </div>
      )}
    </div>
  )
}
