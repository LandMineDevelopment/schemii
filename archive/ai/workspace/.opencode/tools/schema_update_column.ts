import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

const changes = tool.schema.object({
  name: tool.schema.string().trim().min(1).max(63).optional(), type: tool.schema.string().trim().min(1).max(128).optional(),
  nullable: tool.schema.boolean().optional(), default: tool.schema.string().max(1000).nullable().optional(),
}).refine(value => Object.keys(value).length > 0, "At least one column change is required")

export default tool({
  description: "Propose updating one active saved-schema column selected by stable IDs.",
  args: { tableId: tool.schema.string().min(1).max(128), columnId: tool.schema.string().min(1).max(128), changes },
  async execute() { return "Proposal arguments received." },
})
