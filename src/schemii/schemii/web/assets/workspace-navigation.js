const LAYERS = new Set(["tables", "views", "sql"]);
const VIEW_KINDS = new Set(["view", "materialized_view"]);
const DOCK_STATES = new Set(["expanded", "minimized", "dismissed"]);
const WORKSPACE_ID = /^ws_[0-9a-f]{32}$/;
const MAX_IDENTIFIER_LENGTH = 256;
const MAX_CAMERA_COORDINATE = 1_000_000;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 1.7;
const STORAGE_PREFIX = "schemii.workspace-view.v1.";

function identifier(value) {
  return typeof value === "string" && value.length > 0 && value.length <= MAX_IDENTIFIER_LENGTH
    ? value
    : null;
}

function finiteInRange(value, minimum, maximum) {
  return Number.isFinite(value) && value >= minimum && value <= maximum ? value : null;
}

function validatedPreferences(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const x = finiteInRange(raw.camera?.x, -MAX_CAMERA_COORDINATE, MAX_CAMERA_COORDINATE);
  const y = finiteInRange(raw.camera?.y, -MAX_CAMERA_COORDINATE, MAX_CAMERA_COORDINATE);
  const zoom = finiteInRange(raw.camera?.zoom, MIN_ZOOM, MAX_ZOOM);
  const camera = x === null || y === null || zoom === null ? null : { x, y, zoom };
  const inspector = DOCK_STATES.has(raw.inspector) ? raw.inspector : null;
  return {
    ...(camera ? { camera } : {}),
    ...(inspector ? { inspector } : {}),
  };
}

export function readWorkspaceNavigation(urlValue) {
  const url = new URL(urlValue, "https://schemii.invalid/");
  const requestedWorkspace = url.searchParams.get("workspace");
  const workspaceId = requestedWorkspace && WORKSPACE_ID.test(requestedWorkspace)
    ? requestedWorkspace
    : null;
  const requestedLayer = url.searchParams.get("layer");
  const layer = LAYERS.has(requestedLayer) ? requestedLayer : "tables";
  const table = workspaceId && layer === "tables"
    ? identifier(url.searchParams.get("table"))
    : null;
  const requestedViewKind = url.searchParams.get("viewKind");
  const viewKind = workspaceId && layer === "views" && VIEW_KINDS.has(requestedViewKind)
    ? requestedViewKind
    : null;
  const view = viewKind ? identifier(url.searchParams.get("view")) : null;
  return { workspaceId, layer, table, view, viewKind };
}

export function workspaceNavigationHref(urlValue, navigation) {
  const url = new URL(urlValue, "https://schemii.invalid/");
  for (const name of ["workspace", "layer", "table", "view", "viewKind"]) {
    url.searchParams.delete(name);
  }
  if (navigation?.workspaceId && WORKSPACE_ID.test(navigation.workspaceId)) {
    const layer = LAYERS.has(navigation.layer) ? navigation.layer : "tables";
    url.searchParams.set("workspace", navigation.workspaceId);
    if (layer !== "tables") url.searchParams.set("layer", layer);
    if (layer === "tables") {
      const table = identifier(navigation.table);
      if (table) url.searchParams.set("table", table);
    }
    if (layer === "views") {
      const view = identifier(navigation.view);
      const viewKind = VIEW_KINDS.has(navigation.viewKind) ? navigation.viewKind : null;
      if (view && viewKind) {
        url.searchParams.set("view", view);
        url.searchParams.set("viewKind", viewKind);
      }
    }
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

export function readWorkspacePreferences(storage, workspaceId) {
  if (!storage || !WORKSPACE_ID.test(workspaceId || "")) return {};
  try {
    const raw = JSON.parse(storage.getItem(`${STORAGE_PREFIX}${workspaceId}`) || "null");
    return validatedPreferences(raw);
  } catch {
    return {};
  }
}

export function updateWorkspacePreferences(storage, workspaceId, patch) {
  if (!storage || !WORKSPACE_ID.test(workspaceId || "")) return false;
  const current = readWorkspacePreferences(storage, workspaceId);
  const validPatch = validatedPreferences(patch);
  const next = {
    ...current,
    ...validPatch,
  };
  try {
    storage.setItem(`${STORAGE_PREFIX}${workspaceId}`, JSON.stringify(next));
    return true;
  } catch {
    return false;
  }
}
