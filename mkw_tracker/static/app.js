'use strict';
const $ = (id) => document.getElementById(id);
let state = null;
let editing = null;
let wwEditing = null;
let timingEditing = null;
let penaltyEditing = null;
let evidenceEditing = null;
let refreshing = null;
let externalDraftRevision = 0;
let externalActionRevision = 0;
let externalStateRevision = 0;
let externalTestRevision = 0;
const captureDrafts = new Map();
const alphabetical = (rows) => [...rows].sort((a, b) => a.name.localeCompare(b.name) || String(a.id).localeCompare(String(b.id)));
const folderExpansionKey = 'mkw-folder-expansion';
let folderExpansion = {};
try { folderExpansion = JSON.parse(localStorage.getItem(folderExpansionKey) || '{}');
  if (!folderExpansion || typeof folderExpansion !== 'object' || Array.isArray(folderExpansion)) folderExpansion = {}; } catch { /* Storage may be unavailable. */ }
// Each view owns its sections, keeping background capture and drafts intact.
const tabSections = {
  standings: ['team-section', 'player-section', 'ww-section'], capture: ['capture-section', 'review-section'],
  videos: ['import-section', 'videos-section'], history: ['history-section', 'manual-section', 'editor-panel', 'timing-panel', 'evidence-panel', 'penalty-panel', 'ww-editor-panel'],
  chapters: ['recordings-section'], overlays: ['overlay-section'], settings: ['settings-section', 'backup-section', 'experimental-section']
};
let activeTab = 'standings';
function selectTab(name, focus = false) {
  if (!Object.hasOwn(tabSections, name)) return;
  activeTab = name;
  Object.keys(tabSections).forEach((key) => {
    const selected = key === name;
    $(`view-${key}`).hidden = !selected;
    const tab = $(`tab-${key}`); tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
  });
  OverlaySources.setTab(name === 'overlays');
  try { localStorage.setItem('mkw-active-tab', name); } catch { /* Views work without storage. */ }
  if (focus) $(`tab-${name}`).focus();
}
function setupTabs() {
  const main = document.querySelector('main');
  Object.entries(tabSections).forEach(([name, ids]) => {
    const panel = el('div', undefined, name === 'standings' ? 'standings tab-panel' : 'tab-panel');
    panel.id = `view-${name}`; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', `tab-${name}`);
    ids.forEach((id) => panel.append($(id))); main.insertBefore(panel, main.querySelector('footer'));
    const tab = $(`tab-${name}`); tab.addEventListener('click', () => selectTab(name));
    tab.addEventListener('keydown', (event) => {
      const keys = Object.keys(tabSections); let index = keys.indexOf(name);
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') index = (index + 1) % keys.length;
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') index = (index + keys.length - 1) % keys.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = keys.length - 1;
      else return;
      event.preventDefault(); selectTab(keys[index], true);
    });
  });
  document.querySelector('.standings:not(.tab-panel)')?.remove();
  try { activeTab = localStorage.getItem('mkw-active-tab') || 'standings'; } catch { /* Use the default. */ }
  selectTab(Object.hasOwn(tabSections, activeTab) ? activeTab : 'standings');
}
function setCharacterPortrait(image, theme) {
  const url = theme?.icon_url;
  const available = typeof url === 'string' && /^\/api\/themes\/[a-zA-Z0-9-]+\/icon\.png(?:\?v=[a-zA-Z0-9]+)?$/.test(url);
  image.hidden = !available;
  image.alt = available ? `${theme.icon_name || theme.name} icon` : '';
  if (!available) { image.removeAttribute('src'); return; }
  if (image.getAttribute('src') !== url) image.src = url;
  image.onerror = () => { image.hidden = true; };
}
let fontChoices = [];
let fontChoicesLoaded = false;
let fontSaved = null;
function previewFont() {
  const selected = fontChoices.find(item => item.id === $('report-font').value);
  const defaults = state?.display_standings?.font?.faces || [];
  TrackerFonts.apply($('font-preview'), {faces: [...(selected ? [selected] : []),
    ...defaults.filter(item => item.id !== state?.settings?.report_font && item.id !== selected?.id)]});
  $('font-preview').textContent = $('font-preview-text').value;
  $('font-status').textContent = $('report-font').value && !selected
    ? 'This font is unavailable. Available Unicode fonts will be used; your saved choice is kept.'
    : 'Preview uses your browser. PNGs use the same local font with Unicode fallback.';
}
function renderFontChoices() {
  const value = fontSaved !== null ? $('report-font').value : (state?.settings?.report_font || '');
  $('report-font').replaceChildren(new Option('Default Unicode font', ''),
    ...fontChoices.map(font => new Option(`${font.name} (${font.source})`, font.id)));
  if (value && !fontChoices.some(font => font.id === value)) {
    $('report-font').append(new Option('Saved font unavailable', value));
  }
  $('report-font').value = value;
  previewFont();
  OverlaySources.setFonts(fontChoices);
}
async function reloadFonts() {
  const data = await api('/api/fonts');
  fontChoices = data.fonts || [];
  fontChoicesLoaded = true;
  renderFontChoices();
}
function renderFontSettings() {
  const value = state.settings?.report_font || '';
  if (fontSaved === value || $('font-settings').contains(document.activeElement)) return;
  fontSaved = value;
  if (value && !Array.from($('report-font').options).some(option => option.value === value)) {
    $('report-font').append(new Option('Saved font unavailable', value));
  }
  $('report-font').value = value;
  if (fontChoicesLoaded) previewFont();
}
let settingsSignature = '';
function renderSettings() {
  OverlaySources.render(state);
  $('discord-status').textContent = state.discord_status || 'Disabled';
  if (!$('discord-settings').dataset.dirty) {
    $('discord-enabled').checked = !!state.settings?.discord_enabled;
    $('discord-application-id').value = state.settings?.discord_application_id || '';
  }
  renderFontSettings();
  $('mogi-png-graph').value = state.settings?.mogi_png_graph || 'cumulative';
  $('yolo-mode').checked = !!state.settings?.yolo_mode;
  $('yolo-active').hidden = !state.settings?.yolo_mode || state.tracker_mode === 'ww';
  const theme = state.theme;
  if (theme?.tokens) {
    Object.entries(theme.tokens).forEach(([key, value]) => document.documentElement.style.setProperty(`--${key.replaceAll('_', '-')}`, value));
    for (const [alias, key] of [['teal', 'accent'], ['red', 'danger'], ['amber', 'warning']]) document.documentElement.style.setProperty(`--${alias}`, theme.tokens[key]);
    document.documentElement.dataset.theme = theme.id;
  }
  setCharacterPortrait($('character-portrait'), theme);
  const signature = JSON.stringify([state.settings, state.themes, theme, state.teams, state.players, state.tracker_mode]);
  if (signature === settingsSignature) return;
  // A refresh must not overwrite unsaved settings controls.
  if ($('settings-section').contains(document.activeElement)) return;
  settingsSignature = signature;
  options($('character-theme'), alphabetical(state.themes || []), 'Choose a theme', state.settings?.theme);
  setCharacterPortrait($('theme-character-portrait'), theme);
  $('theme-icon-note').textContent = theme?.icon_url ? `${theme.name} uploaded icon` : 'No icon selected. Add your own in the theme editor.';
  if (!themeDraft) loadThemeDraft(theme);
  $('theme-preview').replaceChildren(...(theme?.team_colors || []).map((color) => {
    const chip = el('span'); chip.style.backgroundColor = color; return chip;
  }));

}
function matchIdentity(entry, players) {
  if (entry.manualIdentity) return;
  const matches = entry.name.trim() === 'not-detected-fixme' ? [] :
    players.filter((player) => player.name.trim() === entry.name.trim());
  entry.player_id = matches.length === 1 ? String(matches[0].id) : '';
}
const points = [15, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1];
const same = (a, b) => String(a) === String(b);
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function message(text, error = false) {
  $('message').textContent = text;
  $('message').className = error ? 'error' : '';
}
async function api(path, method = 'GET', body) {
  if (body !== undefined && state) {
    body = {expected_tracker_mode: state.tracker_mode || 'mogi', ...body};
    // An absent mode token explicitly means Mogi on the server, preserving the
    // existing API shape while still rejecting stale Mogi requests in WW mode.
    if (body.expected_tracker_mode === 'mogi') delete body.expected_tracker_mode;
  }
  const response = await fetch(path, {method, headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}
async function downloadFile(path, filename, fallback) {
  const response = await fetch(path);
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).error; } catch { /* A non-JSON error still needs a visible message. */ }
    throw new Error(detail || `${fallback} (${response.status}).`);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = el('a'); link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
let pngPreviewGeneration = 0;
let pngPreviewController = null;
let pngPreviewUrl = null;
let pngPreviewFolder = null;
async function openFolderPng(folder) {
  const dialog = $('folder-png-preview');
  const image = $('folder-png-image');
  const status = $('folder-png-status');
  const copy = $('folder-png-copy');
  const opening = !dialog.open;
  if (opening) {
    pngPreviewFolder = folder;
    const teams = [...state.teams, ...(state.archived_teams || [])].filter(team => team.active !== 0 && same(team.session_id, folder.session_id));
    const selected = same(folder.session_id, state.session.id) ? state.settings?.overlay?.highlight_team_id : null;
    options($('folder-png-team'), alphabetical(teams), 'None', selected);
    const players = [...state.players, ...(state.archived_players || [])].filter(player => player.active !== 0 && same(player.session_id, folder.session_id));
    options($('folder-png-player'), alphabetical(players), 'None', same(folder.session_id, state.session.id) ? state.settings?.overlay?.highlight_player_id : null);
  }
  pngPreviewController?.abort();
  if (pngPreviewUrl) URL.revokeObjectURL(pngPreviewUrl);
  pngPreviewUrl = null;
  const generation = ++pngPreviewGeneration;
  const controller = new AbortController(); pngPreviewController = controller;
  let imageBlob = null;
  const highlightKey = state.tracker_mode === 'ffa' ? 'highlight_player_id' : 'highlight_team_id';
  const highlightValue = $(state.tracker_mode === 'ffa' ? 'folder-png-player' : 'folder-png-team').value;
  const path = `/api/folders/${encodeURIComponent(folder.id)}/preview.png${state.tracker_mode === 'ww' ? '' : `?${highlightKey}=${encodeURIComponent(highlightValue)}`}`;
  $('folder-png-title').textContent = `Folder PNG · ${folder.name}`;
  image.hidden = true; image.alt = `Results for ${folder.name}`;
  status.textContent = 'Loading folder PNG…'; status.className = 'muted';
  copy.disabled = true;
  $('folder-png-full-size').removeAttribute('href');
  $('folder-png-full-size').hidden = true;
  image.onload = () => {
    if (generation !== pngPreviewGeneration || !dialog.open) return;
    image.hidden = false; copy.disabled = false;
    $('folder-png-full-size').hidden = false;
    status.textContent = 'Copy the image, or right-click it and choose Copy image.';
  };
  image.onerror = () => {
    if (generation !== pngPreviewGeneration || !dialog.open) return;
    status.textContent = 'Folder PNG could not be loaded. Close the preview and try again.'; status.className = 'inline-error';
  };
  copy.onclick = async () => {
    if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
      status.textContent = 'Image copying is not supported in this browser. Right-click the image and choose Copy image, or open it full-size.';
      return;
    }
    copy.disabled = true;
    try {
      // Copy the displayed snapshot, even if the live session changes meanwhile.
      await navigator.clipboard.write([new ClipboardItem({'image/png': imageBlob})]);
      if (generation === pngPreviewGeneration && dialog.open) status.textContent = 'PNG copied. Paste it into an app that accepts images.';
    } catch {
      if (generation === pngPreviewGeneration && dialog.open) status.textContent = 'Could not copy the image. Right-click it and choose Copy image, or open it full-size.';
    } finally {
      if (generation === pngPreviewGeneration && dialog.open) copy.disabled = false;
    }
  };
  if (opening) dialog.showModal();
  try {
    const response = await fetch(path, {signal: controller.signal});
    if (!response.ok) {
      let detail;
      try { detail = (await response.json()).error; } catch { /* Use the status fallback. */ }
      throw new Error(detail || `Folder PNG could not be loaded (${response.status}).`);
    }
    imageBlob = await response.blob();
    if (generation !== pngPreviewGeneration || !dialog.open) return;
    pngPreviewUrl = URL.createObjectURL(imageBlob);
    image.src = pngPreviewUrl; $('folder-png-full-size').href = pngPreviewUrl;
  } catch (error) {
    if (generation !== pngPreviewGeneration || !dialog.open) return;
    status.textContent = error.message || 'Folder PNG could not be loaded. Close the preview and try again.';
    status.className = 'inline-error';
  }
}
$('folder-png-player').addEventListener('change', () => openFolderPng(pngPreviewFolder));
$('folder-png-team').addEventListener('change', () => openFolderPng(pngPreviewFolder));
$('folder-png-close').addEventListener('click', () => $('folder-png-preview').close());
$('folder-png-preview').addEventListener('close', () => {
  ++pngPreviewGeneration;
  pngPreviewController?.abort(); pngPreviewController = null;
  const image = $('folder-png-image'); image.onload = null; image.onerror = null; image.removeAttribute('src');
  $('folder-png-full-size').removeAttribute('href'); $('folder-png-copy').onclick = null;
  if (pngPreviewUrl) URL.revokeObjectURL(pngPreviewUrl);
  pngPreviewUrl = null;
  pngPreviewFolder = null;
});
async function run(control, operation) {
  control.disabled = true;
  message('');
  try { await operation(); } catch (error) { message(error.message, true); }
  finally { control.disabled = false; }
}
function options(select, rows, emptyLabel, selected = select.value) {
  select.replaceChildren(new Option(emptyLabel, ''));
  rows.forEach((row) => select.add(new Option(row.name || row.label, row.id)));
  select.value = selected == null ? '' : String(selected);
}
// Recordings can span sessions, and a session can use several recordings.
function renderTimestampRecordings() {
  const folders = new Map(state.folders.map(folder => [folder.id, folder.name]));
  const names = new Map();
  for (const race of state.races) {
    if (!race.recording_id || !folders.has(race.folder_id)) continue;
    if (!names.has(race.recording_id)) names.set(race.recording_id, new Set());
    names.get(race.recording_id).add(folders.get(race.folder_id));
  }
  const counts = new Map();
  const recordings = state.recordings.map(({id, label}) => {
    const name = [...(names.get(id) || [])].join(' / ') || label;
    counts.set(name, (counts.get(name) || 0) + 1);
    return {id, label, name};
  });
  recordings.forEach((row, index) => {
    if (counts.get(row.name) > 1) {
      const reference = row.label === row.name ? '' : ` · ${row.label}`;
      row.name += ` — Recording ${index + 1}${reference}`;
    }
  });
  options($('export-recording'), recordings, 'Choose recording');
  renderTimestampHelp();
}
function renderTimestampHelp() {
  $('resolve-help').hidden = $('export-format').value !== 'edl';
  const recording = state?.recordings.find(row => same(row.id, $('export-recording').value));
  const fps = recording?.fps;
  const rate = fps === '60000/1001' ? '60000/1001 fps (about 59.94 fps)' : fps ? `${fps} fps` : 'unknown';
  $('recording-fps').textContent = recording
    ? `Reported recording rate: ${rate}.`
    : 'Select a recording to see its frame rate.';
}
function table(headers) {
  const node = el('table');
  const head = el('thead');
  const row = el('tr');
  headers.forEach((name) => { const th = el('th', name, name === 'Points' ? 'points' : ''); th.scope = 'col'; row.append(th); });
  head.append(row);
  const body = el('tbody');
  node.append(head, body);
  return [node, body];
}
function cell(row, content, className) {
  const td = el('td', typeof content === 'object' ? undefined : content, className);
  if (typeof content === 'object') td.append(content);
  row.append(td);
  return td;
}
function button(text, action) {
  const node = el('button', text, 'secondary');
  node.type = 'button';
  node.addEventListener('click', () => run(node, action));
  return node;
}
function formatOffset(ms) {
  if (ms == null) return 'Timing unknown';
  const seconds = Math.floor(ms / 1000);
  return `${String(Math.floor(seconds / 3600)).padStart(2, '0')}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}${ms % 1000 ? '.' + String(ms % 1000).padStart(3, '0') : ''}`;
}
function parseOffset(value) {
  const text = value.trim();
  if (!text) return null;
  let seconds;
  if (/^\d+(?:\.\d{1,3})?$/.test(text)) seconds = Number(text);
  else if (/^\d+:[0-5]\d:[0-5]\d(?:\.\d{1,3})?$/.test(text)) {
    const [h, m, s] = text.split(':').map(Number);
    seconds = h * 3600 + m * 60 + s;
  } else throw new Error('Use nonnegative seconds or HH:MM:SS, with up to 3 decimal places.');
  const ms = Math.round(seconds * 1000);
  if (!Number.isSafeInteger(ms)) throw new Error('Timestamp is too large.');
  return ms;
}
function racePlayers(race) {
  const placements = new Set((race.placements || []).map((entry) => String(entry.player_id)));
  const players = new Map([...state.players, ...(state.archived_players || [])]
    .filter((player) => same(player.session_id, race.session_id) && (Boolean(player.active) || placements.has(String(player.id))))
    .map((player) => [String(player.id), player]));
  (race.placements || []).forEach((placement) => {
    if (!players.has(String(placement.player_id))) players.set(String(placement.player_id), {id: placement.player_id, name: placement.player_name || `Player ${placement.player_id}`});
  });
  return alphabetical([...players.values()]);
}
function capturePlayers(candidate) {
  const race = state.races.find((item) => same(item.id, candidate.race_id));
  return race ? racePlayers(race) : [];
}

