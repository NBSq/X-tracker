(() => {
  const target = document.getElementById("relationship-graph");
  const form = document.getElementById("graph-filters");
  if (!target || !form || typeof cytoscape !== "function") return;

  const colors = {
    narrative: "#087f6d", token: "#b77900", unified_event: "#3465a4",
    source: "#3f7d44", watchlist: "#b53a42", rule: "#5f6665",
  };
  const setText = (id, value) => {
    const element = document.getElementById(id);
    if (element) element.textContent = String(value);
  };
  let graph = null;
  const tooltip = document.createElement("div");
  tooltip.className = "graph-tooltip";
  tooltip.hidden = true;

  const detailUrl = (node) =>
    `/graph/nodes/${encodeURIComponent(node.node_type)}/${encodeURIComponent(node.entity_id)}`;

  function showTooltip(event) {
    const node = event.target.data();
    tooltip.replaceChildren();
    [
      node.label,
      `Type: ${node.node_type.replaceAll("_", " ")}`,
      `Activity: ${Number(node.activity_score || 0).toFixed(1)}`,
      `Connections: ${node.degree || 0}`,
      `Weighted degree: ${Number(node.weighted_degree || 0).toFixed(2)}`,
      `First seen: ${node.first_seen_at || "-"}`,
      `Last seen: ${node.last_seen_at || "-"}`,
    ].forEach((value, index) => {
      const line = document.createElement(index === 0 ? "strong" : "span");
      line.textContent = value;
      tooltip.appendChild(line);
    });
    const position = event.target.renderedPosition();
    tooltip.style.left = `${position.x + 16}px`;
    tooltip.style.top = `${position.y + 16}px`;
    tooltip.hidden = false;
  }

  async function loadGraph() {
    const params = new URLSearchParams(new FormData(form));
    [...params.entries()].forEach(([key, value]) => { if (!value) params.delete(key); });
    const response = await fetch(`${target.dataset.endpoint}?${params}`);
    if (!response.ok) throw new Error(`Graph request failed: ${response.status}`);
    const data = await response.json();
    setText("graph-node-count", data.metrics.node_count);
    setText("graph-edge-count", data.metrics.edge_count);
    const elements = [
      ...data.nodes.map((node) => ({data: {...node, id: `n${node.id}`}})),
      ...data.edges.map((edge) => ({data: {
        ...edge, id: `e${edge.id}`, source: `n${edge.source_node_id}`,
        target: `n${edge.target_node_id}`,
      }})),
    ];
    if (graph) graph.destroy();
    tooltip.remove();
    graph = cytoscape({
      container: target,
      elements,
      style: [
        {selector: "node", style: {
          label: "data(label)", "font-size": 10, color: "#18201f",
          "text-background-color": "#fff", "text-background-opacity": .85,
          "text-background-padding": 2, "background-color": (item) => colors[item.data("node_type")] || "#687371",
          width: (item) => 20 + Number(item.data("weight") || 0) * 34,
          height: (item) => 20 + Number(item.data("weight") || 0) * 34,
        }},
        {selector: "edge", style: {
          width: (item) => 1 + Number(item.data("weight") || 0) * 7,
          "line-color": "#aab4b1", "target-arrow-color": "#aab4b1",
          "target-arrow-shape": (item) => ["narrative_related_to_narrative", "token_co_occurs_with_token"].includes(item.data("edge_type")) ? "none" : "triangle",
          "curve-style": "bezier", opacity: .7,
          "line-style": (item) => item.data("derivation") === "ai" ? "dashed" : "solid",
        }},
        {selector: "node:selected", style: {"border-width": 3, "border-color": "#18201f"}},
      ],
      layout: {name: "cose", animate: false, fit: true, padding: 24, nodeRepulsion: 7000},
      minZoom: .2,
      maxZoom: 2.2,
    });
    target.appendChild(tooltip);
    graph.on("tap", "node", (event) => {
      const node = event.target.data();
      setText("graph-selection-label", node.label);
      setText("graph-selection-type", node.node_type.replaceAll("_", " "));
      setText("graph-node-activity", Number(node.activity_score || 0).toFixed(1));
      setText("graph-node-degree", node.degree || 0);
      setText("graph-node-first", node.first_seen_at || "-");
      setText("graph-node-last", node.last_seen_at || "-");
      window.location.assign(detailUrl(node));
    });
    graph.on("mouseover", "node", showTooltip);
    graph.on("mouseout", "node", () => { tooltip.hidden = true; });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadGraph().catch(() => { target.textContent = "Graph data is temporarily unavailable."; });
  });
  loadGraph().catch(() => { target.textContent = "Graph data is temporarily unavailable."; });
})();
