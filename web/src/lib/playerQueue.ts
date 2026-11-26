/** The full player opens with its queue — or its lyrics — shown when it was asked for (the buttons of the desktop's
 * bar). */
let asked = false;
let lyrics = false;

export const askForLyrics = () => {
  lyrics = true;
};

/** Were the lyrics asked for? Read once, as the queue is. */
export function lyricsAsked(): boolean {
  const was = lyrics;
  lyrics = false;
  return was;
}

export const askForQueue = () => {
  asked = true;
};

/** Was the queue asked for? Read once: the next opening of the player is a plain one. */
export function queueAsked(): boolean {
  const was = asked;
  asked = false;
  return was;
}
