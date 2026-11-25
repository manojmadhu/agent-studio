/** Sample documents and extraction values used by simulated runs (ported from the prototype). */
export type SampleKind = 'inv' | 'rcp';

export const SAMPLE_DOCS: Record<string, { id: string; kind: SampleKind; filename: string; docKind: string; mime: string }> = {
  doc_inv_sample: { id: 'doc_inv_sample', kind: 'inv', filename: 'INV-2026-0417.pdf', docKind: 'invoice', mime: 'application/pdf' },
  doc_rcp_sample: { id: 'doc_rcp_sample', kind: 'rcp', filename: 'receipt_0918.jpg', docKind: 'receipt', mime: 'image/jpeg' },
};

export const VALUES: Record<SampleKind, Record<string, [string | number, number]>> = {
  inv: {
    invoice_number: ['INV-2026-0417', 0.99],
    vendor_name: ['Gulf Office Supplies LLC', 0.98],
    vendor_trn: ['100354872600003', 0.97],
    invoice_date: ['2026-11-18', 0.98],
    due_date: ['2026-12-18', 0.95],
    po_number: ['PO-88213', 0.93],
    subtotal: [12400, 0.97],
    vat_amount: [620, 0.96],
    total_amount: [13020, 0.88],
    iban: ['AE07 0331 2345 6789 0123 456', 0.94],
  },
  rcp: {
    merchant: ['Example Hypermarket · Mall branch', 0.95],
    purchase_date: ['2026-11-16', 0.64],
    total_amount: [186.4, 0.93],
    vat_amount: [8.88, 0.9],
    card_last4: ['4417', 0.97],
  },
};
