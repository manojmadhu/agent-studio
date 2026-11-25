import { Icon } from './Icon';
import { useToasts } from './toast';

export function Toasts() {
  const items = useToasts((s) => s.items);
  return (
    <div id="toasts" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={'toast' + (t.bad ? ' bad' : '')}>
          <span className="tile">
            <Icon name={t.bad ? 'warning' : 'checkmark'} size={14} />
          </span>
          <span>{t.msg}</span>
        </div>
      ))}
    </div>
  );
}
