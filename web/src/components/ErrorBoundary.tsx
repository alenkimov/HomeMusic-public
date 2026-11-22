import { Component, type ErrorInfo, type ReactNode } from 'react';

/** The last word of the page: an error thrown while drawing unmounts the whole React tree, and what is left is the
 * manifest's background — a black screen with nothing to tap. Here the error is said, with a way back. Kept plain
 * (no shared components): whatever threw may be among them. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('render failed', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
        <img src="/icons/icon.svg" alt="" width={56} height={56} className="rounded-2xl opacity-60" />
        <div className="text-base font-semibold">Что-то сломалось</div>
        <pre className="max-w-full overflow-auto whitespace-pre-wrap break-words text-xs opacity-60">{error.message || String(error)}</pre>
        <div className="flex gap-3">
          <button type="button" className="rounded-full bg-white/10 px-4 py-2 text-sm" onClick={() => this.setState({ error: null })}>
            Попробовать снова
          </button>
          <button type="button" className="rounded-full bg-white/10 px-4 py-2 text-sm" onClick={() => window.location.reload()}>
            Перезагрузить
          </button>
        </div>
      </div>
    );
  }
}
