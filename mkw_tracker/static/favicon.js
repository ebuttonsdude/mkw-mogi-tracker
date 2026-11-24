/* Follow saved theme state, never unsaved editor drafts or external image URLs. */
window.updateThemeFavicon = (theme) => {
  const link = document.getElementById('theme-favicon');
  if (!link || !theme?.id) return;
  const version = theme.icon_url || `${theme.tokens?.panel}-${theme.tokens?.accent}`;
  const query = new URLSearchParams({theme: theme.id, v: version});
  const href = `/favicon.ico?${query}`;
  // Polling unchanged state must not continually reload the tab icon.
  if (link.getAttribute('href') !== href) link.setAttribute('href', href);
};
