import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"
export default tool({description:"Propose SQL to open in the human SQL Console. Never executes SQL.",args:{sql:tool.schema.string().min(1).max(1048576),summary:tool.schema.string().min(1).max(2048)},async execute(){return "Proposal received for server validation."}})
