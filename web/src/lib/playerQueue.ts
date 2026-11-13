/** The full player opens with its queue shown when it was asked for (the queue button of the desktop's bar). */
let asked = false;

export const askForQueue = () => {
  asked = true;
};

/** Was the queue asked for? Read once: the next opening of the player is a plain one. */
export function queueAsked(): boolean {
  const was = asked;
  asked = false;
  return was;
}
