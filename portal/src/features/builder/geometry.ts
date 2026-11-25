/**
 * Wire geometry — ported verbatim from the prototype.
 * Main-flow wires always leave an output and enter an input horizontally; the handle length grows with
 * vertical distance and with backward links, so stacked or reversed steps get a clean S-curve, not a kink.
 */
export type Pt = [number, number];

export const handle = (x1: number, y1: number, x2: number, y2: number): number => {
  const fw = x2 - x1;
  const dy = Math.abs(y2 - y1);
  return fw >= 48 ? Math.min(220, Math.max(48, fw / 2) + Math.min(dy * 0.18, 70)) : Math.min(280, Math.max(90, dy / 2.2 - fw / 3));
};

export const curve = (x1: number, y1: number, x2: number, y2: number): string => {
  const c = handle(x1, y1, x2, y2);
  return `M${x1} ${y1}C${x1 + c} ${y1},${x2 - c} ${y2},${x2} ${y2}`;
};

/** Cubic bezier point at t = 0.5. */
export const bez = (p0: Pt, p1: Pt, p2: Pt, p3: Pt): Pt => [
  (p0[0] + 3 * p1[0] + 3 * p2[0] + p3[0]) / 8,
  (p0[1] + 3 * p1[1] + 3 * p2[1] + p3[1]) / 8,
];

/** Arrowhead length. Drawn as its own shape (not an SVG marker) so it sits on the input port and follows wire state. */
export const AH = 10;

/** Vertical curve used by AI component wires (component top → slot diamond). */
export const vcurve = (x1: number, y1: number, x2: number, y2: number): string => {
  const dy = Math.max(40, Math.abs(y1 - y2) / 2);
  return `M${x1} ${y1}C${x1} ${y1 - dy},${x2} ${y2 + dy},${x2} ${y2}`;
};

export interface WireGeom {
  d: string;
  /** midpoint (t = 0.5) where the × delete button sits */
  m: Pt;
  /** arrowhead path, main-flow wires only */
  a?: string;
}

/** Main-flow wire from an output port centre (x1,y1) to the input port tip (tx,ty). */
export function flowGeom(x1: number, y1: number, tx: number, ty: number): WireGeom {
  const x2 = tx - AH;
  const c = handle(x1, y1, x2, ty);
  return {
    d: `M${x1} ${y1}C${x1 + c} ${y1},${x2 - c} ${ty},${x2} ${ty}`,
    m: bez([x1, y1], [x1 + c, y1], [x2 - c, ty], [x2, ty]),
    a: `M${x2} ${ty - 5.5}L${tx} ${ty}L${x2} ${ty + 5.5}Z`,
  };
}

/** AI wire from component plug (x1,y1, top centre) to host slot (x2,y2, bottom). Dashed, no arrowhead. */
export function aiGeom(x1: number, y1: number, x2: number, y2: number): WireGeom {
  const dy = Math.max(40, Math.abs(y1 - y2) / 2);
  return { d: vcurve(x1, y1, x2, y2), m: bez([x1, y1], [x1, y1 - dy], [x2, y2 + dy], [x2, y2]) };
}
