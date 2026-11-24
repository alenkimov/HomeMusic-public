/** The app is not zoomed: a pinch shrank it on an iPhone, the player in the middle of the black screen. The viewport
 * forbids it (index.html: `user-scalable=no`, `minimum-scale=1`), but Safari on iOS has ignored that since iOS 10 —
 * there its own pinch gestures are turned off here. The double tap is `touch-action: manipulation` (index.css).
 * A two-finger pinch of the app's own (the artists' map) is pointer events and goes on as before. */
const noPinch = (e: Event) => e.preventDefault();
for (const name of ['gesturestart', 'gesturechange', 'gestureend']) document.addEventListener(name, noPinch, { passive: false });
export {};