function clearEditors() {
  captureDrafts.clear(); wwEditing = null; editing = null; timingEditing = null; penaltyEditing = null; evidenceEditing = null;
  ['editor-panel', 'timing-panel', 'penalty-panel', 'evidence-panel', 'ww-editor-panel'].forEach((id) => { $(id).hidden = true; });
}
function renderEvidence(container, race, resultsLast = false) {
  container.replaceChildren();
  const images = Array.isArray(race.evidence) && race.evidence.length ? race.evidence : race.evidence_url ? [{url: race.evidence_url, label: 'Results evidence'}] : [];
  // In entry editors the last image is nearest the fields. Keep all evidence,
  // but put course pictures first and finishing evidence (latest last) there.
  const priority = item => item.kind === 'results' ? 2 : item.kind === 'suspected_results' ? 1 : 0;
  const ordered = resultsLast ? [...images].sort((a, b) => priority(a) - priority(b)) : images;
  ordered.forEach((item) => {
    const url = typeof item === 'string' ? item : item.url;
    if (!url || !/^\/evidence\/[a-zA-Z0-9_.-]+$/.test(url)) return;
    if (item.kind === 'suspected_results') {
      container.append(el('p', 'Suspected results only. Finishing order is unverified; this may be a partial table or reordered cumulative standings. Verify against the recording before entering positions.', 'review-reasons'));
    } else if (item.label) container.append(el('p', item.label, 'muted'));
    const image = el('img'); image.src = url; image.alt = `${item.label || 'Results evidence'} for ${race.course || 'unknown course'}`;
    image.className = 'capture-screenshot';
    const original = el('a', 'Open full-size evidence'); original.href = url; original.target = '_blank'; original.rel = 'noopener noreferrer'; container.append(image, original);
  });
  if (!container.childElementCount) container.append(el('p', 'No evidence available for this race. Manual entries have no captured screenshot.', 'muted'));
  if (race.auto_confirmed) container.prepend(el('p', 'YOLO auto-confirmed. Check identities and finishing order; corrections replace this race’s points.', 'review-reasons'));
}
function openEvidence(race) {
  selectTab('history');
  evidenceEditing = race.id;
  renderEvidence($('history-evidence'), race); $('evidence-panel').hidden = false;
  $('evidence-panel').scrollIntoView({block: 'start'});
}
function renderPenalties() {
  const race = state.races.find((item) => same(item.id, penaltyEditing));
  if (!race) { penaltyEditing = null; $('penalty-panel').hidden = true; return; }
  $('penalty-title').textContent = `Race penalties · ${race.course}`;
  const participants = (race.placements || []).map((entry) => ({id: entry.player_id, name: entry.player_name || racePlayers(race).find((player) => same(player.id, entry.player_id))?.name || `Player ${entry.player_id}`}));
  options($('penalty-player'), alphabetical(participants), 'Choose race participant');
  const deductions = (race.penalties || []).map((penalty) => {
    const item = el('p'); const name = participants.find((player) => same(player.id, penalty.player_id))?.name || `Player ${penalty.player_id}`;
    item.append(el('span', `${name}: −${penalty.points} points${penalty.reason ? ' · ' + penalty.reason : ''} `), button('Remove penalty', async () => {
      await api(`/api/penalties/${encodeURIComponent(penalty.id)}`, 'DELETE', {}); await refresh();
    })); return item;
  });
  $('race-penalties').replaceChildren(...(deductions.length ? deductions : [el('p', 'No penalties for this race.', 'muted')]));
  $('add-penalty').querySelector('button').disabled = !participants.length;
}
function openPenalties(race) {
  selectTab('history');
  penaltyEditing = race.id; renderPenalties(); $('penalty-panel').hidden = false; $('penalty-panel').scrollIntoView({block: 'start'});
}
function closeRaceEditors(id) {
  if (same(editing, id)) { editing = null; $('editor-panel').hidden = true; }
  if (same(timingEditing, id)) { timingEditing = null; $('timing-panel').hidden = true; }
  if (same(penaltyEditing, id)) { penaltyEditing = null; $('penalty-panel').hidden = true; }
  if (same(evidenceEditing, id)) { evidenceEditing = null; $('evidence-panel').hidden = true; }
}
function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} bytes`;
  const units = ['KiB', 'MiB', 'GiB', 'TiB'];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)) - 1, units.length - 1);
  return `${(bytes / (1024 ** (index + 1))).toFixed(1)} ${units[index]}`;
}
function videoModifiedTime(file) {
  return typeof file.modified_at === 'number' ? file.modified_at * 1000 : Date.parse(file.modified_at);
}
function cleanupResult(result) {
  const count = Array.isArray(result.deleted_files) ? result.deleted_files.length : result.deleted_files ?? 0;
  const skipped = Array.isArray(result.skipped) ? result.skipped.length : result.skipped ?? 0;
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const details = errors.map((failure) => `${failure.name || failure.id || 'Unknown file'}: ${failure.error || 'Deletion failed.'}`).join(' · ');
  message(`Deleted ${count} uploaded video${count === 1 ? '' : 's'} (${formatBytes(result.deleted_bytes)}). Skipped ${skipped}. Failed ${errors.length}.${details ? ' ' + details : ''} Results, evidence and original local-path videos were kept.`, errors.length > 0);
}
function renderVideos() {
  const videos = state.videos || {};
  const files = videos.files || [];
  $('video-storage').textContent = `${files.length} app-managed upload${files.length === 1 ? '' : 's'} · ${formatBytes(videos.managed_bytes ?? files.reduce((sum, file) => sum + file.size_bytes, 0))}`;
  const [library, body] = table(['Uploaded video', 'Size', 'Modified', 'Status', 'Action']);
  files.forEach((file) => {
    const row = el('tr'); row.dataset.videoId = file.id;
    const name = cell(row, file.name); if (file.path) name.append(el('span', file.path, 'muted'));
    cell(row, formatBytes(file.size_bytes));
    const modified = videoModifiedTime(file); cell(row, Number.isFinite(modified) ? new Date(modified).toLocaleString() : 'Unknown');
    cell(row, file.active ? 'Active import or upload' : file.can_delete ? 'Inactive upload' : 'Protected');
    const remove = button('Delete video', async () => {
      if (!window.confirm(`Permanently delete uploaded video “${file.name}” (${formatBytes(file.size_bytes)})? Results and evidence are kept. Original local-path videos are never deleted. This cannot be undone.`)) return;
      const result = await api(`/api/videos/${encodeURIComponent(file.id)}`, 'DELETE', {}); await refresh(); cleanupResult(result);
    });
    remove.disabled = !file.can_delete || Boolean(file.active); cell(row, remove); body.append(row);
  });
  $('video-library').replaceChildren(files.length ? library : el('p', 'No app-managed uploaded videos. Local-path recordings remain in their original locations.', 'muted'));
}
function rememberVisibleFolderExpansion() {
  // Native toggle events are queued; capture the DOM before replacing its nodes.
  let changed = false;
  document.querySelectorAll('#history details.history-folder').forEach(folder => {
    const id = folder.dataset.folderId;
    if (folderExpansion[id] !== folder.open) { folderExpansion[id] = folder.open; changed = true; }
  });
  if (changed) {
    try { localStorage.setItem(folderExpansionKey, JSON.stringify(folderExpansion)); } catch { /* Storage is optional. */ }
  }
}
// Controls operate on the displayed list; the store accepts oldest-first IDs.
let dragRaceRow = null;
function addRaceOrderControls(body, races, folder, direction) {
  if ($('history-sort').value !== 'history_order' || races.length < 2) return;
  const rows = Array.from(body.rows);
  const ids = races.map(race => race.id);
  const ascending = list => direction === 1 ? list : [...list].reverse();
  async function move(from, to) {
    const reordered = [...ids];
    reordered.splice(to, 0, reordered.splice(from, 1)[0]);
    await api(`/api/folders/${encodeURIComponent(folder.id)}/race-order`, 'POST',
      {expected_ids: ascending(ids), race_ids: ascending(reordered)});
    await refresh(false, true);
    const moved = Array.from($('history').querySelectorAll('tr[data-race-id]')).find(row => same(row.dataset.raceId, ids[from]));
    moved?.querySelector('.race-order button')?.focus();
    message(`Moved ${races[from].course} to row ${to + 1} of ${ids.length}.`);
  }
  rows.forEach((row, index) => {
    const controls = el('div', undefined, 'row-actions race-order');
    const handle = el('span', '⠿ Drag', 'race-drag');
    handle.draggable = true;
    handle.title = 'Drag onto another race in this folder to take its place';
    handle.addEventListener('dragstart', event => {
      dragRaceRow = row;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(ids[index]));
    });
    handle.addEventListener('dragend', () => { dragRaceRow = null; });
    row.addEventListener('dragover', event => {
      if (dragRaceRow?.parentNode === body && dragRaceRow !== row) event.preventDefault();
    });
    row.addEventListener('drop', event => {
      if (dragRaceRow?.parentNode !== body || dragRaceRow === row) return;
      event.preventDefault();
      const from = rows.indexOf(dragRaceRow);
      dragRaceRow = null;
      move(from, index).catch(error => message(error.message, true));
    });
    controls.append(handle);
    for (const step of [-1, 1]) {
      if (!rows[index + step]) continue;
      const label = step < 0 ? 'Move up' : 'Move down';
      const control = button(label, () => move(index, index + step));
      control.setAttribute('aria-label', `${label}: ${races[index].course}, row ${index + 1}`);
      controls.append(control);
    }
    row.cells[0].append(controls);
  });
}
function renderHistory() {
  if (state.tracker_mode === 'ww') { renderWWHistory(); return; }
  rememberVisibleFolderExpansion();
  options($('history-folder'), state.folders || [], 'All folders');
  const folderId = $('history-folder').value;
  const key = $('history-sort').value;
  const direction = $('history-direction').value === 'asc' ? 1 : -1;
  const races = state.races.filter((race) => !folderId || same(race.folder_id, folderId));
  races.sort((a, b) => {
    let comparison;
    if (key === 'history_order') comparison = a.history_order - b.history_order;
    else if (key === 'course' || key === 'status') comparison = String(a[key] || '').localeCompare(String(b[key] || ''));
    else if (key === 'offset_ms') {
      if (a.offset_ms == null || b.offset_ms == null) return Number(a.offset_ms == null) - Number(b.offset_ms == null);
      comparison = a.offset_ms - b.offset_ms;
    } else comparison = String(a.created_at || '').localeCompare(String(b.created_at || '')) || a.entry_order - b.entry_order;
    return direction * comparison;
  });
  const groups = [...(state.folders || [])].sort((a, b) =>
    String(b.created_at).localeCompare(String(a.created_at)) || String(b.id).localeCompare(String(a.id))).filter((folder) => !folderId || same(folder.id, folderId)).map((folder) => {
    const section = el('details', undefined, 'history-folder');
    section.open = folderExpansion[String(folder.id)] !== false;
    section.addEventListener('toggle', () => {
      if (!section.isConnected) return;
      folderExpansion[String(folder.id)] = section.open;
      try { localStorage.setItem(folderExpansionKey, JSON.stringify(folderExpansion)); } catch { /* Keep disclosure usable without storage. */ }
    });
    section.dataset.folderId = folder.id;
    const heading = el('summary', undefined, 'folder-heading');
    const members = races.filter((race) => same(race.folder_id, folder.id));
    heading.append(el('span', `${folder.name}${same(folder.id, state.session.folder_id) ? ' · Current session folder' : ''} · ${members.length} races`), button('Rename folder', async () => {
      const name = window.prompt('Folder name', folder.name);
      if (name === null) return;
      if (!name.trim()) throw new Error('Folder name cannot be empty.');
      await api(`/api/folders/${encodeURIComponent(folder.id)}`, 'PATCH', {name: name.trim()}); await refresh();
    }));
    section.append(heading);
    const folderControls = el('div', undefined, 'inline folder-controls');
    const loungeLabel = el('label', 'Lounge ID');
    const loungeInput = el('input'); loungeInput.value = folder.lounge_id || ''; loungeInput.inputMode = 'numeric'; loungeInput.pattern = '[0-9]*'; loungeInput.maxLength = 32;
    loungeInput.setAttribute('aria-label', `Lounge ID for ${folder.name}`); loungeLabel.append(loungeInput);
    folderControls.append(loungeLabel, button('Save Lounge ID', async () => {
      const value = loungeInput.value.trim();
      if (value && !/^\d+$/.test(value)) throw new Error('Lounge ID must contain digits only.');
      await api(`/api/folders/${encodeURIComponent(folder.id)}`, 'PATCH', {lounge_id: value || null}); await refresh();
    }));
    if (folder.lounge_id && /^\d+$/.test(String(folder.lounge_id))) {
      const link = el('a', `Lounge ${folder.lounge_id}`); link.href = `https://lounge.mkcentral.com/mkworld/TableDetails/${folder.lounge_id}`;
      link.target = '_blank'; link.rel = 'noopener noreferrer'; folderControls.append(link);
    }
    // Keep the opener focusable so native dialog close restores keyboard focus.
    const previewButton = el('button', 'View folder PNG', 'secondary');
    previewButton.type = 'button';
    previewButton.addEventListener('click', () => { openFolderPng(folder); });
    folderControls.append(previewButton,
      button('Download folder PNG', () => downloadFile(`/api/folders/${encodeURIComponent(folder.id)}/report.png`, `folder-${folder.id}.png`, 'Folder PNG export failed')));
    folderControls.append(button('Load session', async () => {
      await api(`/api/folders/${encodeURIComponent(folder.id)}/load`, 'POST', {expected_session_id: state.session.id});
      clearEditors(); await refresh(); message(`Loaded session: ${folder.name}`);
    }), button('Delete folder', async () => {
      const replacement = same(folder.id, state.session.folder_id) ? ' A new empty session will be selected.' : '';
      if (!window.confirm(`Delete folder “${folder.name}” and its session, players${state.tracker_mode === 'ffa' ? '' : ', teams'} and race results? This cannot be undone.${replacement}`)) return;
      await api(`/api/folders/${encodeURIComponent(folder.id)}`, 'DELETE', {}); clearEditors(); await refresh(); message('Folder and its session deleted.');
    }));
    section.append(folderControls);
    if (!members.length) { section.append(el('p', 'No races in this folder.', 'muted')); return section; }
    const [historyTable, historyBody] = table(['Race', 'Recording offset', 'Status', 'Results']);
    members.forEach((race) => {
      const row = el('tr'); row.dataset.raceId = race.id;
      const course = courseTitle(race);
      const title = cell(row, course);
      title.append(el('span', `Race ${race.id}${same(race.session_id, state.session.id) ? '' : ' · Archived session ' + race.session_id}`, 'muted'));
      const recording = state.recordings.find((item) => same(item.id, race.recording_id));
      const timing = cell(row, formatOffset(race.offset_ms));
      timing.append(el('span', recording?.label || 'No recording', 'muted'), button('Edit course / chapter', async () => openTiming(race.id)));
      const suspected = !race.revision && race.evidence?.some(item => item.kind === 'suspected_results');
      cell(row, `${race.status}${race.auto_confirmed ? ' · YOLO auto-confirmed' : ''}${race.revision ? ' · revision ' + race.revision : ''}${suspected ? ' · Suspected screenshot available' : ''}`);
      const resultControls = el('div', undefined, 'row-actions');
      resultControls.append(button(race.placements?.length ? 'Review / correct' : 'Enter results', async () => openEditor(race.id)), button('View evidence', async () => openEvidence(race)), button('Penalties', async () => openPenalties(race)));
      if (race.penalties?.length) resultControls.append(el('span', `−${race.penalties.reduce((sum, penalty) => sum + penalty.points, 0)} penalty points`, 'review-reasons'));
      const removeRace = button('Delete race', async () => {
        if (!window.confirm(`Delete race “${race.course || 'Unknown course'}”, its results and penalties? Scores and counters will be recalculated. This cannot be undone.`)) return;
        await api(`/api/races/${encodeURIComponent(race.id)}`, 'DELETE', {}); closeRaceEditors(race.id); await refresh(); message('Race, results and penalties deleted. Scores and counters updated.');
      }); removeRace.classList.add('danger'); resultControls.append(removeRace);
      cell(row, resultControls);
      historyBody.append(row);
    });
    addRaceOrderControls(historyBody, members, folder, direction);
    const wrap = el('div', undefined, 'table-wrap'); wrap.append(historyTable); section.append(wrap); return section;
  });
  $('history').replaceChildren(...(groups.length ? groups : [el('p', 'No race folders yet.', 'muted')]));
}
function courseTitle(course, label = 'span') {
  const title = el('span', undefined, 'course-title');
  if (typeof course.icon_url === 'string' && /^\/api\/course-images\/[0-9a-f]{32}\.png\?v=[0-9a-f]+$/.test(course.icon_url)) {
    const image = el('img'); image.src = course.icon_url; image.alt = ''; image.loading = 'lazy'; image.className = 'course-icon';
    image.addEventListener('error', () => image.remove()); title.append(image);
  }
  title.append(el(label, course.course || 'Unknown course'));
  return title;
}
const mogiCopy = new Map();
function modeText(id, worldwide, ffa) {
  const node = $(id);
  if (!mogiCopy.has(id)) mogiCopy.set(id, node.textContent);
  node.textContent = state.tracker_mode === 'ww' ? worldwide : state.tracker_mode === 'ffa' && ffa ? ffa : mogiCopy.get(id);
}
let switchingMode = false;
function renderModeControl() {
  if (switchingMode) return;
  $('tracker-mode').value = state.tracker_mode || 'mogi';
  const blocked = Boolean(state.mode_switch_warning);
  $('tracker-mode').disabled = blocked;
  $('tracker-mode').hidden = blocked;
  const button = $('blocked-tracker-mode');
  button.hidden = !blocked;
  if (blocked) {
    button.textContent = $('tracker-mode').selectedOptions[0].textContent;
    button.setAttribute('aria-label', `Tracker mode: ${button.textContent}. Show why switching is unavailable.`);
  }
}
function renderTrackerMode() {
  const ww = state.tracker_mode === 'ww';
  document.body.dataset.trackerMode = state.tracker_mode || 'mogi';
  renderModeControl();
  modeText('product-description', 'Personal VR · Worldwides · Recording chapters', 'Mogi FFA · Individual scores · Recording chapters');
  modeText('player-help', '', 'Twelve individual players. Rename a player without losing their history.');
  modeText('penalty-help', '', 'Penalties deduct points from a player. Only participants in this race are eligible.');
  modeText('review-title', 'WW capture');
  modeText('review-help', 'Clear highlighted-player results update automatically. Compare saved evidence and correct results in Race history. Live sessions calculate VR changes from the previous result; imported videos keep unobserved changes unknown.');
  modeText('import-help', 'Process a local recording in the background. Clear WW results update automatically. Use a local path to avoid copying large files. A 0.5-second interval is recommended.');
  modeText('manual-help', 'Add a missing WW race, then enter the highlighted player\'s final VR and finishing position. Leave unknown values blank.');
  modeText('history-help', 'Each folder is an independent WW session. Mogi sessions and results are kept separately. Load a folder before correcting its results.', 'Each folder is an independent Mogi FFA session. Teams and Worldwides sessions remain separate. Load a folder to continue it; existing sessions are never converted.');
  ['camera-settings', 'import-video'].forEach(id => { $(id).elements.name_engine.disabled = ww; });
  modeText('capture-name-engine-help', 'WW uses CPU recognition for the highlighted player\'s position and VR. Name recognition is not used and no name model is loaded.');
  modeText('import-name-engine-help', 'WW uses CPU position and VR recognition. Name recognition is not used.');
  renderNameEngineWarning('camera-settings', 'capture-nvfp4-warning');
  renderNameEngineWarning('import-video', 'import-nvfp4-warning');
  const footer = document.querySelector('footer');
  if (!footer.dataset.mogiText) footer.dataset.mogiText = footer.textContent;
  footer.textContent = ww ? 'Worldwides tracks one player. Unknown values are not zero.' : footer.dataset.mogiText;
  $('add-race').querySelector('button').textContent = ww ? 'Create WW race' : 'Create race for review';
  $('import-video').querySelector('button').textContent = ww ? 'Import WW video' : state.settings?.yolo_mode ? 'Import video with YOLO' : 'Import video for review';
  if (!ww && state.settings?.yolo_mode) {
    $('import-help').textContent = 'YOLO active: future detected results in this import will be scored automatically. Check names, teams and finishing order in Race history.';
    $('review-help').textContent = 'YOLO active: future detected results are scored automatically. Existing pending results below still require your review and confirmation.';
  }
}
const wwNumber = value => Number.isFinite(value) ? String(value) : 'Unknown';
const wwChange = value => Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value}` : 'Unknown';
function renderWW() {
  const ww = state.ww || {};
  $('mode').textContent = 'Worldwides · automatic';
  const form = $('ww-player');
  if (!form.dataset.draftStarted) {
    form.elements.name.value = ww.player_name || 'Local player';
    form.dataset.sessionId = state.session.id;
  }
  $('ww-metrics').replaceChildren(...[
    ['Current VR', ww.current_vr], ['Peak VR', ww.peak_vr], ['Lowest VR', ww.lowest_vr], ['Starting VR', ww.starting_vr]
  ].map(([label, value]) => {
    const card = el('div', undefined, 'ww-metric');
    card.append(el('span', label, 'muted'), el('strong', wwNumber(value)));
    return card;
  }));
  $('ww-graph').replaceChildren(WWGraph.create(ww.graph || []));
  const races = state.races.filter(race => same(race.session_id, state.session.id));
  const known = races.filter(race => Number.isFinite(race.ww_result?.delta));
  $('ww-summary').textContent = `${races.filter(race => race.ww_result).length} results / ${races.length} races · ${known.length} known changes · Session change: ${wwChange(ww.total_change)}`;
  const [courseTable, body] = table(['Course', 'Races', 'Known changes', 'Mean VR change', 'Mean position']);
  const courses = $('ww-stats-scope').value === 'all' ? ww.all_courses || [] : ww.courses || [];
  courses.forEach(course => {
    const row = el('tr');
    cell(row, courseTitle(course)); cell(row, course.races); cell(row, course.known_changes);
    cell(row, Number.isFinite(course.average_change) ? wwChange(Number(course.average_change.toFixed(1))) : 'Unknown');
    cell(row, Number.isFinite(course.average_position) ? course.average_position.toFixed(1) : 'Unknown');
    body.append(row);
  });
  $('ww-courses').replaceChildren(courses.length ? courseTable : el('p', 'No course statistics yet.', 'muted'));
}
function renderWWCapture() {
  $('review-count').textContent = 'Automatic';
  const race = state.capture?.current_race;
  const container = $('current-capture-race');
  container.hidden = !race;
  container.replaceChildren();
  if (race) {
    const heading = el('h3'); heading.append(courseTitle(race));
    container.append(heading, el('p', 'Waiting for a stable final VR reading.'));
    const proof = el('div'); renderEvidence(proof, race); container.append(proof);
    container.append(button('Enter / correct WW result', async () => openWWEditor(race.id)));
  }
  $('capture-reviews').replaceChildren(el('p', 'Saved results and screenshots are available in Race history.', 'muted'));
}
function renderWWHistory() {
  rememberVisibleFolderExpansion();
  options($('history-folder'), state.folders || [], 'All WW folders');
  const filter = $('history-folder').value;
  const key = $('history-sort').value;
  const direction = $('history-direction').value === 'asc' ? 1 : -1;
  const groups = [...state.folders].reverse().filter(folder => !filter || same(folder.id, filter)).map(folder => {
    const group = el('details', undefined, 'history-folder');
    group.dataset.folderId = folder.id;
    group.open = folderExpansion[folder.id] !== false;
    group.addEventListener('toggle', () => {
      if (!group.isConnected) return;
      folderExpansion[folder.id] = group.open;
      try { localStorage.setItem(folderExpansionKey, JSON.stringify(folderExpansion)); } catch { /* Storage is optional. */ }
    });
    const current = same(folder.session_id, state.session.id);
    const heading = el('summary', `${folder.name}${current ? ' · Current WW session' : ''}`, 'folder-heading');
    group.append(heading);
    const controls = el('div', undefined, 'inline folder-controls');
    controls.append(button('Rename folder', async () => {
      const name = window.prompt('Folder name', folder.name);
      if (name === null) return;
      await api(`/api/folders/${encodeURIComponent(folder.id)}`, 'PATCH', {name: name.trim()}); await refresh();
    }), button('Load session', async () => {
      await api(`/api/folders/${encodeURIComponent(folder.id)}/load`, 'POST', {expected_session_id: state.session.id});
      clearEditors(); await refresh();
    }), button('Delete folder', async () => {
      if (!window.confirm(`Delete WW folder “${folder.name}” and all its results? This cannot be undone.`)) return;
      await api(`/api/folders/${encodeURIComponent(folder.id)}`, 'DELETE', {}); clearEditors(); await refresh();
    }));
    const preview = el('button', 'View folder PNG', 'secondary');
    preview.type = 'button';
    preview.addEventListener('click', () => { openFolderPng(folder); });
    controls.append(preview, button('Download folder PNG', () => downloadFile(
      `/api/folders/${encodeURIComponent(folder.id)}/report.png`, `ww-folder-${folder.id}.png`, 'WW PNG export failed')));
    group.append(controls);
    const races = state.races.filter(race => same(race.folder_id, folder.id));
    races.sort((a, b) => {
      if (key === 'history_order') return direction * (a.history_order - b.history_order);
      if (key === 'offset_ms') {
        if (a.offset_ms == null || b.offset_ms == null) return Number(a.offset_ms == null) - Number(b.offset_ms == null);
        return direction * (a.offset_ms - b.offset_ms);
      }
      return direction * (String(a[key] || '').localeCompare(String(b[key] || '')) || a.entry_order - b.entry_order);
    });
    const [historyTable, body] = table(['Course / chapter', 'Position', 'Final VR', 'VR change', 'Result']);
    races.forEach(race => {
      const row = el('tr'); row.dataset.raceId = race.id;
      const result = race.ww_result;
      const course = cell(row, courseTitle(race));
      course.append(el('span', formatOffset(race.offset_ms), 'muted'), button('Edit course / chapter', async () => openTiming(race.id)));
      cell(row, wwNumber(result?.position));
      cell(row, wwNumber(result?.vr_after)); cell(row, wwChange(result?.delta));
      const actions = el('div', undefined, 'row-actions');
      actions.append(el('span', result ? `${result.source || 'Saved'} · revision ${race.revision}` : 'Needs result', 'muted'));
      const edit = button(result ? 'Correct WW result' : 'Enter WW result', async () => openWWEditor(race.id));
      edit.disabled = !current; edit.title = current ? '' : 'Load this session to edit its WW results';
      actions.append(edit, button('View evidence', async () => openEvidence(race)), button('Delete race', async () => {
        if (!window.confirm(`Delete WW race “${race.course}” and its result? This cannot be undone.`)) return;
        await api(`/api/races/${encodeURIComponent(race.id)}`, 'DELETE', {}); clearEditors(); await refresh();
      }));
      cell(row, actions); body.append(row);
    });
    const wrap = el('div', undefined, 'table-wrap');
    addRaceOrderControls(body, races, folder, direction);
    wrap.append(races.length ? historyTable : el('p', 'No races in this WW session.', 'muted'));
    group.append(wrap);
    return group;
  });
  $('history').replaceChildren(...groups);
}
function openWWEditor(id) {
  const race = state.races.find(item => same(item.id, id));
  if (!race || !same(race.session_id, state.session.id)) throw new Error('Load the WW session containing this race before editing.');
  if (wwEditing && wwEditing.id !== id && !window.confirm('Discard the unfinished WW result?')) return;
  wwEditing = {id, sessionId: state.session.id, revision: race.revision, lobbySize: race.ww_result?.lobby_size ?? null};
  selectTab('history');
  $('ww-editor-title').textContent = `WW result · ${race.course}`;
  renderEvidence($('ww-editor-evidence'), race, true);
  const form = $('ww-results');
  ['position', 'vr_after', 'vr_before', 'delta'].forEach(key => { form.elements[key].value = race.ww_result?.[key] ?? ''; });
  $('ww-editor-panel').hidden = false;
  $('ww-editor-panel').scrollIntoView({block: 'start'});
  form.elements.position.focus();
}
let readerDefaultApplied = false;
function render() {
  if (!readerDefaultApplied) {
    ['camera-settings', 'import-video'].forEach((id) => {
      const select = $(id).elements.name_engine;
      if (!select.dataset.userSelected && Array.from(select.options).some(option => option.value === state.reader_default)) select.value = state.reader_default;
    });
    readerDefaultApplied = true;
  }
  // Empty forms follow the displayed session. Started drafts keep their original owner.
  ['add-player', 'add-team', 'add-race'].forEach((id) => {
    const form = $(id);
    if (!form.dataset.draftStarted) { form.dataset.sessionId = state.session.id; form.dataset.trackerMode = state.tracker_mode || 'mogi'; }
  });
  renderTrackerMode();
  renderSettings();
  renderExternalVision();
  $('overlay-visibility').textContent = OverlaySources.selected() === 'default' ? (state.display_standings?.visible === false ? 'Overlay hidden' : 'Overlay shown') : 'Controls apply to the selected named source';
  $('mode').textContent = state.mode === 'demo' ? 'Demo data' : state.tracker_mode === 'ffa' ? 'Mogi FFA · individual scores' : 'Local race tracker';
  $('capture').textContent = captureStatus();
  $('mode-note').textContent = state.mode === 'demo' ? 'Sample references are not live OCR output. Verify names before use.' : 'Manual entries are not automatic detections. Capture status above reports the adapter state.';
  renderCapture();
  renderCameras(state.cameras);
  renderImports();
  renderVideos();
  renderTimestampRecordings();
  $('session').textContent = `Current session: ${state.folders.find((folder) => same(folder.id, state.session.folder_id))?.name || state.session.id} · ${state.session.started_at}`;
  if (state.tracker_mode === 'ww') {
    renderWW();
    options($('race-recording'), state.recordings, 'No recording / timing unknown');
    renderWWHistory();
    return;
  }
  const ffa = state.tracker_mode === 'ffa';
  const room = state.room || {};
  $('logged-races').textContent = `Races logged: ${room.logged_races ?? 0} · Results confirmed: ${room.scored_races ?? 0} / ${room.expected_races ?? 12}`;
  $('player-count').textContent = `Recorded players: ${state.players.length}`;
  const standingsFormat = state.settings?.standings_format === 'md' ? 'md' : 'txt';
  $('standings-format').value = standingsFormat; $('obs-download').href = `/api/standings.${standingsFormat}`; $('obs-download').download = `${ffa ? 'player' : 'team'}-standings.${standingsFormat}`;
  $('room-points').textContent = `Room points: ${room.points ?? room.total_points ?? 0} / ${room.maximum_points ?? 984}`;
  $('obs-text-path').textContent = state.obs_text_path || 'Available after the next standings update';
  $('obs-text-error').hidden = !state.obs_text_error;
  $('obs-text-error').textContent = state.obs_text_error ? `OBS text file update failed. OBS may show stale standings. ${state.obs_text_error}` : '';
  $('ffa-summary').replaceChildren(...['room-points', 'logged-races'].map(id => el('p', $(id).textContent, 'muted')));
  const ffaOutput = el('p', 'OBS text file: ', 'muted'); ffaOutput.append(el('code', $('obs-text-path').textContent));
  const ffaDownload = el('a', 'Download current standings'); ffaDownload.href = $('obs-download').href; ffaDownload.download = $('obs-download').download;
  $('ffa-summary').append(ffaOutput, ffaDownload);
  if (state.obs_text_error) $('ffa-summary').append(el('p', $('obs-text-error').textContent, 'inline-error'));
  const [teamsTable, teamsBody] = table(['Team', 'Points', 'Penalties', 'Gap above', 'Edit']);
  const rankedTeams = [...state.teams].sort((a, b) => b.points - a.points || a.name.localeCompare(b.name));
  rankedTeams.forEach((team, index) => {
    const displayTeam = state.display_standings?.teams.find((entry) => same(entry.id, team.id));
    const row = el('tr'); row.classList.add('team-colored'); row.style.setProperty('--team-color', displayTeam?.color || 'var(--teal)'); cell(row, `${displayTeam?.rank || index + 1}. ${team.name}`); cell(row, team.points, 'points'); cell(row, team.penalty_points || 0, 'penalty-points');
    cell(row, index ? `${rankedTeams[index - 1].points - team.points} points` : 'Leader', 'team-gap');
    const controls = el('div', undefined, 'row-actions');
    controls.append(button('Rename', async () => {
      const name = window.prompt('Team name', team.name);
      if (name === null) return;
      if (!name.trim()) throw new Error('Team name cannot be empty.');
      await api(`/api/teams/${encodeURIComponent(team.id)}`, 'PATCH', {name: name.trim()}); await refresh();
    }), button('Delete team', async () => {
      if (!window.confirm(`Delete team “${team.name}” and unassign its players? Race history is retained.`)) return;
      await api(`/api/teams/${encodeURIComponent(team.id)}`, 'DELETE', {}); await refresh();
    }));
    const color = el('input'); color.type = 'color'; color.value = team.color || displayTeam?.color || '#ff675c';
    color.setAttribute('aria-label', `Color for ${team.name}`);
    color.addEventListener('change', () => run(color, async () => {
      await api(`/api/teams/${encodeURIComponent(team.id)}`, 'PATCH', {color: color.value}); await refresh();
    }));
    controls.append(color, button('Automatic color', async () => {
      await api(`/api/teams/${encodeURIComponent(team.id)}`, 'PATCH', {color: null}); await refresh();
    }));
    cell(row, controls); teamsBody.append(row);
  });
  $('teams').replaceChildren(state.teams.length ? teamsTable : el('p', 'No teams yet. Add a team, then assign players.', 'muted'));
  const [playersTable, playersBody] = table(['#', 'Player', ...(ffa ? [] : ['Team']), 'Races', 'Points', 'Penalties', ...(ffa ? ['Gap above / first', 'Movement'] : []), 'Edit']);
  // FFA gaps/ranks belong to the server's exact ordering, including Unicode ties.
  const rosterById = new Map(state.players.map(player => [String(player.id), player]));
  const rankedPlayers = ffa
    ? (state.display_standings?.players || []).map(player => rosterById.get(String(player.id))).filter(Boolean)
    : [...state.players].sort((a, b) => b.points - a.points || a.name.localeCompare(b.name));
  rankedPlayers.forEach((player, index) => {
    const row = el('tr'); const displayPlayer = state.display_standings?.players.find((entry) => same(entry.id, player.id)); if (displayPlayer?.color) { row.classList.add('team-colored'); row.style.setProperty('--team-color', displayPlayer.color); } cell(row, ffa ? displayPlayer?.rank || index + 1 : index + 1, 'player-number'); cell(row, player.name, 'player-name');
    if (!ffa) {
      const select = el('select'); select.setAttribute('aria-label', `Team for ${player.name}`);
      options(select, state.teams, 'Unassigned', player.team_id);
      select.addEventListener('change', () => run(select, async () => {
        try { await api(`/api/players/${encodeURIComponent(player.id)}`, 'PATCH', {team_id: select.value ? state.teams.find((t) => same(t.id, select.value)).id : null}); await refresh(); }
        catch (error) { select.value = player.team_id == null ? '' : String(player.team_id); throw error; }
      }));
      cell(row, select);
    }
    cell(row, player.races); cell(row, player.points, 'points'); cell(row, player.penalty_points || 0, 'penalty-points');
    if (ffa) {
      const gap = value => value ? `-${value}` : '0';
      cell(row, displayPlayer?.gap_above == null ? 'Leader' : `${gap(displayPlayer.gap_above)} / ${gap(displayPlayer.gap_leader)}`);
      const change = displayPlayer?.rank_change;
      cell(row, Number.isInteger(change) && change !== 0 ? `${change > 0 ? '▲' : '▼'}${Math.abs(change)}` : '—');
    }
    const controls = el('div', undefined, 'row-actions');
    controls.append(button('Rename', async () => {
      const name = window.prompt('Player name', player.name);
      if (name === null) return;
      if (!name.trim()) throw new Error('Player name cannot be empty.');
      await api(`/api/players/${encodeURIComponent(player.id)}`, 'PATCH', {name: name.trim()}); await refresh();
    }), button('Delete player', async () => {
      if (!window.confirm(`Delete player “${player.name}” from the current roster and standings? Race history is retained.`)) return;
      await api(`/api/players/${encodeURIComponent(player.id)}`, 'DELETE', {}); await refresh();
    })); cell(row, controls); playersBody.append(row);
  });
  $('players').replaceChildren(state.players.length ? playersTable : el('p', 'Add the 12 players in your room to begin.', 'muted'));
  options($('race-recording'), state.recordings, 'No recording / timing unknown');
  renderHistory();
  if (penaltyEditing !== null) renderPenalties();
  // Keep in-progress finishing orders while refreshing available identities.
  if (editing !== null) {
    const race = state.races.find((item) => same(item.id, editing));
    if (race) document.querySelectorAll('#placements select').forEach((select) => options(select, alphabetical(racePlayers(race)), 'Choose player'));
  }
}
async function refresh(background = false, afterCurrent = false) {
  // A reorder requires a snapshot started after its write. Other callers
  // still coalesce overlapping polls/refreshes without waiting.
  while (refreshing) {
    if (!afterCurrent) return;
    await refreshing;
  }
  let finish;
  refreshing = new Promise(resolve => { finish = resolve; });
  try {
    const endpointRevision = externalStateRevision;
    const next = await api('/api/state');
    // Preview ownership follows observed server state even while a focused
    // editor prevents the normal dashboard redraw. Never discard its draft.
    if (state && state.tracker_mode !== next.tracker_mode) {
      OverlaySources.pausePreview('Tracker mode changed. Apply or refresh to resume.');
    }
    if (Array.isArray(next.overlay_profiles) && !next.overlay_profiles.some(source => source.slug === OverlaySources.selected())) {
      OverlaySources.pausePreview('The selected source was removed. Choose another source; unsaved drafts are kept.');
    }
    // A snapshot started before a local endpoint mutation must not resurrect its metadata.
    if (endpointRevision !== externalStateRevision && state) next.external_vision = state.external_vision;
    if (background && !canAutoRefresh()) return;
    if (background && state && state.tracker_mode !== next.tracker_mode && !canRedraw()) {
      message('Tracker mode changed in another tab. This draft still belongs to the previous mode. Refresh after finishing or discarding it.', true);
      return;
    }
    const scroll = {left: window.scrollX, top: window.scrollY};
    state = next;
    // Keep progress moving while a focused input or editor protects its draft.
    if (background && !canRedraw()) {
      renderModeControl();
      renderImports(false);
      $('capture').textContent = captureStatus();
    } else render();
    if (background) window.scrollTo(scroll);
  } finally { refreshing = null; finish(); }
}
function canAutoRefresh() {
  return !dragRaceRow && $('auto-refresh').checked && !$('folder-png-preview').open;
}
function canRedraw() {
  return !document.querySelector('.race-order :focus') && !backupBusy && !$('external-settings').dataset.testing && !themeMutationBusy && themeImageReads === 0 &&
    wwEditing === null && editing === null && timingEditing === null && penaltyEditing === null &&
    !document.querySelector('input:focus, select:focus, textarea:focus, [contenteditable]:focus') &&
    !document.querySelector('button:disabled:not(#camera-start):not(#camera-stop):not(#folder-png-copy):not(#theme-update):not(#theme-delete):not(#external-test):not(#external-test-cancel):not(#backup-select-all):not(#backup-export):not(#backup-confirm):not(#lounge-use-size)');
}
function engineStatus(engine, status, requested) {
  const names = {'external-vision': 'External vision endpoint', qwen: 'Qwen3-VL-8B BF16', 'qwen-4b-nf4': 'Qwen3-VL-4B NF4', 'qwen-4b-gguf': 'Qwen3-VL-4B GGUF Q4_K_M',
    'qwen-int8': 'Qwen3-VL-8B INT8',
    'qwen-nvfp4': 'Qwen3-VL-8B NVFP4', 'qwen36-nvfp4': 'Qwen3.6-27B NVFP4',
    paddle: 'PaddleOCR', tesseract: 'Tesseract'};
  if (!engine) return ['loading', 'selecting'].includes(status) ? 'Name reader: selecting engine…' : '';
  return `Name reader: ${names[engine] || engine}${requested === 'auto' ? ' (automatic)' : ''}${status === 'fallback' ? ' · fallback' : status === 'loading' ? ' · loading' : status === 'error' ? ' · unavailable' : ''}`;
}
const nvfp4DismissalKey = 'mkw-nvfp4-warning-dismissed';
let dismissedNvfp4 = [];
try {
  const saved = JSON.parse(localStorage.getItem(nvfp4DismissalKey) || '[]');
  if (Array.isArray(saved)) dismissedNvfp4 = saved.filter(value => typeof value === 'string');
} catch { /* Reminders work without storage. */ }
function renderNameEngineWarning(formId, warningId) {
  const engine = state?.tracker_mode === 'ww' ? 'ww' : $(formId).elements.name_engine.value;
  const warning = $(warningId);
  const nvfp4 = (engine === 'qwen-nvfp4' || engine === 'qwen36-nvfp4') && !dismissedNvfp4.includes(engine);
  const setupWarning = state?.reader_setup_warning || '';
  const signature = JSON.stringify([engine, nvfp4, setupWarning]);
  if (warning.dataset.signature === signature) return;
  warning.dataset.signature = signature;
  warning.hidden = !nvfp4 && !setupWarning;
  warning.replaceChildren();
  if (nvfp4) {
    warning.append(el('span', 'NVFP4 requires a supported NVIDIA Blackwell GPU and CUDA runtime. This app supports RTX 50-series SM120/121, not RTX 4070 or Radeon GPUs. An RTX 5070 supports NVFP4, but may not have enough free VRAM for the selected model.'));
    const dismiss = button('Dismiss hardware reminder', () => {
      dismissedNvfp4.push(engine);
      try { localStorage.setItem(nvfp4DismissalKey, JSON.stringify(dismissedNvfp4)); } catch { /* In-memory dismissal still works. */ }
      renderNameEngineWarning('camera-settings', 'capture-nvfp4-warning');
      renderNameEngineWarning('import-video', 'import-nvfp4-warning');
    });
    dismiss.className = 'secondary'; warning.append(dismiss);
  }
  if (setupWarning) warning.append(el('span', setupWarning));
}
['camera-settings', 'import-video'].forEach((formId) => {
  const warningId = formId === 'camera-settings' ? 'capture-nvfp4-warning' : 'import-nvfp4-warning';
  $(formId).elements.name_engine.addEventListener('change', (event) => { event.target.dataset.userSelected = 'true'; renderNameEngineWarning(formId, warningId); });
  $(formId).addEventListener('reset', () => { $(warningId).hidden = true; $(warningId).textContent = ''; delete $(warningId).dataset.signature; });
  renderNameEngineWarning(formId, warningId);
});
function captureStatus() {
  const capture = state.capture || {};
  const engine = engineStatus(capture.name_engine, capture.name_engine_status, state.cameras?.status?.name_engine);
  const timing = capture.sample_timing || {};
  const measured = ['acquisition_ms', 'recognition_ms', 'cycle_ms'].every(key => Number.isFinite(timing[key]))
    ? ` · Last sample: capture ${Math.round(timing.acquisition_ms)} ms, recognition ${Math.round(timing.recognition_ms)} ms, cycle ≈${Math.round(timing.cycle_ms)} ms` : '';
  const target = measured && Number.isFinite(timing.requested_interval_ms)
    ? ` · Target interval: ${Math.round(timing.requested_interval_ms)} ms` : '';
  return `${capture.status || 'Unavailable'}: ${capture.message || 'Automatic capture is not active.'}${engine ? ` · ${engine}` : ''}${measured}${target}`;
}
function renderImports(updateFolder = true) {
  if (updateFolder) options($('import-folder'), state.folders || [], 'Choose folder', $('import-folder').value || state.session.folder_id);
  const jobs = state.imports?.jobs || [];
  $('import-jobs').replaceChildren(...jobs.map((job) => {
    const card = el('article', undefined, 'import-job');
    card.append(el('h3', job.path || 'Imported video'), el('p', `${job.status} · ${formatOffset(Math.round((job.progress_seconds || 0) * 1000))} / ${job.duration_seconds ? formatOffset(Math.round(job.duration_seconds * 1000)) : 'duration unknown'} · ${job.observed_races ?? 0} races · ${job.observed_results ?? 0} results detected`));
    const engine = engineStatus(job.resolved_name_engine, job.name_engine_status, job.name_engine);
    if (engine) card.append(el('p', engine, 'muted'));
    if (Number.isFinite(job.percent)) { const progress = el('progress'); progress.max = 100; progress.value = job.percent; progress.setAttribute('aria-label', 'Video import progress'); card.append(progress, el('span', ` ${Math.round(job.percent)}%`)); }
    if (job.error) card.append(el('p', job.error, 'review-reasons'));
    if (['queued', 'running', 'starting', 'cancelling'].includes(job.status)) card.append(button('Cancel import', async () => { await api(`/api/imports/${encodeURIComponent(job.id)}/cancel`, 'POST', {}); await refresh(); }));
    return card;
  }));
}
function renderCurrentRace(pending) {
  const race = state.capture?.current_race;
  const container = $('current-capture-race');
  container.hidden = !race || pending.some(candidate => same(candidate.race_id, race.id));
  if (container.hidden) { container.replaceChildren(); return; }
  const card = el('article', undefined, 'capture-card current-race-card');
  card.dataset.raceId = race.id;
  card.append(el('h3', 'Current race'));
  const course = courseTitle(race, 'strong'); card.append(course);
  const recording = state.recordings.find(item => same(item.id, race.recording_id));
  card.append(el('p', `Race ${race.id} · ${race.status || 'pending'}${race.auto_confirmed ? ' · YOLO auto-confirmed' : ''} · Chapter ${formatOffset(race.offset_ms)} · ${recording?.label || 'No recording'}`, 'muted'));
  card.append(el('p', state.settings?.yolo_mode ? 'Waiting for results. YOLO will automatically score eligible detections.' : 'Waiting for results. No points are counted until you confirm the finishing positions.', 'muted'));
  const proof = el('div'); renderEvidence(proof, race); card.append(proof);
  const controls = el('div', undefined, 'inline');
  controls.append(button('Edit course / chapter', async () => openTiming(race.id)),
    button('View evidence', async () => openEvidence(race)), button('Enter results', async () => openEditor(race.id)));
  card.append(controls); container.replaceChildren(card);
}
function renderCapture() {
  if (state.tracker_mode === 'ww') { renderWWCapture(); return; }
  const pending = state.capture?.pending || [];
  renderCurrentRace(pending);
  $('review-count').textContent = `${pending.length} waiting`;
  const ids = new Set(pending.map((candidate) => String(candidate.id)));
  for (const key of captureDrafts.keys()) if (!ids.has(key)) captureDrafts.delete(key);
  const cards = pending.map((candidate) => {
    const key = String(candidate.id);
    const availablePlayers = alphabetical(capturePlayers(candidate));
    const race = state.races.find((item) => same(item.id, candidate.race_id));
    const archived = race && !same(race.session_id, state.session.id);
    if (!captureDrafts.has(key)) {
      captureDrafts.set(key, Array.from({length: 12}, (_, index) => {
        const row = candidate.rows?.find((entry) => entry.position === index + 1);
        const name = row?.name || '';
        const entry = {name, player_id: '', manualIdentity: false, manualName: false};
        matchIdentity(entry, availablePlayers); return entry;
      }));
    }
    const draft = captureDrafts.get(key);
    draft.forEach((entry, index) => {
      if (!entry.manualName && !entry.manualIdentity) {
        entry.name = candidate.rows?.find((row) => row.position === index + 1)?.name || '';
      }
      matchIdentity(entry, availablePlayers);
    });
    const card = el('article', undefined, 'capture-card');
    card.append(el('h3', candidate.course || 'Course unknown'));
    card.append(el('p', `Race ${candidate.race_id}${archived ? ' · Archived capture; affects archived results only' : ''}. Confirm all 12 identities. Matching names are suggestions and require your confirmation.`, 'muted'));
    if (state.tracker_mode !== 'ffa' && race && ![...state.players, ...state.archived_players, ...state.teams, ...state.archived_teams]
      .some(item => same(item.session_id, race.session_id)) &&
      !state.races.some(item => same(item.session_id, race.session_id) && item.revision)) {
      card.append(el('p', 'First roster: confirming creates editable teams from the first character of each reviewed name, including symbols, numbers and Japanese characters, ignoring case. No points count until you confirm.', 'muted'));
    }
    if (candidate.partial) {
      const observed = new Set((candidate.rows || []).map(row => row.position).filter(position => Number.isInteger(position) && position >= 1 && position <= 12)).size;
      card.append(el('p', `Partial screenshot: ${observed} of 12 positions observed. Unobserved slots stay blank. Complete all 12 identities before counting points. Fuller captures update only untouched fields.`, 'partial-review-note muted'));
    }
    if (candidate.reasons?.length) card.append(el('p', candidate.reasons.join(' · '), 'review-reasons'));
    if (candidate.screenshot_url && /^\/evidence\/[a-zA-Z0-9_.-]+$/.test(candidate.screenshot_url)) {
      const screenshot = el('img');
      screenshot.src = candidate.screenshot_url;
      screenshot.alt = `Captured results for ${candidate.course || 'unknown course'}`;
      screenshot.loading = 'lazy';
      screenshot.className = 'capture-screenshot';
      card.append(screenshot);
    }
    const form = el('form');
    const placements = el('div', undefined, 'placements');
    draft.forEach((entry, index) => {
      const row = candidate.rows?.find((item) => item.position === index + 1);
      const group = el('div', undefined, 'capture-placement');
      const confidence = Number(row?.confidence);
      group.append(el('strong', `${index + 1}. ${points[index]} points`));
      const quality = row?.confidence_available === false ? 'confidence unavailable' :
        `confidence ${Number.isFinite(confidence) ? Math.round(confidence * 100) + '%' : 'unknown'}`;
      group.append(el('p', `OCR: ${row?.name || 'Unreadable'} · ${quality}`, 'muted'));
      const nameLabel = el('label', 'Name for new player');
      const name = el('input'); name.value = entry.name; name.maxLength = 120;
      name.addEventListener('input', () => { entry.name = name.value; entry.manualName = true; matchIdentity(entry, availablePlayers); player.value = entry.player_id; name.required = !player.value; });
      nameLabel.append(name);
      const playerLabel = el('label', 'Player identity');
      const player = el('select');
      options(player, availablePlayers, 'Create new player using name above', entry.player_id);
      entry.player_id = player.value;
      player.addEventListener('change', () => { entry.player_id = player.value; entry.manualIdentity = true; name.required = !player.value; });
      name.required = !player.value;
      playerLabel.append(player); group.append(nameLabel, playerLabel); placements.append(group);
    });
    const controls = el('div', undefined, 'inline');
    const confirm = el('button', 'Confirm identities and count points'); confirm.type = 'submit';
    controls.append(confirm, button('Dismiss capture', async () => {
      await api('/api/capture/dismiss', 'POST', {candidate_id: candidate.id});
      captureDrafts.delete(key); await refresh();
    }));
    form.append(placements, controls);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      run(confirm, async () => {
        const selected = draft.filter((entry) => entry.player_id).map((entry) => entry.player_id);
        if (new Set(selected).size !== selected.length) throw new Error('An existing player cannot occupy two finishing positions.');
        if (draft.some((entry) => !entry.player_id && !entry.name.trim())) throw new Error('Every new player needs a name.');
        const players = draft.map((entry) => entry.player_id ? {player_id: availablePlayers.find((player) => same(player.id, entry.player_id))?.id} : {name: entry.name.trim()});
        await api('/api/capture/review', 'POST', {candidate_id: candidate.id, players});
        captureDrafts.delete(key); await refresh(); message('Capture confirmed. Points were calculated from finishing positions.');
      });
    });
    card.append(form);
    return card;
  });
  $('capture-reviews').replaceChildren(...(cards.length ? cards : [el('p', 'No captured results waiting for review.', 'muted')]));
}
function openTiming(id) {
  selectTab('history');
  const race = state.races.find((item) => same(item.id, id));
  if (!race) throw new Error('Race no longer exists.');
  if (timingEditing !== null && !same(timingEditing, id) && !window.confirm('Discard the unfinished chapter edit?')) return;
  timingEditing = race.id;
  const form = $('race-timing');
  form.elements.course.value = race.course || '';
  options($('timing-recording'), state.recordings, 'No recording / timing unknown', race.recording_id);
  form.elements.offset.value = race.offset_ms == null ? '' : formatOffset(race.offset_ms);
  $('timing-panel').hidden = false;
  $('timing-panel').scrollIntoView({block: 'start'});
  form.elements.course.focus();
}
function openEditor(id) {
  if (state.tracker_mode === 'ww') { openWWEditor(id); return; }
  selectTab('history');
  const race = state.races.find((r) => same(r.id, id));
  if (!race) throw new Error('Race no longer exists. Refresh the page.');
  if (editing !== null && !same(editing, id) && !window.confirm('Discard the unfinished result entry?')) return;
  editing = race.id;
  $('editor-title').textContent = `${race.placements?.length ? 'Correct' : 'Review'} results · ${race.course}`;
  renderEvidence($('editor-evidence'), race, true);
  $('placements').replaceChildren();
  points.forEach((score, index) => {
    const label = el('label', `${index + 1}. ${score} points`);
    const select = el('select'); select.required = true; select.name = `position-${index + 1}`;
    options(select, racePlayers(race), 'Choose player', race.placements?.find((p) => p.position === index + 1)?.player_id);
    label.append(select); $('placements').append(label);
  });
  $('save-results').textContent = race.placements?.length ? 'Save corrected results' : 'Confirm and count points';
  $('editor-panel').hidden = false;
  $('editor-panel').scrollIntoView({block: 'start'});
  $('placements').querySelector('select').focus();
}
function submit(id, operation) {
  const form = $(id);
  form.addEventListener('submit', (event) => { event.preventDefault(); run(form.querySelector('button[type=submit], button:not([type])'), () => operation(form, new FormData(form))); });
}
submit('import-video', async (form, data) => {
  const path = data.get('path').trim();
  const file = form.elements.video.files[0];
  if (Boolean(path) === Boolean(file)) throw new Error('Provide a local video path or choose a file, not both.');
  const parameters = {folder_id: data.get('folder_id'), name_engine: state.tracker_mode === 'ww' ? 'auto' : data.get('name_engine'), stride: Number(data.get('stride')), expected_tracker_mode: state.tracker_mode || 'mogi'};
  if (!parameters.folder_id) throw new Error('Choose a destination folder.');
  if (file) {
    const response = await fetch(`/api/imports/upload?${new URLSearchParams(parameters)}`, {method: 'POST', headers: {'X-Filename': encodeURIComponent(file.name)}, body: file});
    const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Video upload failed.');
  } else await api('/api/imports', 'POST', {path, ...parameters});
  await refresh(); message(state.tracker_mode === 'ww' ? 'Video import started. Clear results update WW history automatically.' : state.settings?.yolo_mode ? 'Video import started with YOLO. Check automatically scored results in Race history.' : 'Video import started. Recognized results will appear under Capture review.');
});
submit('add-folder', async (form, data) => { await api('/api/sessions', 'POST', {name: data.get('name').trim(), expected_session_id: state.session.id}); form.reset(); clearEditors(); await refresh(); });
$('history-collapse-all').addEventListener('click', () => {
  // Include filtered-out folders without rebuilding history or touching editor drafts.
  (state?.folders || []).forEach(folder => { folderExpansion[String(folder.id)] = false; });
  document.querySelectorAll('#history details.history-folder').forEach(folder => { folder.open = false; });
  try { localStorage.setItem(folderExpansionKey, JSON.stringify(folderExpansion)); } catch { /* Storage is optional. */ }
});
['history-folder', 'history-sort', 'history-direction'].forEach((id) => $(id).addEventListener('change', renderHistory));
['add-player', 'add-team', 'add-race'].forEach((id) => {
  $(id).addEventListener('input', () => { $(id).dataset.draftStarted = 'true'; });
  $(id).addEventListener('change', () => { $(id).dataset.draftStarted = 'true'; });
});
submit('add-team', async (form, data) => { await api('/api/teams', 'POST', {name: data.get('name').trim(), expected_session_id: form.dataset.sessionId, expected_tracker_mode: form.dataset.trackerMode}); form.reset(); delete form.dataset.draftStarted; await refresh(); });
submit('add-player', async (form, data) => { await api('/api/players', 'POST', {name: data.get('name').trim(), expected_session_id: form.dataset.sessionId, expected_tracker_mode: form.dataset.trackerMode}); form.reset(); delete form.dataset.draftStarted; await refresh(); });
submit('add-recording', async (form, data) => { await api('/api/recordings', 'POST', {label: data.get('label').trim()}); form.reset(); await refresh(); });
submit('add-race', async (form, data) => {
  const offset = parseOffset(data.get('offset'));
  const recording = state.recordings.find((r) => same(r.id, data.get('recording_id')));
  if (offset !== null && !recording) throw new Error('Choose the recording that this timestamp belongs to.');
  const race = await api('/api/races', 'POST', {course: data.get('course').trim(), recording_id: recording?.id ?? null, offset_ms: offset, expected_session_id: form.dataset.sessionId, expected_tracker_mode: form.dataset.trackerMode});
  delete form.dataset.draftStarted;
  form.elements.course.value = ''; form.elements.offset.value = ''; await refresh(); openEditor(race.id);
});
submit('race-timing', async (form, data) => {
  const course = data.get('course').trim();
  if (!course) throw new Error('Course name cannot be empty.');
  const offset = parseOffset(data.get('offset'));
  const recording = state.recordings.find((item) => same(item.id, data.get('recording_id')));
  if (offset !== null && !recording) throw new Error('Choose the recording that this timestamp belongs to.');
  await api(`/api/races/${encodeURIComponent(timingEditing)}`, 'PATCH', {course, recording_id: recording?.id ?? null, offset_ms: offset});
  timingEditing = null; $('timing-panel').hidden = true; await refresh(); message('Race chapter updated.');
});
$('cancel-timing').addEventListener('click', () => { timingEditing = null; $('timing-panel').hidden = true; });
submit('results', async () => {
  const selected = [...document.querySelectorAll('#placements select')].map((select) => select.value);
  if (selected.some((id) => !id) || new Set(selected).size !== 12) throw new Error('Choose 12 different players, one for each finishing position.');
  const race = state.races.find((r) => same(r.id, editing));
  if (!race) throw new Error('Race no longer exists.');
  const correction = Boolean(race.placements?.length);
  if (correction && !window.confirm('Replace this race result and recalculate its session scores?')) return;
  await api(`/api/races/${encodeURIComponent(editing)}/results`, 'POST', {player_ids: selected.map((id) => racePlayers(race).find((player) => same(player.id, id)).id), correction});
  editing = null; $('editor-panel').hidden = true; await refresh(); message('Results saved. Points are calculated from finishing position.');
});
['export-format', 'export-recording'].forEach(id => $(id).addEventListener('change', renderTimestampHelp));
submit('export', (form, data) => downloadFile(`/api/chapters?${new URLSearchParams(data)}`,
  `race-chapters.${data.get('format')}`, 'Timestamp export failed'));
$('cancel-edit').addEventListener('click', () => { editing = null; $('editor-panel').hidden = true; });
submit('cleanup-videos', async (form, data) => {
  const days = Number(data.get('older_than_days'));
  if (!Number.isInteger(days) || days < 0) throw new Error('Cleanup age must be a nonnegative whole number of days.');
  const eligible = (state.videos?.files || []).filter((file) => file.can_delete && !file.active && (days === 0 || videoModifiedTime(file) <= Date.now() - days * 86400000));
  const size = eligible.reduce((sum, file) => sum + file.size_bytes, 0);
  const cutoff = days === 0 ? 'all inactive app-managed uploads' : `inactive app-managed uploads older than ${days} days`;
  if (!window.confirm(`Permanently delete ${cutoff}? The current library lists ${eligible.length} eligible files (${formatBytes(size)}). Results, evidence and original local-path videos are kept. This cannot be undone.`)) return;
  const result = await api('/api/videos/cleanup', 'POST', {older_than_days: days}); await refresh(); cleanupResult(result);
});
submit('new-session', async (form, data) => {
  await api('/api/sessions', 'POST', {name: data.get('name').trim(), expected_session_id: state.session.id});
  clearEditors(); form.reset(); await refresh(); message(state.tracker_mode === 'ww' ? 'New WW session created. Earlier WW sessions remain in their folders.' : state.tracker_mode === 'ffa' ? 'New Mogi FFA session created with empty players and races. Earlier sessions remain in their folders.' : 'New session created with empty players, teams and races. Earlier sessions remain in their folders.');
});
submit('add-penalty', async (form, data) => {
  await api(`/api/races/${encodeURIComponent(penaltyEditing)}/penalties`, 'POST', {player_id: data.get('player_id'), points: Number(data.get('points')), reason: data.get('reason').trim()});
  form.elements.points.value = ''; form.elements.reason.value = ''; await refresh();
});
$('report-font').addEventListener('change', previewFont);
$('font-preview-text').addEventListener('input', previewFont);
$('font-reload').addEventListener('click', () => reloadFonts().catch(error => message(error.message, true)));
$('discord-settings').addEventListener('input', () => { $('discord-settings').dataset.dirty = 'true'; });
submit('discord-settings', async (form) => {
  const enabled = $('discord-enabled').checked;
  const applicationId = $('discord-application-id').value.trim();
  await api('/api/settings', 'PATCH', {discord_enabled: enabled, discord_application_id: applicationId});
  if (enabled === $('discord-enabled').checked && applicationId === $('discord-application-id').value.trim()) {
    delete form.dataset.dirty;
  }
  await refresh(); message('Discord settings saved.');
});
let yoloDraft = null;
$('yolo-mode').addEventListener('change', async () => {
  const enabled = $('yolo-mode').checked;
  $('yolo-mode').checked = !!state.settings?.yolo_mode;
  const ownership = {expected_session_id: state.session.id, expected_tracker_mode: state.tracker_mode || 'mogi'};
  if (enabled) {
    yoloDraft = ownership;
    $('yolo-warning').showModal();
    return;
  }
  try {
    await api('/api/settings', 'PATCH', {...ownership, yolo_mode: false});
    await refresh(); message('YOLO disabled. Future results require review; scored races are unchanged.');
  } catch (error) { message(error.message, true); }
});
$('yolo-cancel').addEventListener('click', () => $('yolo-warning').close());
$('yolo-warning').addEventListener('close', () => { yoloDraft = null; });
$('yolo-warning').addEventListener('cancel', (event) => {
  if ($('yolo-confirm').disabled) event.preventDefault();
});
$('yolo-confirm').addEventListener('click', async () => {
  if (!yoloDraft) return;
  const ownership = yoloDraft;
  $('yolo-confirm').disabled = true;
  $('yolo-cancel').disabled = true;
  try {
    await api('/api/settings', 'PATCH', {...ownership, yolo_mode: true, confirm_yolo: true});
    message('YOLO enabled. Future Mogi results will be scored automatically. Check race history for mistakes.');
  } catch (error) { message(error.message, true); }
  finally {
    $('yolo-confirm').disabled = false;
    $('yolo-cancel').disabled = false;
    $('yolo-warning').close();
    await refresh();
  }
});

$('font-settings').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/settings', 'PATCH', {report_font: $('report-font').value});
    await refresh(); message('Overlay and PNG font saved.');
  } catch (error) { message(error.message, true); }
});
$('font-reset').addEventListener('click', async () => {
  try {
    await api('/api/settings', 'PATCH', {report_font: ''});
    $('report-font').value = ''; await refresh(); previewFont();
    message('Default Unicode font restored.');
  } catch (error) { message(error.message, true); }
});
reloadFonts().catch(error => message(error.message, true));
$('mogi-png-graph').addEventListener('change', (event) => {
  const select = event.currentTarget;
  run(select, async () => {
    try {
      await api('/api/settings', 'PATCH', {mogi_png_graph: select.value});
      await refresh(false, true); message('Mogi PNG graph saved.');
    } catch (error) { select.value = state.settings?.mogi_png_graph || 'cumulative'; throw error; }
  });
});
$('standings-format').addEventListener('change', () => run($('standings-format'), async () => {
  try { await api('/api/settings', 'PATCH', {standings_format: $('standings-format').value}); await refresh(); message('Standings format saved. Update the OBS text source file path to the path shown in Standings.'); }
  catch (error) { $('standings-format').value = state.settings?.standings_format || 'txt'; throw error; }
}));
$('close-evidence').addEventListener('click', () => { evidenceEditing = null; $('evidence-panel').hidden = true; });
$('close-penalties').addEventListener('click', () => { penaltyEditing = null; $('penalty-panel').hidden = true; });
$('refresh').addEventListener('click', () => run($('refresh'), refresh));
// Poll only when controls and editors are idle. Capture drafts survive rerenders.
setInterval(() => { if (canAutoRefresh()) refresh(true).catch((error) => message(error.message, true)); }, 3000);
const themeTokenLabels = {bg: 'Background', panel: 'Panel', input: 'Input', text: 'Text', muted: 'Muted text', line: 'Border', accent: 'Accent', accent_text: 'Accent text', danger: 'Danger', warning: 'Warning'};
let themeDraft = null;
let themeImageBase64;
let themeObjectUrl = null;
let themeSuggestions = null;
let themeDraftGeneration = 0;
let themeMutationBusy = false;
let themeImageReads = 0;
let themeImageGeneration = 0;
function renderThemeMutationControls() {
  const busy = themeMutationBusy || themeImageReads > 0;
  $('theme-save-copy').disabled = busy;
  $('theme-update').disabled = busy || !themeDraft?.custom;
  $('theme-delete').disabled = busy || !themeDraft?.custom;
  $('theme-delete').textContent = themeDraft?.overridden ? 'Restore built-in' : 'Delete saved theme';
  $('theme-settings').querySelector('button').disabled = busy;
}
async function mutateTheme(operation) {
  if (themeMutationBusy || themeImageReads > 0) return;
  themeMutationBusy = true;
  renderThemeMutationControls();
  message('');
  try { await operation(); } catch (error) { message(error.message, true); }
  finally { themeMutationBusy = false; renderThemeMutationControls(); }
}
$('custom-theme-form').addEventListener('input', () => { ++themeDraftGeneration; });
$('character-theme').addEventListener('change', () => { ++themeDraftGeneration; });
function showModeSwitchWarning(text, anchor, event) {
  const warning = $('mode-switch-warning');
  warning.textContent = text;
  warning.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const x = event?.detail ? event.clientX : rect.left;
  const y = event?.detail ? event.clientY : rect.bottom;
  warning.style.left = Math.max(8, Math.min(x + 10, innerWidth - warning.offsetWidth - 8)) + 'px';
  warning.style.top = Math.max(8, Math.min(y + 10, innerHeight - warning.offsetHeight - 8)) + 'px';
}
document.addEventListener('click', () => { $('mode-switch-warning').hidden = true; }, true);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') $('mode-switch-warning').hidden = true;
});
$('blocked-tracker-mode').addEventListener('click', event => {
  showModeSwitchWarning(state.mode_switch_warning, event.currentTarget, event);
});
$('tracker-mode').addEventListener('change', () => { ++themeDraftGeneration; });
function themeDraftStatus(text) { $('theme-editor-status').textContent = text; }
function clearThemeImagePreview() {
  if (themeObjectUrl) URL.revokeObjectURL(themeObjectUrl);
  themeObjectUrl = null;
}
function loadThemeDraft(theme) {
  if (!theme) return;
  ++themeDraftGeneration; ++themeImageGeneration;
  clearThemeImagePreview();
  themeDraft = structuredClone(theme);
  themeImageBase64 = undefined;
  themeSuggestions = null;
  $('custom-theme-name').value = theme.custom ? theme.name : `${theme.name} custom`;
  $('custom-theme-image').value = '';
  $('theme-suggestions').hidden = true;
  renderThemeMutationControls();
  setCharacterPortrait($('custom-theme-icon'), theme);
  $('custom-theme-tokens').replaceChildren(...Object.entries(themeTokenLabels).map(([key, name]) => {
    const label = el('label', name); const input = el('input'); input.type = 'color'; input.value = theme.tokens[key]; input.dataset.token = key;
    input.addEventListener('input', () => { themeDraft.tokens[key] = input.value; renderThemeDraftPreview(); });
    label.append(input); return label;
  }));
  $('custom-theme-colors').replaceChildren(...theme.team_colors.map((color, index) => {
    const label = el('label', `Standings color ${index + 1}`); const input = el('input'); input.type = 'color'; input.value = color; input.dataset.colorIndex = index;
    input.addEventListener('input', () => { themeDraft.team_colors[index] = input.value; renderThemeDraftPreview(); });
    label.append(input); return label;
  }));
  renderThemeDraftPreview();
  themeDraftStatus(theme.custom ? 'Editing a saved theme. Changes are not applied until saved.' : 'Built-in palette copied into a new draft.');
}
function renderThemeDraftPreview() {
  const tokens = themeDraft.tokens; const preview = $('custom-theme-preview');
  preview.style.backgroundColor = tokens.panel; preview.style.color = tokens.text; preview.style.borderColor = tokens.line;
  preview.querySelector('span').style.color = tokens.muted;
  preview.querySelector('button').style.backgroundColor = tokens.accent;
  preview.querySelector('button').style.color = tokens.accent_text;
  const luminance = (hex) => {
    const channels = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16) / 255).map((c) => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4);
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
  };
  const contrast = (a, b) => { const values = [luminance(a), luminance(b)].sort((x, y) => y - x); return (values[0] + .05) / (values[1] + .05); };
  const low = contrast(tokens.text, tokens.panel) < 4.5 || contrast(tokens.accent_text, tokens.accent) < 4.5;
  $('theme-contrast-note').textContent = low ? 'Low text contrast: consider lighter text or darker backgrounds before saving.' : 'Text and accent preview contrast is at least 4.5:1. Check other colors in your actual layout.';
}
$('theme-edit-selected').addEventListener('click', () => {
  if (themeDraft && !confirm('Replace the current editor draft with the selected theme?')) return;
  loadThemeDraft((state.themes || []).find((theme) => theme.id === $('character-theme').value) || state.theme);
});
$('custom-theme-image').addEventListener('change', () => run($('theme-suggest'), async () => {
  ++themeDraftGeneration;
  const generation = ++themeImageGeneration;
  const file = $('custom-theme-image').files[0];
  if (!file) return;
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 4 * 1024 * 1024) throw new Error('Choose a PNG, JPEG or WebP image no larger than 4 MiB.');
  ++themeImageReads; renderThemeMutationControls();
  try {
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('Could not read the selected image.')); reader.readAsDataURL(file);
    });
    if (generation !== themeImageGeneration) return;
    ++themeDraftGeneration;
    clearThemeImagePreview(); themeObjectUrl = URL.createObjectURL(file); themeImageBase64 = base64;
    const preview = $('custom-theme-icon'); preview.src = themeObjectUrl; preview.hidden = false; preview.alt = 'Uploaded icon preview';
    themeSuggestions = null; $('theme-suggestions').hidden = true;
    themeDraftStatus('Image added to the draft. Save the theme to keep it; colors have not changed.');
  } finally { --themeImageReads; renderThemeMutationControls(); }
}));
$('theme-remove-icon').addEventListener('click', () => {
  ++themeDraftGeneration; ++themeImageGeneration; clearThemeImagePreview(); themeImageBase64 = null; $('custom-theme-image').value = '';
  $('custom-theme-icon').hidden = true; $('custom-theme-icon').removeAttribute('src'); themeSuggestions = null; $('theme-suggestions').hidden = true;
  themeDraftStatus('Icon removed from the draft. Save to apply.');
});
$('theme-suggest').addEventListener('click', () => run($('theme-suggest'), async () => {
  if (!themeImageBase64) throw new Error('Upload an image before requesting color suggestions.');
  const generation = themeDraftGeneration;
  const suggestion = await api('/api/themes/suggest', 'POST', {image_base64: themeImageBase64});
  if (generation !== themeDraftGeneration) return;
  themeSuggestions = suggestion;
  $('theme-suggestion-palette').replaceChildren(...suggestion.palette.map((color) => { const chip = el('span'); chip.style.backgroundColor = color; chip.title = color; return chip; }));
  $('theme-suggestions').hidden = false; themeDraftStatus('Suggestions ready. Your current colors are unchanged.');
}));
$('theme-apply-suggestions').addEventListener('click', () => {
  if (!themeSuggestions) return;
  ++themeDraftGeneration;
  themeDraft.tokens = {...themeSuggestions.tokens}; themeDraft.team_colors = [...themeSuggestions.team_colors];
  document.querySelectorAll('#custom-theme-tokens input').forEach((input) => { input.value = themeDraft.tokens[input.dataset.token]; });
  document.querySelectorAll('#custom-theme-colors input').forEach((input) => { input.value = themeDraft.team_colors[Number(input.dataset.colorIndex)]; });
  renderThemeDraftPreview(); themeDraftStatus('Suggested colors applied to the draft. You can edit every color before saving.');
});
async function saveCustomTheme(update) {
  if (!themeDraft) return;
  const generation = themeDraftGeneration;
  const body = {name: $('custom-theme-name').value.trim(), tokens: {...themeDraft.tokens}, team_colors: [...themeDraft.team_colors]};
  if (!body.name) throw new Error('Give this theme a name.');
  if (update) {
    if (!themeDraft.custom) throw new Error('Built-in palettes cannot be changed. Save as a new theme.');
    body.id = themeDraft.id;
  }
  if (themeImageBase64 !== undefined) body.icon_base64 = themeImageBase64;
  else if (!update && themeDraft.icon_url) {
    // A copied custom theme keeps its icon without trusting arbitrary file paths.
    const response = await fetch(themeDraft.icon_url);
    if (!response.ok) throw new Error('Could not copy the saved icon. Upload it again.');
    const blob = await response.blob();
    body.icon_base64 = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(new Error('Could not copy icon.')); reader.readAsDataURL(blob); });
  }
  const saved = await api('/api/themes', 'POST', body);
  const current = () => {
    if (generation === themeDraftGeneration) return true;
    themeDraftStatus('Earlier snapshot saved. Your newer draft and selection were kept; choose the saved theme when ready.');
    return false;
  };
  if (!current()) return;
  await api('/api/settings', 'PATCH', {theme: saved.id});
  if (!current()) return;
  settingsSignature = ''; document.activeElement?.blur(); await refresh();
  if (!current()) return;
  loadThemeDraft(saved);
  themeDraftStatus('Theme and icon saved and selected for the dashboard, PNG and overlay.');
  message('Custom theme saved.');
}
$('custom-theme-form').addEventListener('submit', (event) => {
  event.preventDefault(); mutateTheme(() => saveCustomTheme(false));
});
$('theme-update').addEventListener('click', () => mutateTheme(() => saveCustomTheme(true)));
$('theme-delete').addEventListener('click', () => mutateTheme(async () => {
  if (!themeDraft?.custom) return;
  const overridden = !!themeDraft.overridden;
  const prompt = overridden
    ? `Restore built-in theme “${themeDraft.name}”? Removes the imported/custom override. Sessions remain unchanged.`
    : `Delete saved theme “${themeDraft.name}”? This does not delete any sessions.`;
  if (!confirm(prompt)) return;
  const id = themeDraft.id;
  const generation = themeDraftGeneration;
  if (!overridden && state.settings.theme === id) await api('/api/settings', 'PATCH', {theme: 'mario'});
  await api(`/api/themes/${encodeURIComponent(id)}`, 'DELETE', {});
  if (generation !== themeDraftGeneration) { themeDraftStatus('Saved theme deleted. Your newer draft was kept.'); return; }
  settingsSignature = ''; document.activeElement?.blur(); await refresh();
  if (generation !== themeDraftGeneration) { themeDraftStatus('Saved theme deleted. Your newer draft was kept.'); return; }
  loadThemeDraft(state.theme);
  themeDraftStatus(overridden ? 'Built-in theme restored. Sessions and the selected theme ID are unchanged.' : 'Saved theme deleted. The built-in palette remains available.');
}));

submit('theme-settings', async (form, data) => {
  if (themeMutationBusy || themeImageReads > 0) return;
  await api('/api/settings', 'PATCH', {theme: data.get('theme')});
  settingsSignature = ''; form.querySelector('button').focus(); document.activeElement.blur();
  await refresh(); message('Theme saved for the dashboard, PNG and overlay.');
});
OverlaySources.init();
function collectOverlaySettings(form, mode = state.tracker_mode || 'mogi') {
  const data = new FormData(form);
  const overlay = {};
  (mode !== 'mogi' ? ['title', 'slide_direction', 'transition_style'] : ['layout', 'title', 'slide_direction', 'transition_style']).forEach((key) => { overlay[key] = data.get(key); });
  ['width', 'height', 'font_size', 'opacity', 'scroll_speed', 'slide_duration'].forEach((key) => { overlay[key] = Number(data.get(key)); });
  if (mode === 'mogi') overlay.highlight_team_id = data.get('highlight_team_id') || null;
  if (mode === 'ffa') overlay.highlight_player_id = data.get('highlight_player_id') || null;
  for (const key of ['auto_visibility', 'auto_visibility_during_races']) overlay[key] = form.elements[key].checked;
  (mode === 'ww' ? ['show_header', 'animate', 'scroll', 'ww_show_graph', 'ww_show_current', 'ww_show_peak', 'ww_show_lowest', 'ww_show_last_10', 'ww_show_character'] : ['show_header', 'show_players', 'show_gaps', 'animate', 'scroll', 'alternating_backgrounds']).forEach((key) => { if (mode !== 'ffa' || key !== 'show_players') overlay[key] = form.elements[key].checked; });
  return overlay;
}
submit('overlay-settings', async (form) => {
  await OverlaySources.saveOverlay(collectOverlaySettings(form));
  await refresh(); message('OBS browser overlay settings saved.');
});
$('copy-overlay-url').addEventListener('click', () => run($('copy-overlay-url'), async () => {
  try { await navigator.clipboard.writeText($('overlay-url').value); message('Overlay URL copied.'); }
  catch { $('overlay-url').focus(); $('overlay-url').select(); message('Select and copy the URL above.'); }
}));
['show', 'hide', 'toggle'].forEach(action => {
  const control = $(`overlay-${action}`);
  control.addEventListener('click', () => run(control, async () => {
    await api(`${OverlaySources.endpoint()}/${action}`, 'POST', {}); await refresh();
  }));
});
let cameraSignature = '';
function renderCameras(cameras) {
  if (!cameras) return;
  const status = cameras.status || {};
  const form = $('camera-settings');
  if (!form.elements.capture_mode.dataset.dirty && status.capture_mode) form.elements.capture_mode.value = status.capture_mode;
  if (!form.elements.obs_source.dataset.dirty && status.obs_source) form.elements.obs_source.value = status.obs_source;
  const obsMode = form.elements.capture_mode.value === 'obs';
  $('camera-device-field').hidden = obsMode;
  $('camera-refresh').hidden = obsMode;
  $('camera-obs-clock-field').hidden = obsMode;
  $('camera-input-note').hidden = obsMode;
  $('obs-input-note').hidden = !obsMode;
  $('obs-source-field').hidden = !obsMode;
  $('camera-obs-fields').hidden = !obsMode && !form.elements.use_obs_clock.checked;
  form.elements.obs_source.required = obsMode;
  $('camera-status').textContent = `${status.state || 'stopped'}: ${status.message || 'Choose a capture input, then start capture.'}`;
  const devices = cameras.devices || [];
  const signature = JSON.stringify(devices);
  if (signature !== cameraSignature) {
    const selection = $('camera-device').value || status.device_id || devices.find(device => device.available && /obs/i.test(device.name))?.id;
    $('camera-device').replaceChildren(new Option(devices.length ? 'Choose a camera' : 'No camera inputs found', ''), ...devices.map((device) => {
      const option = new Option(`${device.name} (${device.path || device.id})${device.available ? '' : ' — unavailable'}`, device.id);
      option.disabled = !device.available; option.title = device.error || ''; return option;
    }));
    $('camera-device').value = selection || ''; cameraSignature = signature;
  }
  const busy = typeof status.busy === 'boolean' ? status.busy : ['starting', 'running', 'stopping'].includes(status.state);
  $('camera-start').disabled = busy || (!obsMode && !devices.some(device => device.available));
  $('camera-stop').disabled = !['starting', 'running', 'error'].includes(status.state);
  $('camera-device').disabled = busy || obsMode;
  form.elements.capture_mode.disabled = busy;
  form.elements.obs_source.disabled = busy || !obsMode;
  const errors = devices.filter(device => device.error).map(device => `${device.name}: ${device.error}`).join(' · ');
  $('camera-errors').textContent = errors; $('camera-errors').hidden = obsMode || !errors;
  const connection = state?.obs_connection || {};
  $('obs-credentials-error').textContent = connection.error || '';
  $('obs-credentials-error').hidden = !connection.error;
  if (connection.url && document.activeElement !== form.elements.obs_url && !form.elements.obs_url.dataset.dirty) form.elements.obs_url.value = connection.url;
  form.elements.obs_password.placeholder = connection.password_saved ? 'Leave blank to use saved password' : 'OBS WebSocket password';
  $('obs-password-status').textContent = connection.password_saved ? 'Saved locally' : 'No password saved';
  $('obs-password-clear').hidden = !connection.password_saved;
}
$('camera-refresh').addEventListener('click', () => run($('camera-refresh'), async () => {
  if ($('capture-mode').value === 'obs') return;
  renderCameras(await api('/api/cameras')); message('Camera list refreshed.');
}));
$('camera-stop').addEventListener('click', () => run($('camera-stop'), async () => {
  await api('/api/cameras/stop', 'POST', {}); await refresh();
}).finally(() => renderCameras(state?.cameras)));
$('camera-settings').elements.obs_url.addEventListener('input', (event) => { event.target.dataset.dirty = 'true'; });
$('obs-password-clear').addEventListener('click', () => run($('obs-password-clear'), async () => {
  await api('/api/obs/credentials', 'DELETE', {}); $('camera-settings').elements.obs_password.value = '';
  await refresh(); message('Saved OBS password removed.');
}));
$('camera-obs-clock').addEventListener('change', () => renderCameras(state?.cameras));
$('capture-mode').addEventListener('change', (event) => {
  event.target.dataset.dirty = 'true'; renderCameras(state?.cameras);
});
$('obs-source').addEventListener('input', (event) => { event.target.dataset.dirty = 'true'; });
async function switchMode() {
  const target = $('tracker-mode').value;
  if (wwEditing || editing || timingEditing || document.querySelector('form[data-draft-started]')) {
    if (!window.confirm('Switch tracker mode and discard unfinished editors and entries? Saved results are kept.')) {
      $('tracker-mode').value = state.tracker_mode || 'mogi'; return;
    }
  }
  try {
    await api('/api/tracker-mode', 'POST', {tracker_mode: target, expected_session_id: state.session.id});
    clearEditors();
    ['add-player', 'add-team', 'add-race', 'ww-player'].forEach(id => { $(id).reset(); delete $(id).dataset.draftStarted; });
    $('history-folder').value = ''; $('import-folder').value = ''; settingsSignature = '';
    await refresh();
    message(`Switched to ${target === 'ww' ? 'Worldwides' : target === 'ffa' ? 'Mogi FFA' : 'Mogi Teams'}. Sessions remain separate.`);
  } catch (error) {
    $('tracker-mode').value = state.tracker_mode || 'mogi';
    showModeSwitchWarning(error.message, $('tracker-mode'));
  }
}
$('tracker-mode').addEventListener('change', async () => {
  switchingMode = true;
  try { await run($('tracker-mode'), switchMode); }
  finally { switchingMode = false; renderModeControl(); }
});
$('ww-stats-scope').addEventListener('change', renderWW);
$('ww-player').addEventListener('input', () => { $('ww-player').dataset.draftStarted = 'true'; });
submit('ww-player', async (form, data) => {
  await api('/api/ww/player', 'POST', {name: data.get('name').trim(), expected_session_id: form.dataset.sessionId, expected_tracker_mode: 'ww'});
  delete form.dataset.draftStarted; await refresh(); message('WW player label saved. Recognition still follows the highlighted local player.');
});
submit('ww-results', async (form, data) => {
  if (!wwEditing) throw new Error('Open a WW race first.');
  const result = {};
  ['position', 'vr_after', 'vr_before', 'delta'].forEach(key => {
    const value = data.get(key).trim();
    result[key] = value === '' ? null : Number(value);
    if (result[key] !== null && !Number.isSafeInteger(result[key])) throw new Error('WW result values must be whole numbers.');
  });
  if (wwEditing.revision && !window.confirm('Replace this WW result and recalculate its session statistics?')) return;
  // Keep legacy lobby metadata unless this correction proves it obsolete.
  const lobbySize = wwEditing.lobbySize !== null && result.position > wwEditing.lobbySize ? null : wwEditing.lobbySize;
  await api(`/api/ww/races/${encodeURIComponent(wwEditing.id)}/result`, 'PATCH', {...result, lobby_size: lobbySize,
    expected_session_id: wwEditing.sessionId, expected_revision: wwEditing.revision, expected_tracker_mode: 'ww'});
  wwEditing = null; $('ww-editor-panel').hidden = true; await refresh(); message('WW result saved. Unknown values remain unknown.');
});
$('ww-cancel-edit').addEventListener('click', () => { wwEditing = null; $('ww-editor-panel').hidden = true; });
$('camera-settings').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget; const data = new FormData(form);
  run($('camera-start'), async () => {
    const obsMode = data.get('capture_mode') === 'obs';
    const useClock = obsMode || form.elements.use_obs_clock.checked;
    await api('/api/cameras/start', 'POST', {capture_mode: data.get('capture_mode'), name_engine: state.tracker_mode === 'ww' ? 'auto' : data.get('name_engine'),
      ...(obsMode ? {obs_source: data.get('obs_source')} : {device_id: data.get('device_id'), use_obs_clock: useClock}),
      ...(useClock ? {obs_url: data.get('obs_url'), ...(data.get('obs_password') ? {obs_password: data.get('obs_password')} : {})} : {})});
    form.elements.obs_password.value = ''; delete form.elements.obs_url.dataset.dirty; await refresh(); message('Capture requested.');
  }).finally(() => renderCameras(state?.cameras));
});
setupTabs();
run($('refresh'), refresh);


// Backups span all modes; nothing is imported until a validated preview is confirmed.
let backupToken = null;
let backupBusy = false;
const backupModeNames = {mogi: 'Mogi Teams', ffa: 'FFA', ww: 'Worldwides'};
function backupStatus(message) { $('backup-status').textContent = message; }
function clearBackupPreview() {
  backupToken = null; $('backup-confirm').disabled = true; $('backup-summary').replaceChildren();
}
async function backupAction(action) {
  if (backupBusy) return;
  backupBusy = true;
  for (const id of ['backup-reload', 'backup-preview', 'backup-export', 'backup-confirm']) $(id).disabled = true;
  try { await action(); } catch (error) { backupStatus(error.message); }
  finally {
    backupBusy = false;
    $('backup-reload').disabled = false; $('backup-preview').disabled = false;
    $('backup-export').disabled = !$('backup-sessions').querySelector('input:checked');
    $('backup-confirm').disabled = !backupToken;
  }
}
$('backup-reload').onclick = () => backupAction(async () => {
  const result = await api('/api/backups/sessions');
  $('backup-sessions').replaceChildren(...result.sessions.map((session) => {
    const label = el('label', undefined, 'checkbox-label');
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
    checkbox.dataset.mode = session.mode; checkbox.value = session.id;
    checkbox.onchange = () => { $('backup-export').disabled = backupBusy || !$('backup-sessions').querySelector('input:checked'); };
    label.append(checkbox, document.createTextNode(`${backupModeNames[session.mode]} · ${session.name} (${session.races} races)`));
    return label;
  }));
  $('backup-select-all').disabled = !result.sessions.length;
  backupStatus('Choose the sessions to include. Stop capture and imports before exporting.');
});
$('backup-select-all').onclick = () => {
  $('backup-sessions').querySelectorAll('input').forEach((input) => { input.checked = true; });
  $('backup-export').disabled = backupBusy;
};
$('backup-export').onclick = () => backupAction(async () => {
  const sessions = [...$('backup-sessions').querySelectorAll('input:checked')].map((input) => ({mode: input.dataset.mode, id: input.value}));
  const response = await fetch('/api/backups/export', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sessions})});
  if (!response.ok) throw new Error((await response.json()).error || 'Backup export failed.');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href = url; link.download = 'mkw-sessions.zip';
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  backupStatus(`Downloaded ${sessions.length} selected sessions. Saved screenshots may contain player names.`);
});
$('backup-file').onchange = () => { clearBackupPreview(); backupStatus('Preview this file before importing.'); };
$('backup-preview').onclick = () => backupAction(async () => {
  clearBackupPreview();
  const file = $('backup-file').files[0];
  if (!file || file.size > 128 * 1024 * 1024) throw new Error('Choose a session ZIP no larger than 128 MiB.');
  backupStatus('Validating archive…');
  const response = await fetch('/api/backups/preview', {method: 'POST', headers: {'Content-Type': 'application/zip'}, body: file});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Invalid backup.');
  // A file changed while the request was in flight must never confirm the older file.
  if ($('backup-file').files[0] !== file) return;
  backupToken = result.token;
  const list = document.createElement('ul');
  result.sessions.forEach((session) => list.append(el('li', `${backupModeNames[session.mode]} · ${session.name} (${session.races} races)`)));
  $('backup-summary').replaceChildren(el('p', `${result.sessions.length} sessions, ${result.evidence_files} evidence images. ${result.policy}`), list);
  backupStatus('Preview ready for 10 minutes. Check the list, then confirm import.');
});
$('backup-confirm').onclick = () => backupAction(async () => {
  if (!backupToken) return;
  const result = await api('/api/backups/import', 'POST', {token: backupToken});
  clearBackupPreview(); await refresh();
  backupStatus(`Imported ${result.imported} sessions as new copies. Load them in Race history in the matching mode.`);
});

function renderExternalVision() {
  const config = state?.external_vision;
  if (!config) return;
  const form = $('external-settings');
  if (!form.dataset.dirty) {
    form.elements.url.value = config.url;
    form.elements.model.value = config.model;
    form.elements.acknowledged.checked = config.acknowledged;
    form.dataset.generation = String(config.generation);
  }
  $('external-key-status').textContent = config.api_key_saved ? 'Saved privately; blank keeps the key for the same destination.' : 'No API key saved';
  $('external-test').disabled = !!form.dataset.dirty || !config.url || !!form.dataset.testing;
}
$('external-settings').addEventListener('input', (event) => {
  const form = event.currentTarget;
  form.dataset.dirty = 'true';
  externalDraftRevision++; externalTestRevision++;
  $('external-status').textContent = 'Unsaved changes. Save and test this configuration; earlier test results will be ignored.';
  if (['url', 'model'].includes(event.target.name)) {
    form.elements.acknowledged.checked = false;
    form.elements.api_key.value = '';
  }
  renderExternalVision();
});
$('external-settings').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const draft = externalDraftRevision;
  const previousGeneration = state?.external_vision?.generation;
  const action = ++externalActionRevision;
  externalStateRevision++; externalTestRevision++;
  run(form.querySelector('button'), async () => {
    const body = {url: form.elements.url.value, model: form.elements.model.value,
      acknowledged: form.elements.acknowledged.checked, generation: form.dataset.generation};
    if (form.elements.api_key.value) body.api_key = form.elements.api_key.value;
    let config;
    try { config = await api('/api/external-vision', 'PATCH', body); }
    catch (error) { if (action === externalActionRevision) throw error; else return; }
    if (action !== externalActionRevision) return;
    if (![previousGeneration, config.generation].includes(state?.external_vision?.generation)) {
      $('external-status').textContent = 'Configuration changed elsewhere; reload before saving or testing.';
      return;
    }
    externalStateRevision++;
    state.external_vision = config;
    form.dataset.generation = config.generation;
    if (draft === externalDraftRevision) {
      form.elements.api_key.value = ''; delete form.dataset.dirty;
      $('external-status').textContent = 'Configuration saved. Test sends only a synthetic image.';
    } else {
      $('external-status').textContent = 'Previous values saved; your newer unsaved changes are preserved.';
    }
    renderExternalVision();
  });
});
$('external-forget').addEventListener('click', () => {
  const form = $('external-settings');
  const previousGeneration = state?.external_vision?.generation;
  const action = ++externalActionRevision;
  externalStateRevision++; externalTestRevision++;
  // Clear secrets immediately, without discarding a URL/model draft or later typing.
  form.elements.api_key.value = ''; form.elements.acknowledged.checked = false;
  const draft = ++externalDraftRevision;
  run($('external-forget'), async () => {
    let config;
    try { config = await api('/api/external-vision/key', 'DELETE', {}); }
    catch (error) { if (action === externalActionRevision) throw error; else return; }
    if (action !== externalActionRevision) return;
    if (![previousGeneration, config.generation].includes(state?.external_vision?.generation)) {
      $('external-status').textContent = 'Configuration changed elsewhere; reload before saving or testing.';
      return;
    }
    externalStateRevision++;
    state.external_vision = config; form.dataset.generation = config.generation;
    renderExternalVision();
    if (draft === externalDraftRevision) $('external-status').textContent = 'API key forgotten and consent revoked. Existing requests interrupted.';
  });
});
$('external-test').addEventListener('click', () => run($('external-test'), async () => {
  const form = $('external-settings'); form.dataset.testing = 'true';
  const testId = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
  form.dataset.testId = testId;
  const revision = ++externalTestRevision;
  const draft = externalDraftRevision, action = externalActionRevision;
  const generation = form.dataset.generation;
  const current = () => revision === externalTestRevision && draft === externalDraftRevision &&
    action === externalActionRevision && generation === state?.external_vision?.generation;
  $('external-test-cancel').disabled = false;
  $('external-status').textContent = 'Testing one synthetic name image…';
  try {
    const result = await api('/api/external-vision/test', 'POST', {test_id: testId, generation});
    if (current()) $('external-status').textContent = result.message;
  } catch (error) {
    if (current()) $('external-status').textContent = error.message;
  } finally {
    if (form.dataset.testId === testId) {
      delete form.dataset.testing; $('external-test-cancel').disabled = true;
      if (revision === externalTestRevision && generation !== state?.external_vision?.generation)
        $('external-status').textContent = 'Configuration changed; the earlier test result was discarded.';
    }
  }
}).finally(renderExternalVision));
$('external-test-cancel').addEventListener('click', () => {
  const form = $('external-settings');
  const testId = form.dataset.testId;
  const revision = ++externalTestRevision;
  run($('external-test-cancel'), async () => {
    await api('/api/external-vision/test/cancel', 'POST', {test_id: testId});
    if (revision === externalTestRevision && form.dataset.testId === testId)
      $('external-status').textContent = 'Test cancelled. Remote computation may continue.';
  }).finally(() => { $('external-test-cancel').disabled = !form.dataset.testing; });
});

// Batch drafts are local to this panel, never rebuilt by state polling.
let courseImageDrafts = [];
let courseImageGeneration = 0;
let courseImageBusy = false;
let courseImageListRevision = 0;
let savedCourseImages = [];
function setCourseImageBusy(busy) {
  courseImageBusy = busy;
  ['course-image-import', 'course-image-clear', 'course-image-reload', 'course-image-files'].forEach(id => { $(id).disabled = busy; });
  document.querySelectorAll('#course-image-drafts input, #course-image-library button').forEach(control => { control.disabled = busy; });
}
function clearCourseImageDrafts() {
  ++courseImageGeneration;
  courseImageDrafts.forEach(draft => { if (draft.preview) URL.revokeObjectURL(draft.preview); });
  courseImageDrafts = []; $('course-image-files').value = ''; $('course-image-drafts').replaceChildren();
}
function renderCourseImageDrafts() {
  const [node, body] = table(['Picture', 'Course association', 'Import status']);
  courseImageDrafts.forEach((draft, index) => {
    const row = el('tr'); const picture = el('div', undefined, 'course-title');
    if (draft.preview) { const image = el('img'); image.src = draft.preview; image.alt = ''; image.className = 'course-icon'; picture.append(image); }
    picture.append(el('span', draft.file.name)); cell(row, picture);
    const input = el('input'); input.value = draft.course; input.setAttribute('list', 'course-image-courses'); input.maxLength = 160;
    input.setAttribute('aria-label', `Course for ${draft.file.name}`); input.disabled = courseImageBusy;
    input.addEventListener('input', () => { draft.course = input.value; draft.edited = true; draft.result = null; if (!draft.error) draft.status.textContent = input.value.trim() ? 'Assigned; ready to import' : 'Pending: choose a course'; });
    cell(row, input); draft.status = cell(row, draft.error || draft.result || (draft.course ? 'Assigned; ready to import' : 'Pending: choose a course'));
    row.dataset.courseImageDraft = index; body.append(row);
  });
  $('course-image-drafts').replaceChildren(...(courseImageDrafts.length ? [node] : []));
}
async function loadCourseImages() {
  const revision = ++courseImageListRevision;
  const result = await api('/api/course-images');
  if (revision !== courseImageListRevision) return;
  savedCourseImages = result.images || [];
  $('course-image-courses').replaceChildren(...(result.courses || []).map(course => new Option(course, course)));
  $('course-image-library').replaceChildren(...savedCourseImages.map(image => {
    const item = el('div', undefined, 'course-image-item'); item.append(courseTitle(image));
    item.append(button(`Delete picture for ${image.course}`, async () => {
      if (courseImageBusy) return;
      if (!window.confirm(`Delete the local picture for “${image.course}”?`)) return;
      setCourseImageBusy(true);
      try {
        await api(`/api/course-images/${encodeURIComponent(image.id)}`, 'DELETE', {});
        await loadCourseImages(); await refresh();
        $('course-image-status').textContent = `Deleted picture for ${image.course}.`;
      } finally { setCourseImageBusy(false); }
    }));
    item.querySelector('button').disabled = courseImageBusy;
    return item;
  }));
  if (!savedCourseImages.length) $('course-image-library').append(el('p', 'No saved course pictures.', 'muted'));
}
$('course-images-details').addEventListener('toggle', () => {
  if ($('course-images-details').open) loadCourseImages().catch(error => { $('course-image-status').textContent = error.message; });
});
$('course-image-reload').addEventListener('click', () => run($('course-image-reload'), loadCourseImages));
$('course-image-clear').addEventListener('click', () => { if (!courseImageBusy) { clearCourseImageDrafts(); $('course-image-status').textContent = 'Batch cleared.'; } });
$('course-image-files').addEventListener('change', async () => {
  if (courseImageBusy) return;
  const files = [...$('course-image-files').files]; clearCourseImageDrafts();
  const generation = courseImageGeneration;
  if (files.length > 256) { $('course-image-status').textContent = 'Choose at most 256 files per batch.'; return; }
  courseImageDrafts = files.map(file => {
    const error = file.size > 4 * 1024 * 1024 ? 'Too large: limit is 4 MiB.' : !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) ? 'Choose PNG, JPEG or WebP.' : '';
    return {file, course: '', error, preview: error ? null : URL.createObjectURL(file)};
  });
  renderCourseImageDrafts(); $('course-image-status').textContent = 'Matching filenames… Review course associations before importing.';
  try {
    const result = await api('/api/course-images/match', 'POST', {filenames: files.map(file => file.name)});
    if (generation !== courseImageGeneration) return;
    courseImageDrafts.forEach((draft, index) => {
      // A manual assignment made while matching is in flight wins.
      if (!draft.edited) draft.course = result.matches?.[index]?.course || '';
    });
    renderCourseImageDrafts(); $('course-image-status').textContent = 'Review associations. Unmatched pictures remain pending until you choose a course.';
  } catch (error) { if (generation === courseImageGeneration) $('course-image-status').textContent = `Filename matching failed: ${error.message} Assign courses manually.`; }
});
$('course-image-import').addEventListener('click', async () => {
  if (courseImageBusy) return;
  setCourseImageBusy(true);
  const generation = ++courseImageGeneration; // Discard any older filename-match response.
  renderCourseImageDrafts();
  let imported = 0, skipped = 0, failed = 0, pending = 0;
  try {
    await loadCourseImages();
    for (const draft of courseImageDrafts) {
      if (generation !== courseImageGeneration) break;
      const course = draft.course.trim();
      if (draft.error) { failed++; continue; }
      if (draft.result === 'Imported') { skipped++; continue; }
      if (!course) { pending++; draft.status.textContent = 'Pending: choose a course'; continue; }
      draft.status.textContent = 'Checking course…';
      try {
        // Resolve on the server: aliases and same-batch uploads share its identity rules.
        const resolved = await api('/api/course-images/resolve', 'POST', {course});
        if (generation !== courseImageGeneration) break;
        if (resolved.exists && !window.confirm(`Replace the existing picture for “${resolved.course}” with “${draft.file.name}”? Cancel skips this file.`)) {
          skipped++; draft.result = 'Skipped: existing picture kept'; draft.status.textContent = draft.result; continue;
        }
        draft.status.textContent = 'Importing…';
        const image = await new Promise((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = () => reject(new Error('Could not read picture.')); reader.readAsDataURL(draft.file);
        });
        if (generation !== courseImageGeneration) break;
        const saved = await api('/api/course-images', 'POST', {course: resolved.course, image_base64: image, overwrite: resolved.exists});
        if (generation !== courseImageGeneration) break;
        imported++; draft.result = 'Imported'; draft.status.textContent = draft.result;
        savedCourseImages = [...savedCourseImages.filter(item => item.id !== saved.id), saved];
      } catch (error) { failed++; draft.result = `Failed: ${error.message}`; draft.status.textContent = draft.result; }
    }
    $('course-image-status').textContent = `${imported} imported · ${skipped} skipped · ${failed} failed · ${pending} pending`;
    await loadCourseImages(); await refresh();
  } catch (error) { $('course-image-status').textContent = `Import stopped: ${error.message}`; }
  finally {
    setCourseImageBusy(false);
    renderCourseImageDrafts();
  }
});
window.addEventListener('pagehide', () => courseImageDrafts.forEach(draft => { if (draft.preview) URL.revokeObjectURL(draft.preview); }));
