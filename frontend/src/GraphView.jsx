import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'

// One look for every graph in the app. Colour AND shape per kind, so the kinds can be told apart without relying on colour.
export const KIND_STYLE = {
  KnowledgeSpace: { color: '#0b6f6a', shape: 'round-rectangle', label: 'Knowledge space' },
  Document: { color: '#5b6e75', shape: 'rectangle', label: 'Document' },
  Brand: { color: '#1d5f9e', shape: 'ellipse', label: 'Brand' },
  Drug: { color: '#7a4fb0', shape: 'diamond', label: 'Drug' },
  DrugClass: { color: '#a0589d', shape: 'round-diamond', label: 'Drug class' },
  Indication: { color: '#b4540a', shape: 'hexagon', label: 'Indication' },
  TherapeuticArea: { color: '#1d7a45', shape: 'star', label: 'Therapeutic area' },
  Geography: { color: '#8a6d00', shape: 'triangle', label: 'Geography' },
}

const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback

export function Legend({ kinds }) {
  return (
    <div className="legend">
      {Object.entries(KIND_STYLE)
        .filter(([k]) => !kinds || kinds.has(k))
        .map(([k, s]) => (
          <span key={k}>
            <span className="dot" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
    </div>
  )
}

/** graph = { nodes: [{id,label,kind,...}], edges: [{id,source,target,label}] }. onSelect(node data | null). */
export default function GraphView({ graph, onSelect, selected, height = 520, showEdgeLabels = false }) {
  const box = useRef(null)
  const cy = useRef(null)

  useEffect(() => {
    const ink = css('--ink', '#13242b')
    const line = css('--muted', '#5b6e75')
    const instance = cytoscape({
      container: box.current,
      elements: [
        ...graph.nodes.map((n) => ({ data: { ...n } })),
        ...graph.edges.map((e) => ({ data: { ...e } })),
      ],
      minZoom: 0.2,
      maxZoom: 3,
      wheelSensitivity: 0.25,
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'font-size': 10,
            color: ink,
            'text-valign': 'bottom',
            'text-margin-y': 4,
            'text-wrap': 'ellipsis',
            'text-max-width': 90,
            width: 22,
            height: 22,
            'background-color': '#5b6e75',
            'border-width': 1,
            'border-color': '#ffffff',
          },
        },
        ...Object.entries(KIND_STYLE).map(([kind, s]) => ({
          selector: `node[kind = "${kind}"]`,
          style: {
            'background-color': s.color,
            shape: s.shape,
            ...(kind === 'KnowledgeSpace' ? { width: 34, height: 26, 'font-size': 11, 'font-weight': 700 } : {}),
            ...(kind === 'Document' ? { width: 16, height: 16 } : {}),
          },
        })),
        {
          selector: 'edge',
          style: {
            width: 1,
            'line-color': line,
            'curve-style': 'bezier',
            opacity: 0.55,
            'target-arrow-shape': 'triangle',
            'target-arrow-color': line,
            'arrow-scale': 0.7,
            ...(showEdgeLabels ? { label: 'data(label)', 'font-size': 8, color: line, 'text-rotation': 'autorotate' } : {}),
          },
        },
        { selector: 'edge[label = "coMentionedWith"]', style: { 'line-style': 'dashed', 'target-arrow-shape': 'none', opacity: 0.8 } },
        { selector: '.faded', style: { opacity: 0.12 } },
        { selector: 'node:selected', style: { 'border-width': 3, 'border-color': css('--accent', '#0b6f6a') } },
      ],
      layout: { name: 'cose', animate: false, nodeRepulsion: 9000, idealEdgeLength: 70, padding: 20, randomize: false, numIter: 800 },
    })
    instance.on('tap', 'node', (evt) => {
      const n = evt.target
      instance.elements().addClass('faded')
      n.closedNeighborhood().removeClass('faded')
      onSelect?.(n.data())
    })
    instance.on('tap', (evt) => {
      if (evt.target === instance) {
        instance.elements().removeClass('faded')
        onSelect?.(null)
      }
    })
    cy.current = instance
    return () => instance.destroy()
    // the graph is rebuilt whenever the data (or the label setting) changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, showEdgeLabels])

  useEffect(() => {
    if (!cy.current) return
    cy.current.nodes().unselect()
    if (selected) cy.current.getElementById(selected).select()
  }, [selected, graph])

  return (
    <div
      ref={box}
      role="img"
      aria-label={`Knowledge graph with ${graph.nodes.length} nodes and ${graph.edges.length} edges. Use the list beside it to read the same items.`}
      style={{ height, width: '100%', border: '1px solid var(--line)', borderRadius: 8, background: 'var(--surface)' }}
    />
  )
}
