/** Seed workspace for the in-app mock backend — ported from the prototype's `seed()`. */
import type {
  ApiKey,
  Connection,
  Graph,
  GraphNode,
  KnowledgeCollection,
  McpServer,
  Pipeline,
  Policy,
  Provider,
  ReviewItem,
  Run,
  Skill,
  VersionSummary,
  WebhookSubscription,
  Worker,
  WorkspaceSettings,
} from '../types';
import { def } from '../../features/builder/catalog';
import { INVOICE_SCHEMA, RECEIPT_SCHEMA, type Config } from '../../features/builder/catalog';
import { newNode } from '../../features/builder/graph';
import { SAMPLE_DOCS } from './sampleData';

const iso = (msAgo: number, now = Date.now()) => new Date(now - msAgo).toISOString();
const H = 36e5;
const D = 864e5;

/** Small graph builder with deterministic ids. */
function G(prefix: string) {
  let k = 0;
  const g: Graph = { nodes: [], edges: [] };
  const add = (type: string, x: number, y: number, cfg: Config = {}, name?: string): GraphNode => {
    const n = newNode(type, x, y, cfg, name, `${prefix}_n${++k}`);
    g.nodes.push(n);
    return n;
  };
  const link = (a: GraphNode, b: GraphNode, port = 'out') => {
    g.edges.push({ id: `${prefix}_e${++k}`, from: a.id, from_port: port, to: b.id });
    return b;
  };
  const plug = (host: GraphNode, specs: [string, Config?][]) => {
    const m = specs.length;
    specs.forEach(([t, cfg], j) => {
      const n = add(t, host.x + 20 + (j - (m - 1) / 2) * 224, host.y + 68 + 112, cfg ?? {});
      const slot = def(t).sub!;
      g.edges.push({ id: `${prefix}_e${++k}`, from: n.id, from_port: 'plug', to: host.id, slot });
    });
  };
  return { g, add, link, plug };
}
const X = (i: number) => 40 + i * 300;
const sch = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export function graphAP(prefix = 'ap'): Graph {
  const { g, add, link, plug } = G(prefix);
  const M = 420;
  const B = 120;
  const a = add('intake.email', X(0), M);
  const b = add('prep.enhance', X(1), M);
  const c = add('read.ocr', X(2), M);
  const d = add('read.layout', X(3), M);
  const e = add('ai.classify', X(4), M);
  const r = add('route.type', X(5), M);
  const x = add('ai.extract', X(6), B, {}, 'Extract invoice fields');
  const ru = add('val.rules', X(7), B);
  const pi = add('val.pii', X(8), B);
  const lk = add('val.lookup', X(9), B);
  const gt = add('route.confidence', X(10), B);
  const erp = add('out.erp', X(11), B - 90);
  const db = add('out.db', X(12), B - 90);
  const hr = add('review.human', X(11), B + 100);
  const rc = add('ai.extract', X(6), 720, { skill: 'skl_receipt', instructions: 'Extract merchant, date and totals from the receipt.' }, 'Extract receipt fields');
  const ex = add('out.export', X(7), 720);
  const oth = add('out.email', X(6), 560, {}, 'Reply: unsupported document');
  ([[a, b], [b, c], [c, d], [d, e], [e, r]] as const).forEach(([p, q]) => link(p, q));
  link(r, x, 'invoice');
  link(r, rc, 'receipt');
  link(r, oth, 'other');
  link(x, ru);
  link(ru, pi);
  link(pi, lk);
  link(lk, gt);
  link(gt, erp, 'pass');
  link(gt, hr, 'review');
  link(hr, erp);
  link(erp, db);
  link(rc, ex);
  plug(e, [['ai.model', { provider: 'prv_anthropic', model: 'claude-haiku-5-5' }]]);
  plug(x, [
    ['ai.model', { provider: 'prv_anthropic', model: 'claude-sonnet-5-5' }],
    ['tool.calc'],
    ['tool.mcp', { server: 'mcp_vendors', tools: ['match_vendor', 'match_po'] }],
    ['ai.knowledge'],
    ['ai.parser', { schema: sch(INVOICE_SCHEMA) }],
  ]);
  plug(rc, [
    ['ai.model', { provider: 'prv_gemini', model: 'gemini-2.5-flash' }],
    ['ai.parser', { schema: sch(RECEIPT_SCHEMA) }],
  ]);
  return g;
}

