import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose one read-only PostgreSQL query for an exact Schemii profile and namespace. UI approval is required; this tool cannot insert, update, delete, create tables, or create views.",
  args: {
    profileId: tool.schema.string().min(1).max(128).describe("Exact selected connection profile ID"),
    namespace: tool.schema.string().min(1).max(63).describe("Exact selected PostgreSQL namespace"),
    sql: tool.schema.string().trim().min(1).max(10000).describe("One read-only SQL statement; no writes or transaction control"),
    purpose: tool.schema.string().trim().min(1).max(500).describe("Why this query is needed and what it will inspect"),
  },
  async execute() {
    return "Proposal arguments received."
  },
})
