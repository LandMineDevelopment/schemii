import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose opening an exact local Schemii project from the supplied availableProjects list.",
  args: {
    schemaId: tool.schema.string().regex(/^[A-Za-z0-9_-]{1,128}$/),
    projectName: tool.schema.string().trim().min(1).max(256),
  },
  async execute(args) {
    return "Proposal arguments received."
  },
})
