/** Mobile stacked tables: copy each column header into `data-l` of its cells (prototype MutationObserver). */
export function installStackLabels(): void {
  const apply = () => {
    for (const c of document.querySelectorAll<HTMLTableCellElement>('.stack td:not([data-l])')) {
      let i = 0;
      let p = c.previousElementSibling as HTMLTableCellElement | null;
      while (p) {
        i += p.colSpan;
        p = p.previousElementSibling as HTMLTableCellElement | null;
      }
      c.dataset.l = c.closest('table')?.rows[0]?.cells[i]?.textContent ?? '';
    }
  };
  new MutationObserver(apply).observe(document.documentElement, { childList: true, subtree: true });
}
