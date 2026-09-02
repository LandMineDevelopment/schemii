const UNSAFE_KEYS = new Set(["__proto__", "constructor", "prototype"]);

function clone(value) {
  return globalThis.structuredClone
    ? globalThis.structuredClone(value)
    : JSON.parse(JSON.stringify(value));
}

function assertSegment(segment) {
  if (typeof segment === "number" && Number.isSafeInteger(segment) && segment >= 0) return;
  if (typeof segment === "string" && !UNSAFE_KEYS.has(segment)) return;
  throw new Error("Invalid JSON delta path");
}

function parentAt(root, path) {
  let parent = root;
  for (const segment of path.slice(0, -1)) {
    assertSegment(segment);
    if (parent === null || typeof parent !== "object" || !(segment in parent)) {
      throw new Error("JSON delta path does not exist");
    }
    parent = parent[segment];
  }
  return parent;
}

/** Apply a trusted server-derived delta without mutating the current value. */
export function applyJsonDelta(value, operations = []) {
  let result = clone(value);
  for (const item of operations) {
    if (!item || !["add", "remove", "replace"].includes(item.operation) || !Array.isArray(item.path)) {
      throw new Error("Invalid JSON delta operation");
    }
    if (!item.path.length) {
      if (item.operation === "remove") throw new Error("Cannot remove the JSON document root");
      result = clone(item.value);
      continue;
    }
    const key = item.path.at(-1);
    assertSegment(key);
    const parent = parentAt(result, item.path);
    if (parent === null || typeof parent !== "object") throw new Error("Invalid JSON delta parent");
    if (Array.isArray(parent)) {
      if (typeof key !== "number" || key > parent.length) throw new Error("Invalid JSON delta array index");
      if (item.operation === "add") parent.splice(key, 0, clone(item.value));
      else if (item.operation === "remove") {
        if (key >= parent.length) throw new Error("JSON delta array index does not exist");
        parent.splice(key, 1);
      } else {
        if (key >= parent.length) throw new Error("JSON delta array index does not exist");
        parent[key] = clone(item.value);
      }
      continue;
    }
    if (item.operation === "remove") delete parent[key];
    else parent[key] = clone(item.value);
  }
  return result;
}
