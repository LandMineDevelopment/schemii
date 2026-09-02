const BUILTIN_POSTGRES_TYPES = Object.freeze([
  { value: "smallint", group: "Numbers", keywords: "int2 two byte integer" },
  { value: "integer", group: "Numbers", keywords: "int int4 four byte integer" },
  { value: "bigint", group: "Numbers", keywords: "int8 eight byte integer" },
  { value: "numeric", group: "Numbers", keywords: "decimal arbitrary precision exact" },
  { value: "decimal", group: "Numbers", keywords: "numeric arbitrary precision exact" },
  { value: "real", group: "Numbers", keywords: "float4 single precision" },
  { value: "double precision", group: "Numbers", keywords: "float8" },
  { value: "smallserial", group: "Generated numbers", keywords: "serial2 auto increment" },
  { value: "serial", group: "Generated numbers", keywords: "serial4 auto increment" },
  { value: "bigserial", group: "Generated numbers", keywords: "serial8 auto increment" },
  { value: "money", group: "Numbers", keywords: "currency" },
  { value: "text", group: "Text", keywords: "string unlimited character" },
  { value: "varchar", group: "Text", description: "Variable-length text · optional limit", keywords: "character varying string length" },
  { value: "character", group: "Text", keywords: "char fixed length string" },
  { value: "name", group: "Text", keywords: "identifier" },
  { value: "boolean", group: "Boolean", keywords: "bool true false" },
  { value: "date", group: "Date and time" },
  { value: "time without time zone", group: "Date and time", keywords: "time" },
  { value: "time with time zone", group: "Date and time", keywords: "timetz" },
  { value: "timestamp without time zone", group: "Date and time", keywords: "timestamp" },
  { value: "timestamp with time zone", group: "Date and time", keywords: "timestamptz" },
  { value: "interval", group: "Date and time", keywords: "duration" },
  { value: "uuid", group: "Identifiers", keywords: "universally unique identifier" },
  { value: "bytea", group: "Binary", keywords: "bytes binary blob" },
  { value: "jsonb", group: "Structured data", description: "Binary JSON", keywords: "json document" },
  { value: "json", group: "Structured data", keywords: "document" },
  { value: "xml", group: "Structured data", keywords: "document" },
  { value: "text[]", group: "Arrays", keywords: "string array" },
  { value: "integer[]", group: "Arrays", keywords: "int int4 array" },
  { value: "bigint[]", group: "Arrays", keywords: "int8 array" },
  { value: "uuid[]", group: "Arrays", keywords: "identifier array" },
  { value: "jsonb[]", group: "Arrays", keywords: "json array" },
  { value: "inet", group: "Network", keywords: "ipv4 ipv6 address" },
  { value: "cidr", group: "Network", keywords: "ipv4 ipv6 network" },
  { value: "macaddr", group: "Network", keywords: "mac address" },
  { value: "macaddr8", group: "Network", keywords: "eui64 mac address" },
  { value: "point", group: "Geometric" },
  { value: "line", group: "Geometric" },
  { value: "lseg", group: "Geometric", keywords: "line segment" },
  { value: "box", group: "Geometric" },
  { value: "path", group: "Geometric" },
  { value: "polygon", group: "Geometric" },
  { value: "circle", group: "Geometric" },
  { value: "bit", group: "Bit strings", keywords: "fixed bit string" },
  { value: "bit varying", group: "Bit strings", keywords: "varbit variable" },
  { value: "tsvector", group: "Text search", keywords: "full text search document" },
  { value: "tsquery", group: "Text search", keywords: "full text search query" },
  { value: "int4range", group: "Ranges", keywords: "integer range" },
  { value: "int8range", group: "Ranges", keywords: "bigint range" },
  { value: "numrange", group: "Ranges", keywords: "numeric range" },
  { value: "tsrange", group: "Ranges", keywords: "timestamp range" },
  { value: "tstzrange", group: "Ranges", keywords: "timestamp time zone range" },
  { value: "daterange", group: "Ranges", keywords: "date range" },
  { value: "oid", group: "System identifiers", keywords: "object identifier" },
  { value: "pg_lsn", group: "System identifiers", keywords: "log sequence number" },
]);

function typeKey(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

function optionalInteger(value, { label, min, max }) {
  const source = String(value ?? "").trim();
  if (!source) return null;
  if (!/^-?\d+$/.test(source)) throw new RangeError(`${label} must be a whole number.`);
  const parsed = Number(source);
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) {
    throw new RangeError(`${label} must be between ${min} and ${max}.`);
  }
  return parsed;
}