export function graphExp(prefix = 'exp'): Graph {
  const { g, add, link, plug } = G(prefix);
  const Y = 160;
  const a = add('intake.whatsapp', X(0), Y);
  const b = add('prep.enhance', X(1), Y);
  const c = add('read.ocr', X(2), Y);
  const x = add('ai.extract', X(3), Y, { skill: 'skl_receipt' }, 'Extract receipt fields');
  const p = add('val.pii', X(4), Y, { entities: ['Card number', 'Phone'] });
  const d = add('out.db', X(5), Y, { table: 'expense_receipts' });
  ([[a, b], [b, c], [c, x], [x, p], [p, d]] as const).forEach(([u, v]) => link(u, v));
  plug(x, [
    ['ai.model', { provider: 'prv_gemini', model: 'gemini-2.5-flash' }],
    ['ai.parser', { schema: sch(RECEIPT_SCHEMA) }],
  ]);
  return g;
}

export function graphKyc(prefix = 'kyc'): Graph {
  const { g, add, link, plug } = G(prefix);
  const Y = 160;
  const a = add('intake.upload', X(0), Y, { types: 'jpg, png, pdf' });
  const b = add('prep.enhance', X(1), Y, { ops: ['Deskew', 'Crop borders'] });
  const c = add('read.ocr', X(2), Y);
  const x = add('ai.extract', X(3), Y, { skill: 'skl_identity', instructions: 'Extract identity fields. Never guess a missing digit.' }, 'Extract identity fields');
  const p = add('val.pii', X(4), Y, { entities: ['Emirates ID', 'Passport number'] });
  const l = add('val.lookup', X(5), Y, { server: 'mcp_customers', tool: 'find_customer', key: 'Customer name' }, 'Registry match');
  const gt = add('route.confidence', X(6), Y, { threshold: 0.92 });
  const db = add('out.db', X(7), Y - 90, { table: 'kyc_profiles' });
  const hr = add('review.human', X(7), Y + 100, { queue: 'Compliance' });
  ([[a, b], [b, c], [c, x], [x, p], [p, l], [l, gt]] as const).forEach(([u, v]) => link(u, v));
  link(gt, db, 'pass');
  link(gt, hr, 'review');
  plug(x, [
    ['ai.model', { provider: 'prv_azure', model: 'gpt-prod' }],
    [
      'ai.parser',
      {
        schema: [
          { name: 'full_name', type: 'string', req: true },
          { name: 'document_number', type: 'string', req: true, pii: true },
          { name: 'nationality', type: 'string' },
          { name: 'date_of_birth', type: 'date', pii: true },
          { name: 'expiry_date', type: 'date', req: true },
        ],
      },
    ],
  ]);
  return g;
}

export function graphXls(prefix = 'xls'): Graph {
  const { g, add, link, plug } = G(prefix);
  const Y = 160;
  const a = add('intake.api', X(0), Y, { path: '/v1/spreadsheets' });
  const b = add('read.sheet', X(1), Y);
  const c = add('val.injection', X(2), Y);
  const x = add('ai.agent', X(3), Y, { instructions: 'Explain the workbook: purpose, key figures and anomalies. Cite cell references.', skill: 'none' }, 'Workbook analyst');
  const o = add('out.whatsapp', X(4), Y);
  ([[a, b], [b, c], [c, x], [x, o]] as const).forEach(([u, v]) => link(u, v));
  plug(x, [
    ['ai.model', { provider: 'prv_openai', model: 'gpt-5' }],
    ['ai.memory'],
    ['tool.worker', { worker: 'wrk_excel', desc: 'Re-read one sheet in full when the summary needs more rows.' }],
    ['tool.calc'],
    ['ai.knowledge', { collection: 'chart_of_accounts' }],
  ]);
  return g;
}

export interface SeedPipeline {
  pipeline: Pipeline;
  draft: Graph;
  changes: string[];
  versions: (VersionSummary & { graph: Graph })[];
}

