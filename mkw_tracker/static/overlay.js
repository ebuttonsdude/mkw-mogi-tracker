'use strict';

(() => {
  const overlay = document.querySelector('#overlay');
  const header = document.querySelector('#overlay-header');
  const viewport = document.querySelector('#standings-viewport');
  const standings = document.querySelector('#standings');
  const status = document.querySelector('#connection-status');
  const profile = /^\/overlay\/([a-z0-9-]+)$/.exec(location.pathname)?.[1];
  const endpoint = profile ? `/api/overlay/${profile}` : '/api/overlay';
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let signature = '';
  let settings = {};
  let direction = 1;
  let lastFrame = 0;
  let pauseUntil = 0;
  let position = 0;
  let visible = true;
  let initialized = false;
  let maskAnimation = null;
  let maskProgress = 1;
  const svgNS = 'http://www.w3.org/2000/svg';
  const maskSvg = document.createElementNS(svgNS, 'svg');
  maskSvg.setAttribute('width', '0');
  maskSvg.setAttribute('height', '0');
  maskSvg.setAttribute('aria-hidden', 'true');
  maskSvg.style.position = 'absolute';
  const clip = document.createElementNS(svgNS, 'clipPath');
  clip.id = 'overlay-reveal';
  clip.setAttribute('clipPathUnits', 'objectBoundingBox');
  const tiles = Array.from({length: 96}, (_, index) => {
    const rect = document.createElementNS(svgNS, 'rect');
    clip.append(rect);
    return rect;
  });
  maskSvg.append(clip);
  document.body.append(maskSvg);

  function finishMask() {
    maskAnimation = null;
    maskProgress = visible ? 1 : 0;
    overlay.style.removeProperty('clip-path');
  }

  function drawMask(now) {
    if (!maskAnimation) return;
    const elapsed = Math.min(1, Math.max(0, (now - maskAnimation.start) / maskAnimation.duration));
    maskProgress = maskAnimation.from + (maskAnimation.to - maskAnimation.from) * elapsed;
    tiles.forEach((rect, index) => {
      const column = index % 12;
      const row = Math.floor(index / 12);
      // A fixed permutation avoids random flicker between polling updates.
      const order = maskAnimation.style === 'dissolve' ? ((index * 37) % 96) / 96
        : ((column + row) / 18 * .65 + ((column + row) % 2) * .2);
      const scale = Math.min(1, Math.max(0, (maskProgress - order * .7) / .3));
      rect.setAttribute('x', (column + (1 - scale) / 2) / 12);
      rect.setAttribute('y', (row + (1 - scale) / 2) / 8);
      rect.setAttribute('width', scale / 12);
      rect.setAttribute('height', scale / 8);
    });
    if (elapsed === 1) finishMask();
  }

  function applyVisibility(data) {
    const previous = visible;
    visible = data.visible !== false;
    const style = settings.transition_style || 'slide';
    const duration = settings.animate === false || reducedMotion.matches ? 0 : (settings.slide_duration ?? 400);
    if (overlay.dataset.transition !== style || !duration) finishMask();
    overlay.dataset.transition = style;
    overlay.style.setProperty('--slide-duration', `${duration}ms`);
    if (initialized && previous !== visible && duration && ['checkered', 'dissolve'].includes(style)) {
      maskAnimation = {from: maskAnimation ? maskProgress : (previous ? 1 : 0),
        to: visible ? 1 : 0, start: performance.now(), duration, style};
      overlay.style.clipPath = 'url(#overlay-reveal)';
      drawMask(maskAnimation.start);
    } else if (!maskAnimation) {
      maskProgress = visible ? 1 : 0;
    }
    overlay.classList.toggle('slide-right', settings.slide_direction === 'right');
    overlay.classList.toggle('slide-hidden', !visible);
    overlay.setAttribute('aria-hidden', String(!visible));
    if (!initialized) {
      // Resolve the first response before enabling transitions so refreshing a
      // hidden browser source cannot flash the scoreboard or animate it away.
      overlay.classList.add('ready');
      void overlay.offsetWidth;
      overlay.classList.add('slide-ready');
      initialized = true;
    }
  }

  function element(tag, className, value) {
    const node = document.createElement(tag);
    node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }

  function card(row, isTeam, index, panelChannels, opacity, ffa) {
    const node = element('section', 'standing-card');
    if (settings.alternating_backgrounds !== false) node.classList.add(index % 2 ? 'row-darker' : 'row-brighter');
    const teamId = isTeam ? row.id : row.team_id;
    const highlighted = ffa ? settings.highlight_player_id != null && String(row.id) === String(settings.highlight_player_id) : settings.highlight_team_id != null && String(teamId) === String(settings.highlight_team_id);
    node.classList.toggle('highlighted-team', highlighted);
    if (row.color && CSS.supports('color', row.color)) node.style.setProperty('--team-color', row.color);
    const rank = element('div', 'standing-rank', row.rank);
    if ((isTeam || ffa) && Number.isInteger(row.rank_change) && row.rank_change !== 0) {
      const up = row.rank_change > 0;
      const places = Math.abs(row.rank_change);
      const movement = element('span', `rank-movement ${up ? 'rank-up' : 'rank-down'}`, `${up ? '▲' : '▼'}${places}`);
      movement.setAttribute('role', 'img');
      movement.setAttribute('aria-label', `${up ? 'Up' : 'Down'} ${places} ${places === 1 ? 'place' : 'places'} since the last race`);
      rank.append(movement);
    }
    node.append(rank);
    const name = element('div', isTeam ? 'standing-name' : 'standing-name player-name', row.name);
    if (!isTeam && !ffa && row.team_name) name.append(element('div', 'standing-subtitle', row.team_name));
    if (highlighted) {
      const star = element('span', 'highlight-badge', '★');
      star.setAttribute('role', 'img');
      star.setAttribute('aria-label', ffa ? 'Highlighted player' : 'Highlighted team');
      name.append(star);
      const color = /^#[0-9a-f]{6}$/i.test(row.color || '') ? row.color : '#ffffff';
      const tint = [1, 3, 5].map(start => parseInt(color.slice(start, start + 2), 16));
      const channels = panelChannels.map((channel, index) => Math.round(channel * .7 + tint[index] * .3));
      node.style.setProperty('--card-highlight', `rgba(${channels.join(', ')}, ${opacity})`);
    }
    node.append(name);
    const score = element('div', 'standing-score', row.points ?? 0);
    if (settings.show_gaps && row.gap_above !== undefined && row.gap_above !== null) {
      const gap = value => value === 0 ? '0' : `-${value}`;
      const label = element('span', 'standing-gap', `${gap(row.gap_above)} / ${gap(row.gap_leader)}`);
      label.setAttribute('aria-label', `${row.gap_above} points behind the ${isTeam ? 'team' : 'player'} above, ${row.gap_leader} points behind the leader`);
      score.append(label);
    }
    node.append(score);
    if (isTeam && settings.show_players) {
      const members = element('div', 'standing-members');
      for (const player of row.players || []) {
        const member = element('div', 'standing-member');
        member.append(element('span', 'member-rank', player.rank ? `#${player.rank}` : ''),
          element('span', 'member-name', player.name), element('span', 'member-points', player.points ?? 0));
        members.append(member);
      }
      node.append(members);
    }
    return node;
  }

  function loungeCard(player, metrics = {}, appearance = {}) {
    const card = element('section', 'lounge-card');
    for (const [key, choices] of Object.entries({
      background: ['aurora', 'carbon', 'theme', 'transparent', 'custom', 'hud'],
      border: ['rainbow', 'division', 'accent', 'none', 'hud'], shape: ['pill', 'rounded']
    })) card.dataset[key] = choices.includes(appearance[key]) ? appearance[key] : choices[0];
    // Accept only a server-owned local image route, never arbitrary CSS/URLs.
    if (card.dataset.background === 'custom' && typeof appearance.image_url === 'string' &&
        /^\/api\/overlay-profiles\/[a-z0-9-]+\/background\.png\?v=[0-9a-f]{16}$/.test(appearance.image_url)) {
      const image = element('img', 'lounge-background-image');
      image.alt = ''; image.setAttribute('aria-hidden', 'true');
      image.addEventListener('load', () => { card.dataset.imageReady = 'true'; });
      image.addEventListener('error', () => { delete card.dataset.imageReady; image.remove(); });
      image.src = appearance.image_url;
      card.append(image);
    }
    const data = player.data;
    const division = data?.division || 'Unknown';
    const divisionColors = {iron: '#bbc2ca', bronze: '#edab77', silver: '#d2dce8',
      gold: '#ffda70', platinum: '#91e9e2', sapphire: '#85b8ff', emerald: '#7ce5ae',
      ruby: '#ff8d9f', diamond: '#b5dfff', master: '#e6a3ff', grandmaster: '#ff9dce'};
    card.style.setProperty('--division-color', divisionColors[division.toLowerCase()] || 'var(--accent)');
    if (metrics.username !== false) card.append(element('h2', 'lounge-name', data?.name || player.username));
    const rating = element('div', 'lounge-rating');
    const value = n => Number.isFinite(n) ? String(Math.round(n * 100) / 100) : 'Unknown';
    const percent = n => Number.isFinite(n) ? `${Math.round(n * 1000) / 10}%` : 'Unknown';
    if (data && metrics.mmr !== false) {
      const mmr = element('strong', 'lounge-mmr', value(data.mmr));
      mmr.setAttribute('aria-label', `MMR ${value(data.mmr)}`);
      rating.append(mmr);
    }
    if (data && metrics.division !== false) rating.append(element('span', 'lounge-division', division));
    if (rating.childElementCount) card.append(rating);
    const statusLabel = player.status === 'stale' ? '↻ Stale' :
      player.status === 'loading' ? '◌ Loading' : player.status === 'unavailable' ? '◇ Unavailable' : '';
    if (statusLabel) card.append(element('p', 'lounge-status', statusLabel));
    if (!data) return card;
    const scope = `Season ${data.season} · ${data.game === 'mkworld' ? 'Combined room sizes' : `${data.room_size} players`}`;
    card.title = scope;
    function group(label, title) {
      return {label, title};
    }
    function metric(target, key, label, val, symbol, description, delta) {
      if (metrics[key] === false) return;
      const tile = element('div', 'lounge-metric');
      tile.dataset.metric = key;
      tile.title = `${target.title} · ${description}`;
      tile.setAttribute('aria-label', `${target.title} · ${description}: ${val}`);
      const icon = element('span', 'lounge-symbol', symbol);
      icon.setAttribute('aria-hidden', 'true');
      if (delta !== undefined && Number.isFinite(delta)) {
        tile.classList.add(delta > 0 ? 'lounge-positive' : delta < 0 ? 'lounge-negative' : 'lounge-neutral');
        icon.textContent = delta > 0 ? '▲' : delta < 0 ? '▼' : '−';
      }
      const caption = element('span', 'lounge-metric-caption');
      caption.append(element('span', 'lounge-metric-scope', target.label), element('span', 'lounge-metric-label', label));
      if (symbol) tile.append(icon);
      tile.append(caption, element('strong', 'lounge-metric-value', val));
      card.append(tile);
      return tile;
    }
    const signed = n => Number.isFinite(n) ? `${n > 0 ? '+' : ''}${value(n)}` : 'Unknown';
    const season = group(data.game === 'mkworld' ? 'SZN·ALL' : 'SZN', scope);
    metric(season, 'leaderboard_rank', 'Rank', Number.isFinite(data.leaderboard_rank) ? `#${value(data.leaderboard_rank)}` : 'Unknown', '#', 'Leaderboard position, not division');
    metric(season, 'peak_mmr', 'Peak', value(data.peak_mmr), '◆', 'Season peak MMR');
    metric(season, 'events', 'Events', value(data.events), '⚑', 'Season events played');
    metric(season, 'positive_mmr_rate', 'Win %', percent(data.positive_mmr_rate), '↗', 'Season positive-MMR event percentage, not first-place race wins');
    metric(season, 'average_score', 'Avg', value(data.average_score), '◎', 'Season average event score, not per-race points');
    const wins = data.last_ten_wins;
    const losses = data.last_ten_losses;
    const count = wins + losses;
    const validCount = Number.isInteger(wins) && Number.isInteger(losses) && wins >= 0 && losses >= 0 && count > 0 && count <= 10;
    const recentLabel = validCount ? `Last ${count} events` : 'Last ≤10 events';
    const recent = group((validCount ? `L${count}` : 'L≤10') + (data.game === 'mkworld' ? '·ALL' : ''), recentLabel + (data.game === 'mkworld' ? ' · combined room sizes' : ''));
    metric(recent, 'last_ten_mmr_change', 'MMR', signed(data.last_ten_mmr_change), '↔', 'MMR change over the most recent up to ten events', data.last_ten_mmr_change);
    metric(recent, 'last_ten_record', 'Win %', validCount ? percent(wins / count) : 'Unknown', '↗', 'Positive-MMR share of the most recent up to ten events; zero-MMR events are non-positive');
    metric(recent, 'last_ten_average_score', 'Avg', value(data.last_ten_average_score), '◎', 'Average score over the most recent up to ten events');
    const week = data.weekly;
    const weekly = group('7D', `Derived from table verification times, not race times · ${data.room_size}-player events`);
    metric(weekly, 'weekly_events', 'Events', value(week?.events), '⚑', 'Verified events in the derived trailing seven-day window');
    metric(weekly, 'weekly_positive_mmr_rate', 'Win %', percent(week?.positive_mmr_rate), '↗', 'Positive-MMR event percentage in the derived trailing seven-day window');
    metric(weekly, 'weekly_event_mmr_change', 'MMR', signed(week?.event_mmr_change), '↔', 'Seven-day event MMR change excluding penalties and bonuses', week?.event_mmr_change);
    metric(weekly, 'weekly_average_score', 'Avg', value(week?.average_score), '◎', 'Average event score in the derived trailing seven-day window');
    if (metrics.last_five_form === true) {
      const form = data.last_five_form;
      const valid = Array.isArray(form) && form.length <= 5 && form.every(outcome => outcome === 'W' || outcome === 'L');
      const formScope = group((valid && form.length ? `L${form.length}` : 'L≤5') + (data.game === 'mkworld' ? '·ALL' : ''),
        'Most recent verified events, oldest to newest' + (data.game === 'mkworld' ? ' · combined room sizes' : ''));
      const text = valid ? form.length ? form.join(' ') : 'No events' : 'Unknown';
      const tile = metric(formScope, 'last_five_form', 'Form →', text, '', 'Positive MMR is W; zero or negative MMR is L');
      if (valid && form.length) {
        const marks = tile.querySelector('.lounge-metric-value');
        marks.classList.add('lounge-form');
        marks.replaceChildren(...form.map((outcome, index) => {
          const mark = element('span', `lounge-form-mark ${outcome === 'W' ? 'lounge-form-win' : 'lounge-form-loss'}`, outcome);
          mark.setAttribute('aria-label', `${outcome === 'W' ? 'Win' : 'Loss'}${index === form.length - 1 ? ', newest event' : ''}`);
          return mark;
        }));
      }
    }
    card.classList.toggle('lounge-expanded', Boolean(card.querySelector('.lounge-metric')));
    return card.childElementCount ? card : null;
  }

  function apply(data) {
    TrackerFonts.apply(overlay, data.font);
    settings = data.settings || {};
    applyVisibility(data);
    const nextSignature = JSON.stringify({...data, visible: undefined,
      settings: {...settings, slide_direction: undefined, slide_duration: undefined,
        transition_style: undefined, auto_visibility: undefined}});
    if (nextSignature === signature) return;
    signature = nextSignature;
    const tokens = data.theme?.tokens || {};
    for (const key of ['text', 'muted', 'accent']) {
      if (tokens[key] && CSS.supports('color', tokens[key])) overlay.style.setProperty(`--${key}`, tokens[key]);
    }
    // Set background alpha without dimming scores or relying on newer CEF CSS.
    const panel = /^#[0-9a-f]{6}$/i.test(tokens.panel || '') ? tokens.panel : '#221820';
    const channels = [1, 3, 5].map(start => parseInt(panel.slice(start, start + 2), 16));
    const opacity = Math.min(1, Math.max(0, Number(settings.opacity ?? .85)));
    overlay.style.setProperty('--lounge-image-opacity', String(opacity));
    overlay.style.setProperty('--lounge-hud-alpha', String(opacity * .8));
    overlay.style.setProperty('--lounge-hud-gloss', String(opacity * .16));
    overlay.style.setProperty('--lounge-glow-alpha', String(opacity * .32));
    overlay.style.setProperty('--lounge-line-alpha', String(opacity * .09));
    overlay.style.setProperty('--card-background', `rgba(${channels.join(', ')}, ${opacity})`);
    for (const [key, delta] of [['brighter', 12], ['darker', -8]]) {
      const adjusted = channels.map(value => Math.max(0, Math.min(255, value + delta)));
      overlay.style.setProperty(`--card-${key}`, `rgba(${adjusted.join(', ')}, ${opacity})`);
    }
    overlay.style.width = `${settings.width || 560}px`;
    overlay.style.height = `${settings.height || 600}px`;
    // Treat the selected font size as an upper bound so rank, score and both
    // gaps stay inside narrow OBS sources, including 240px at the 48px setting.
    overlay.style.fontSize = `${Math.min(settings.font_size || 22, (settings.width || 560) / 11)}px`;
    overlay.classList.toggle('animate', Boolean(settings.animate) && !reducedMotion.matches);
    const content = data.content || {};
    const worldwide = data.tracker_mode === 'ww';
    const showCharacter = content.header !== false && (worldwide ? settings.ww_show_character !== false : settings.show_header);
    header.hidden = content.header === false || (!settings.show_header && !showCharacter);
    header.classList.toggle('character-only', !settings.show_header && showCharacter);
    header.replaceChildren();
    if (content.header !== false && (settings.show_header || showCharacter)) {
      const room = data.room || {};
      const detail = element('div', 'overlay-room');
      if (data.tracker_mode === 'ww') {
        detail.append(element('span', '', 'Worldwides'), element('span', '', 'Observed VR'));
      } else {
        detail.append(element('span', '', `${room.scored_races ?? 0} ${room.scored_races === 1 ? 'race' : 'races'}`),
          element('span', '', `${room.points ?? 0} / ${room.maximum_points ?? 984} pts`));
      }
      const title = element('div', 'overlay-heading');
      const iconUrl = data.theme?.icon_url;
      if (showCharacter && typeof iconUrl === 'string' && /^\/api\/themes\/[a-zA-Z0-9-]+\/icon\.png(?:\?v=[a-zA-Z0-9]+)?$/.test(iconUrl)) {
        const portrait = element('img', 'overlay-character');
        portrait.alt = `${data.theme.icon_name || data.theme.name} icon`;
        portrait.onerror = () => { portrait.hidden = true; };
        portrait.src = iconUrl;
        title.append(portrait);
      }
      if (settings.show_header) {
        title.append(element('h1', 'overlay-title', settings.title || data.session?.name || 'MKW-Mogi-Tracker'));
      }
      header.append(title);
      if (settings.show_header) header.append(detail);
    }
    const ffa = data.tracker_mode === 'ffa';
    const isTeam = !ffa && settings.layout !== 'players';
    const rows = isTeam ? data.teams : data.players;
    const fragment = document.createDocumentFragment();
    if (data.tracker_mode === 'ww') {
      const ww = data.ww || {};
      const playerRow = element('div', 'ww-overlay-player-row');
      playerRow.append(element('div', 'ww-overlay-player', ww.player_name || 'Local player'));
      if (typeof ww.current_track === 'string' && ww.current_track.trim()) {
        playerRow.append(element('div', 'ww-overlay-track', `Current track: ${ww.current_track}`));
      }
      if (content.ww_player !== false) fragment.append(playerRow);
      for (const [toggle, key, label] of [
        ['ww_show_current', 'current_vr', 'Current VR'],
        ['ww_show_peak', 'peak_vr', 'Peak VR'],
        ['ww_show_lowest', 'lowest_vr', 'Lowest VR'],
        ['ww_show_last_10', 'last_10_change', `Last ${ww.last_10_race_count || 10} ${ww.last_10_race_count === 1 ? 'race' : 'races'} VR change`]
      ]) {
        if (content.ww_stats === false || settings[toggle] === false) continue;
        const metric = element('section', `ww-overlay-metric ww-${key}`);
        const value = Number.isFinite(ww[key]) ? (key === 'last_10_change' && ww[key] > 0 ? `+${ww[key]}` : ww[key]) : 'Unknown';
        metric.append(element('span', 'ww-overlay-label', label),
          element('strong', 'ww-overlay-value', value));
        fragment.append(metric);
      }
      if (content.ww_graph !== false && settings.ww_show_graph !== false) {
        const graph = element('section', 'ww-overlay-graph');
        graph.append(element('h2', 'ww-overlay-label', 'VR by race'), WWGraph.create(ww.graph || []));
        fragment.append(graph);
      }
    } else if (content.standings !== false) {
      (rows || []).forEach((row, index) => fragment.append(card(row, isTeam, index, channels, opacity, ffa)));
      if (!rows?.length) fragment.append(element('div', 'overlay-empty', isTeam ? 'Waiting for teams' : 'Waiting for players'));
    }
    if (!worldwide && content.lounge !== false && data.lounge?.enabled) {
      for (const player of data.lounge.players || []) {
        const lounge = loungeCard(player, data.lounge.metrics, data.lounge.appearance);
        if (lounge) fragment.append(lounge);
      }
    }
    const previousScroll = viewport.scrollTop;
    standings.replaceChildren(fragment);
    viewport.scrollTop = previousScroll;
    position = viewport.scrollTop;
    if (!settings.scroll || reducedMotion.matches) {
      viewport.scrollTop = 0;
      position = 0;
    }
    pauseUntil = performance.now() + 1600;
  }

  async function poll() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(endpoint, {cache: 'no-store', signal: controller.signal});
      if (!response.ok) throw new Error('Standings unavailable');
      apply(await response.json());
      status.hidden = true;
      status.textContent = '';
    } catch {
      status.textContent = signature ? 'Reconnecting · showing last update' : 'Connecting to standings…';
      status.hidden = false;
    } finally {
      clearTimeout(timeout);
      setTimeout(poll, 1500);
    }
  }

  function animate(now) {
    drawMask(now);
    const elapsed = lastFrame ? Math.min((now - lastFrame) / 1000, .1) : 0;
    lastFrame = now;
    const maximum = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    if (visible && settings.scroll && !reducedMotion.matches && maximum > 1 && now >= pauseUntil) {
      const speed = Math.max(1, Number(settings.scroll_speed || 30));
      position = Math.max(0, Math.min(maximum, position + elapsed * speed * direction));
      viewport.scrollTop = position;
      if (position >= maximum || position <= 0) {
        direction *= -1;
        pauseUntil = now + 1800;
      }
    }
    requestAnimationFrame(animate);
  }
  reducedMotion.addEventListener('change', () => {
    applyVisibility({visible});
    overlay.classList.toggle('animate', Boolean(settings.animate) && !reducedMotion.matches);
    if (reducedMotion.matches) { viewport.scrollTop = 0; position = 0; }
  });
  poll();
  requestAnimationFrame(animate);
})();
