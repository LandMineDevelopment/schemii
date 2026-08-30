import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose creating a new local Schemii project after explicit UI confirmation.",
  args: { projectName: tool.schema.string().trim().min(1).max(256) },
  async execute(args) {
    return "Proposal arguments received."
  },
})
