/**
 * Step catalogue — ported 1:1 from the approved prototype (object `T` in src5.html).
 * Type IDs are stable and shared with the backend (`/catalog/step-types`).
 * Field types follow the OpenAPI `FieldSpec.type` enum; resource references
 * (connections, workers, MCP servers, skills, providers) are stored by id.
 */
import type { FieldType, ServiceId, SlotName, Stage, StepType } from '../../api/types';
import { list } from '../../lib/util';

export type Config = Record<string, unknown>;

export interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  default: unknown;
  required?: boolean;
  options?: string[];
  service?: ServiceId;
  hint?: string;
  monospace?: boolean;
  min?: number;
  max?: number;
}

/** Name lookups used by summaries (resolved from server state). */
export interface SummaryCtx {
  connName: (id: unknown) => string;
  workerName: (id: unknown) => string;
  mcpName: (id: unknown) => string;
  skillName: (id: unknown) => string;
}

export interface StepDef {
  type: string;
  stage: Stage;
  name: string;
  /** IBM Carbon icon name */
  icon: string;
  description: string;
  ai?: boolean;
  /** false for intake steps and AI components */
  hasInput: boolean;
  /** AI component: the slot it plugs into */
  sub?: SlotName;
  slots?: SlotName[];
  /** required slots; AI steps default to ['model'] */
  need?: SlotName[];
  /** dynamic outputs derived from config */
  outs?: (c: Config) => string[];
  fields: FieldDef[];
  sum: (c: Config, ctx: SummaryCtx) => string;
}

export const STAGES: Record<Stage, { label: string; color: string }> = {
  intake: { label: 'Intake', color: '--st-intake' },
  prepare: { label: 'Prepare', color: '--st-prepare' },
  read: { label: 'Read', color: '--st-read' },
  understand: { label: 'Understand', color: '--st-understand' },
  validate: { label: 'Validate', color: '--st-validate' },
  decide: { label: 'Decide', color: '--st-decide' },
  deliver: { label: 'Deliver', color: '--st-deliver' },
  ai: { label: 'AI components', color: '--st-ai' },
};
export const STAGE_ORDER = Object.keys(STAGES) as Stage[];

export const SLOTS: Record<SlotName, { label: string; max: number; description: string }> = {
  model: { label: 'Model', max: 1, description: 'The language model that does the reasoning' },
  memory: { label: 'Memory', max: 1, description: 'Context kept between calls' },
  tools: { label: 'Tools', max: 12, description: 'Functions the model may call' },
  knowledge: { label: 'Knowledge', max: 3, description: 'Reference collections retrieved at run time' },
  parser: { label: 'Output', max: 1, description: 'Structured output schema' },
};

export interface SchemaField {
  name: string;
  type: 'string' | 'number' | 'date' | 'boolean' | 'array';
  req?: boolean;
  pii?: boolean;
}

export const INVOICE_SCHEMA: SchemaField[] = [
  { name: 'invoice_number', type: 'string', req: true },
  { name: 'vendor_name', type: 'string', req: true },
  { name: 'vendor_trn', type: 'string', req: true },
  { name: 'invoice_date', type: 'date', req: true },
  { name: 'due_date', type: 'date' },
  { name: 'po_number', type: 'string' },
  { name: 'subtotal', type: 'number', req: true },
  { name: 'vat_amount', type: 'number', req: true },
  { name: 'total_amount', type: 'number', req: true },
  { name: 'iban', type: 'string', pii: true },
];
export const RECEIPT_SCHEMA: SchemaField[] = [
  { name: 'merchant', type: 'string', req: true },
  { name: 'purchase_date', type: 'date', req: true },
  { name: 'total_amount', type: 'number', req: true },
  { name: 'vat_amount', type: 'number' },
  { name: 'card_last4', type: 'string', pii: true },
];
export const FIELD_COLORS = ['#1652c8', '#0e7490', '#8c4a8f', '#b5541c', '#9a6a12', '#2f7d4f', '#4659a8', '#a43b5c', '#4a5668', '#127a6b'];

const F = (key: string, label: string, type: FieldType, def: unknown, x: Partial<FieldDef> = {}): FieldDef => ({
  key,
  label,
  type,
  default: def,
  ...x,
});
const arr = (v: unknown): string[] => (Array.isArray(v) ? (v as string[]) : []);
const str = (v: unknown): string => (v == null ? '' : String(v));

