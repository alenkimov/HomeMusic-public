// Handing text to another app (an invite code to a messenger): the browser's Web Share, or the clipboard.

export function canShare(): boolean {
  return typeof navigator.share === 'function';
}

/** Resolves false when the person closed the share sheet. */
export async function shareText(text: string): Promise<boolean> {
  try {
    await navigator.share({ text });
    return true;
  } catch {
    return false;
  }
}

/** Copies within the tap: the old way first — plain http has no Clipboard API at all; then the Clipboard API. */
export async function copyText(text: string): Promise<boolean> {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.top = '0';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, text.length);
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  ta.remove();
  if (ok) return true;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
