import { tool } from "/opt/opencode/node_modules/@opencode-ai/plugin/dist/index.js"
const column=tool.schema.object({name:tool.schema.string().min(1).max(63),data_type:tool.schema.string().min(1).max(512),nullable:tool.schema.boolean().optional(),default_expression:tool.schema.string().max(262144).nullable().optional(),identity:tool.schema.enum(["always","by_default"]).nullable().optional(),generated_expression:tool.schema.string().max(262144).nullable().optional()})
const tableKey=tool.schema.object({kind:tool.schema.enum(["primary","unique"]),columns:tool.schema.array(tool.schema.string().min(1).max(63)).min(1).max(100),name:tool.schema.string().min(1).max(63).nullable().optional()})
const designObject=tool.schema.record(tool.schema.string(),tool.schema.any())
const action=tool.schema.discriminatedUnion("type",[
 tool.schema.object({type:tool.schema.literal("add_table"),name:tool.schema.string().min(1).max(63),columns:tool.schema.array(column).min(1).max(100),keys:tool.schema.array(tableKey).max(100).optional()}),
 tool.schema.object({type:tool.schema.literal("rename_table"),table_id:tool.schema.string(),name:tool.schema.string().min(1).max(63)}),
 tool.schema.object({type:tool.schema.literal("add_column"),table_id:tool.schema.string(),column}),
 tool.schema.object({type:tool.schema.literal("update_column"),table_id:tool.schema.string(),column_id:tool.schema.string(),changes:tool.schema.record(tool.schema.string(),tool.schema.any())}),
 tool.schema.object({type:tool.schema.literal("put_top_level_object"),collection:tool.schema.enum(["types","tables","relationships","functions","views","triggers"]),object:designObject}),
 tool.schema.object({type:tool.schema.literal("put_table_member"),table_id:tool.schema.string(),collection:tool.schema.enum(["columns","keys","checks","indexes"]),object:designObject}),
 tool.schema.object({type:tool.schema.literal("delete_object"),object_id:tool.schema.string()})])
export default tool({description:"Propose one structured desired-schema edit using stable IDs and complete object definitions from context. Supports types, tables, columns, keys, checks, indexes, relationships, routines, views, and triggers.",args:{action,summary:tool.schema.string().min(1).max(2048)},async execute(){return "Proposal received for server validation."}})