const defs: StepDef[] = [
  // ── Intake
  { type: 'intake.email', stage: 'intake', name: 'Email inbox', icon: 'email', description: 'Collect attachments from a mailbox', hasInput: false,
    fields: [F('conn', 'Mailbox', 'connection', 'con_gmail', { service: 'gmail', required: true }), F('filter', 'Search filter', 'text', 'in:inbox has:attachment', { monospace: true, hint: 'Gmail search syntax.' }), F('poll', 'Check every', 'select', '5 minutes', { options: ['1 minute', '5 minutes', '15 minutes', '1 hour'] }), F('markRead', 'Mark messages as read after collection', 'switch', true)],
    sum: (c, x) => x.connName(c.conn) },
  { type: 'intake.upload', stage: 'intake', name: 'Upload portal', icon: 'upload', description: 'Users or systems upload files', hasInput: false,
    fields: [F('types', 'Accepted file types', 'text', 'pdf, png, jpg, tiff', { required: true }), F('maxMb', 'Maximum file size (MB)', 'number', 25)],
    sum: (c) => str(c.types) },
  { type: 'intake.whatsapp', stage: 'intake', name: 'WhatsApp inbound', icon: 'chat', description: 'Photos and PDFs sent to a business number', hasInput: false,
    fields: [F('conn', 'Business number', 'connection', 'con_wa', { service: 'whatsapp_business', required: true })],
    sum: (c, x) => x.connName(c.conn) },
  { type: 'intake.api', stage: 'intake', name: 'API endpoint', icon: 'webhook', description: 'Another system posts documents', hasInput: false,
    fields: [F('path', 'Path', 'text', '/v1/documents', { monospace: true, required: true }), F('auth', 'Authentication', 'select', 'HMAC signature', { options: ['HMAC signature', 'API key', 'OAuth client credentials'] })],
    sum: (c) => 'POST ' + str(c.path) },
  { type: 'intake.storage', stage: 'intake', name: 'Storage watch', icon: 'folder--move-to', description: 'Poll an SFTP folder or S3 bucket', hasInput: false,
    fields: [F('conn', 'Storage connection', 'connection', '', { service: 'amazon_s3', required: true }), F('path', 'Folder', 'text', '/inbound/scans', { monospace: true })],
    sum: (c) => str(c.path) },
  // ── Prepare
  { type: 'prep.enhance', stage: 'prepare', name: 'Image enhancement', icon: 'image', description: 'Deskew, denoise and rotate scans', hasInput: true,
    fields: [F('ops', 'Operations', 'chips', ['Deskew', 'Auto-rotate', 'Denoise'], { options: ['Deskew', 'Auto-rotate', 'Denoise', 'Binarize', 'Crop borders'] }), F('dpi', 'Target resolution (DPI)', 'number', 300)],
    sum: (c) => arr(c.ops).join(', ') },
  { type: 'prep.split', stage: 'prepare', name: 'Split pages', icon: 'layers', description: 'Split multi-document files', hasInput: true,
    fields: [F('mode', 'Split by', 'select', 'Detected document boundaries', { options: ['Detected document boundaries', 'Every page', 'Blank separator pages'] })],
    sum: (c) => str(c.mode) },
  // ── Read
  { type: 'read.ocr', stage: 'read', name: 'Text recognition', icon: 'scan', description: 'OCR with reading order', hasInput: true,
    fields: [F('worker', 'Worker', 'worker', 'wrk_paddle', { required: true }), F('lang', 'Languages', 'select', 'English + Arabic', { options: ['English', 'Arabic', 'English + Arabic', 'Auto-detect'] }), F('mode', 'Output', 'select', 'Text and layout', { options: ['Text only', 'Text and layout', 'Text, layout and tables'] })],
    sum: (c, x) => x.workerName(c.worker) + ' · ' + str(c.lang) },
  { type: 'read.layout', stage: 'read', name: 'Layout and tables', icon: 'table--split', description: 'Tables, key-value pairs, signatures', hasInput: true,
    fields: [F('detect', 'Detect', 'chips', ['Tables', 'Key-value pairs'], { options: ['Tables', 'Key-value pairs', 'Signatures', 'Stamps', 'Checkboxes'] })],
    sum: (c) => arr(c.detect).join(', ') },
  { type: 'read.sheet', stage: 'read', name: 'Spreadsheet reader', icon: 'data-table', description: 'Excel Parser Service · POST /to-text', hasInput: true,
    fields: [F('worker', 'Worker', 'worker', 'wrk_excel', { required: true }), F('max', 'Maximum characters', 'number', 14000)],
    sum: (c, x) => x.workerName(c.worker) },
  // ── Understand (AI)
  { type: 'ai.classify', stage: 'understand', ai: true, name: 'Classify document', icon: 'tag--group', description: 'Assign a document type', hasInput: true, slots: ['model'],
    fields: [F('classes', 'Document types', 'text', 'invoice, receipt, credit_note', { required: true, hint: 'Comma separated. Anything else is classified as “other”.' })],
    sum: (c) => list(c.classes).length + ' document types' },
  { type: 'ai.extract', stage: 'understand', ai: true, name: 'Extract fields', icon: 'code', description: 'Structured data against a schema', hasInput: true, slots: ['model', 'tools', 'knowledge', 'parser'], need: ['model', 'parser'],
    fields: [F('skill', 'Skill', 'skill', 'skl_invoice'), F('instructions', 'Instructions', 'textarea', 'Extract the fields exactly as printed. Amounts as numbers in the document currency. Dates as YYYY-MM-DD.')],
    sum: (c, x) => (c.skill && c.skill !== 'none' ? x.skillName(c.skill) : 'Custom instructions') },
  { type: 'ai.agent', stage: 'understand', ai: true, name: 'AI agent', icon: 'ai-generate', description: 'Reasoning with tools, memory and knowledge', hasInput: true, slots: ['model', 'memory', 'tools', 'knowledge', 'parser'], need: ['model'],
    fields: [F('skill', 'Skill', 'skill', 'none'), F('instructions', 'Instructions', 'textarea', '', { required: true }), F('maxSteps', 'Maximum tool calls', 'number', 8)],
    sum: (c) => 'Max ' + str(c.maxSteps) + ' tool calls' },
  // ── Validate
  { type: 'val.rules', stage: 'validate', name: 'Business rules', icon: 'list--checked', description: 'Arithmetic, dates, duplicates', hasInput: true,
    fields: [F('rules', 'Rules', 'chips', ['Total = subtotal + VAT', 'Due date after invoice date', 'No duplicate invoice number'], { options: ['Total = subtotal + VAT', 'Due date after invoice date', 'No duplicate invoice number', 'Currency is AED', 'TRN has 15 digits'] }), F('onFail', 'When a rule fails', 'select', 'Send to review', { options: ['Send to review', 'Stop the run', 'Continue and flag'] })],
    sum: (c) => arr(c.rules).length + ' rules' },
  { type: 'val.pii', stage: 'validate', name: 'Redact personal data', icon: 'view--off', description: 'Mask PII before it leaves the platform', hasInput: true,
    fields: [F('entities', 'Entities', 'chips', ['Email', 'Phone', 'IBAN', 'Card number'], { options: ['Email', 'Phone', 'IBAN', 'Card number', 'Emirates ID', 'Passport number', 'Address'] }), F('mode', 'Method', 'select', 'Mask', { options: ['Mask', 'Hash', 'Remove field'] })],
    sum: (c) => arr(c.entities).length + ' entity types · ' + str(c.mode) },
  { type: 'val.lookup', stage: 'validate', name: 'Master data match', icon: 'data--reference', description: 'Match against a master via MCP', hasInput: true,
    fields: [F('server', 'MCP server', 'mcp_server', 'mcp_vendors', { required: true }), F('tool', 'Tool', 'text', 'match_vendor', { monospace: true }), F('key', 'Match on', 'select', 'Vendor TRN', { options: ['Vendor TRN', 'Vendor name', 'PO number', 'Customer name'] })],
    sum: (c, x) => x.mcpName(c.server) + ' › ' + str(c.tool) },
  { type: 'val.injection', stage: 'validate', name: 'Prompt-injection screen', icon: 'rule--locked', description: 'Strip hidden instructions in documents', hasInput: true,
    fields: [F('level', 'Sensitivity', 'select', 'Medium', { options: ['Low', 'Medium', 'High'] })],
    sum: (c) => str(c.level) + ' sensitivity' },
  // ── Decide
  { type: 'route.type', stage: 'decide', name: 'Route by type', icon: 'branch', description: 'One output per document type', hasInput: true,
    outs: (c) => [...list(c.paths), 'other'],
    fields: [F('paths', 'Outputs', 'text', 'invoice, receipt', { required: true, hint: 'Comma separated. An “other” output is always added.' })],
    sum: (c) => list(c.paths).length + 1 + ' outputs' },
  { type: 'route.confidence', stage: 'decide', name: 'Confidence gate', icon: 'meter', description: 'Low-confidence results go to review', hasInput: true,
    outs: () => ['pass', 'review'],
    fields: [F('threshold', 'Minimum field confidence', 'number', 0.85, { min: 0, max: 1, hint: 'Between 0 and 1. Applied to the lowest-scoring required field.' })],
    sum: (c) => 'Pass at ≥ ' + str(c.threshold) },
  { type: 'review.human', stage: 'decide', name: 'Human review', icon: 'user--follow', description: 'Queue for an operator', hasInput: true,
    fields: [F('queue', 'Queue', 'select', 'Accounts payable', { options: ['Accounts payable', 'Compliance', 'Expenses'] }), F('sla', 'SLA (hours)', 'number', 4)],
    sum: (c) => str(c.queue) + ' · ' + str(c.sla) + ' h SLA' },
  // ── Deliver
  { type: 'out.erp', stage: 'deliver', name: 'Post to ERP', icon: 'send', description: 'Create a supplier invoice in the ERP', hasInput: true,
    fields: [F('conn', 'ERP connection', 'connection', 'con_erp', { service: 'http_api', required: true }), F('object', 'Object', 'select', 'AP invoice (draft)', { options: ['AP invoice (draft)', 'AP invoice (posted)', 'Expense report'] })],
    sum: (c) => str(c.object) },
  { type: 'out.db', stage: 'deliver', name: 'Save to database', icon: 'data--base', description: 'Upsert results', hasInput: true,
    fields: [F('conn', 'Database', 'connection', 'con_pg', { service: 'postgresql', required: true }), F('table', 'Table', 'text', 'extracted_documents', { monospace: true, required: true })],
    sum: (c) => str(c.table) },
  { type: 'out.email', stage: 'deliver', name: 'Send email', icon: 'email', description: 'Reply or notify via Gmail', hasInput: true,
    fields: [F('conn', 'Send from', 'connection', 'con_gmail', { service: 'gmail', required: true }), F('to', 'To', 'text', '{{intake.sender}}', { monospace: true }), F('subject', 'Subject', 'text', 'We could not process {{document.name}}')],
    sum: (c) => 'To ' + str(c.to) },
  { type: 'out.whatsapp', stage: 'deliver', name: 'Send WhatsApp', icon: 'chat', description: 'Template message', hasInput: true,
    fields: [F('conn', 'From number', 'connection', 'con_wa', { service: 'whatsapp_business', required: true }), F('template', 'Template', 'text', 'document_processed', { monospace: true })],
    sum: (c) => str(c.template) },
  { type: 'out.export', stage: 'deliver', name: 'Export file', icon: 'document--export', description: 'JSON or CSV per document', hasInput: true,
    fields: [F('format', 'Format', 'select', 'JSON', { options: ['JSON', 'CSV'] }), F('path', 'Path', 'text', 'exports/{{run.id}}.json', { monospace: true })],
    sum: (c) => str(c.format) },
  // ── AI components (plug into an AI step's slots)
  { type: 'ai.model', stage: 'ai', sub: 'model', name: 'Language model', icon: 'machine-learning-model', description: 'Anthropic, OpenAI, Azure, Gemini, Ollama', hasInput: false,
    fields: [F('provider', 'Provider', 'provider', 'prv_anthropic'), F('model', 'Model', 'model', 'claude-sonnet-5-5'), F('conn', 'Connection', 'provider_connection', ''), F('temperature', 'Temperature', 'range', 0.1, { min: 0, max: 1 }), F('maxTokens', 'Maximum output tokens', 'number', 2048)],
    sum: (c) => str(c.model).replace(' (deployment)', '') },
  { type: 'ai.memory', stage: 'ai', sub: 'memory', name: 'Memory', icon: 'layers', description: 'Conversation or per-document memory', hasInput: false,
    fields: [F('scope', 'Scope', 'select', 'Per document', { options: ['Per document', 'Per sender', 'Per pipeline run'] }), F('window', 'Messages kept', 'number', 10)],
    sum: (c) => str(c.scope) + ' · ' + str(c.window) },
  { type: 'ai.knowledge', stage: 'ai', sub: 'knowledge', name: 'Knowledge collection', icon: 'catalog', description: 'Vector search over reference documents', hasInput: false,
    fields: [F('collection', 'Collection', 'select', 'vendor_policies', { options: ['vendor_policies', 'tax_rules_uae', 'chart_of_accounts'] }), F('topK', 'Results per query', 'number', 5), F('conn', 'Vector store', 'connection', 'con_qdrant', { service: 'qdrant' })],
    sum: (c) => str(c.collection) + ' · top ' + str(c.topK) },
  { type: 'ai.parser', stage: 'ai', sub: 'parser', name: 'Output schema', icon: 'document--tasks', description: 'Fields, types, required and PII flags', hasInput: false,
    fields: [F('schema', 'Fields', 'schema', []), F('retry', 'Retry once on invalid output', 'switch', true)],
    sum: (c) => arr(c.schema).length + ' fields' },
  { type: 'tool.mcp', stage: 'ai', sub: 'tools', name: 'MCP tools', icon: 'plug', description: 'Tools from a registered MCP server', hasInput: false,
    fields: [F('server', 'Server', 'mcp_server', 'mcp_vendors'), F('tools', 'Tools exposed', 'mcp_tools', [])],
    sum: (c, x) => x.mcpName(c.server) },
  { type: 'tool.worker', stage: 'ai', sub: 'tools', name: 'Worker tool', icon: 'chip', description: 'Let the model call a document worker', hasInput: false,
    fields: [F('worker', 'Worker', 'worker', 'wrk_paddle'), F('desc', 'When to use it', 'textarea', 'Re-run OCR on a page when the text looks incomplete.', { hint: 'The model reads this to decide when to call the tool.' })],
    sum: (c, x) => x.workerName(c.worker) },
  { type: 'tool.calc', stage: 'ai', sub: 'tools', name: 'Calculator', icon: 'calculator', description: 'Exact arithmetic for totals and tax', hasInput: false, fields: [],
    sum: () => 'Totals, VAT, rounding' },
  { type: 'tool.search', stage: 'ai', sub: 'tools', name: 'Web search', icon: 'earth', description: 'Look up public information', hasInput: false,
    fields: [F('max', 'Maximum results', 'number', 5)],
    sum: (c) => 'Top ' + str(c.max) },
  { type: 'tool.http', stage: 'ai', sub: 'tools', name: 'HTTP tool', icon: 'webhook', description: 'Call an internal REST API', hasInput: false,
    fields: [F('url', 'URL', 'text', 'https://api.example.com/fx', { monospace: true, required: true }), F('conn', 'Authentication', 'connection', 'con_erp', { service: 'http_api' })],
    sum: (c) => str(c.url).replace(/^https?:\/\//, '') },
];

export const CATALOG: Record<string, StepDef> = Object.fromEntries(defs.map((d) => [d.type, d]));
export const STEP_TYPES = defs.map((d) => d.type);

export function def(type: string): StepDef {
  const d = CATALOG[type];
  if (!d) throw new Error(`Unknown step type ${type}`);
  return d;
}
export const hasDef = (type: string): boolean => type in CATALOG;

/** Default config for a new node of `type` (deep-cloned field defaults). */
export function defaults(type: string): Config {
  const c: Config = {};
  for (const f of def(type).fields) c[f.key] = f.default === undefined ? undefined : JSON.parse(JSON.stringify(f.default));
  return c;
}

/** Catalogue entry in the OpenAPI `StepType` shape (served by `/catalog/step-types`). */
export function toStepType(d: StepDef): StepType {
  const st: StepType = {
    type: d.type,
    stage: d.stage,
    name: d.name,
    description: d.description,
    icon: d.icon,
    ai: !!d.ai,
    has_input: d.hasInput,
    fields: d.fields.map((f) => {
      const spec: StepType['fields'][number] = { key: f.key, label: f.label, type: f.type, default: f.default };
      if (f.required) spec.required = true;
      if (f.options) spec.options = f.options;
      if (f.service) spec.service = f.service;
      if (f.hint) spec.hint = f.hint;
      if (f.monospace) spec.monospace = true;
      if (f.min != null) spec.min = f.min;
      if (f.max != null) spec.max = f.max;
      return spec;
    }),
  };
  if (d.slots) st.slots = d.slots;
  if (d.slots) st.required_slots = d.need ?? ['model'];
  if (d.sub) st.plugs_into = d.sub;
  if (d.type === 'route.confidence') st.outputs = ['pass', 'review'];
  else if (!d.sub && d.type !== 'route.type') st.outputs = ['out'];
  return st;
}
