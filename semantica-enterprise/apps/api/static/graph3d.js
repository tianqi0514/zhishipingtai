(function () {
  'use strict';

  const TYPE_COLORS = {
    '组织': '#50d2b6', '人物': '#7fb4ff', '产品': '#ffbd66', '时间': '#d49bff',
    '地点': '#71df8b', '事件': '#ff7d8f', '技术': '#55c5e8', '其他': '#92a7bc'
  };
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  function colorForType(type) {
    if (TYPE_COLORS[type]) return TYPE_COLORS[type];
    const palette = ['#50d2b6', '#7fb4ff', '#ffbd66', '#d49bff', '#71df8b', '#ff7d8f', '#55c5e8'];
    let hash = 0;
    for (const char of String(type || '其他')) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return palette[Math.abs(hash) % palette.length];
  }

  function seedFor(value) {
    let hash = 2166136261;
    for (const char of String(value)) {
      hash ^= char.charCodeAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0) / 4294967295;
  }

  function pointLineDistance(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    if (!dx && !dy) return Math.hypot(px - x1, py - y1);
    const t = clamp(((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy), 0, 1);
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
  }

  class KnowledgeGraph3D {
    constructor(canvas, data, options = {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.options = options;
      this.nodes = (data.nodes || []).map((item, index) => {
        const angle = index * 2.399963229728653;
        const radius = 115 + 150 * seedFor(item.id);
        const z = (seedFor(`${item.id}:z`) - .5) * 300;
        return {
          ...item, color: colorForType(item.type), degree: 0,
          x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, z,
          vx: 0, vy: 0, vz: 0
        };
      });
      this.nodeMap = new Map(this.nodes.map(node => [node.id, node]));
      this.edges = (data.edges || []).filter(edge => this.nodeMap.has(edge.source) && this.nodeMap.has(edge.target));
      for (const edge of this.edges) {
        this.nodeMap.get(edge.source).degree += 1;
        this.nodeMap.get(edge.target).degree += 1;
      }
      this.yaw = -.38;
      this.pitch = .2;
      this.zoom = .82;
      this.autoRotate = true;
      this.selected = null;
      this.hovered = null;
      this.projected = new Map();
      this.layoutIteration = 0;
      this.dragging = false;
      this.dragMoved = false;
      this.lastPointer = null;
      this.destroyed = false;
      this._bindEvents();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(canvas.parentElement || canvas);
      this.resize();
      this.frame = requestAnimationFrame(time => this._loop(time));
    }

    _bindEvents() {
      this.onPointerDown = event => {
        this.dragging = true; this.dragMoved = false;
        this.lastPointer = {x: event.clientX, y: event.clientY};
        this.canvas.setPointerCapture?.(event.pointerId);
      };
      this.onPointerMove = event => {
        if (this.dragging && this.lastPointer) {
          const dx = event.clientX - this.lastPointer.x, dy = event.clientY - this.lastPointer.y;
          if (Math.abs(dx) + Math.abs(dy) > 2) this.dragMoved = true;
          this.yaw += dx * .007; this.pitch = clamp(this.pitch + dy * .006, -1.35, 1.35);
          this.lastPointer = {x: event.clientX, y: event.clientY};
          this.canvas.style.cursor = 'grabbing';
        } else {
          const hit = this._hit(event);
          this.hovered = hit;
          this.canvas.style.cursor = hit ? 'pointer' : 'grab';
        }
      };
      this.onPointerUp = event => {
        if (!this.dragMoved) {
          const hit = this._hit(event);
          this.select(hit);
        }
        this.dragging = false; this.lastPointer = null; this.canvas.style.cursor = 'grab';
      };
      this.onPointerLeave = () => { this.dragging = false; this.lastPointer = null; this.hovered = null; };
      this.onWheel = event => {
        event.preventDefault();
        this.zoom = clamp(this.zoom * Math.exp(-event.deltaY * .001), .45, 3.6);
      };
      this.onKeyDown = event => {
        if (event.key === 'ArrowLeft') this.yaw -= .1;
        else if (event.key === 'ArrowRight') this.yaw += .1;
        else if (event.key === 'ArrowUp') this.pitch = clamp(this.pitch - .1, -1.35, 1.35);
        else if (event.key === 'ArrowDown') this.pitch = clamp(this.pitch + .1, -1.35, 1.35);
        else if (event.key === '+' || event.key === '=') this.zoomBy(1.16);
        else if (event.key === '-') this.zoomBy(.86);
        else if (event.key === 'Escape') this.select(null);
        else return;
        event.preventDefault();
      };
      this.canvas.addEventListener('pointerdown', this.onPointerDown);
      this.canvas.addEventListener('pointermove', this.onPointerMove);
      this.canvas.addEventListener('pointerup', this.onPointerUp);
      this.canvas.addEventListener('pointerleave', this.onPointerLeave);
      this.canvas.addEventListener('wheel', this.onWheel, {passive: false});
      this.canvas.addEventListener('keydown', this.onKeyDown);
    }

    resize() {
      const rect = this.canvas.getBoundingClientRect();
      this.width = Math.max(320, Math.floor(rect.width));
      this.height = Math.max(420, Math.floor(rect.height));
      const ratio = clamp(window.devicePixelRatio || 1, 1, 2);
      this.canvas.width = Math.floor(this.width * ratio);
      this.canvas.height = Math.floor(this.height * ratio);
      this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    _stepLayout() {
      if (this.layoutIteration >= 260 || this.dragging) return;
      const nodes = this.nodes;
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) {
          const a = nodes[i], b = nodes[j];
          let dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
          const distance2 = Math.max(180, dx * dx + dy * dy + dz * dz);
          const distance = Math.sqrt(distance2), force = 9200 / distance2;
          dx /= distance; dy /= distance; dz /= distance;
          a.vx += dx * force; a.vy += dy * force; a.vz += dz * force;
          b.vx -= dx * force; b.vy -= dy * force; b.vz -= dz * force;
        }
      }
      for (const edge of this.edges) {
        const a = this.nodeMap.get(edge.source), b = this.nodeMap.get(edge.target);
        let dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
        const distance = Math.max(1, Math.hypot(dx, dy, dz));
        const force = (distance - 125) * .0017;
        dx /= distance; dy /= distance; dz /= distance;
        a.vx += dx * force; a.vy += dy * force; a.vz += dz * force;
        b.vx -= dx * force; b.vy -= dy * force; b.vz -= dz * force;
      }
      for (const node of nodes) {
        node.vx -= node.x * .0007; node.vy -= node.y * .0007; node.vz -= node.z * .0007;
        node.vx *= .88; node.vy *= .88; node.vz *= .88;
        node.x += node.vx; node.y += node.vy; node.z += node.vz;
      }
      this.layoutIteration += 1;
    }

    _project(node) {
      const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
      const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
      const x1 = node.x * cy - node.z * sy;
      const z1 = node.x * sy + node.z * cy;
      const y2 = node.y * cp - z1 * sp;
      const z2 = node.y * sp + z1 * cp;
      const camera = 720;
      const scale = this.zoom * camera / Math.max(300, camera - z2);
      return {x: this.width / 2 + x1 * scale, y: this.height / 2 + y2 * scale, z: z2, scale};
    }

    _connected(id) {
      if (!this.selected || this.selected.kind !== 'node') return false;
      return this.edges.some(edge => (edge.source === this.selected.id && edge.target === id) || (edge.target === this.selected.id && edge.source === id));
    }

    _drawEdge(edge) {
      const source = this.projected.get(edge.source), target = this.projected.get(edge.target);
      if (!source || !target) return;
      const selected = this.selected?.kind === 'edge' && this.selected.id === edge.id;
      const hovered = this.hovered?.kind === 'edge' && this.hovered.id === edge.id;
      const contextual = this.selected?.kind === 'node' && (edge.source === this.selected.id || edge.target === this.selected.id);
      const active = selected || hovered || contextual;
      const ctx = this.ctx;
      const gradient = ctx.createLinearGradient(source.x, source.y, target.x, target.y);
      gradient.addColorStop(0, active ? 'rgba(8,125,109,.94)' : 'rgba(72,103,116,.42)');
      gradient.addColorStop(1, active ? 'rgba(52,118,201,.9)' : 'rgba(72,103,116,.28)');
      ctx.beginPath(); ctx.moveTo(source.x, source.y); ctx.lineTo(target.x, target.y);
      ctx.setLineDash(edge.inferred ? [6, 4] : []);
      ctx.strokeStyle = edge.inferred ? (active ? '#7a4bc2' : 'rgba(122,75,194,.58)') : gradient; ctx.lineWidth = active ? 2.2 : 1; ctx.stroke();
      ctx.setLineDash([]);
      const angle = Math.atan2(target.y - source.y, target.x - source.x);
      const radius = target.radius || 8;
      const ax = target.x - Math.cos(angle) * (radius + 2), ay = target.y - Math.sin(angle) * (radius + 2);
      ctx.beginPath(); ctx.moveTo(ax, ay);
      ctx.lineTo(ax - Math.cos(angle - .48) * (active ? 9 : 6), ay - Math.sin(angle - .48) * (active ? 9 : 6));
      ctx.lineTo(ax - Math.cos(angle + .48) * (active ? 9 : 6), ay - Math.sin(angle + .48) * (active ? 9 : 6));
      ctx.closePath(); ctx.fillStyle = edge.inferred ? '#7a4bc2' : active ? '#087d6d' : 'rgba(72,103,116,.58)'; ctx.fill();
      if (active || this.edges.length < 18) {
        const mx = (source.x + target.x) / 2, my = (source.y + target.y) / 2;
        ctx.font = `${active ? 12 : 10}px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif`;
        const width = ctx.measureText(edge.label).width + 12;
        ctx.fillStyle = active ? 'rgba(231,246,242,.98)' : 'rgba(255,255,255,.9)';
        ctx.beginPath(); ctx.roundRect(mx - width / 2, my - 10, width, 19, 8); ctx.fill();
        ctx.strokeStyle = active ? 'rgba(8,125,109,.4)' : 'rgba(86,109,119,.18)'; ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = active ? '#075f54' : '#526774'; ctx.textAlign = 'center'; ctx.fillText(edge.label, mx, my + 4);
      }
    }

    _drawNode(node) {
      const point = this.projected.get(node.id); if (!point) return;
      const selected = this.selected?.kind === 'node' && this.selected.id === node.id;
      const hovered = this.hovered?.kind === 'node' && this.hovered.id === node.id;
      const neighbor = this._connected(node.id);
      const muted = this.selected?.kind === 'node' && !selected && !neighbor;
      const radius = clamp((6.5 + Math.sqrt(node.degree + 1) * 2.2) * point.scale, 5, 22);
      point.radius = radius;
      const ctx = this.ctx;
      if (selected || hovered) {
        const halo = ctx.createRadialGradient(point.x, point.y, radius, point.x, point.y, radius * 2.7);
        halo.addColorStop(0, `${node.color}70`); halo.addColorStop(1, `${node.color}00`);
        ctx.beginPath(); ctx.arc(point.x, point.y, radius * 2.7, 0, Math.PI * 2); ctx.fillStyle = halo; ctx.fill();
      }
      const sphere = ctx.createRadialGradient(point.x - radius * .35, point.y - radius * .4, radius * .12, point.x, point.y, radius);
      sphere.addColorStop(0, '#ffffff'); sphere.addColorStop(.38, node.color); sphere.addColorStop(1, '#d7e2e7');
      ctx.globalAlpha = muted ? .22 : 1;
      ctx.beginPath(); ctx.arc(point.x, point.y, radius, 0, Math.PI * 2); ctx.fillStyle = sphere; ctx.fill();
      ctx.lineWidth = selected ? 2.5 : 1; ctx.strokeStyle = selected ? '#087d6d' : `${node.color}cc`; ctx.stroke();
      ctx.globalAlpha = 1;
      if (!muted || selected || hovered) {
        const label = String(node.label || '');
        ctx.font = `${selected || hovered ? 600 : 500} ${selected || hovered ? 13 : 11}px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif`;
        const labelWidth = Math.min(190, ctx.measureText(label).width + 14);
        const lx = point.x + radius + 5, ly = point.y - 9;
        ctx.fillStyle = selected || hovered ? 'rgba(232,247,243,.98)' : 'rgba(255,255,255,.92)';
        ctx.beginPath(); ctx.roundRect(lx, ly, labelWidth, 20, 7); ctx.fill();
        ctx.strokeStyle = selected || hovered ? 'rgba(8,125,109,.42)' : 'rgba(77,101,112,.16)'; ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = selected || hovered ? '#075f54' : '#334b58'; ctx.textAlign = 'left';
        ctx.save(); ctx.beginPath(); ctx.rect(lx + 7, ly, labelWidth - 12, 20); ctx.clip(); ctx.fillText(label, lx + 7, ly + 14); ctx.restore();
      }
    }

    render() {
      const ctx = this.ctx; ctx.clearRect(0, 0, this.width, this.height);
      this.projected.clear();
      for (const node of this.nodes) this.projected.set(node.id, this._project(node));
      const sortedEdges = [...this.edges].sort((a, b) => {
        const aSource = this.projected.get(a.source), aTarget = this.projected.get(a.target);
        const bSource = this.projected.get(b.source), bTarget = this.projected.get(b.target);
        const az = aSource && aTarget ? (aSource.z + aTarget.z) / 2 : -Infinity;
        const bz = bSource && bTarget ? (bSource.z + bTarget.z) / 2 : -Infinity;
        return az - bz;
      });
      for (const edge of sortedEdges) this._drawEdge(edge);
      const sortedNodes = [...this.nodes]
        .filter(node => this.projected.has(node.id))
        .sort((a, b) => this.projected.get(a.id).z - this.projected.get(b.id).z);
      for (const node of sortedNodes) this._drawNode(node);
      if (!this.nodes.length) {
        ctx.fillStyle = '#6f808a'; ctx.font = '14px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif';
        ctx.textAlign = 'center'; ctx.fillText('当前知识空间还没有节点', this.width / 2, this.height / 2);
      }
    }

    _loop(time) {
      if (this.destroyed) return;
      this._stepLayout(); this._stepLayout();
      if (this.autoRotate && !this.dragging) this.yaw += .00013 * Math.min(32, time - (this.lastTime || time));
      this.lastTime = time; this.render();
      this.frame = requestAnimationFrame(next => this._loop(next));
    }

    _eventPoint(event) {
      const rect = this.canvas.getBoundingClientRect();
      return {x: event.clientX - rect.left, y: event.clientY - rect.top};
    }

    _hit(event) {
      const point = this._eventPoint(event);
      const nodes = [...this.nodes]
        .filter(node => this.projected.has(node.id))
        .sort((a, b) => this.projected.get(b.id).z - this.projected.get(a.id).z);
      for (const node of nodes) {
        const projected = this.projected.get(node.id);
        if (projected && Math.hypot(point.x - projected.x, point.y - projected.y) <= (projected.radius || 9) + 5) return {kind: 'node', id: node.id};
      }
      for (const edge of this.edges) {
        const source = this.projected.get(edge.source), target = this.projected.get(edge.target);
        if (source && target && pointLineDistance(point.x, point.y, source.x, source.y, target.x, target.y) < 7) return {kind: 'edge', id: edge.id};
      }
      return null;
    }

    select(selection) {
      this.selected = selection;
      this.options.onSelect?.(selection);
    }

    focusNode(id) {
      if (!this.nodeMap.has(id)) return false;
      this.zoom = Math.max(this.zoom, 1.25); this.select({kind: 'node', id}); return true;
    }

    focusByQuery(query) {
      const value = String(query || '').trim().toLocaleLowerCase();
      if (!value) { this.select(null); return null; }
      const node = this.nodes.find(item => String(item.label).toLocaleLowerCase().includes(value));
      if (node) this.focusNode(node.id);
      return node || null;
    }

    resetView() { this.yaw = -.38; this.pitch = .2; this.zoom = .82; this.select(null); }
    zoomBy(factor) { this.zoom = clamp(this.zoom * factor, .45, 3.6); }
    toggleAutoRotate(force) { this.autoRotate = typeof force === 'boolean' ? force : !this.autoRotate; return this.autoRotate; }

    destroy() {
      this.destroyed = true; cancelAnimationFrame(this.frame); this.resizeObserver?.disconnect();
      this.canvas.removeEventListener('pointerdown', this.onPointerDown);
      this.canvas.removeEventListener('pointermove', this.onPointerMove);
      this.canvas.removeEventListener('pointerup', this.onPointerUp);
      this.canvas.removeEventListener('pointerleave', this.onPointerLeave);
      this.canvas.removeEventListener('wheel', this.onWheel);
      this.canvas.removeEventListener('keydown', this.onKeyDown);
    }
  }

  window.KnowledgeGraph3D = KnowledgeGraph3D;
})();
