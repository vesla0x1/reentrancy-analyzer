import React, { useEffect, useRef, useState, forwardRef, useImperativeHandle } from 'react';
import * as d3 from 'd3';
import './App.css';

export interface GraphNode {
  id: string;
  label: string;
  type: string;
  contract: string;
  function: string;
  visibility: string;
  state_mutability?: string;
  has_state_changes: boolean;
  external_calls_count: number;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

interface Edge {
  source: string | GraphNode;
  target: string | GraphNode;
  type: string;
  is_resolved?: boolean;
  via_interface?: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: Edge[];
}

interface CallGraphProps {
  data: GraphData | null;
  onNodeClick: (node: GraphNode) => void;
  selectedNode: string | null;
  highlightExternalCalls: boolean;
  selectedContract: string | null;
}

type ImperativeHandle = {
  resetZoom: () => void;
};

const CallGraph = forwardRef<ImperativeHandle, CallGraphProps>(
  ({ data, onNodeClick, selectedNode, highlightExternalCalls, selectedContract }, ref) => {
    const svgRef = useRef<SVGSVGElement | null>(null);
    const containerRef = useRef<HTMLDivElement | null>(null);
    const simulationRef = useRef<d3.Simulation<any, undefined> | null>(null);
    const linkSelectionRef = useRef<d3.Selection<SVGLineElement, any, SVGGElement, any> | null>(null);
    const nodeSelectionRef = useRef<d3.Selection<SVGCircleElement, any, SVGGElement, any> | null>(null);
    const labelSelectionRef = useRef<d3.Selection<SVGTextElement, any, SVGGElement, any> | null>(null);
    const zoomBehaviorRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
    const gRef = useRef<SVGGElement | null>(null);

    const [dimensions, setDimensions] = useState({ width: 1200, height: 800 });

    // Expose resetZoom to parent if needed
    useImperativeHandle(ref, () => ({
      resetZoom: () => {
        if (!svgRef.current || !zoomBehaviorRef.current) return;
        d3.select(svgRef.current)
          .transition()
          .duration(600)
          .call(zoomBehaviorRef.current.transform as any, d3.zoomIdentity);
      }
    }));

    // Setup SVG, defs, zoom and groups once
    useEffect(() => {
      if (!svgRef.current) return;

      const svg = d3.select(svgRef.current);
      svg.selectAll('*').remove(); // start clean on mount (but after mount, we won't remove)

      // group for zoom/pan
      const g = svg.append('g').attr('class', 'zoom-group');
      gRef.current = g.node() as SVGGElement;

      // defs for markers (arrows)
      const defs = svg.append('defs');

      const markerTypes = ['internal', 'external', 'cross_contract', 'inherited'];
      const markerData = defs.selectAll('marker').data(markerTypes);

      markerData.enter().append('marker')
        .attr('id', (d) => `arrow-${d}`)
        .attr('viewBox', '0 -5 10 10')
        .attr('refX', 22)
        .attr('refY', 0)
        .attr('markerWidth', 6)
        .attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-5L10,0L0,5')
        .attr('fill', (d) => {
          switch (d) {
            case 'external': return '#ff4444';
            case 'cross_contract': return '#ff8844';
            case 'inherited': return '#8844ff';
            default: return '#666';
          }
        });

      // create groups for links, nodes, labels (kept empty until data arrives)
      g.append('g').attr('class', 'links');
      g.append('g').attr('class', 'nodes');
      g.append('g').attr('class', 'labels');

      // zoom behavior
      const zoom = d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.1, 10])
        .on('zoom', (event) => {
          g.attr('transform', event.transform.toString());
        });

      zoomBehaviorRef.current = zoom;
      svg.call(zoom);

      return () => {
        // cleanup handled by React unmount
      };
    }, []);

