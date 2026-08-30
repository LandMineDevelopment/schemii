import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose opening an exact listed PostgreSQL connection and namespace. Schemii contacts it only after explicit UI confirmation and never reveals credentials.",
  args: {
    profileId: tool.schema.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/),
    name: tool.schema.string().trim().min(1).max(256),
    database: tool.schema.string().trim().min(1).max(63),
    namespace: tool.schema.string().trim().min(1).max(63),
  },
  async execute(args) {
    return "Proposal arguments received."
  },
})
