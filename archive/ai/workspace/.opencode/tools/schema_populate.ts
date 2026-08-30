import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

const column = tool.schema.object({
  name: tool.schema.string().trim().min(1).max(63), type: tool.schema.string().trim().min(1).max(128),
  primary: tool.schema.boolean().optional(), nullable: tool.schema.boolean().optional(), unique: tool.schema.boolean().optional(), default: tool.schema.string().max(1000).optional(),
})
const table = tool.schema.object({ name: tool.schema.string().trim().min(1).max(63), purpose: tool.schema.string().trim().min(1).max(500), columns: tool.schema.array(column).min(1).max(50) })
const relationship = tool.schema.object({
  fromTableName: tool.schema.string().trim().min(1).max(63), fromColumnName: tool.schema.string().trim().min(1).max(63),
  toTableName: tool.schema.string().trim().min(1).max(63), toColumnName: tool.schema.string().trim().min(1).max(63),
  constraintName: tool.schema.string().trim().min(1).max(63).optional(),
  onDelete: tool.schema.enum(["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]),
  onUpdate: tool.schema.enum(["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]),
})

export default tool({
  description: "Use when the user asks to design or create several tables and relationships. Propose schema definitions in the active saved design after UI confirmation; never insert rows or directly change PostgreSQL.",
  args: { purpose: tool.schema.string().trim().min(1).max(500), tables: tool.schema.array(table).min(1).max(20), relationships: tool.schema.array(relationship).max(50) },
  async execute() { return "Proposal arguments received." },
})
