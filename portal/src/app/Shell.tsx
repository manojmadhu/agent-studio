/** App shell: 240px navy sidebar (Operate · Build · Govern) + 52px top bar; compact rail in builder/review. */
import { Fragment, useEffect } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router';
import { Icon } from '../ui/Icon';
import { Toasts } from '../ui/Toasts';
import { Menu } from '../ui/Menu';
import { closeMenu } from '../ui/menuStore';
import { useReviewItems } from '../api/hooks';
import { getApiConfig, onApiConfig } from '../api/config';
import { useSyncExternalStore } from 'react';
import { CommandPalette, useCmdk } from './CommandPalette';
import { NAV } from './nav';
import { useCrumbStore } from './crumbs';

function Logo() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="6" fill="#1652c8" />
      <path d="M9 7h7l4 4v10H9z" fill="#fff" />
      <path d="M16 7v4h4z" fill="#b9cdf5" />
      <rect x="11.5" y="14" width="6" height="1.6" rx=".8" fill="#0e7490" />
      <rect x="11.5" y="17.2" width="4" height="1.6" rx=".8" fill="#1652c8" />
    </svg>
  );
}

const useApiMode = () => useSyncExternalStore(onApiConfig, () => getApiConfig().mode);

export function Shell() {
  const loc = useLocation();
  const nav = useNavigate();
  const crumbs = useCrumbStore((s) => s.crumbs);
  const review = useReviewItems().data?.length ?? 0;
  const mode = useApiMode();
  const seg = loc.pathname.split('/').filter(Boolean);
  const page = seg[0] ?? 'pipelines';
  const compact = page === 'builder' || (page === 'review' && !!seg[1]);

  useEffect(() => {
    closeMenu();
    document.querySelector('#content')?.scrollTo?.(0, 0);
  }, [loc.pathname]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        useCmdk.getState().set(true);
      }
      if (e.key === 'Escape') closeMenu();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <>
      <a href="#content" className="skip-link" onClick={(e) => {
        e.preventDefault();
        document.getElementById('content')?.focus();
      }}>
        Skip to content
      </a>
      <div className={`app ${compact ? 'compact' : ''}`}>
        <aside className="side" aria-label="Primary">
          <div className="ws">
            <span className="logo">
              <Logo />
            </span>
            <div>
              <b>Doc Intelligence</b>
              <small>Example Trading · {mode === 'mock' ? 'Sandbox' : 'Live API'}</small>
            </div>
          </div>
          <nav className="nav" id="nav">
            {NAV.map((g, gi) => (
              <Fragment key={gi}>
                {g.h ? <h4>{g.h}</h4> : <h4 aria-hidden="true">&nbsp;</h4>}
                {g.items.map((it) => {
                  const on = page === it.key || (it.key === 'pipelines' && page === 'builder');
                  const n = it.badge === 'review' ? review : 0;
                  return (
                    <Link key={it.key} to={it.to} className={on ? 'on' : ''} aria-current={on ? 'page' : undefined} title={it.label}>
                      <Icon name={it.icon} size={18} />
                      <span>{it.label}</span>
                      {n ? <span className="badge brand">{n}</span> : null}
                    </Link>
                  );
                })}
              </Fragment>
            ))}
          </nav>
          <div className="me">
            <span className="avatar">MM</span>
            <div>
              <b>Manoj M.</b>
              <small>Workspace admin</small>
            </div>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div className="crumbs" id="crumbs">
              {crumbs.map(([l, to], i) =>
                i < crumbs.length - 1 ? (
                  <Fragment key={i}>
                    <Link to={to}>{l}</Link>
                    <Icon name="chevron--right" size={14} />
                  </Fragment>
                ) : (
                  <b key={i}>{l}</b>
                ),
              )}
            </div>
            <div className="grow" />
            <button className="search" aria-label="Search and commands" onClick={() => useCmdk.getState().set(true)}>
              <Icon name="search" />
              <span className="stx">Search or jump to…</span>
              <span className="kbd">⌘K</span>
            </button>
            <button className="env" title={mode === 'mock' ? 'Runs use sample documents and the in-app mock backend' : 'Connected to the live API'} onClick={() => nav('/settings')}>
              <i style={mode === 'live' ? { background: 'var(--success)' } : undefined} />
              {mode === 'mock' ? 'Sandbox' : 'Live'}
            </button>
            <button className="btn ghost icon sm" aria-label="Notifications">
              <Icon name="notification" size={18} />
            </button>
          </header>
          <main className="content" id="content" tabIndex={-1}>
            <Outlet />
          </main>
        </div>
      </div>
      <Toasts />
      <Menu />
      <CommandPalette />
    </>
  );
}