export const TEMPLATES: { id: string; name: string; description: string; graph: (p: string) => Graph }[] = [
  { id: 'tpl_ap', name: 'Accounts payable invoices', description: 'Mailbox → OCR → classify → extract → validate → ERP.', graph: graphAP },
  { id: 'tpl_exp', name: 'Employee expense receipts', description: 'WhatsApp receipts, masked and filed.', graph: graphExp },
  { id: 'tpl_kyc', name: 'Identity documents', description: 'Onboarding documents matched to the customer registry.', graph: graphKyc },
  { id: 'tpl_xls', name: 'Spreadsheet insights', description: 'Unstructured Excel files explained in plain language.', graph: graphXls },
];

export interface SeedState {
  pipelines: SeedPipeline[];
  connections: (Connection & { _secret?: string })[];
  providers: Provider[];
  workers: Worker[];
  mcp: McpServer[];
  skills: Skill[];
  policies: Policy[];
  collections: KnowledgeCollection[];
  review: ReviewItem[];
  runs: Run[];
  apiKeys: ApiKey[];
  webhooks: WebhookSubscription[];
  settings: WorkspaceSettings;
}

export function seed(now = Date.now()): SeedState {
  const ts = (msAgo: number) => iso(msAgo, now);
  const pipe = (
    id: string,
    name: string,
    description: string,
    status: Pipeline['status'],
    live: number | null,
    owner: string,
    updatedAgo: number,
    kpi: { docs: number; stp: number; p50: number },
    graph: Graph,
    history: { v: number; by: string; ago: number; note: string }[],
    changes: string[] = [],
  ): SeedPipeline => ({
    pipeline: {
      id,
      name,
      description,
      status,
      owner_team: owner,
      live_version: live,
      draft_changes: changes,
      kpi: { documents_24h: kpi.docs, straight_through_rate: kpi.stp / 100, median_ms: Math.round(kpi.p50 * 1000), review_waiting: 0 },
      created_at: ts(120 * D),
      updated_at: ts(updatedAgo),
      updated_by: history[0]?.by ?? 'Manoj M.',
    },
    draft: graph,
    changes,
    versions: history.map((h) => ({ version: h.v, release_note: h.note, published_at: ts(h.ago), published_by: h.by, live: h.v === live, graph: JSON.parse(JSON.stringify(graph)) as Graph })),
  });

  const conn = (
    id: string,
    service: Connection['service'],
    auth_type: Connection['auth_type'],
    name: string,
    settings: Record<string, unknown>,
    hint: string | null,
    status: Connection['status'],
    usedAgo: number,
  ): Connection => ({
    id,
    name,
    service,
    auth_type,
    settings,
    secret_hint: hint,
    status,
    last_used_at: ts(usedAgo),
    last_tested_at: status === 'connected' ? ts(usedAgo + H) : null,
    expires_at: null,
    usage_count: 0,
    created_at: ts(200 * D),
    updated_at: ts(30 * D),
  });

  const model = (id: string, display?: string, x: Partial<{ context_window: number; supports_tools: boolean; supports_vision: boolean; embedding: boolean }> = {}) => ({
    id,
    display_name: display ?? id,
    context_window: 200000,
    supports_tools: true,
    supports_vision: true,
    enabled: true,
    ...x,
  });

  const docOf = (id: string, filename: string, mime = 'application/pdf') => ({ id, filename, mime_type: mime, pages: 1, size_bytes: 184320, uploaded_at: ts(2 * H) });

  return {
    pipelines: [
      pipe('pl_ap', 'Accounts payable · supplier invoices', 'Supplier invoices from the AP mailbox to draft invoices in the ERP.', 'active', 14, 'Finance Operations', 2 * H, { docs: 1284, stp: 91.2, p50: 6.8 }, graphAP(), [
        { v: 14, by: 'Manoj M.', ago: 3 * D, note: 'Raised confidence gate to 0.85' },
        { v: 13, by: 'Sara K.', ago: 11 * D, note: 'Added vendor master match' },
        { v: 12, by: 'Manoj M.', ago: 20 * D, note: 'Receipt path' },
      ]),
      pipe('pl_exp', 'Employee expense receipts', 'Receipts sent over WhatsApp, checked and filed to the expense system.', 'active', 6, 'People Operations', 26 * H, { docs: 342, stp: 84.5, p50: 4.1 }, graphExp(), [
        { v: 6, by: 'Lina A.', ago: 6 * D, note: 'Card masking' },
      ]),
      pipe('pl_kyc', 'Customer onboarding · identity documents', 'Identity documents captured during onboarding, matched to the customer registry.', 'paused', 3, 'Compliance', 9 * D, { docs: 0, stp: 0, p50: 0 }, graphKyc(), [
        { v: 3, by: 'Omar H.', ago: 9 * D, note: 'Paused pending DPIA review' },
      ]),
      pipe('pl_xls', 'Spreadsheet insights', 'Unstructured Excel files explained in plain language.', 'draft', null, 'Finance Operations', 30 * 6e4, { docs: 0, stp: 0, p50: 0 }, graphXls(), [], ['Created pipeline']),
    ],
    connections: [
      conn('con_gmail', 'gmail', 'oauth2_authorization_code', 'AP mailbox', { account: 'ap-invoices@example.com' }, '••••9fQe', 'connected', 6e5),
      conn('con_wa', 'whatsapp_business', 'bearer', 'Employee expenses line', { phone_number: '+971 4 555 0100', phone_number_id: '104455667788' }, '••••ZD0Q', 'connected', H),
      conn('con_anthropic', 'anthropic', 'api_key', 'Anthropic · production', { account: 'Workspace key' }, '••••Rk3w', 'connected', 2e5),
      conn('con_openai', 'openai', 'api_key', 'OpenAI · production', { project: 'doc-intel' }, '••••a91f', 'connected', 9e6),
      conn('con_azure', 'azure_openai', 'api_key', 'Azure OpenAI · UAE North', { endpoint: 'https://docintel-uaen.openai.azure.com', api_version: '2025-04-01' }, '••••7c0e', 'connected', 5e7),
      conn('con_gemini', 'google_gemini', 'api_key', 'Gemini · production', { project: 'doc-intel-uae' }, '••••Q2xk', 'connected', 4e6),
      conn('con_ollama', 'ollama', 'none', 'Ollama · on-prem', { base_url: 'http://10.0.4.12:11434' }, null, 'unverified', 9e8),
      conn('con_pg', 'postgresql', 'basic', 'Extraction store', { host: 'db.internal', port: 5432, database: 'docintel', username: 'docintel_app' }, '••••5432', 'connected', 6e5),
      conn('con_s3', 'amazon_s3', 'aws_sigv4', 'Scanned archive bucket', { bucket: 'example-scans', region: 'me-central-1' }, '••••K2pa', 'connected', 3 * D),
      conn('con_erp', 'http_api', 'oauth2_client_credentials', 'ERP API', { base_url: 'https://erp.example.com/api' }, '••••x8Lm', 'connected', 6e5),
      conn('con_qdrant', 'qdrant', 'api_key', 'Knowledge store', { url: 'http://qdrant.internal:6333' }, '••••0f2a', 'connected', 7e6),
      conn('con_mcp', 'mcp', 'bearer', 'Internal MCP gateway', { url: 'https://mcp.internal' }, '••••m7Tz', 'connected', 2 * H),
      conn('con_workers', 'document_worker', 'api_key', 'Worker cluster key', { base_url: 'http://localhost:8000' }, '••••wk01', 'unverified', 2 * D),
    ],
    providers: [
      { id: 'prv_anthropic', kind: 'anthropic', name: 'Anthropic', enabled: true, base_url: 'https://api.anthropic.com', connection_id: 'con_anthropic', data_region: 'Global', default_model: 'claude-sonnet-5-5',
        models: [model('claude-opus-5-5'), model('claude-sonnet-5-5'), model('claude-haiku-5-5')], usage_count: 0, limits: { requests_per_minute: 400, tokens_per_minute: 400000, monthly_budget_usd: 4000 } },
      { id: 'prv_openai', kind: 'openai', name: 'OpenAI', enabled: true, base_url: 'https://api.openai.com/v1', connection_id: 'con_openai', data_region: 'Global', default_model: 'gpt-5',
        models: [model('gpt-5'), model('gpt-5-mini')], usage_count: 0 },
      { id: 'prv_azure', kind: 'azure_openai', name: 'Azure OpenAI', enabled: true, base_url: 'https://docintel-uaen.openai.azure.com', connection_id: 'con_azure', data_region: 'UAE North', default_model: 'gpt-prod',
        models: [model('gpt-prod', 'gpt-prod (deployment)'), model('gpt-mini-prod', 'gpt-mini-prod (deployment)')], usage_count: 0 },
      { id: 'prv_gemini', kind: 'google_gemini', name: 'Google Gemini', enabled: true, base_url: 'https://generativelanguage.googleapis.com', connection_id: 'con_gemini', data_region: 'Global', default_model: 'gemini-2.5-pro',
        models: [model('gemini-2.5-pro'), model('gemini-2.5-flash')], usage_count: 0 },
      { id: 'prv_ollama', kind: 'ollama', name: 'Ollama (self-hosted)', enabled: false, base_url: 'http://10.0.4.12:11434', connection_id: 'con_ollama', data_region: 'On-premises', default_model: 'qwen2.5:7b',
        models: [model('qwen2.5:7b', undefined, { context_window: 32768, supports_vision: false }), model('llama3.1:8b', undefined, { context_window: 131072, supports_vision: false })], usage_count: 0 },
    ],
    workers: [
      { id: 'wrk_paddle', name: 'Paddle DocParse', kind: 'ocr', base_url: 'http://localhost:8001', adapter: 'paddle_docparse_v1', invoke_path: '/parse', health_path: '/health', connection_id: 'con_workers',
        accepts: ['application/pdf', 'image/png', 'image/jpeg', 'image/tiff'], capabilities: ['text', 'layout', 'tables', 'languages:en,ar'], timeout_s: 60, max_concurrency: 4, data_region: 'UAE North', health: { status: 'unknown', message: 'Routes to confirm' } },
      { id: 'wrk_excel', name: 'Excel Parser Service', kind: 'spreadsheet', base_url: 'http://localhost:8000', adapter: 'excel_parser_v1', invoke_path: '/to-text', health_path: '/health', connection_id: null,
        accepts: ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.ms-excel'], capabilities: ['text', 'sheets'], timeout_s: 60, max_concurrency: 4, data_region: 'UAE North', health: { status: 'unknown' } },
      { id: 'wrk_enhance', name: 'Image enhancement', kind: 'image', base_url: 'http://localhost:8001', adapter: 'generic_pages_v1', invoke_path: '/preprocess', health_path: '/health', connection_id: null,
        accepts: ['image/png', 'image/jpeg', 'image/tiff'], capabilities: ['deskew', 'denoise', 'rotate'], timeout_s: 30, max_concurrency: 8, data_region: 'UAE North', health: { status: 'unknown', message: 'POST /preprocess (planned)' } },
    ],
    mcp: [
      { id: 'mcp_vendors', name: 'Vendor master', transport: 'streamable_http', url: 'https://mcp.internal/vendors', connection_id: 'con_mcp', enabled: true, status: 'connected', last_discovered_at: ts(2 * D), usage_count: 0,
        tools: [
          { name: 'match_vendor', description: 'Find a vendor by TRN or name', enabled: true, input_schema: { type: 'object', properties: { trn: { type: 'string' }, name: { type: 'string' } } } },
          { name: 'match_po', description: 'Match an invoice to an open purchase order', enabled: true, input_schema: { type: 'object', properties: { po_number: { type: 'string' } }, required: ['po_number'] } },
          { name: 'get_vendor', description: 'Vendor record by id', enabled: true, input_schema: { type: 'object', properties: { id: { type: 'string' } }, required: ['id'] } },
        ] },
      { id: 'mcp_docs', name: 'Document store', transport: 'streamable_http', url: 'https://mcp.internal/docs', connection_id: 'con_mcp', enabled: true, status: 'connected', last_discovered_at: ts(5 * D), usage_count: 0,
        tools: [
          { name: 'search_documents', description: 'Full-text search over stored documents', enabled: true, input_schema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] } },
          { name: 'get_document', description: 'Fetch a document by id', enabled: true, input_schema: { type: 'object', properties: { id: { type: 'string' } } } },
          { name: 'save_extraction', description: 'Persist an extraction result', enabled: true, input_schema: { type: 'object', properties: { document_id: { type: 'string' }, fields: { type: 'object' } } } },
        ] },
      { id: 'mcp_customers', name: 'Customer registry', transport: 'sse', url: 'https://mcp.internal/customers', connection_id: 'con_mcp', enabled: false, status: 'unknown', last_discovered_at: null, usage_count: 0,
        tools: [{ name: 'find_customer', description: 'Find a customer by name or document number', enabled: true, input_schema: { type: 'object', properties: { name: { type: 'string' } } } }] },
    ],
    skills: [
      { id: 'skl_invoice', name: 'Invoice field extraction', description: 'UAE tax invoices: TRN, VAT 5%, AED amounts, ISO dates.', version: 7, instructions: 'You read UAE tax invoices. The TRN has 15 digits. VAT is 5%. Return amounts as numbers in AED and dates as YYYY-MM-DD.',
        versions: [{ version: 7, release_note: 'Credit-note handling', published_at: ts(14 * D), published_by: 'Sara K.' }, { version: 6, release_note: 'Arabic vendor names', published_at: ts(40 * D), published_by: 'Manoj M.' }], usage_count: 0 },
      { id: 'skl_receipt', name: 'Receipt reading', description: 'Thermal receipts, merchant normalisation, VAT-inclusive totals.', version: 3, instructions: 'Receipts are VAT inclusive. Normalise merchant names to their brand and branch.', versions: [{ version: 3, release_note: 'Branch names', published_at: ts(20 * D), published_by: 'Lina A.' }], usage_count: 0 },
      { id: 'skl_identity', name: 'Identity document reading', description: 'Machine-readable zones and printed field cross-checks.', version: 2, instructions: 'Cross-check the MRZ against printed fields. Never guess a missing digit.', versions: [{ version: 2, release_note: 'MRZ checksum', published_at: ts(30 * D), published_by: 'Omar H.' }], usage_count: 0 },
      { id: 'skl_bilingual', name: 'Bilingual OCR clean-up', description: 'Arabic–English reading order and mixed-script corrections.', version: 4, instructions: 'Fix reading order for mixed Arabic and English text.', versions: [{ version: 4, release_note: 'Digits in Arabic script', published_at: ts(9 * D), published_by: 'Manoj M.' }], usage_count: 0 },
    ],
    policies: [
      { id: 'pol_pii', name: 'Redact personal data before external model calls', description: 'Applies to providers outside the UAE region.', kind: 'pii_redaction', enabled: true, scope: { all_pipelines: true }, config: { entities: ['Email', 'Phone', 'IBAN', 'Card number'] } },
      { id: 'pol_inj', name: 'Screen document text for prompt injection', description: 'Hidden or invisible instructions are removed before AI steps.', kind: 'prompt_injection', enabled: true, scope: { all_pipelines: true } },
      { id: 'pol_schema', name: 'Require schema-valid output', description: 'AI steps retry once, then route to review.', kind: 'schema_enforcement', enabled: true, scope: { all_pipelines: true } },
      { id: 'pol_region', name: 'Keep documents in region', description: 'Only UAE-hosted models and workers may receive document content.', kind: 'data_residency', enabled: false, scope: { owner_teams: ['Compliance'] }, config: { allowed_regions: ['UAE North'] } },
      { id: 'pol_ret', name: 'Retain documents for 7 years', description: 'Originals and extractions are retained for tax audit.', kind: 'retention', enabled: true, scope: { pipeline_ids: ['pl_ap'] }, config: { retention_days: 2555 } },
    ],
    collections: [
      { id: 'kc_vendor', name: 'vendor_policies', connection_id: 'con_qdrant', embedding_provider_id: 'prv_openai', embedding_model: 'text-embedding-3-large', document_count: 42, chunk_count: 1310, status: 'ready' },
      { id: 'kc_tax', name: 'tax_rules_uae', connection_id: 'con_qdrant', embedding_provider_id: 'prv_openai', embedding_model: 'text-embedding-3-large', document_count: 18, chunk_count: 620, status: 'ready' },
      { id: 'kc_coa', name: 'chart_of_accounts', connection_id: 'con_qdrant', embedding_provider_id: 'prv_openai', embedding_model: 'text-embedding-3-large', document_count: 3, chunk_count: 96, status: 'ready' },
    ],
    review: [
      { id: 'rv1', run_id: 'run_48202', pipeline_id: 'pl_ap', document: docOf('doc_rv1', 'INV-2026-0388.pdf'), reason: 'Low confidence · total_amount 0.71', reason_code: 'low_confidence', queue: 'Accounts payable', assignee: 'Sara K.', sla_due_at: new Date(now - 52 * 6e4 + 4 * H).toISOString(), created_at: ts(52 * 6e4), status: 'claimed' },
      { id: 'rv2', run_id: 'run_48204', pipeline_id: 'pl_ap', document: docOf('doc_rv2', 'INV-2026-0391.pdf'), reason: 'Rule failed · total ≠ subtotal + VAT', reason_code: 'rule_failed', queue: 'Accounts payable', assignee: null, sla_due_at: new Date(now - 2.6 * H + 4 * H).toISOString(), created_at: ts(2.6 * H), status: 'open' },
      { id: 'rv3', run_id: 'run_48206', pipeline_id: 'pl_exp', document: docOf('doc_rv3', 'receipt_0918.jpg', 'image/jpeg'), reason: 'Low confidence · purchase_date 0.64', reason_code: 'low_confidence', queue: 'Expenses', assignee: 'Lina A.', sla_due_at: new Date(now - 5.2 * H + 8 * H).toISOString(), created_at: ts(5.2 * H), status: 'claimed' },
      { id: 'rv4', run_id: 'run_48208', pipeline_id: 'pl_ap', document: docOf('doc_rv4', 'INV-2026-0402.pdf'), reason: 'No vendor master match', reason_code: 'no_master_match', queue: 'Accounts payable', assignee: null, sla_due_at: new Date(now - 26 * 6e4 + 4 * H).toISOString(), created_at: ts(26 * 6e4), status: 'open' },
    ],
    runs: ['INV-2026-0411.pdf', 'INV-2026-0412.pdf', 'receipt_0915.jpg', 'INV-2026-0413.pdf', 'INV-2026-0414.pdf', 'INV-2026-0415.pdf', 'receipt_0917.jpg', 'INV-2026-0416.pdf'].map((d, i) => {
      const rcp = d.startsWith('rec');
      const ms = 4200 + i * 610;
      const at = now - (9 - i) * 41e5;
      return {
        id: 'run_' + (48210 + i),
        pipeline_id: rcp ? 'pl_exp' : 'pl_ap',
        pipeline_version: rcp ? 6 : 14,
        document: docOf('doc_' + (48210 + i), d, rcp ? 'image/jpeg' : 'application/pdf'),
        status: i === 2 ? 'review' : i === 5 ? 'failed' : 'completed',
        test: false,
        started_at: new Date(at).toISOString(),
        finished_at: new Date(at + ms).toISOString(),
        duration_ms: ms,
        field_count: rcp ? 5 : 10,
        ...(i === 5 ? { error: { type: 'https://docs.docintel.example.com/errors/worker', title: 'Worker timed out', status: 504, code: 'worker_timeout', detail: 'Paddle DocParse did not answer within 60 s' } } : {}),
      } satisfies Run;
    }),
    apiKeys: [
      { id: 'key_7Hf2', name: 'ERP ingest', prefix: 'dik_live_7Hf2', scopes: ['ingest:write', 'runs:read'], expires_at: null, last_used_at: ts(20 * 6e4), created_by: 'Manoj M.', created_at: ts(60 * D) },
      { id: 'key_Q9sd', name: 'Reporting (read only)', prefix: 'dik_live_Q9sd', scopes: ['pipelines:read', 'runs:read'], expires_at: new Date(now + 90 * D).toISOString(), last_used_at: ts(2 * D), created_by: 'Sara K.', created_at: ts(30 * D) },
    ],
    webhooks: [{ id: 'wh_1', url: 'https://hooks.example.com/docintel', events: ['run.failed', 'review.created'], enabled: true, pipeline_ids: [] }],
    settings: { name: 'Example Trading', environment: 'sandbox', default_region: 'UAE North', retention_days: 2555, allowed_regions: ['UAE North', 'EU West'], sso: { provider: 'Azure AD', issuer: 'https://login.microsoftonline.com/example/v2.0' } },
  };
}

export { SAMPLE_DOCS };
