import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose adding a column to one active saved-schema table.",
  args: {
    tableId: tool.schema.string().min(1).max(128), name: tool.schema.string().trim().min(1).max(63),
    columnType: tool.schema.string().trim().min(1).max(128), nullable: tool.schema.boolean(), default: tool.schema.string().max(1000).optional(),
  },
  async execute() { return "Proposal arguments received." },
})
