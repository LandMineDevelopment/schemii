import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a bounded server-generated read of one exact PostgreSQL table without supplying SQL.",
  args: {
    profileId: tool.schema.string().min(1).max(128),
    namespace: tool.schema.string().min(1).max(63),
    relation: tool.schema.string().min(1).max(63),
    offset: tool.schema.number().int().min(0).max(10000000),
    limit: tool.schema.number().int().min(1).max(50),
    purpose: tool.schema.string().trim().min(1).max(500),
  },
  async execute() { return "Proposal arguments received." },
})
