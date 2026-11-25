/**
 * Sample document page with overlays (text blocks, tables, extracted fields, redactions) — ported from
 * the prototype's paperHTML()/drawPaper(). Overlay boxes are measured from the rendered page so they line up.
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { FIELD_COLORS } from '../builder/catalog';

export type PaperKind = 'inv' | 'rcp';
export interface OverlayLayers {
  blocks?: boolean;
  tables?: boolean;
  fields?: boolean;
  redactions?: boolean;
}
export interface OverlayField {
  name: string;
  confidence: number;
}

type Box = [number, number, number, number];

function Invoice({ docNo }: { docNo: string }) {
  const td = { border: 0, padding: '2px 10px 2px 0' } as const;
  const tv = { border: 0, padding: '2px 0' } as const;
  return (
    <div className="paper">
      <span className="wm">Sample document</span>
      <div className="row2">
        <div data-b="">
          <span className="logo2">GO</span>
          <h5 data-f="vendor_name">Gulf Office Supplies LLC</h5>
          <div>
            Warehouse 14, Al Quoz Industrial Area 3<br />
            Dubai, United Arab Emirates
          </div>
          <div>
            TRN <span data-f="vendor_trn">100354872600003</span>
          </div>
        </div>
        <div style={{ textAlign: 'right' }} data-b="">
          <h5 style={{ fontSize: 22, letterSpacing: '.04em' }}>TAX INVOICE</h5>
          <table style={{ margin: '8px 0 0', width: 'auto', marginLeft: 'auto' }}>
            <tbody>
              <tr>
                <td className="lbl" style={td}>
                  Invoice no.
                </td>
                <td style={tv}>
                  <b data-f="invoice_number">{docNo}</b>
                </td>
              </tr>
              <tr>
                <td className="lbl" style={td}>
                  Invoice date
                </td>
                <td style={tv} data-f="invoice_date">
                  18 Nov 2026
                </td>
              </tr>
              <tr>
                <td className="lbl" style={td}>
                  Due date
                </td>
                <td style={tv} data-f="due_date">
                  18 Dec 2026
                </td>
              </tr>
              <tr>
                <td className="lbl" style={td}>
                  PO number
                </td>
                <td style={tv} data-f="po_number">
                  PO-88213
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div style={{ marginTop: 22 }} data-b="">
        <div className="lbl">Bill to</div>
        <b>Example Trading FZ-LLC</b>
        <div>Office 1203, Business Bay, Dubai</div>
        <div>
          <span data-pii="">accounts@example.com</span> · <span data-pii="">+971 4 555 0142</span>
        </div>
      </div>
      <table data-t="">
        <thead>
          <tr>
            <th>Description</th>
            <th className="r">Qty</th>
            <th className="r">Unit price</th>
            <th className="r">Amount (AED)</th>
          </tr>
        </thead>
        <tbody>
          {[
            ['A4 copy paper, 80 gsm, box of 5', '40', '95.00', '3,800.00'],
            ['Laser toner cartridge, black', '12', '410.00', '4,920.00'],
            ['Ergonomic office chair', '4', '920.00', '3,680.00'],
          ].map((r) => (
            <tr key={r[0]}>
              <td>{r[0]}</td>
              <td className="r">{r[1]}</td>
              <td className="r">{r[2]}</td>
              <td className="r">{r[3]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row2">
        <div style={{ maxWidth: 280 }} data-b="">
          <div className="lbl">Payment details</div>
          <div>Example Bank PJSC</div>
          <div>
            IBAN{' '}
            <span data-f="iban" data-pii="">
              AE07 0331 2345 6789 0123 456
            </span>
          </div>
          <div style={{ marginTop: 6, color: '#667085' }}>Payment terms: 30 days from invoice date</div>
        </div>
        <table style={{ width: 240, margin: 0 }} data-b="">
          <tbody>
            <tr className="tot">
              <td className="lbl">Subtotal</td>
              <td className="r" data-f="subtotal">
                12,400.00
              </td>
            </tr>
            <tr className="tot">
              <td className="lbl">VAT 5%</td>
              <td className="r" data-f="vat_amount">
                620.00
              </td>
            </tr>
            <tr className="grand">
              <td>Total AED</td>
              <td className="r" data-f="total_amount">
                13,020.00
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p style={{ margin: '28px 0 0', color: '#98a2b3', fontSize: 10.5 }} data-b="">
        This tax invoice is issued in accordance with UAE VAT legislation.
      </p>
    </div>
  );
}

function Receipt() {
  const cell = { border: 0, padding: '2px 0' } as const;
  return (
    <div className="paper" style={{ width: 380, fontFamily: 'var(--mono)', fontSize: 11.5, padding: '32px 30px' }}>
      <span className="wm">Sample document</span>
      <div style={{ textAlign: 'center' }} data-b="">
        <b style={{ fontSize: 14 }} data-f="merchant">
          Example Hypermarket · Mall branch
        </b>
        <br />
        Tax registration 100299882200003
        <br />
        Tel 04 555 0199
      </div>
      <div style={{ margin: '14px 0', borderTop: '1px dashed #98a2b3' }} />
      <div className="row2" data-b="">
        <span>
          Date <span data-f="purchase_date">16/11/26</span> 19:42
        </span>
        <span>Till 07 · Rcpt 55821</span>
      </div>
      <table data-t="" style={{ margin: '10px 0' }}>
        <tbody>
          {[
            ['Basmati rice 5 kg', '48.50'],
            ['Olive oil 1 L', '36.75'],
            ['Laundry liquid 3 L', '41.90'],
            ['Fresh produce', '27.35'],
            ['Bottled water 12×1.5 L', '31.90'],
          ].map(([a, b]) => (
            <tr key={a}>
              <td style={cell}>{a}</td>
              <td className="r" style={cell}>
                {b}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ borderTop: '1px dashed #98a2b3', margin: '8px 0' }} />
      <div className="row2" data-b="">
        <b>TOTAL AED</b>
        <b data-f="total_amount">186.40</b>
      </div>
      <div className="row2" data-b="">
        <span>VAT included 5%</span>
        <span data-f="vat_amount">8.88</span>
      </div>
      <div className="row2" data-b="" style={{ marginTop: 10 }}>
        <span>
          Card ••••{' '}
          <span data-f="card_last4" data-pii="">
            4417
          </span>
        </span>
        <span>Approved</span>
      </div>
      <p style={{ textAlign: 'center', margin: '18px 0 0', color: '#667085' }} data-b="">
        Thank you for shopping with us
      </p>
    </div>
  );
}

/** Offset of `el` relative to `root`, unaffected by transforms/zoom on ancestors. */
function offsetBox(el: HTMLElement, root: HTMLElement): Box {
  let x = 0;
  let y = 0;
  let cur: HTMLElement | null = el;
  while (cur && cur !== root) {
    x += cur.offsetLeft;
    y += cur.offsetTop;
    cur = cur.offsetParent as HTMLElement | null;
  }
  return [x, y, el.offsetWidth, el.offsetHeight];
}

