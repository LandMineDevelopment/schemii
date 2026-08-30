import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

const column = tool.schema.object({
  name: tool.schema.string().trim().min(1).max(63), type: tool.schema.string().trim().min(1).max(128),
  primary: tool.schema.boolean().optional(), nullable: tool.schema.boolean().optional(), unique: tool.schema.boolean().optional(),
  default: tool.schema.string().max(1000).optional(),
})

export default tool({
  description: "Use when the user asks to create, add, or design one table. Propose one complete table in the active saved Schemii design after UI confirmation; do not directly create it in PostgreSQL.",
  args: { name: tool.schema.string().trim().min(1).max(63), purpose: tool.schema.string().trim().min(1).max(500), columns: tool.schema.array(column).min(1).max(50) },
  async execute() { return "Proposal arguments received." },
})
