import { useEffect, useRef, type ReactNode } from 'react';

export interface DialogProps {
  title: string;
  desc?: ReactNode;
  children?: ReactNode;
  onClose: () => void;
  /** omit for an info dialog with only "Close" */
  onOk?: () => void | boolean | Promise<void | boolean>;
  okLabel?: string;
  danger?: boolean;
  busy?: boolean;
  okDisabled?: boolean;
  wide?: boolean;
  footer?: ReactNode;
  cancelLabel?: string;
}

/** Modal dialog (prototype `.scrim .dialog`): Esc and scrim click close; focus moves to the first field. */
export function Dialog({ title, desc, children, onClose, onOk, okLabel = 'Save', danger, busy, okDisabled, wide, footer, cancelLabel }: DialogProps) {
  const ref = useRef<HTMLDivElement>(null);
  const prev = useRef<Element | null>(null);
  useEffect(() => {
    prev.current = document.activeElement;
    const el = ref.current?.querySelector<HTMLElement>('input:not([type=hidden]):not([disabled]),textarea,select') ?? ref.current?.querySelector<HTMLElement>('[data-ok],[data-x]');
    el?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
      if (e.key === 'Tab' && ref.current) {
        const f = [...ref.current.querySelectorAll<HTMLElement>('button,input,select,textarea,a[href],[tabindex]:not([tabindex="-1"])')].filter((x) => !x.hasAttribute('disabled'));
        if (!f.length) return;
        const first = f[0]!;
        const last = f[f.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      (prev.current as HTMLElement | null)?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const ok = async () => {
    if (!onOk) return;
    const r = await onOk();
    if (r !== false) onClose();
  };
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dlgT" ref={ref} style={wide ? { width: 'min(720px,100%)' } : undefined}>
        <div className="dh">
          <h2 id="dlgT">{title}</h2>
          {desc && <p>{desc}</p>}
        </div>
        {children && <div className="db">{children}</div>}
        <div className="df">
          {footer}
          <button className="btn" data-x onClick={onClose}>
            {cancelLabel ?? (onOk ? 'Cancel' : 'Close')}
          </button>
          {onOk && (
            <button
              className={'btn ' + (danger ? '' : 'primary')}
              style={danger ? { background: 'var(--danger)', borderColor: 'var(--danger)', color: '#fff' } : undefined}
              data-ok
              disabled={busy || okDisabled}
              onClick={() => void ok()}
            >
              {okLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