interface Measured {
  blk: Box[];
  tbl: Box[];
  pii: Box[];
  fld: Record<string, Box>;
  w: number;
  h: number;
}

export interface DocumentViewerProps {
  kind: PaperKind;
  zoom?: number;
  layers: OverlayLayers;
  /** schema order determines colours */
  fields?: OverlayField[];
  threshold?: number;
  docNo?: string;
  children?: ReactNode;
}

export function DocumentViewer({ kind, zoom = 1, layers, fields = [], threshold = 0.85, docNo = 'INV-2026-0417' }: DocumentViewerProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [m, setM] = useState<Measured | null>(null);
  useLayoutEffect(() => {
    const paper = ref.current?.querySelector<HTMLElement>('.paper');
    if (!paper) return;
    const all = (sel: string) => [...paper.querySelectorAll<HTMLElement>(sel)].map((el) => offsetBox(el, paper));
    const fld: Record<string, Box> = {};
    paper.querySelectorAll<HTMLElement>('[data-f]').forEach((el) => (fld[el.dataset.f!] = offsetBox(el, paper)));
    setM({ blk: all('[data-b]'), tbl: all('[data-t]'), pii: all('[data-pii]'), fld, w: paper.offsetWidth, h: paper.offsetHeight });
  }, [kind, docNo]);
  const box = (b: Box) => ({ left: b[0] - 3, top: b[1] - 2, width: b[2] + 6, height: b[3] + 4 });
  return (
    <div className="pagewrap" style={m ? { width: m.w * zoom, height: m.h * zoom } : undefined} data-testid="docview">
      <div ref={ref} style={{ transform: `scale(${zoom})`, transformOrigin: '0 0', width: m?.w }}>
        <div style={{ position: 'relative' }}>
          {kind === 'rcp' ? <Receipt /> : <Invoice docNo={docNo} />}
          {m && (
            <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }} aria-hidden="true">
              {layers.blocks && m.blk.map((b, i) => <div key={'b' + i} className="ov blk" style={box(b)} />)}
              {layers.tables && m.tbl.map((b, i) => <div key={'t' + i} className="ov tbl" style={box(b)} />)}
              {layers.fields &&
                fields.map((f, i) => {
                  const b = m.fld[f.name];
                  if (!b) return null;
                  return (
                    <div key={'f' + f.name} className={`ov fld ${f.confidence < threshold ? 'low' : ''}`} style={{ ...box(b), ['--fc' as string]: FIELD_COLORS[i % 10] }} title={`${f.name} · ${f.confidence}`}>
                      <span>
                        {f.name} · {f.confidence.toFixed(2)}
                      </span>
                    </div>
                  );
                })}
              {layers.redactions && m.pii.map((b, i) => <div key={'p' + i} className="ov pii" style={box(b)} />)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
