import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"
export default tool({ description: "Propose creating a new empty Schemer dashboard after confirmation.", args: { title: tool.schema.string().trim().min(1).max(128) }, async execute() { return "Proposal arguments received." } })
