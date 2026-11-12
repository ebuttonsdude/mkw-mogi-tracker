(() => {
  const root = document.documentElement;
  const picker = document.getElementById('character-theme');
  if (!picker) return;
  const choices = Array.from(picker.options, option => option.value);
  function apply(value) {
    const choice = choices.includes(value) ? value : 'mario';
    picker.value = choice;
    root.dataset.character = choice;
    root.classList.toggle('dark', choice !== 'light');
    root.classList.toggle('light', choice === 'light');
    root.style.colorScheme = choice === 'light' ? 'light' : 'dark';
  }
  apply(root.dataset.character || 'mario');
  picker.addEventListener('change', () => {
    apply(picker.value);
    try {
      localStorage.setItem('mkw-docs-character', picker.value);
      localStorage.setItem('color-theme', picker.value === 'light' ? 'light' : 'dark');
    } catch (_) { /* Themes still work when browser storage is unavailable. */ }
  });
})();
