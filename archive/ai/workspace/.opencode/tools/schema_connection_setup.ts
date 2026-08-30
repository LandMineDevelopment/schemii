import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"

export default tool({
  description: "Propose connection-profile fields without a password. The user enters secrets only in Schemii UI.",
  args: {
    name: tool.schema.string().trim().min(1).max(100),
    host: tool.schema.string().trim().min(1).max(253),
    port: tool.schema.number().int().min(1).max(65535),
    database: tool.schema.string().trim().min(1).max(63),
    user: tool.schema.string().trim().min(1).max(63),
    sslmode: tool.schema.enum(["disable", "allow", "prefer", "require", "verify-ca", "verify-full"]),
  },
  async execute(args) {
    return "Proposal arguments received."
  },
})
