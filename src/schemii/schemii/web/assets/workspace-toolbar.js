const WORKSPACE_LAYERS = new Set(["tables", "views", "sql"]);

function controlLayers(control) {
  return new Set((control.dataset.toolLayers || "").split(/\s+/).filter(Boolean));
}

export function syncWorkspaceToolbar(root, layer) {
  if (!root) throw new TypeError("A workspace toolbar root is required");
  if (!WORKSPACE_LAYERS.has(layer)) throw new TypeError(`Unknown workspace toolbar layer: ${layer}`);

  root.dataset.workspace = layer;
  root.setAttribute("aria-label", `${layer === "sql" ? "SQL" : `${layer[0].toUpperCase()}${layer.slice(1)}`} tools`);
  for (const control of root.querySelectorAll("[data-tool-layers]")) {
    control.toggleAttribute("data-tool-active", controlLayers(control).has(layer));
  }
  return layer;
}
