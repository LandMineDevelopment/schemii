import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a bounded PostgreSQL write script for server-owned transactional execution after explicit user confirmation. This tool never executes SQL.",
  args: {
    profileId: tool.schema.string().min(1).max(128),
    namespace: tool.schema.string().min(1).max(63),
    sql: tool.schema.string().trim().min(1).max(100000),
    purpose: tool.schema.string().trim().min(1).max(500),
  },
  async execute() { return "Proposal arguments received." },
})
