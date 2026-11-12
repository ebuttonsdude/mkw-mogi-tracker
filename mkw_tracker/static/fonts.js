'use strict';

// Shared by the dashboard preview and OBS. FontFace keeps the CSP intact;
// display: swap leaves names readable while loading, or after a failed request.
window.TrackerFonts = (() => {
  const registered = new Map();
  const fallback = '"Noto Sans CJK JP", "Yu Gothic", Meiryo, "Noto Sans", system-ui, sans-serif';
  function apply(target, description) {
    const families = [];
    for (const face of description?.faces || []) {
      if (!face?.id || typeof face.url !== 'string' || !face.url.startsWith('/api/fonts/')) continue;
      let entry = registered.get(face.id);
      if (!entry || entry.url !== face.url || entry.font.status === 'error') {
        const family = entry?.family || `TrackerFont${registered.size}`;
        try {
          const font = new FontFace(family, `url(${JSON.stringify(face.url)})`, {display: 'swap'});
          if (entry) document.fonts.delete(entry.font);
          document.fonts.add(font);
          // Avoid an unhandled rejection if a runtime font disappears.
          font.loaded.catch(() => {});
          entry = {family, font, url: face.url};
          registered.set(face.id, entry);
        } catch { continue; }
      }
      families.push(`"${entry.family}"`);
    }
    target.style.fontFamily = [...families, fallback].join(', ');
  }
  return {apply};
})();
