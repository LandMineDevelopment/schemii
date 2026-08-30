import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

const jsonValue: any = tool.schema.lazy(() => tool.schema.union([
  tool.schema.string(), tool.schema.number(), tool.schema.boolean(), tool.schema.null(),
  tool.schema.array(jsonValue), tool.schema.record(tool.schema.string(), jsonValue),
]))

export default tool({
  description: "Propose a read-only preview for inserting structured rows into one exact PostgreSQL table. Schemii issues a separate apply proposal after review; this tool never writes or accepts SQL.",
  args: {
    profileId: tool.schema.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/),
    namespace: tool.schema.string().trim().min(1).max(63),
    relation: tool.schema.string().trim().min(1).max(63),
    rows: tool.schema.array(tool.schema.record(tool.schema.string(), jsonValue)).min(1).max(100),
    purpose: tool.schema.string().trim().min(1).max(500),
  },
  async execute(args) {
    return "Proposal arguments received."
  },
})
