import { describe, expect, it } from 'vitest';
import { CATALOG, SLOTS, STAGES, STEP_TYPES, defaults, toStepType } from './catalog';
import { hasIcon } from '../../ui/Icon';
import { SERVICES } from '../../api/mock/services';

const STABLE = [
  'intake.email', 'intake.upload', 'intake.whatsapp', 'intake.api', 'intake.storage',
  'prep.enhance', 'prep.split', 'read.ocr', 'read.layout', 'read.sheet',
  'ai.classify', 'ai.extract', 'ai.agent', 'val.rules', 'val.pii', 'val.lookup', 'val.injection',
  'route.type', 'route.confidence', 'review.human', 'out.erp', 'out.db', 'out.email', 'out.whatsapp', 'out.export',
  'ai.model', 'ai.memory', 'ai.knowledge', 'ai.parser', 'tool.mcp', 'tool.worker', 'tool.calc', 'tool.search', 'tool.http',
];
const FIELD_TYPES = ['text', 'number', 'select', 'chips', 'switch', 'textarea', 'range', 'connection', 'worker', 'mcp_server', 'mcp_tools', 'skill', 'provider', 'model', 'provider_connection', 'schema'];

describe('catalogue integrity', () => {
  it('has exactly the stable type IDs, in palette order', () => {
    expect(STEP_TYPES).toEqual(STABLE);
  });
  it('every entry is well formed', () => {
    for (const t of STEP_TYPES) {
      const d = CATALOG[t]!;
      expect(d.type).toBe(t);
      expect(Object.keys(STAGES)).toContain(d.stage);
      expect(hasIcon(d.icon), `${t} icon ${d.icon}`).toBe(true);
      expect(d.name.length).toBeGreaterThan(0);
      for (const f of d.fields) {
        expect(FIELD_TYPES).toContain(f.type);
        if (f.type === 'select' || f.type === 'chips') expect(f.options?.length, `${t}.${f.key} options`).toBeGreaterThan(0);
        if (f.type === 'connection') expect(SERVICES.map((s) => s.id)).toContain(f.service);
        if (f.type === 'select' && f.default) expect(f.options).toContain(f.default);
      }
      expect(() => d.sum(defaults(t), { connName: String, workerName: String, mcpName: String, skillName: String })).not.toThrow();
    }
  });
  it('intake steps have no input; AI components plug into one slot and have no input', () => {
    for (const d of Object.values(CATALOG)) {
      if (d.stage === 'intake') expect(d.hasInput).toBe(false);
      if (d.stage === 'ai') {
        expect(d.sub && d.sub in SLOTS).toBe(true);
        expect(d.hasInput).toBe(false);
      }
      if (d.ai) {
        expect(d.stage).toBe('understand');
        expect(d.slots).toContain('model');
      }
    }
    expect(CATALOG['ai.extract']!.need).toEqual(['model', 'parser']);
  });
  it('every slot has at least one component', () => {
    for (const s of Object.keys(SLOTS)) expect(Object.values(CATALOG).some((d) => d.sub === s)).toBe(true);
  });
  it('defaults are deep copies', () => {
    const a = defaults('val.pii');
    (a.entities as string[]).push('X');
    expect(defaults('val.pii').entities).not.toContain('X');
  });
  it('serialises to the OpenAPI StepType shape', () => {
    const st = toStepType(CATALOG['ai.extract']!);
    expect(st).toMatchObject({ type: 'ai.extract', stage: 'understand', ai: true, has_input: true, slots: ['model', 'tools', 'knowledge', 'parser'], required_slots: ['model', 'parser'] });
    expect(toStepType(CATALOG['ai.model']!).plugs_into).toBe('model');
    expect(toStepType(CATALOG['route.confidence']!).outputs).toEqual(['pass', 'review']);
    expect(toStepType(CATALOG['intake.email']!).fields[0]).toMatchObject({ key: 'conn', type: 'connection', service: 'gmail', required: true });
  });
});
