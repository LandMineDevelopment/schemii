import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose deleting one active saved-schema table or column by stable ID.",
  args: {
    elementType: tool.schema.enum(["table", "column"]), tableId: tool.schema.string().min(1).max(128),
    columnId: tool.schema.string().min(1).max(128).optional(), reason: tool.schema.string().trim().min(1).max(500),
  },
  async execute(args) {
    if (args.elementType === "column" && !args.columnId) throw new Error("columnId is required when deleting a column")
    return "Proposal arguments received."
  },
})
