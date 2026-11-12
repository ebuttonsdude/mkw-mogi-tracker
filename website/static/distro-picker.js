(() => {
  const picker = document.querySelector('[data-distro-picker]');
  if (!picker) return;
  const tabs = Array.from(picker.querySelectorAll('[role="tab"]'));
  function select(tab, focus = false) {
    for (const item of tabs) {
      const active = item === tab;
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !active;
    }
    for (const note of document.querySelectorAll('[data-distro-note]')) {
      note.hidden = note.dataset.distroNote !== tab.id.replace('tab-', '');
    }
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      let target;
      if (event.key === 'ArrowRight') target = tabs[(index + 1) % tabs.length];
      if (event.key === 'ArrowLeft') target = tabs[(index + tabs.length - 1) % tabs.length];
      if (event.key === 'Home') target = tabs[0];
      if (event.key === 'End') target = tabs[tabs.length - 1];
      if (target) { event.preventDefault(); select(target, true); }
    });
  });
  function fromHash() {
    const id = { '#arch-linux': 'tab-arch', '#fedora': 'tab-fedora', '#ubuntu': 'tab-ubuntu' }[location.hash];
    const tab = tabs.find(tab => tab.id === id);
    if (tab) select(tab);
  }
  select(tabs[0]);
  fromHash();
  window.addEventListener('hashchange', fromHash);
})();
