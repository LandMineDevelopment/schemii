import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose a read-only preview for creating one expected-absent ordinary PostgreSQL view. Schemii issues a separate apply proposal after review; this tool never applies DDL.",
  args: {
    profileId: tool.schema.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/),
    namespace: tool.schema.string().trim().min(1).max(63),
    relation: tool.schema.string().trim().min(1).max(63),
    definition: tool.schema.string().trim().min(1).max(10000).describe("One CREATE VIEW statement targeting the exact namespace and relation; no OR REPLACE or MATERIALIZED VIEW"),
    purpose: tool.schema.string().trim().min(1).max(500),
  },
  async execute(args) {
    return "Proposal arguments received."
  },
})
