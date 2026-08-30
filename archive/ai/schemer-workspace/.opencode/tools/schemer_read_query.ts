import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a bounded read-only PostgreSQL query for the exact Schemer analytic target. Execution always requires separate UI confirmation.",
  args: {
    dashboardId: tool.schema.string().min(1).max(128).describe("Exact active Schemer dashboard ID"),
    expectedRevision: tool.schema.number().int().min(0).describe("Exact active dashboard revision"),
    profileId: tool.schema.string().min(1).max(64).describe("Exact selected Schemer profile ID"),
    database: tool.schema.string().min(1).max(63).describe("Exact selected PostgreSQL database"),
    namespace: tool.schema.string().min(1).max(63).describe("Exact selected PostgreSQL namespace"),
    sql: tool.schema.string().trim().min(1).max(10000).describe("One read-only SELECT, WITH, VALUES, or TABLE statement; EXPLAIN is disabled"),
    purpose: tool.schema.string().trim().min(1).max(500).describe("Why this data is needed"),
  },
  async execute() {
    return "Proposal arguments received."
  },
})
