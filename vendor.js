const pendingScripts = new Map();

/** Load a classic browser bundle once and return its global export. */
export function loadGlobalScript(globalName, src) {
  if (globalThis[globalName]) return Promise.resolve(globalThis[globalName]);
  if (pendingScripts.has(globalName)) return pendingScripts.get(globalName);

  const promise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-pdf-tools-lib="${globalName}"]`);
    const script = existing || document.createElement('script');
    const finish = () => {
      if (globalThis[globalName]) resolve(globalThis[globalName]);
      else reject(new Error(`${globalName} loaded from ${src}, but did not expose the expected browser global.`));
    };
    script.addEventListener('load', finish, { once: true });
    script.addEventListener('error', () => reject(new Error(`Could not load ${globalName}. Check your internet connection or CDN access: ${src}`)), { once: true });
    if (!existing) {
      script.src = src;
      script.async = true;
      script.crossOrigin = 'anonymous';
      script.dataset.pdfToolsLib = globalName;
      document.head.append(script);
    } else if (globalThis[globalName]) {
      resolve(globalThis[globalName]);
    }
  }).catch((error) => {
    pendingScripts.delete(globalName);
    throw error;
  });

  pendingScripts.set(globalName, promise);
  return promise;
}
