import { describe, expect, it } from 'vitest';
import { AH, aiGeom, bez, curve, flowGeom, handle, vcurve } from './geometry';
import { edgeGeometry } from './FlowEdge';
import { newNode, NH, NW, SW, slotX } from './graph';

describe('handle() routing', () => {
  it('forward links: half the horizontal distance, min 48, plus a vertical bonus capped at 70, max 220', () => {
    expect(handle(0, 0, 300, 0)).toBe(150);
    expect(handle(0, 0, 60, 0)).toBe(48);
    expect(handle(0, 0, 300, 100)).toBeCloseTo(150 + 18);
    expect(handle(0, 0, 300, 1000)).toBe(220); // 150 + 70 = 220 cap
    expect(handle(0, 0, 1000, 0)).toBe(220);
  });
  it('backward or stacked links: S-curve handle between 90 and 280', () => {
    expect(handle(0, 0, 0, 0)).toBe(90);
    expect(handle(0, 0, -300, 0)).toBe(100); // max(90, 0/2.2 - (-300)/3)
    expect(handle(0, 0, 0, 440)).toBeCloseTo(200);
    expect(handle(0, 0, -3000, 0)).toBe(280);
    expect(handle(0, 0, 47, 0)).toBe(90);
  });
  it('curve() leaves and enters horizontally', () => {
    const c = handle(10, 20, 310, 20);
    expect(curve(10, 20, 310, 20)).toBe(`M10 20C${10 + c} 20,${310 - c} 20,310 20`);
  });
});

describe('drawn arrowhead geometry', () => {
  it('ends the wire AH before the port and draws a 10px triangle whose tip is the port', () => {
    const g = flowGeom(0, 50, 400, 120);
    expect(AH).toBe(10);
    expect(g.a).toBe('M390 114.5L400 120L390 125.5Z');
    expect(g.d.endsWith('390 120')).toBe(true);
    expect(g.d.startsWith('M0 50C')).toBe(true);
  });
  it('places the delete button at the bezier midpoint (t = 0.5)', () => {
    const g = flowGeom(0, 0, 410, 0);
    const c = handle(0, 0, 400, 0);
    expect(g.m).toEqual(bez([0, 0], [c, 0], [400 - c, 0], [400, 0]));
    expect(g.m).toEqual([200, 0]);
  });
  it('AI wires are vertical curves without an arrowhead', () => {
    const g = aiGeom(100, 300, 120, 100);
    expect(g.a).toBeUndefined();
    expect(g.d).toBe(vcurve(100, 300, 120, 100));
    expect(g.d).toBe('M100 300C100 200,120 200,120 100');
  });
  it('edgeGeometry uses output port and input port positions', () => {
    const a = newNode('read.ocr', 0, 0, {}, undefined, 'a');
    const b = newNode('read.layout', 300, 0, {}, undefined, 'b');
    const g = edgeGeometry(a, b, 'out');
    expect(g.d.startsWith(`M${NW} ${NH / 2}C`)).toBe(true);
    expect(g.a).toContain(`L${300 - 8} ${NH / 2}`);
    const m = newNode('ai.model', 0, 300, {}, undefined, 'm');
    const h = newNode('ai.extract', 0, 0, {}, undefined, 'h');
    const s = edgeGeometry(m, h, 'plug', 'model');
    expect(s.d.startsWith(`M${SW / 2} 300C`)).toBe(true);
    expect(s.d.endsWith(`${slotX(h, 'model')} ${NH}`)).toBe(true);
  });
});
