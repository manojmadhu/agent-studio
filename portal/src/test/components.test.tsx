import { describe, expect, it } from 'vitest';
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { ReactFlow, ReactFlowProvider } from '@xyflow/react';
import type { ReactNode } from 'react';
import { makeQueryClient } from '../api/hooks';
import { routes } from '../app/App';
import { DocumentViewer } from '../features/document/DocumentViewer';
import { Dialog } from '../ui/Dialog';
import { StepNode, AiComponentNode, type StepFlowNode } from '../features/builder/StepNode';
import { BuilderActionsCtx } from '../features/builder/actions';
import { newNode } from '../features/builder/graph';

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const qc = makeQueryClient();
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe('screens', () => {
  it('Pipelines lists the seeded pipelines with KPIs and filters', async () => {
    renderAt('/pipelines');
    expect(await screen.findByText('Accounts payable · supplier invoices')).toBeInTheDocument();
    expect(screen.getByText('Employee expense receipts')).toBeInTheDocument();
    expect(await screen.findByText('1,626')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Paused/ }));
    expect(screen.queryByText('Employee expense receipts')).not.toBeInTheDocument();
    expect(screen.getByText('Customer onboarding · identity documents')).toBeInTheDocument();
  });
  it('New connection renders credential fields for the chosen auth scheme (secrets as password inputs)', async () => {
    renderAt('/connections');
    fireEvent.click(await screen.findByRole('button', { name: /New connection/ }));
    const dlg = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dlg).getByRole('option', { name: 'PostgreSQL' })).toBeInTheDocument());
    fireEvent.change(within(dlg).getByDisplayValue('HTTP API'), { target: { value: 'postgresql' } });
    await waitFor(() => expect(within(dlg).getByText('Host')).toBeInTheDocument());
    expect(within(dlg).getByText('Username')).toBeInTheDocument();
    const pwd = dlg.querySelector('input[name=a_password]') as HTMLInputElement;
    expect(pwd.type).toBe('password');
    expect(pwd.autocomplete).toBe('new-password');
  });
  it('Review queue shows items and SLA badges', async () => {
    renderAt('/review');
    expect(await screen.findByText('INV-2026-0388.pdf')).toBeInTheDocument();
    expect(screen.getByText('Low confidence · total_amount 0.71')).toBeInTheDocument();
  });
});

describe('components', () => {
  it('DocumentViewer draws the requested overlay layers', () => {
    const { container } = render(<DocumentViewer kind="inv" layers={{ blocks: true, tables: true, fields: true, redactions: true }} fields={[{ name: 'total_amount', confidence: 0.71 }]} />);
    expect(container.querySelector('.paper')).toBeInTheDocument();
    expect(container.querySelectorAll('.ov.blk').length).toBeGreaterThan(3);
    expect(container.querySelectorAll('.ov.tbl')).toHaveLength(1);
    expect(container.querySelectorAll('.ov.pii')).toHaveLength(3);
    const f = container.querySelector('.ov.fld')!;
    expect(f).toHaveClass('low');
    expect(f.textContent).toContain('total_amount · 0.71');
  });
  it('Dialog closes on Escape and runs onOk', async () => {
    let closed = 0;
    let ok = 0;
    render(
      <Dialog title="Hello" onClose={() => closed++} onOk={() => void ok++} okLabel="Go">
        <input aria-label="x" />
      </Dialog>,
    );
    expect(screen.getByLabelText('x')).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));
    await waitFor(() => expect(ok).toBe(1));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(closed).toBe(2);
  });
  it('StepNode renders ports, AI tag and slots; AiComponentNode renders its plug', () => {
    const x = newNode('ai.extract', 0, 0, {}, 'Extract invoice fields', 'x');
    const m = newNode('ai.model', 0, 200, {}, undefined, 'm');
    const nodes: StepFlowNode[] = [
      { id: 'x', type: 'step', position: { x: 0, y: 0 }, data: { gn: x, summary: 'Invoice field extraction', bad: true, full: { model: true }, selected: true } },
      { id: 'm', type: 'component', position: { x: 0, y: 200 }, data: { gn: m, summary: 'claude-sonnet-5-5', bad: false, full: {}, selected: false, status: 'ok' } },
    ];
    const wrap = (c: ReactNode) => (
      <BuilderActionsCtx.Provider value={{ testNode: () => {}, configure: () => {}, slotMenu: () => {}, portMenu: () => {} }}>
        <ReactFlowProvider>
          <div style={{ width: 800, height: 600 }}>{c}</div>
        </ReactFlowProvider>
      </BuilderActionsCtx.Provider>
    );
    const { container } = render(wrap(<ReactFlow nodes={nodes} edges={[]} nodeTypes={{ step: StepNode, component: AiComponentNode }} />));
    const step = container.querySelector('.nd:not(.sub)')!;
    expect(step).toHaveClass('ai', 'sel', 'bad');
    expect(step.querySelector('.aitag')?.textContent).toBe('AI');
    expect(step.querySelectorAll('.port.in')).toHaveLength(1);
    expect(step.querySelectorAll('.port.out')).toHaveLength(1);
    expect([...step.querySelectorAll('.slot')].map((s) => s.getAttribute('data-handleid'))).toEqual(['model', 'tools', 'knowledge', 'parser']);
    expect(step.querySelector('.slot.full')?.getAttribute('data-handleid')).toBe('model');
    expect(step.querySelector('.slot.need')?.getAttribute('data-handleid')).toBe('parser');
    const sub = container.querySelector('.nd.sub')!;
    expect(sub).toHaveClass('ok');
    expect(sub.querySelectorAll('.port.plug')).toHaveLength(1);
    expect(sub.textContent).toContain('claude-sonnet-5-5');
  });
});
