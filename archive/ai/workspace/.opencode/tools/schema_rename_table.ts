import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose renaming one active saved-schema table selected by stable ID.",
  args: { tableId: tool.schema.string().min(1).max(128), newName: tool.schema.string().trim().min(1).max(63) },
  async execute() { return "Proposal arguments received." },
})
