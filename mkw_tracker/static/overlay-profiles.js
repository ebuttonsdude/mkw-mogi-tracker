'use strict';
// Source drafts are separate from in-game roster identities and from polling.
const OverlaySources = (() => {
  let selected = 'default';
  let dirty = false;
  let overlayDirty = false;
  let contentDirty = false;
  let revision = 0;
  let signature = '';
  let cachedProfiles = null;
  let serverRevision = -1;
  let serverInstance = null;
  let fonts = [];
  let fontsLoaded = false;
  let fontSignature = '';
  let tabVisible = false;
  let previewActive = false;
  let previewKey = null;
  let previewMode = null;
  let previewSize = null;
  let previewEpoch = 0;
  let lastMode = null;
  let saving = false;
  const metricLabels = {username: 'Username', division: 'Division', mmr: 'MMR', leaderboard_rank: 'Leaderboard rank', peak_mmr: 'Peak MMR · season', events: 'Events · season', positive_mmr_rate: 'Positive-MMR event rate · season', average_score: 'Average event score · season', last_five_form: 'Form · last up to 5 events', last_ten_record: 'Win % · last up to 10 events', last_ten_mmr_change: 'Event MMR change · last up to 10', last_ten_average_score: 'Average score · last up to 10', weekly_events: 'Verified events · derived last 7 days', weekly_positive_mmr_rate: 'Positive-MMR rate · derived last 7 days', weekly_event_mmr_change: 'Event MMR change · derived last 7 days', weekly_average_score: 'Average score · derived last 7 days'};
  const blocks = ['header', 'standings', 'ww_player', 'ww_stats', 'ww_graph', 'lounge'];
  const byId = id => document.getElementById(id);
  const current = state => (cachedProfiles || state.overlay_profiles)?.find(p => p.slug === selected);
  const invalidate = () => { dirty = true; revision++; };
  const endpoint = () => selected === 'default' ? '/api/overlay' : `/api/overlay/${selected}`;
  function accept(profiles, version, instance) {
    if (instance !== serverInstance) { serverRevision = -1; serverInstance = instance; }
    if (version >= serverRevision) { cachedProfiles = profiles; serverRevision = version; }
  }
  async function reload() {
    const result = await api('/api/overlay-profiles');
    accept(result.profiles, result.revision, result.instance);
    signature = '';
  }
  function pausePreview(reason = 'Preview paused. Apply or refresh to load saved settings.') {
    previewEpoch++;
    previewActive = false; previewKey = null; previewMode = null; previewSize = null;
    byId('overlay-live-preview').removeAttribute('src');
    byId('overlay-preview-stage').hidden = true;
    byId('overlay-preview-status').textContent = reason;
  }
  function sizePreview() {
    if (!tabVisible || !previewActive || !previewSize) return;
    const stage = byId('overlay-preview-stage');
    const frame = byId('overlay-live-preview');
    const [width, height] = previewSize;
    const shell = stage.parentElement;
    const shellStyle = getComputedStyle(shell);
    const available = shell.clientWidth - parseFloat(shellStyle.paddingLeft) - parseFloat(shellStyle.paddingRight);
    const scale = Math.min(1, available / width, Math.max(120, Math.min(360, innerHeight * .55)) / height);
    frame.style.transform = `scale(${scale})`;
    stage.style.width = `${Math.ceil(width * scale)}px`;
    stage.style.height = `${Math.ceil(height * scale)}px`;
  }
  function previewGeometry(source) {
    const width = source.effective.overlay.width;
    const height = source.effective.overlay.height;
    previewSize = [width, height];
    const frame = byId('overlay-live-preview');
    frame.width = width; frame.height = height;
    frame.style.width = `${width}px`; frame.style.height = `${height}px`;
    sizePreview();
  }
  function previewMetadata(source) {
    byId('overlay-preview-description').textContent = `${source.url} · saved ${source.effective.overlay.width} × ${source.effective.overlay.height} px · scaled to fit this page`;
    if (previewActive && previewKey === selected) previewGeometry(source);
  }
  function setTab(visible) {
    tabVisible = visible;
    if (!visible) pausePreview('Preview paused while away from Overlays. Apply or refresh to resume.');
    else sizePreview();
  }
  function startPreview(key, mode) {
    if (!tabVisible || selected !== key || (state.tracker_mode || 'mogi') !== mode) return;
    const source = current(state);
    if (!source || !/^\/overlay(?:\/[a-z0-9-]+)?$/.test(source.url)) {
      pausePreview('Saved source is unavailable. Choose another source.');
      return;
    }
    previewActive = true; previewKey = key; previewMode = mode;
    byId('overlay-preview-stage').hidden = false;
    previewMetadata(source);
    byId('overlay-preview-status').textContent = 'Loading saved preview…';
    byId('overlay-live-preview').src = source.url;
  }
  function refreshActivePreview(key, mode) {
    if (previewActive && previewKey === key && previewMode === mode) startPreview(key, mode);
  }
  async function requestPreview() {
    if (saving) throw new Error('An overlay save is still running. Refresh the preview after it finishes.');
    const key = selected, mode = state.tracker_mode || 'mogi', epoch = previewEpoch;
    await reload();
    if (epoch === previewEpoch) startPreview(key, mode);
  }
  function clearBackgroundFile() {
    byId('lounge-background-file').value = '';
  }
  function renderBackground(source) {
    const url = source.lounge.appearance.image_url;
    const safe = typeof url === 'string' && /^\/api\/overlay-profiles\/[a-z0-9-]+\/background\.png\?v=[0-9a-f]{16}$/.test(url);
    const thumbnail = byId('lounge-background-thumbnail');
    thumbnail.hidden = !safe;
    if (safe) { if (thumbnail.getAttribute('src') !== url) thumbnail.src = url; }
    else thumbnail.removeAttribute('src');
    byId('lounge-background-remove').hidden = !safe;
    byId('lounge-background-status').textContent = safe ?
      'Uploaded background saved for this source. Select Uploaded image and Save or Apply to use it.' :
      'No uploaded background for this source. Uploaded image uses the source background until an image is saved.';
  }
  async function changeBackground(remove = false) {
    if (saving) throw new Error('Another overlay save is still running.');
    const key = selected, before = revision, mode = state.tracker_mode || 'mogi', epoch = previewEpoch;
    const file = byId('lounge-background-file').files[0];
    if (!remove && (!file || !file.size || file.size > 4 * 1024 * 1024)) {
      throw new Error('Choose a PNG, JPEG or WebP still image no larger than 4 MiB.');
    }
    saving = true;
    let saved = false;
    try {
      let encoded;
      if (!remove) {
        encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = () => reject(new Error('The image could not be read.'));
          reader.onabort = () => reject(new Error('Image reading was cancelled.'));
          reader.readAsDataURL(file);
        });
        if (selected !== key || revision !== before || previewEpoch !== epoch ||
            (state.tracker_mode || 'mogi') !== mode || byId('lounge-background-file').files[0] !== file) {
          throw new Error('Source or draft changed while reading the image. Choose the image again for the intended source.');
        }
      }
      await api(`/api/overlay-profiles/${key}/background`, remove ? 'DELETE' : 'POST',
        {expected_tracker_mode: mode, ...(!remove ? {image_base64: encoded} : {})});
      saved = true;
      await reload();
      if (key === selected && before === revision && epoch === previewEpoch && (state.tracker_mode || 'mogi') === mode) {
        clearBackgroundFile();
        if (!remove) {
          byId('lounge-background').value = 'custom';
          contentDirty = true; invalidate();
        }
      }
      await refresh(); render(state);
      refreshActivePreview(key, mode);
      message(`${remove ? 'Background removed' : 'Background uploaded'} for source ${key}. ` +
        (remove ? 'Other appearance drafts are unchanged.' : 'Choose Uploaded image, then Save or Apply & preview to activate it. Other drafts are kept.'));
    } catch (error) {
      throw new Error(`${saved ? 'Background change was saved, but the dashboard could not refresh.' : 'Background was not changed.'} ${error.message}`);
    } finally { saving = false; }
  }
  function sourceValues() {
    const season = byId('lounge-season').value;
    return {
      name: byId('overlay-source-name').value,
      theme: byId('overlay-source-theme').value || null,
      report_font: byId('overlay-source-font').value || null,
      content: Object.fromEntries(blocks.map(block => [block, byId(`source-${block}`).checked])),
      lounge: {enabled: byId('lounge-enabled').checked,
        appearance: Object.fromEntries(['background', 'border', 'shape'].map(key => [key, byId(`lounge-${key}`).value])),
        metrics: Object.fromEntries(Object.keys(metricLabels).map(key => [key, byId(`lounge-metric-${key}`).checked])),
        players: byId('lounge-usernames').value.split('\n').map(value => value.trim()).filter(Boolean),
        season: season === '' ? null : Number(season), room_size: Number(byId('lounge-room-size').value)}
    };
  }
  async function writeDisplay(key, overlay, mode) {
    await api(key === 'default' ? '/api/settings' : `/api/overlay-profiles/${key}`, 'PATCH',
      {overlay, expected_tracker_mode: mode});
  }
  async function applyAndPreview() {
    if (saving) throw new Error('Another overlay save is still running.');
    for (const form of [byId('overlay-source-settings'), byId('overlay-settings')]) {
      if (!form.checkValidity()) {
        const details = form.closest('details');
        if (details) details.open = true;
        form.reportValidity();
        return;
      }
    }
    const key = selected, before = revision, mode = state.tracker_mode || 'mogi';
    const source = sourceValues();
    const overlay = collectOverlaySettings(byId('overlay-settings'), mode);
    pausePreview('Applying source and display settings…');
    const epoch = previewEpoch;
    let sourceSaved = false, displaySaved = false;
    saving = true;
    try {
      await api(`/api/overlay-profiles/${key}`, 'PATCH', {...source, expected_tracker_mode: mode});
      sourceSaved = true;
      if ((state.tracker_mode || 'mogi') !== mode) throw new Error('Tracker mode changed during Apply.');
      await writeDisplay(key, overlay, mode);
      displaySaved = true;
      await reload();
      const sameDraft = key === selected && before === revision && (state.tracker_mode || 'mogi') === mode;
      if (sameDraft) { dirty = false; overlayDirty = false; contentDirty = false; signature = ''; }
      await refresh(); render(state);
      if (sameDraft && key === selected && before === revision && epoch === previewEpoch &&
          tabVisible && (state.tracker_mode || 'mogi') === mode) {
        startPreview(key, mode);
        message('Source and display settings applied. Preview shows saved settings.');
      } else message('Captured source and display settings saved. Newer drafts are kept; preview remains paused.');
    } catch (error) {
      if (key === selected) { dirty = true; overlayDirty = true; contentDirty = true; }
      // These are two existing saves, not an atomic transaction.
      const outcome = displaySaved ? 'Both settings were saved, but the preview could not refresh.' :
        sourceSaved ? 'Source content was saved, but display settings were not saved.' : 'Settings were not applied.';
      throw new Error(`${outcome} Drafts are kept. ${error.message}`);
    } finally { saving = false; }
  }
  function renderSourceFonts(value) {
    const next = JSON.stringify([fonts, fontsLoaded, value]);
    if (next === fontSignature) { byId('overlay-source-font').value = value; return; }
    fontSignature = next;
    const select = byId('overlay-source-font');
    select.replaceChildren(new Option('Use dashboard font', ''),
      ...fonts.map(font => new Option(`${font.name} (${font.source})`, font.id)));
    if (value && !fonts.some(font => font.id === value)) {
      select.append(new Option(`${fontsLoaded ? 'Saved font unavailable' : 'Saved font · catalog not loaded'} (${value})`, value));
    }
    select.value = value;
  }
  function setFonts(choices) {
    fonts = choices;
    fontsLoaded = true;
    // Catalog refresh is not a source edit: preserve even an unsaved selection.
    renderSourceFonts(byId('overlay-source-font').value);
  }
  function recommendLoungeSize(state) {
    const output = byId('lounge-size-recommendation');
    const button = byId('lounge-use-size');
    button.disabled = true;
    delete output.dataset.width;
    delete output.dataset.height;
    const names = byId('lounge-usernames').value.split('\n').map(name => name.trim()).filter(Boolean);
    const enabled = Object.keys(metricLabels).filter(key => byId(`lounge-metric-${key}`).checked);
    const form = byId('overlay-settings');
    const font = Number(form.elements.namedItem('font_size').value);
    if (state.tracker_mode === 'ww' || !byId('source-lounge').checked || !names.length || !enabled.length) {
      output.textContent = 'Lounge size estimate: select Lounge cards, at least one metric and a username in Mogi Teams or FFA.';
      return null;
    }
    if (names.length > 8 || names.some(name => name.length > 64) || !Number.isFinite(font) || font < 12 || font > 48) {
      output.textContent = 'Lounge size estimate: use up to eight usernames (64 characters each) and a font size from 12 to 48.';
      return null;
    }
    // No Lounge lookup: representative values make this a local draft estimate,
    // not a guarantee about a chosen font or future remote values.
    const context = document.createElement('canvas').getContext('2d');
    const family = getComputedStyle(byId('font-preview')).fontFamily;
    const measure = (text, scale = 1) => {
      if (!context) return Array.from(text).length * font * scale;
      context.font = `850 ${font * scale}px ${family}`;
      return context.measureText(text).width;
    };
    const samples = {
      leaderboard_rank: ['SZN', 'Rank', '#99999'], peak_mmr: ['SZN', 'Peak', '99999'],
      events: ['SZN', 'Events', '999'], positive_mmr_rate: ['SZN', 'Win %', '100%'],
      average_score: ['SZN', 'Avg', '999.99'], last_ten_record: ['L10', 'Win %', '100%'],
      last_ten_mmr_change: ['L10', 'MMR', '-9999'], last_ten_average_score: ['L10', 'Avg', '999.99'],
      weekly_events: ['7D', 'Events', '999'], weekly_positive_mmr_rate: ['7D', 'Win %', '100%'],
      weekly_event_mmr_change: ['7D', 'MMR', '-9999'], weekly_average_score: ['7D', 'Avg', '999.99']
    };
    const season = byId('lounge-season').value;
    const combined = season !== '' && Number(season) < 2;
    const metrics = enabled.filter(key => Object.hasOwn(samples, key)).map(key => {
      const [scope, label, value] = samples[key];
      const scoped = combined && scope !== '7D' ? scope + '·ALL' : scope;
      return Math.max(measure(scoped, .45), measure(label, .5)) + measure(value, .9) + font * 2.2;
    });
    if (enabled.includes('last_five_form')) metrics.push(font * 6);
    const cards = names.map(name => {
      const parts = [...metrics];
      if (enabled.includes('username')) parts.unshift(measure(name, 1.05) + font * .35);
      if (enabled.includes('mmr') || enabled.includes('division')) {
        parts.push(Math.max(enabled.includes('mmr') ? measure('99999', 1.2) : 0,
          enabled.includes('division') ? measure('GRANDMASTER', .53) + font * 1.2 : 0));
      }
      return parts;
    });
    const gap = font * .35;
    const padding = font * 1.3 + 4;
    const rawWidth = 16 + Math.max(...cards.map(parts => padding + parts.reduce((sum, width) => sum + width, 0) + gap * Math.max(0, parts.length - 1)));
    const width = Math.max(240, Math.min(1920, Math.ceil(rawWidth / 10) * 10));
    const available = width - 16 - padding;
    const heights = cards.map(parts => {
      let rows = 1, used = 0;
      for (const part of parts) {
        if (used && used + gap + part > available) { rows++; used = 0; }
        if (part > available) {
          rows += Math.ceil(part / available) - 1;
          used = available;
        } else used += (used ? gap : 0) + part;
      }
      // Rating stack includes MMR, badge line-height/borders and its gap.
      // Reserve that taller row plus the flex row gap; do not size from text alone.
      return font * (rows * 2.05 + .8 + Math.max(0, rows - 1) * .35) + 6;
    });
    const rawHeight = 16 + heights.reduce((sum, height) => sum + height, 0) + Math.max(0, names.length - 1) * 8;
    const height = Math.max(40, Math.min(2160, Math.ceil(rawHeight / 10) * 10));
    const mixed = byId('source-standings').checked ||
      (byId('source-header').checked && form.elements.namedItem('show_header').checked);
    output.dataset.width = String(width);
    output.dataset.height = String(height);
    output.textContent = `Recommended Lounge size (estimated): ${width} × ${height} px.` +
      (mixed ? ' Lounge cards only; other visible content needs additional space. Use a Lounge-only source to apply this estimate.' :
        ' Use these dimensions for the OBS browser source after saving display settings.') +
      (rawHeight > 2160 ? ' The height limit is reached; use fewer players, smaller text or separate sources.' : '');
    button.disabled = mixed || rawHeight > 2160;
    return button.disabled ? null : {width, height};
  }
  function render(state) {
    const mode = state.tracker_mode || 'mogi';
    if (lastMode !== null && lastMode !== mode) {
      clearBackgroundFile();
      pausePreview('Tracker mode changed. Apply or refresh to preview this mode.');
    }
    lastMode = mode;
    accept(state.overlay_profiles || [], state.overlay_profiles_revision ?? 0, state.overlay_profiles_instance);
    const profiles = cachedProfiles || [];
    if (!profiles.some(p => p.slug === selected)) {
      pausePreview('The selected source was removed. Choose another source; unsaved drafts are kept.');
      // Preserve a deleted source's draft rather than silently submitting it elsewhere.
      if (dirty) return;
      selected = 'default';
    }
    const source = current(state);
    if (!source) return;
    previewMetadata(source);
    renderBackground(source);
    const next = JSON.stringify([profiles, state.players, state.teams, state.themes, state.tracker_mode]);
    if (dirty || next === signature) { recommendLoungeSize(state); return; }
    signature = next;
    for (const id of ['overlay-source', 'overlay-move-target']) {
      const select = byId(id);
      const old = select.value;
      select.replaceChildren(...profiles.map(p => new Option(p.name, p.slug)));
      select.value = id === 'overlay-source' ? selected : profiles.some(p => p.slug === old) ? old : selected;
    }
    const overlay = source.effective.overlay;
    options(byId('overlay-highlight-player'), alphabetical(state.players || []), 'No highlighted player', overlay.highlight_player_id);
    options(byId('overlay-highlight-team'), alphabetical(state.teams || []), 'No highlighted team', overlay.highlight_team_id);
    Object.entries(overlay).forEach(([key, value]) => {
      const input = byId('overlay-settings').elements.namedItem(key);
      if (!input) return;
      if (input.type === 'checkbox') input.checked = value; else input.value = value ?? '';
    });
    byId('overlay-url').value = `${location.origin}${source.url}`;
    byId('overlay-open').href = source.url;
    byId('overlay-source-name').value = source.name;
    byId('overlay-source-theme').replaceChildren(new Option('Use dashboard theme', ''), ...(state.themes || []).map(t => new Option(t.name, t.id)));
    byId('overlay-source-theme').value = source.theme || '';
    renderSourceFonts(source.report_font ?? '');
    blocks.forEach(key => { byId(`source-${key}`).checked = source.content[key]; });
    byId('lounge-enabled').checked = source.lounge.enabled;
    byId('lounge-usernames').value = source.lounge.players.join('\n');
    byId('lounge-season').value = source.lounge.season ?? '';
    byId('lounge-room-size').value = source.lounge.room_size;
    for (const key of ['background', 'border', 'shape']) byId(`lounge-${key}`).value = source.lounge.appearance[key];
    Object.keys(metricLabels).forEach(key => { byId(`lounge-metric-${key}`).checked = key === 'last_five_form' ? source.lounge.metrics[key] === true : source.lounge.metrics[key] !== false; });
    for (const key of ['auto_visibility', 'transition_style']) byId(`source-option-${key}`).hidden = !Object.hasOwn(overlay, key);
    byId('overlay-source-delete').hidden = selected === 'default';
    recommendLoungeSize(state);
  }
  function init() {
    byId('lounge-background-upload').addEventListener('click', () => run(byId('lounge-background-upload'), () => changeBackground()));
    byId('lounge-background-remove').addEventListener('click', () => run(byId('lounge-background-remove'), () => changeBackground(true)));
    byId('lounge-hud-preset').addEventListener('click', () => {
      byId('lounge-background').value = 'hud';
      byId('lounge-border').value = 'hud';
      byId('lounge-shape').value = 'rounded';
      contentDirty = true; invalidate();
      message('Counter HUD style selected. Save source content or Apply & preview to use it.');
    });
    byId('overlay-apply-preview').addEventListener('click', () => run(byId('overlay-apply-preview'), applyAndPreview));
    byId('overlay-refresh-preview').addEventListener('click', () => run(byId('overlay-refresh-preview'), requestPreview));
    byId('overlay-pause-preview').addEventListener('click', () => pausePreview());
    byId('tracker-mode').addEventListener('change', () => pausePreview('Tracker mode is changing. Apply or refresh to resume.'));
    byId('overlay-live-preview').addEventListener('load', () => {
      if (!previewActive) return;
      byId('overlay-preview-status').textContent = 'Live saved preview. Visibility matches this source; use Show overlay if it is hidden.';
    });
    new ResizeObserver(sizePreview).observe(byId('overlay-preview-stage').parentElement);
    window.addEventListener('resize', sizePreview);
    for (const [key, label] of Object.entries(metricLabels)) {
      const node = document.createElement('label'); node.className = 'checkbox-label';
      const input = document.createElement('input'); input.type = 'checkbox'; input.id = `lounge-metric-${key}`;
      node.append(input, document.createTextNode(label)); byId('lounge-metrics').append(node);
    }
    byId('overlay-settings').addEventListener('input', () => { overlayDirty = true; invalidate(); recommendLoungeSize(state); });
    byId('overlay-source-settings').addEventListener('input', () => { contentDirty = true; invalidate(); recommendLoungeSize(state); });
    byId('lounge-use-size').addEventListener('click', () => {
      const size = recommendLoungeSize(state);
      if (!size) return;
      for (const key of ['width', 'height']) byId('overlay-settings').elements.namedItem(key).value = size[key];
      overlayDirty = true; invalidate();
      message('Recommended size copied to display settings. Save display settings, then match the OBS browser source dimensions.');
    });
    byId('overlay-source').addEventListener('change', () => {
      const target = byId('overlay-source').value;
      if (dirty && !confirm('Discard unsaved source settings?')) {
        byId('overlay-source').value = selected; return;
      }
      pausePreview('Source changed. Apply or refresh to preview its saved settings.');
      clearBackgroundFile();
      selected = target; dirty = false; overlayDirty = false; contentDirty = false; signature = ''; revision++;
      render(state);
    });
    byId('overlay-source-reset').addEventListener('click', () => {
      clearBackgroundFile();
      dirty = false; overlayDirty = false; contentDirty = false; signature = ''; revision++; render(state);
    });
    submit('overlay-source-create', async (form, data) => {
      const key = data.get('slug');
      await api('/api/overlay-profiles', 'POST', {slug: key, name: data.get('name')});
      await reload();
      // Creating a source must not discard another source's unsaved edits.
      if (!dirty) { clearBackgroundFile(); pausePreview('New source selected. Apply or refresh to preview it.'); selected = key; signature = ''; }
      await refresh(); render(state); message('Named overlay source created.');
    });
    submit('overlay-source-settings', async () => {
      if (saving) throw new Error('Another overlay save is still running.');
      const key = selected, before = revision, mode = state.tracker_mode || 'mogi';
      const source = sourceValues();
      saving = true;
      try {
        await api(`/api/overlay-profiles/${key}`, 'PATCH', {...source, expected_tracker_mode: mode});
        await reload();
        if (key === selected && revision === before) { contentDirty = false; dirty = overlayDirty; signature = ''; }
        await refresh(); render(state);
        refreshActivePreview(key, mode);
        message('Source content and Lounge preferences saved.');
      } finally { saving = false; }
    });
    byId('overlay-source-delete').addEventListener('click', () => run(byId('overlay-source-delete'), async () => {
      const key = selected;
      if (key === 'default' || !confirm(`Delete source ${key}? Its OBS URL will stop working.`)) return;
      pausePreview('Source deletion requested. Preview paused.');
      await api(`/api/overlay-profiles/${key}`, 'DELETE', {});
      await reload();
      if (selected === key) { clearBackgroundFile(); selected = 'default'; dirty = false; overlayDirty = false; contentDirty = false; signature = ''; revision++; }
      await refresh(); render(state); message('Source deleted.');
    }));
    byId('overlay-move').addEventListener('click', () => run(byId('overlay-move'), async () => {
      if (dirty) throw new Error('Save or discard source edits before moving content.');
      await api('/api/overlay-profiles/move', 'POST', {
        block: byId('overlay-move-block').value, target: byId('overlay-move-target').value
      });
      await reload(); signature = ''; await refresh(); render(state); message('Content assigned exclusively to the target source.');
    }));
  }
  async function saveOverlay(overlay) {
    if (saving) throw new Error('Another overlay save is still running.');
    const key = selected, before = revision, mode = state.tracker_mode || 'mogi';
    const effective = current(state)?.effective.overlay || {};
    for (const option of ['auto_visibility', 'transition_style']) {
      const input = byId('overlay-settings').elements.namedItem(option);
      if (Object.hasOwn(effective, option)) overlay[option] = input.type === 'checkbox' ? input.checked : input.value;
    }
    saving = true;
    try {
      await writeDisplay(key, overlay, mode);
      await reload();
      // Content drafts have their own Save button; do not discard them here.
      if (key === selected && before === revision) { overlayDirty = false; dirty = contentDirty; signature = ''; }
      refreshActivePreview(key, mode);
    } finally { saving = false; }
  }

  return {init, render, setFonts, setTab, pausePreview, endpoint, saveOverlay, selected: () => selected};
})();
