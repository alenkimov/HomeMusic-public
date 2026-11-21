/** Spotify URLs in a pasted link or an operating-system share payload. */
export function spotifyURL(text: string): string | undefined {
  return text.match(/(?:https?:\/\/(?:open\.spotify\.com|play\.spotify\.com|spotify\.link|spotify\.app\.link)\/[\w./?&=%+#-]+|spotify:(?:artist|album|playlist|track):[\da-zA-Z]{22})/)?.[0];
}
