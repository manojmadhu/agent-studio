/**
 * Pipeline validation — a 1:1 port of the prototype's `issues()` / `fieldErr()`.
 * Pure: the caller supplies the connections and providers that exist in the workspace.
 * The backend runs the same rules (`POST /pipelines/{id}/validate`) and is authoritative.
 */
import type { Graph, GraphNode, ValidationIssue } from '../../api/types';
import { CATALOG, type Config, type FieldDef, type SchemaField } from './catalog';
import { hostOf, insOf, isSub, mainNodes, outsOf, subsOf } from './graph';

export interface ValidationCtx {
  connections: { id: string }[];
  providers: { id: string; name: string; enabled: boolean }[];
}

export const SNAKE = /^[a-z][a-z0-9_]*$/;

export function fieldErr(f: FieldDef, c: Config, ctx: ValidationCtx): string {
  const v = c[f.key];
  if (f.required && (v === '' || v == null || (Array.isArray(v) && !v.length))) return 'Required';
  if (f.type === 'connection' && v && !ctx.connections.some((x) => x.id === v)) return 'Connection was removed';
  if (f.type === 'number' && v !== '' && v != null && Number.isNaN(Number(v))) return 'Must be a number';
  return '';
}

export function issues(g: Graph, ctx: ValidationCtx): ValidationIssue[] {
  const out: ValidationIssue[] = [];
  const add = (level: ValidationIssue['level'], message: string, node_id?: string, code?: string, field?: string) => {
    const i: ValidationIssue = { level, message };
    if (node_id) i.node_id = node_id;
    if (code) i.code = code;
    if (field) i.field = field;
    out.push(i);
  };
  const main = mainNodes(g);
  if (!main.some((n) => !insOf(n))) add('error', 'Add an intake step to start the pipeline', undefined, 'missing_intake');
  if (!main.some((n) => CATALOG[n.type]?.stage === 'deliver')) add('warning', 'No deliver step: results are not stored anywhere', undefined, 'missing_deliver');

  g.nodes.forEach((n: GraphNode) => {
    const d = CATALOG[n.type];
    if (!d) {
      add('error', `${n.name}: unknown step type ${n.type}`, n.id, 'unknown_type');
      return;
    }
    const c = n.config as Config;
    d.fields.forEach((f) => {
      const er = fieldErr(f, c, ctx);
      if (er) add('error', `${n.name}: ${f.label} — ${er.toLowerCase()}`, n.id, 'required_field', f.key);
    });
    if (isSub(n)) {
      if (!hostOf(g, n)) add('warning', `${n.name} is not plugged into an AI step`, n.id, 'unplugged_component');
      if (n.type === 'ai.model') {
        const pv = ctx.providers.find((p) => p.id === c.provider);
        if (!pv) add('error', `${n.name}: choose a provider`, n.id, 'missing_provider');
        else if (!pv.enabled) add('error', `${n.name}: ${pv.name} is disabled in Models`, n.id, 'provider_disabled');
      }
      if (n.type === 'ai.parser') {
        const schema = (Array.isArray(c.schema) ? c.schema : []) as SchemaField[];
        if (!schema.length) add('error', `${n.name}: add at least one field`, n.id, 'invalid_schema_field');
        else if (schema.some((f) => !SNAKE.test(f.name))) add('error', `${n.name}: field names must be snake_case`, n.id, 'invalid_schema_field');
      }
      return;
    }
    if (insOf(n) && !g.edges.some((e) => !e.slot && e.to === n.id)) add('error', `${n.name} has no incoming connection`, n.id, 'no_incoming_connection');
    if (d.stage !== 'deliver') {
      const outs = outsOf(n);
      outs.forEach((o) => {
        if (o !== 'error' && !g.edges.some((e) => e.from === n.id && e.from_port === o))
          add('warning', `${n.name}${outs.length > 1 ? ` → “${o}”` : ''} leads nowhere`, n.id, 'dangling_output');
      });
    }
    (d.need ?? d.slots?.filter((s) => s === 'model') ?? []).forEach((s) => {
      if (!subsOf(g, n, s).length)
        add('error', `${n.name} needs ${s === 'parser' ? 'an output schema' : 'a language model'}`, n.id, s === 'parser' ? 'missing_parser' : 'missing_model');
    });
    if (n.type === 'route.confidence') {
      const t = Number(c.threshold);
      if (!(t > 0 && t <= 1)) add('error', `${n.name}: threshold must be between 0 and 1`, n.id, 'invalid_threshold', 'threshold');
    }
  });

  // loop detection over main-flow edges
  const adj: Record<string, string[]> = {};
  g.edges.filter((e) => !e.slot).forEach((e) => (adj[e.from] = adj[e.from] ?? []).push(e.to));
  const st: Record<string, number> = {};
  let cyc = false;
  const dfs = (id: string) => {
    st[id] = 1;
    for (const t of adj[id] ?? []) {
      if (st[t] === 1) cyc = true;
      else if (!st[t]) dfs(t);
    }
    st[id] = 2;
  };
  main.forEach((n) => {
    if (!st[n.id]) dfs(n.id);
  });
  if (cyc) add('error', 'The pipeline contains a loop. Use retries instead of wiring back.', undefined, 'loop');
  return out;
}

export const errorCount = (list: ValidationIssue[]): number => list.filter((i) => i.level === 'error').length;
