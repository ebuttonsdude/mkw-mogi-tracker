'use strict';

// The dashboard and OBS use the same observed-rating graph. Unknown ratings
// break the line; no interpolation creates a missing result or starting VR.
window.WWGraph = {
  create(observations = []) {
    const container = document.createElement('div');
    container.className = 'ww-rating-graph';
    const known = observations.filter(point => Number.isFinite(point.vr));
    if (!known.length) {
      container.textContent = 'Waiting for a known VR reading';
      return container;
    }
    const svgNode = (tag, attributes = {}, text) => {
      const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
      Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const svg = svgNode('svg', {viewBox: '0 0 500 190', role: 'img', 'aria-label': 'Observed VR by race'});
    const low = Math.min(...known.map(point => point.vr));
    const high = Math.max(...known.map(point => point.vr));
    svg.append(svgNode('title', {}, `Observed VR ranges from ${low} to ${high}. Unknown readings break the line.`));
    const y = value => high === low ? 82 : 18 + 124 * (high - value) / (high - low);
    [high, ...(high === low ? [] : [low])].forEach(value => {
      svg.append(svgNode('line', {x1: 58, x2: 480, y1: y(value), y2: y(value), stroke: 'currentColor', opacity: '.2'}),
        svgNode('text', {x: 50, y: y(value) + 4, 'text-anchor': 'end', fill: 'currentColor', 'font-size': 12}, value));
    });
    let path = '';
    let connected = false;
    observations.forEach((point, index) => {
      if (!Number.isFinite(point.vr)) { connected = false; return; }
      if (point.gap_before) connected = false;
      const x = observations.length === 1 ? 269 : 64 + 410 * index / (observations.length - 1);
      path += `${connected ? ' L' : ' M'}${x},${y(point.vr)}`;
      connected = true;
      const dot = svgNode('circle', {cx: x, cy: y(point.vr), r: 3.5, fill: 'var(--accent, var(--teal))'});
      dot.append(svgNode('title', {}, `${point.course || (point.race_id ? 'Race' : 'Starting VR')}: ${point.vr} VR`));
      svg.append(dot);
    });
    svg.append(svgNode('path', {d: path.trim(), fill: 'none', stroke: 'var(--accent, var(--teal))', 'stroke-width': 2.5}),
      svgNode('text', {x: 64, y: 174, fill: 'currentColor', 'font-size': 12}, observations[0]?.race_id ? 'First race' : 'Start'),
      svgNode('text', {x: 474, y: 174, 'text-anchor': 'end', fill: 'currentColor', 'font-size': 12}, 'Latest'));
    container.append(svg);
    return container;
  }
};
