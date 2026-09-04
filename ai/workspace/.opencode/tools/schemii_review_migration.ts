import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"
export default tool({description:"Propose opening a server-derived migration review.",args:{summary:tool.schema.string().min(1).max(2048)},async execute(){return "Proposal received for server validation."}})
