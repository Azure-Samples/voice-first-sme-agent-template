// Dev-only console wrappers.
//
// Vite replaces `import.meta.env.DEV` with a literal `true` / `false`
// at build time, so the production bundle tree-shakes these to no-ops
// and never ships the debug strings to end users' browser consoles.
//
// Use these for informational tracing; keep real `console.warn` /
// `console.error` calls intact so genuine failures stay visible in
// production devtools.
export const devLog: typeof console.log = import.meta.env.DEV
  ? console.log.bind(console)
  : () => {};

export const devInfo: typeof console.info = import.meta.env.DEV
  ? console.info.bind(console)
  : () => {};
