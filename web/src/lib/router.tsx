// A tiny hash router: FastAPI only has to serve index.html, everything after "#" is client side.
import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from 'react';

/** Fired by `navigate()` (pushState does not fire `hashchange`). */
export const ROUTE_EVENT = 'hm:route';

function subscribe(cb: () => void) {
  window.addEventListener('hashchange', cb);
  window.addEventListener(ROUTE_EVENT, cb);
  return () => {
    window.removeEventListener('hashchange', cb);
    window.removeEventListener(ROUTE_EVENT, cb);
  };
}

const getHash = () => window.location.hash;

export interface Route {
  /** Path without the leading "#", e.g. "/album/dz:123". */
  path: string;
  query: URLSearchParams;
  /** Full hash target ("/search?q=x"), usable as `navigate()` argument. */
  full: string;
}

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '') || '/';
  const qi = raw.indexOf('?');
  const path = (qi >= 0 ? raw.slice(0, qi) : raw) || '/';
  const query = new URLSearchParams(qi >= 0 ? raw.slice(qi + 1) : '');
  return { path, query, full: raw };
}

export function useRoute(): Route {
  const background = useContext(RouteBackground);
  const hash = useSyncExternalStore(subscribe, getHash, getHash);
  return useMemo(() => background ?? parseHash(hash), [background, hash]);
}

export const RouteBackground = createContext<Route | null>(null);

// history.state.idx counts in-app entries so "back" never leaves the app.
type HistoryEntry = { idx?: number; background?: string };
const idx = (): number => {
  const st = window.history.state as HistoryEntry | null;
  return typeof st?.idx === 'number' ? st.idx : 0;
};

export function backgroundRoute(): Route | null {
  const background = (window.history.state as HistoryEntry | null)?.background;
  return background ? parseHash(background) : null;
}

type NavigationOptions = { replace?: boolean; overlay?: boolean };
export function navigate(to: string, opts: NavigationOptions = {}) {
  const target = '#' + to;
  if (opts.replace) {
    window.history.replaceState({ idx: idx() }, '', target);
  } else if (window.location.hash !== target) {
    const state: HistoryEntry = { idx: idx() + 1 };
    if (opts.overlay) state.background = getHash();
    window.history.pushState(state, '', target);
  } else {
    return;
  }
  window.dispatchEvent(new Event(ROUTE_EVENT));
  if (!opts.overlay) window.scrollTo(0, 0);
}

/** Go back if we have in-app history, otherwise to `fallback`. */
export function goBack(fallback: string) {
  if (idx() > 0) window.history.back();
  else navigate(fallback, { replace: true });
}

/** Match "/album/:ref" against a path. Returns decoded params or null. */
export function matchPath(pattern: string, path: string): Record<string, string> | null {
  const ps = pattern.split('/').filter(Boolean);
  const xs = path.split('/').filter(Boolean);
  if (ps.length !== xs.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < ps.length; i++) {
    const p = ps[i];
    if (p.startsWith(':')) {
      try {
        params[p.slice(1)] = decodeURIComponent(xs[i]);
      } catch {
        params[p.slice(1)] = xs[i];
      }
    } else if (p !== xs[i]) {
      return null;
    }
  }
  return params;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & NavigationOptions & { to: string };

export function Link({ to, replace, overlay, onClick, ...rest }: LinkProps) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    e.preventDefault();
    navigate(to, { replace, overlay });
  };
  return <a href={'#' + to} onClick={handle} {...rest} />;
}

export const enc = encodeURIComponent;

export const artistPath = (artist: { hub_artist_id?: number | null; ref: string }) =>
  `/artist/${enc(artist.hub_artist_id ?? artist.ref)}`;

export const albumPath = (album: { release_id?: number | null; ref: string }) =>
  `/album/${enc(album.release_id ?? album.ref)}`;

export function useCanonicalPath(from: string, path?: string) {
  const background = useContext(RouteBackground);
  useEffect(() => {
    if (background) return;
    const current = parseHash(window.location.hash);
    if (!path || current.path === path) return;
    try {
      // A response can arrive after the user has already followed another link.
      if (decodeURIComponent(current.path) !== decodeURIComponent(from)) return;
    } catch {
      return;
    }
    const query = current.query.toString();
    navigate(query ? `${path}?${query}` : path, { replace: true });
  }, [background, from, path]);
}
