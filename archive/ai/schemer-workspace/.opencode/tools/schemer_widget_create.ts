import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"
const id = tool.schema.string().regex(/^[A-Za-z0-9_-]{1,128}$/)
const label = tool.schema.string().trim().min(1).max(128)
const pgName = tool.schema.string().trim().min(1).max(63)
const numberFormat = tool.schema.union([
  tool.schema.object({ style: tool.schema.literal("auto") }).strict(), tool.schema.object({ style: tool.schema.literal("integer") }).strict(),
  tool.schema.object({ style: tool.schema.enum(["decimal", "percent"]), fractionDigits: tool.schema.number().int().min(0).max(20) }).strict(),
  tool.schema.object({ style: tool.schema.literal("currency"), currency: tool.schema.string().regex(/^[A-Z]{3}$/), fractionDigits: tool.schema.number().int().min(0).max(20) }).strict(),
])
const scalar = tool.schema.union([tool.schema.string().max(2048), tool.schema.number().finite(), tool.schema.boolean()])
const measure = tool.schema.union([
  tool.schema.object({ id, label, column: tool.schema.null(), aggregation: tool.schema.literal("count_rows"), distinct: tool.schema.literal(false), nullBehavior: tool.schema.literal("preserve"), numberFormat }).strict(),
  tool.schema.object({ id, label, column: pgName, aggregation: tool.schema.literal("count"), distinct: tool.schema.boolean(), nullBehavior: tool.schema.literal("preserve"), numberFormat }).strict(),
  tool.schema.object({ id, label, column: pgName, aggregation: tool.schema.enum(["sum", "average", "minimum", "maximum"]), distinct: tool.schema.literal(false), nullBehavior: tool.schema.enum(["preserve", "zero"]), numberFormat }).strict(),
])
const query = tool.schema.object({
  version: tool.schema.literal(2), dimensions: tool.schema.array(tool.schema.object({ id, label, column: pgName }).strict()).max(32), measures: tool.schema.array(measure).min(1).max(32),
  filters: tool.schema.array(tool.schema.object({ id, conditions: tool.schema.array(tool.schema.object({ id, column: pgName, operator: tool.schema.enum(["eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "not_in", "like", "contains", "starts_with", "ends_with", "is_null", "is_not_null"]), values: tool.schema.array(scalar).max(100) }).strict()).min(1).max(64) }).strict()).max(32),
  sort: tool.schema.array(tool.schema.object({ targetKind: tool.schema.enum(["dimension", "measure"]), targetId: id, direction: tool.schema.enum(["asc", "desc"]), nulls: tool.schema.enum(["first", "last"]) }).strict()).max(64), limit: tool.schema.number().int().min(1).max(500),
}).strict()
export default tool({ description: "Propose a complete functioning aggregate widget from an exact verified source, or omit source/query for a placeholder.", args: { dashboardId: id, expectedRevision: tool.schema.number().int().min(0), title: label, source: tool.schema.object({ profileId: tool.schema.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/), database: pgName, namespace: pgName, relation: pgName, kind: tool.schema.enum(["table", "view", "materialized_view"]), fingerprint: tool.schema.string().regex(/^[0-9a-f]{64}$/) }).strict().optional(), query: query.optional(), visualizationMode: tool.schema.enum(["table", "kpi", "bar", "line", "donut"]).optional() }, async execute(args) { const count = [args.source, args.query, args.visualizationMode].filter(value => value !== undefined).length; if (count !== 0 && count !== 3) throw new Error("source, query, and visualizationMode must be supplied together"); return "Proposal arguments received." } })