    // Resize handling (one-time listener)
    useEffect(() => {
      const handleResize = () => {
        const container = containerRef.current;
        if (!container || !svgRef.current) return;
        const w = container.clientWidth || 1200;
        const h = container.clientHeight || 800;
        setDimensions({ width: w, height: h });

        d3.select(svgRef.current).attr('width', w).attr('height', h);
        // update center force if exists
        if (simulationRef.current) {
          const center = d3.forceCenter(w / 2, h / 2);
          simulationRef.current.force('center', center);
          simulationRef.current.alpha(0.3).restart();
        }
      };

      window.addEventListener('resize', handleResize);
      handleResize();
      return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Create or update simulation & DOM when data changes
    useEffect(() => {
      if (!data || !svgRef.current || !gRef.current) return;

      const svg = d3.select(svgRef.current);
      const g = d3.select(gRef.current);
      const linkGroup = g.select<SVGGElement>('g.links');
      const nodeGroup = g.select<SVGGElement>('g.nodes');
      const labelGroup = g.select<SVGGElement>('g.labels');

      // Convert edges to D3-friendly format: resolve source/target references to ids/objects
      // d3.forceLink expects node objects for source/target or id accessor
      // We'll ensure edges reference node objects
      const nodes = data.nodes.map(n => ({ ...n }));
      const nodeById = new Map(nodes.map(n => [n.id, n]));
      const edges = data.edges.map((e: Edge) => {
        const sourceId = typeof e.source === 'object' ? e.source.id : e.source;
        const targetId = typeof e.target === 'object' ? e.target.id : e.target;
        return {
          ...e,
          source: nodeById.get(sourceId) || sourceId,
          target: nodeById.get(targetId) || targetId,
        } as any;
      });

      // Filter nodes and edges based on selectedContract
      let filteredNodes = nodes;
      let filteredLinks = edges;
      if (selectedContract) {
        filteredNodes = nodes.filter(n => n.contract === selectedContract);
        const externalTargets = new Set<string>();
        edges.forEach((e: any) => {
          const sourceContract = (e.source as any).contract;
          const targetContract = (e.target as any).contract;
          if (
            sourceContract === selectedContract &&
            targetContract !== selectedContract &&
            (e.type === 'cross_contract' || e.type === 'external')
          ) {
            externalTargets.add((e.target as any).id);
          }
        });
        externalTargets.forEach(id => {
          const node = nodeById.get(id);
          if (node) filteredNodes.push(node as any);
        });

        const filteredNodeIds = new Set(filteredNodes.map(n => n.id));
        filteredLinks = edges.filter((e: any) => {
          const sId = (e.source as any).id;
          const tId = (e.target as any).id;
          return filteredNodeIds.has(sId) && filteredNodeIds.has(tId);
        });
      }

      // --- Simulation setup or update ---
      let simulation = simulationRef.current;
      if (!simulation) {
        simulation = d3.forceSimulation(filteredNodes)
          .force('link', d3.forceLink(filteredLinks).id((d: any) => d.id).distance(100).strength(1))
          .force('charge', d3.forceManyBody().strength(-300))
          .force('center', d3.forceCenter(dimensions.width / 2, dimensions.height / 2))
          .force('collision', d3.forceCollide().radius(30));
        simulationRef.current = simulation;
      } else {
        // update nodes & links on existing simulation
        simulation.nodes(filteredNodes as any);
        const linkForce = simulation.force('link') as d3.ForceLink<any, any>;
        if (linkForce) {
          linkForce.links(filteredLinks as any);
        }
        // update center
        simulation.force('center', d3.forceCenter(dimensions.width / 2, dimensions.height / 2));
      }

      // ---- Data join for links ----
      const linkSel = linkGroup.selectAll<SVGLineElement, any>('line')
        .data(filteredLinks, (d: any) => `${(d.source as any).id || d.source}--${(d.target as any).id || d.target}`);

      // exit
      linkSel.exit().remove();

      // enter
      const linkEnter = linkSel.enter().append('line')
        //.attr('stroke-width', (d: any) => d.type === 'external' ? 2 : 1)
        .attr('stroke-dasharray', (d: any) => d.type === 'inherited' ? '5,5' : 'none')
        .attr('marker-end', (d: any) => `url(#arrow-${d.type})`)
        .attr('opacity', 0.8);

      // merge
      const linkMerge = linkEnter.merge(linkSel as any);
      linkSelectionRef.current = linkMerge as any;

      // ---- Data join for nodes ----
      const nodeSel = nodeGroup.selectAll<SVGCircleElement, any>('circle')
        .data(filteredNodes, (d: any) => d.id);

      nodeSel.exit().remove();

      const nodeEnter = nodeSel.enter().append('circle')
        .attr('r', (d: GraphNode) => Math.min(20, 8 + 2 * d.external_calls_count))
        .attr('class', 'graph-node')
        .style('cursor', 'pointer')
        .on('click', (event: any, d: any) => {
          event.stopPropagation();
          onNodeClick(d);
        })
        .call(d3.drag<SVGCircleElement, any>()
          .on('start', (event: any, d: any) => {
            if (!simulation) return;
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on('drag', (event: any, d: any) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on('end', (event: any, d: any) => {
            if (!simulation) return;
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          }) as any
        );

      const nodeMerge = nodeEnter.merge(nodeSel as any);
      nodeSelectionRef.current = nodeMerge as any;

      // ---- Data join for labels ----
      const labelSel = labelGroup.selectAll<SVGTextElement, any>('text')
        .data(filteredNodes, (d: any) => d.id);

      labelSel.exit().remove();

      const labelEnter = labelSel.enter().append('text')
        .text((d: GraphNode) => d.function || d.label)
        .attr('font-size', 10)
        .attr('dx', 15)
        .attr('dy', 4)
        .attr('class', 'graph-label');

      const labelMerge = labelEnter.merge(labelSel as any);
      labelSelectionRef.current = labelMerge as any;

      // add titles for tooltip on newly entered nodes
      nodeEnter.append('title')
        .text((d: GraphNode) => `${d.id}\nType: ${d.type}\nVisibility: ${d.visibility}\nExternal Calls: ${d.external_calls_count}`);

      // ---- Tick handler ----
      simulation.on('tick', () => {
        // update link positions (source/target might be objects)
        linkMerge
          .attr('x1', (d: any) => (d.source as any).x)
          .attr('y1', (d: any) => (d.source as any).y)
          .attr('x2', (d: any) => (d.target as any).x)
          .attr('y2', (d: any) => (d.target as any).y);

        nodeMerge
          .attr('cx', (d: any) => d.x)
          .attr('cy', (d: any) => d.y);

        labelMerge
          .attr('x', (d: any) => d.x)
          .attr('y', (d: any) => d.y);
      });

      // slightly lower alpha so it stabilizes
      simulation.alpha(0.5).restart();

      // ---- Styling & highlighting update function ----
      const updateHighlighting = () => {
        // update nodes fill & stroke based on selectedNode/highlightExternalCalls
        nodeMerge
          .attr('stroke', (d: GraphNode) => d.id === selectedNode ? '#000' : '#fff')
          .attr('fill', (d: GraphNode) => {
            if (highlightExternalCalls && selectedNode && d.id !== selectedNode) {
              return '#f0f0f0';
            }
            switch (d.type) {
              case 'external': return '#ff4444';
              case 'public': return '#44ff44';
              case 'internal': return '#8888ff';
              case 'inherited': return '#ff8844';
              default: return '#ccc';
            }
          })
          .attr('opacity', (d: GraphNode) => {
            if (highlightExternalCalls && selectedNode) {
              if (d.id === selectedNode) return 1;
              const isRelated = filteredLinks.some((e: any) => {
                const s = (e.source as any).id || e.source;
                return e.source === selectedNode && e.target === d.id;
              });
              return 1;
            }
            return 1;
          });

        // update link styles similarly
        linkMerge
          .attr('stroke', (d: any) => {
            if (highlightExternalCalls && selectedNode) {
              const s = (d.source as any).id || d.source;
              const t = (d.target as any).id || d.target;
              const isRelated = s === selectedNode || t === selectedNode;
              const isExternal = d.type === 'external' || d.type === 'cross_contract';
              if (!isRelated || !isExternal) return '#e0e0e0';
            }
            switch (d.type) {
              case 'external': return '#ff4444';
              case 'cross_contract': return '#ff8844';
              case 'inherited': return '#8844ff';
              default: return '#666';
            }
          })
          //.attr('opacity', (d: any) => {
          //  if (highlightExternalCalls && selectedNode) {
          //    const s = (d.source as any).id || d.source;
          //    const t = (d.target as any).id || d.target;
          //    const isRelated = s === selectedNode || t === selectedNode;
          //    const isExternal = d.type === 'external' || d.type === 'cross_contract';
          //    return 0.2;
          //  }
          //  return 1;
          //});

        // labels opacity
        labelMerge
          .attr('opacity', (d: GraphNode) => {
            if (highlightExternalCalls && selectedNode) {
              if (d.id === selectedNode) return 1;
              const isRelated = filteredLinks.some((e: any) => {
                const s = (e.source as any).id || e.source;
                const t = (e.target as any).id || e.target;
                return s === selectedNode && t === d.id;
              });
              return isRelated ? 1 : 0.2;
            }
            return 1;
          });
      };

      updateHighlighting();

      // store references
      linkSelectionRef.current = linkMerge as any;
      nodeSelectionRef.current = nodeMerge as any;
      labelSelectionRef.current = labelMerge as any;

      // cleanup if data changes next time - don't remove svg, just allow d3 join to manage elements
      return () => {
        // detach tick listener to avoid stacking listeners
        if (simulation) simulation.on('tick', null);
      };
    }, [data, dimensions, selectedContract]); // rebuild binding when data, dimensions, or selectedContract changes

    // Update highlighting when selectedNode or highlightExternalCalls changes (no rebind)
    useEffect(() => {
      // If no selections exist yet, skip
      const nodes = nodeSelectionRef.current;
      const links = linkSelectionRef.current;
      const labels = labelSelectionRef.current;
      if (!nodes || !links || !labels || !data) return;

      // we need access to links array used by the simulation; recompute the easy mapping
      const nodeIds = new Set(data.nodes.map(n => n.id));
      const linksArr = data.edges.map((e: Edge) => {
        const s = typeof e.source === 'object' ? e.source.id : e.source;
        const t = typeof e.target === 'object' ? e.target.id : e.target;
        return { source: s, target: t, type: e.type };
      });

      // Filter linksArr if selectedContract (to match the filtered view)
      let filteredLinksArr = linksArr;
      if (selectedContract) {
        const filteredNodeIds = new Set();
        data.nodes.forEach(n => {
          if (n.contract === selectedContract) filteredNodeIds.add(n.id);
        });
        const externalTargets = new Set<string>();
        linksArr.forEach((e: any) => {
          const sourceNode = data.nodes.find(n => n.id === e.source);
          const targetNode = data.nodes.find(n => n.id === e.target);
          if (
            sourceNode?.contract === selectedContract &&
            targetNode?.contract !== selectedContract &&
            (e.type === 'cross_contract' || e.type === 'external')
          ) {
            externalTargets.add(e.target);
          }
        });
        externalTargets.forEach(id => filteredNodeIds.add(id));
        filteredLinksArr = linksArr.filter((e: any) => filteredNodeIds.has(e.source) && filteredNodeIds.has(e.target));
      }

      // update node fills, strokes, opacity
      nodes
        .attr('stroke', (d: GraphNode) => d.id === selectedNode ? '#000' : '#fff')
        //.attr('stroke-width', (d: GraphNode) => d.id === selectedNode ? 3 : 1.5)
        .attr('fill', (d: GraphNode) => {
          const related = filteredLinksArr.filter((e: any) => {
            return (e.source === selectedNode);
          }).map((e: any) => e.target);
          if (highlightExternalCalls && selectedNode && d.id !== selectedNode && related.indexOf(d.id) === -1) {
            return '#f0f0f0';
          }
          switch (d.type) {
            case 'external': return '#ff4444';
            case 'public': return '#44ff44';
            case 'internal': return '#8888ff';
            case 'inherited': return '#ff8844';
            default: return '#ccc';
          }
        })
        .attr('opacity', (d: GraphNode) => {
          if (highlightExternalCalls && selectedNode) {
            if (d.id === selectedNode) return 1;
            const related = filteredLinksArr.filter((e: any) => {
              return (e.source === selectedNode);
            }).map((e: any) => e.target);

            return related.indexOf(d.id) >= 0 ? 1 : 0.2;
          }
          return 1;
        });

      // update links
      links
        .attr('stroke', (d: any) => {
          if (highlightExternalCalls && selectedNode) {
            const s = (d.source as any).id || d.source;
            const t = (d.target as any).id || d.target;
            const isRelated = s === selectedNode || t === selectedNode;
            if (!isRelated) return '#e0e0e0';
          }
          switch (d.type) {
            case 'external': return '#ff4444';
            case 'cross_contract': return '#ff8844';
            case 'inherited': return '#8844ff';
            default: return '#666';
          }
        })
        .attr('stroke-width', (d: any) => {
          return d.type === 'external' ? 2 : 1;
        })
        .attr('opacity', (d: any) => {
          if (highlightExternalCalls && selectedNode) {
            const s = (d.source as any).id || d.source;
            return s === selectedNode ? 1 : 0.2;
          }
          return 0.8;
        });

      // labels opacity
      labels.attr('opacity', (d: any) => {
        if (highlightExternalCalls && selectedNode) {
          if (d.id === selectedNode) return 1;
          const isRelated = filteredLinksArr.some((e: any) => {
            return (e.source === selectedNode && e.target === d.id);
          });
          return isRelated ? 1 : 0.3;
        }
        return 1;
      });

    }, [selectedNode, highlightExternalCalls, data, selectedContract]);

    return (
      <div ref={containerRef} style={{ width: '100%', height: '100%' }}>
        <svg
          ref={svgRef}
          width={dimensions.width}
          height={dimensions.height}
          style={{ border: '1px solid #ddd', borderRadius: 8, background: '#fafafa', display: 'block' }}
        />
      </div>
    );
  }
);

export default CallGraph;
