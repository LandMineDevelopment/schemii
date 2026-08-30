import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a foreign-key relationship between exact active saved-schema columns.",
  args: {
    fromTableId: tool.schema.string().min(1).max(128), fromColumnId: tool.schema.string().min(1).max(128),
    toTableId: tool.schema.string().min(1).max(128), toColumnId: tool.schema.string().min(1).max(128),
    fromTableName: tool.schema.string().trim().min(1).max(63), fromColumnName: tool.schema.string().trim().min(1).max(63),
    toTableName: tool.schema.string().trim().min(1).max(63), toColumnName: tool.schema.string().trim().min(1).max(63),
    constraintName: tool.schema.string().trim().min(1).max(63).optional(),
    onDelete: tool.schema.enum(["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]),
    onUpdate: tool.schema.enum(["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"]),
  },
  async execute() { return "Proposal arguments received." },
})