export function parsePostgresTypeModifier(value) {
  const source = typeKey(value);
  let match = /^(character varying|varchar|character|char|bit varying|varbit|bit)(?:\s*\(\s*(\d+)\s*\))?$/.exec(source);
  if (match) {
    const bitString = ["bit", "bit varying", "varbit"].includes(match[1]);
    return Object.freeze({
      baseType: match[1],
      kind: "length",
      length: match[2] ? Number(match[2]) : null,
      maxLength: bitString ? 83_886_080 : 10_485_760,
    });
  }

  match = /^(numeric|decimal)(?:\s*\(\s*(\d+)\s*(?:,\s*(-?\d+)\s*)?\))?$/.exec(source);
  if (match) {
    return Object.freeze({
      baseType: match[1],
      kind: "numeric",
      precision: match[2] ? Number(match[2]) : null,
      scale: match[3] !== undefined ? Number(match[3]) : null,
    });
  }

  match = /^(timestamp|time)\s*(?:\(\s*(\d+)\s*\))?\s*(with(?:out)? time zone)?$/.exec(source);
  if (match) {
    return Object.freeze({
      baseType: `${match[1]}${match[3] ? ` ${match[3]}` : ""}`,
      family: match[1],
      zone: match[3] || "",
      kind: "fractional",
      precision: match[2] ? Number(match[2]) : null,
    });
  }

  match = /^(timestamptz|timetz)(?:\s*\(\s*(\d+)\s*\))?$/.exec(source);
  if (match) {
    return Object.freeze({
      baseType: match[1],
      family: match[1],
      zone: "",
      kind: "fractional",
      precision: match[2] ? Number(match[2]) : null,
    });
  }
  return null;
}

export function composePostgresTypeModifier(modifier, values = {}) {
  if (!modifier) throw new TypeError("A configurable PostgreSQL type is required.");
  if (modifier.kind === "length") {
    const length = optionalInteger(values.length, { label: "Length", min: 1, max: modifier.maxLength });
    return `${modifier.baseType}${length === null ? "" : `(${length})`}`;
  }
  if (modifier.kind === "numeric") {
    const precision = optionalInteger(values.precision, { label: "Precision", min: 1, max: 1_000 });
    const scale = optionalInteger(values.scale, { label: "Scale", min: -1_000, max: 1_000 });
    if (precision === null && scale !== null) throw new RangeError("Set precision before setting scale.");
    if (precision === null) return modifier.baseType;
    return `${modifier.baseType}(${precision}${scale === null ? "" : `,${scale}`})`;
  }
  if (modifier.kind === "fractional") {
    const precision = optionalInteger(values.precision, { label: "Fractional precision", min: 0, max: 6 });
    if (precision === null) return modifier.baseType;
    if (["timestamp", "time"].includes(modifier.family)) {
      return `${modifier.family}(${precision})${modifier.zone ? ` ${modifier.zone}` : ""}`;
    }
    return `${modifier.baseType}(${precision})`;
  }
  throw new TypeError(`Unsupported PostgreSQL type modifier: ${modifier.kind}`);
}

export function postgresTypeModifierSummary(modifier) {
  if (!modifier) return "";
  if (modifier.kind === "length") {
    if (modifier.length !== null) return `Length ${modifier.length.toLocaleString()}`;
    return ["character", "char", "bit"].includes(modifier.baseType) ? "Default length 1" : "Unlimited length";
  }
  if (modifier.kind === "numeric") {
    if (modifier.precision === null) return "Unconstrained precision";
    return modifier.scale === null
      ? `Precision ${modifier.precision} · scale 0`
      : `Precision ${modifier.precision} · scale ${modifier.scale}`;
  }
  if (modifier.precision === null) return "Default fractional precision";
  return `${modifier.precision} fractional digits`;
}

export function postgresTypeOptions({ customTypes = [], currentValue = "" } = {}) {
  const options = [...BUILTIN_POSTGRES_TYPES];
  for (const customType of customTypes) {
    if (!customType?.name) continue;
    options.push({
      value: customType.name,
      group: "Designed types",
      description: customType.kind === "domain" ? "Custom domain" : "Custom enum",
      keywords: `${customType.kind || "custom"} ${(customType.enumValues || []).join(" ")}`,
    });
  }
  const current = String(currentValue ?? "").trim();
  if (current && !options.some(option => typeKey(option.value) === typeKey(current))) {
    options.unshift({ value: current, group: "Current type", description: "Already used by this column" });
  }
  return options;
}

export { BUILTIN_POSTGRES_TYPES };
