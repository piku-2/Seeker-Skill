"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const data = JSON.parse($("explorer-data").textContent);
  const viewport = $("view");
  const svg = viewport.querySelector("svg");
  const nodes = new Map(data.nodes.map(n => [n.node_id, n]));
  const edges = new Map(data.edges.map(e => [e.edge_id, e]));
  const artwork = new Map(data.assets.map(a => [a.id, a]));
  const sources = new Map(data.sources.map(s => [s.number, s]));
  const claims = new Map((data.research.claims || []).map(c => [c.id, c]));
  const evidence = new Map((data.research.evidence || []).map(e => [e.id, e]));
  const indexDOM = key => new Map([...svg.querySelectorAll(`[data-${key}-id]`)].map(el => [el.getAttribute(`data-${key}-id`), el]));
  const nodeElements = indexDOM("node"), edgeElements = indexDOM("edge"), assetElements = indexDOM("asset");
  const leaders = new Map([...svg.querySelectorAll("[data-anchor-leader]")].map(el => [el.dataset.anchorLeader, el]));
  const kinds = {concept:"概念",technology:"技術",invention:"発明",person:"人物",organization:"組織",product:"製品・作品",event:"出来事",culture:"文化",document:"史料・文献",object:"物品",place:"場所",other:"その他"};
  const relations = {dependency:"依存",influence:"影響",context:"背景",confluence:"合流",parallel:"並行",opposition:"対立",hypothesis:"仮説",other:"その他"};
  const states = {verified:"確認済み",favored:"有力説",contested:"論争中",hypothetical:"仮説",unverified:"未検証",none:"評価記録なし"};
  const adjacent = new Map(data.nodes.map(n => [n.node_id, []]));
  data.edges.forEach(e => {
    adjacent.get(e.source_node_id).push(e);
    if (e.source_node_id !== e.target_node_id) adjacent.get(e.target_node_id).push(e);
  });
  const owners = new Map(data.assets.map(a => [a.id, []]));
  data.nodes.forEach(n => (n.asset_refs || []).forEach(id => owners.get(id)?.push(n.node_id)));
  const normalize = s => String(s || "").normalize("NFKC").toLocaleLowerCase("ja");
  const searchText = new Map(data.nodes.map(n => [n.node_id, normalize([
    n.node_id, n.label, n.original_label, ...(n.aliases || []), n.summary,
    n.date_label, kinds[n.type],
    ...(n.claim_refs || []).map(id => claims.get(id)?.statement),
    ...adjacent.get(n.node_id).flatMap(e => [e.label, relations[e.relation_type]]),
  ].join(" "))]));
  function element(tag, text, parent) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (parent) parent.append(el);
    return el;
  }
  function link(parent, url, text) {
    if (!/^https?:\/\//i.test(url || "")) return;
    const a = element("a", text || url, parent);
    a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
  }
  function options(id, values, labels) {
    [...new Set(values)].sort().forEach(value => {
      const opt = element("option", labels[value] || value, $(id)); opt.value = value;
    });
  }
  options("level", ["1","2","3","4"], {1:"L1",2:"L2",3:"L3",4:"L4"});
  options("kind", data.nodes.map(n => n.type), kinds);
  options("relation", data.edges.map(e => e.relation_type), relations);
  options("assessment", Object.keys(states), states);

  let scale = 1, x = 0, y = 0, active = null, page = 0, frame = 0, fitted = true;
  let visibleNodes = new Set(nodes.keys()), visibleEdges = new Set(edges.keys());
  const dimensions = () => [Math.max(1, viewport.clientWidth), Math.max(1, viewport.clientHeight)];
  const fitScale = () => {
    const [w,h] = dimensions();
    return Math.min(1, w / data.width, h / data.height);
  };
  const bounded = (position, content, viewportSize) =>
    content <= viewportSize ? (content - viewportSize) / 2 : Math.max(-viewportSize * .12, Math.min(position, content - viewportSize * .88));
  function draw() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const [w,h] = dimensions();
      const vw = w / scale, vh = h / scale;
      x = bounded(x, data.width, vw); y = bounded(y, data.height, vh);
      svg.setAttribute("viewBox", `${x} ${y} ${vw} ${vh}`);
      $("zoom-level").value = `${scale < .01 ? (scale * 100).toPrecision(2) : Math.round(scale * 100)}%`;
    });
  }
  function fit() { fitted = true; scale = fitScale(); x = 0; y = 0; draw(); }
  function zoom(factor, px, py) {
    const [w,h] = dimensions();
    px ??= w/2; py ??= h/2;
    const next = Math.max(fitScale() * .65, Math.min(6, scale * factor));
    x += px / scale - px / next; y += py / scale - py / next;
    scale = next; fitted = false; draw();
  }
  function center(box) {
    if (!box) return;
    const [w,h] = dimensions(), [bx,by,bw,bh] = box;
    scale = Math.max(fitScale(), Math.min(4, Math.min(w / Math.max(1,bw), h / Math.max(1,bh)) * .78));
    x = bx + bw/2 - w/(2*scale); y = by + bh/2 - h/(2*scale);
    fitted = false; draw();
  }
  $("fit").onclick = fit;
  $("zoom-in").onclick = () => zoom(1.35);
  $("zoom-out").onclick = () => zoom(1/1.35);
  if ("ResizeObserver" in window) new ResizeObserver(() => fitted ? fit() : draw()).observe(viewport);
  else window.addEventListener("resize", () => fitted ? fit() : draw());
  viewport.addEventListener("wheel", event => {
    event.preventDefault();
    const box = viewport.getBoundingClientRect();
    const multiplier = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1;
    if (event.deltaX && !event.ctrlKey) { x += event.deltaX * multiplier / scale; fitted = false; draw(); }
    else zoom(Math.exp(-event.deltaY * multiplier / 420), event.clientX-box.left, event.clientY-box.top);
  }, {passive: false});
  const pointers = new Map();
  let dragged = false;
  viewport.addEventListener("pointerdown", event => {
    if (event.button !== 0 || event.target.closest("a, button")) return;
    if (!pointers.size) dragged = false;
    pointers.set(event.pointerId, {x:event.clientX, y:event.clientY, dx:0, dy:0});
  });
  window.addEventListener("pointermove", event => {
    const p = pointers.get(event.pointerId);
    if (!p) return;
    const dx = event.clientX-p.x, dy = event.clientY-p.y;
    p.dx += dx; p.dy += dy;
    const other = [...pointers.values()].find(v => v !== p);
    if (other) {
      const distance = Math.hypot(p.x-other.x,p.y-other.y);
      const box = viewport.getBoundingClientRect();
      if (distance > 1) zoom(Math.hypot(event.clientX-other.x,event.clientY-other.y)/distance,
        (event.clientX+other.x)/2-box.left,(event.clientY+other.y)/2-box.top);
      x -= dx/(2*scale); y -= dy/(2*scale); dragged = true;
    } else if (dragged || Math.hypot(p.dx,p.dy) > 4) {
      x -= (dragged ? dx : p.dx)/scale; y -= (dragged ? dy : p.dy)/scale; dragged = true;
    }
    p.x = event.clientX; p.y = event.clientY;
    if (dragged) { fitted = false; viewport.classList.add("dragging"); draw(); }
  });
  function release(event) {
    pointers.delete(event.pointerId);
    if (!pointers.size) viewport.classList.remove("dragging");
  }
  window.addEventListener("pointerup", release);
  window.addEventListener("pointercancel", release);
  window.addEventListener("blur", () => { pointers.clear(); viewport.classList.remove("dragging"); });
  viewport.addEventListener("keydown", event => {
    const moves = {ArrowLeft:[-65,0], ArrowRight:[65,0], ArrowUp:[0,-65], ArrowDown:[0,65]};
    if (moves[event.key]) {
      event.preventDefault(); const [dx,dy] = moves[event.key]; x += dx/scale; y += dy/scale; fitted = false; draw();
    } else if (event.key === "+" || event.key === "=" || event.key === "-") {
      event.preventDefault(); zoom(event.key === "-" ? 1/1.35 : 1.35);
    } else if (event.key === "Home") { event.preventDefault(); fit(); }
  });

  const fields = ["level","kind","relation","assessment","period","from","to"];
  function clearFields() { fields.forEach(k => { $(k).value = ""; }); }
  function yearLabel(year) { return year <= 0 ? `紀元前${1-year}年` : `${year}年`; }
  function dateAssertion(date) {
    if (date.label) return date.label;
    if (date.earliest != null && date.latest != null) {
      return date.earliest === date.latest ? yearLabel(date.earliest)
        : `${yearLabel(date.earliest)}〜${yearLabel(date.latest)}`;
    }
    if (date.earliest != null) return `${yearLabel(date.earliest)}以降`;
    if (date.latest != null) return `${yearLabel(date.latest)}以前`;
    return "";
  }
  function dateBound(value) { return value === "" ? null : +value < 0 ? +value + 1 : +value; }
  function isInRange(n, from, to) {
    if (from === null && to === null) return true;
    if (n.anchor_year == null) return false;
    // Internal sequence placement never supplies a calendar date.
    const date = n.date_placement;
    if (date?.mode === "sequence") return false;
    const lo = date && "earliest" in date ? date.earliest : n.anchor_year;
    const hi = date && "latest" in date ? date.latest : n.anchor_year;
    return (from === null || hi === null || hi >= from) && (to === null || lo === null || lo <= to);
  }
  const hasState = (record, value) => value === "none" ? !(record.assessments || []).length : (record.assessments || []).includes(value);
  function applyFilters() {
    const [level,kind,relation,state,period] = fields.slice(0,5).map(k => $(k).value);
    const from = dateBound($("from").value), to = dateBound($("to").value);
    const reversed = from !== null && to !== null && from > to;
    visibleNodes = new Set(data.nodes.filter(n =>
      (!level || String(n.importance.level) === level) && (!kind || n.type === kind)
      && (!period || (n.display_date?.mode || (n.anchor_year == null ? "unknown" : "anchored")) === period)
      && (!state || hasState(n, state) || adjacent.get(n.node_id).some(e => hasState(e, state)))
      && !reversed && isInRange(n, from, to)).map(n => n.node_id));
    visibleEdges = new Set(data.edges.filter(e =>
      visibleNodes.has(e.source_node_id) && visibleNodes.has(e.target_node_id)
      && (!relation || relation === e.relation_type) && (!state || hasState(e, state))
      && (!level || String(e.importance.level) === level)).map(e => e.edge_id));
    if (relation) {
      const endpoint = new Set([...visibleEdges].flatMap(id => [edges.get(id).source_node_id,edges.get(id).target_node_id]));
      visibleNodes = new Set([...visibleNodes].filter(id => endpoint.has(id)));
    }
    for (const [id, el] of nodeElements) el.classList.toggle("filtered", !visibleNodes.has(id));
    for (const [id, el] of leaders) el.classList.toggle("filtered", !visibleNodes.has(id));
    for (const [id, el] of edgeElements) el.classList.toggle("filtered", !visibleEdges.has(id));
    for (const [id, el] of assetElements) {
      const ids = owners.get(id);
      el.classList.toggle("filtered", ids?.length > 0 && !ids.some(n => visibleNodes.has(n)));
    }
    if (active && !visibleNodes.has(active)) select(null);
    if (reversed) $("count").textContent = "期間の開始年と終了年を確認してください。";
    page = 0; showResults(reversed); highlight();
  }
  function showResults(reversed = false) {
    const tokens = normalize($("search").value).trim().split(/\s+/).filter(Boolean);
    const results = data.nodes.filter(n => visibleNodes.has(n.node_id) && tokens.every(token => searchText.get(n.node_id).includes(token)));
    const limit = (page+1)*60;
    $("results").replaceChildren();
    results.slice(0,limit).forEach(n => {
      const button = element("button", n.label, element("li", undefined, $("results")));
      button.type = "button"; button.dataset.target = n.node_id;
      if (active === n.node_id) button.setAttribute("aria-current","true");
    });
    $("more").hidden = results.length <= limit;
    if (!reversed) $("count").textContent = tokens.length
      ? `検索 ${results.length}件 · 表示 ${visibleNodes.size}対象・${visibleEdges.size}関係`
      : `表示 ${visibleNodes.size}/${nodes.size} 対象 · ${visibleEdges.size}/${edges.size} 関係`;
  }
  fields.forEach(key => $(key).addEventListener("input", applyFilters));
  $("reset").onclick = () => { clearFields(); $("search").value = ""; applyFilters(); };
  $("search").addEventListener("input", () => { page = 0; showResults(); });
  $("search").addEventListener("keydown", e => {
    if (e.key === "Enter") $("results").querySelector("button")?.click();
  });
  $("more").onclick = () => { page++; showResults(); };
  $("results").onclick = event => { if (event.target.dataset.target) select(event.target.dataset.target, true); };

  function highlight() {
    const close = new Set(active ? [active] : []), connections = new Set();
    for (const e of adjacent.get(active) || []) {
      if (!visibleEdges.has(e.edge_id)) continue;
      close.add(e.source_node_id); close.add(e.target_node_id); connections.add(e.edge_id);
    }
    for (const [id,el] of nodeElements) {
      el.classList.toggle("selected",id === active);
      el.classList.toggle("neighbor",id !== active && close.has(id));
      el.classList.toggle("dimmed",Boolean(active) && !close.has(id));
    }
    for (const [id,el] of assetElements) {
      el.classList.toggle("dimmed", Boolean(active) && !owners.get(id)?.includes(active));
    }
    for (const [id,el] of edgeElements) {
      const e = edges.get(id), near = connections.has(id);
      el.classList.toggle("related",near); el.classList.toggle("dimmed",Boolean(active) && !near);
      el.classList.toggle("incoming",near && e.target_node_id === active);
      el.classList.toggle("outgoing",near && e.source_node_id === active);
    }
  }
  function field(parent,label,value) { if (value) element("p", `${label}　${value}`,parent); }
  function heading(parent,text) { element("h3",text,parent); return element("ul",undefined,parent); }
  function citations(parent, entries) {
    entries.forEach(source => {
      if (!source) return;
      const li = element("li", [source.number && `[${source.number}]`,source.title,source.locator].filter(Boolean).join(" "),parent);
      if (source.source_url) { li.append(" "); link(li,source.source_url,"出典を開く"); }
    });
  }
  function claimList(parent, ids) {
    (ids || []).forEach(id => {
      const claim = claims.get(id);
      if (!claim) return;
      const li = element("li", claim.statement,parent);
      field(li,"評価",states[claim.assessment]);
      for (const [label, refs] of [["裏付け",claim.supporting_evidence_ids],["反対・異説",claim.contradicting_evidence_ids]]) {
        if (refs?.length) {
          element("strong",label,li);
          citations(element("ul",undefined,li),refs.map(r => evidence.get(r)));
        }
      }
    });
  }
  function renderDetails(n) {
    const content = $("content"); content.replaceChildren();
    element("h2",n.label,content);
    if (n.original_label && n.original_label !== n.label) field(content,"原表記",n.original_label);
    field(content,"別名",(n.aliases || []).join("・"));
    field(content,"重要度",`L${n.importance.level} ${n.importance.reason || ""}`);
    field(content,"検証状態",(n.assessments || []).map(s => states[s] || s).join("・"));
    if (n.summary) element("p",n.summary,content);
    field(content,"年代",n.date_label);
    field(content,"配置の根拠",n.display_date?.anchor_reason || n.display_date?.reason);
    field(content,"不確実性",n.display_date?.uncertainty);
    const assertions = (n.dates || []).filter(date => dateAssertion(date));
    if (assertions.length) {
      const list = heading(content,"年代資料");
      assertions.forEach(date => element("li", [
        data.date_kinds?.[date.kind], dateAssertion(date), date.status === "estimated" ? "推定" : "",
      ].filter(Boolean).join(" · "), list));
    }
    const dateClaims = n.date_claim_refs || [];
    if (dateClaims.some(id => claims.has(id))) claimList(heading(content,"年代の根拠"),dateClaims);
    if (n.claim_refs?.some(id => claims.has(id))) claimList(heading(content,"関連する主張"),n.claim_refs);
    if (n.source_numbers?.length) citations(heading(content,"出典"),n.source_numbers.map(no => sources.get(no)));
    if (n.asset_refs?.length) {
      const list = heading(content,"画像・図表");
      n.asset_refs.forEach(id => {
        const a = artwork.get(id);
        if (!a) return;
        const li = element("li",a.caption,list);
        const original = assetElements.get(id)?.querySelector("image");
        if (original) { const image = element("img",undefined,li); image.loading = "lazy"; image.alt = a.caption; image.src = original.getAttribute("href"); }
        if (a.bbox) { li.append(" "); const button = element("button","図版の位置へ",li); button.onclick = () => center(a.bbox); }
        a.urls.forEach(url => { li.append(" "); link(li,url,"提供元"); });
      });
    }
    if (adjacent.get(n.node_id).length) {
      const list = heading(content,"接続関係");
      for (const e of adjacent.get(n.node_id)) {
        const from = e.source_node_id === n.node_id, other = from ? e.target_node_id : e.source_node_id;
        const li = element("li",`${from ? "出力 →" : "入力 ←"} ${relations[e.relation_type] || e.relation_type} · `,list);
        const button = element("button",nodes.get(other).label,li);
        button.onclick = () => select(other,true);
        field(li,"関係",e.label); field(li,"方向の意味",e.direction_meaning);
        field(li,"評価",(e.assessments || []).map(s => states[s] || s).join("・"));
        if (e.claim_refs?.some(id => claims.has(id))) claimList(element("ul",undefined,li),e.claim_refs);
        if (e.source_numbers?.length) citations(element("ul",undefined,li),e.source_numbers.map(no => sources.get(no)));
      }
    }
  }
  function select(id, move = false) {
    if (id && !visibleNodes.has(id)) { clearFields(); applyFilters(); }
    active = id;
    $("detail").hidden = !id;
    if (id) {
      renderDetails(nodes.get(id));
      $("detail").scrollTop = 0;
      if (move) center(nodes.get(id).bbox);
    } else $("content").replaceChildren();
    highlight();
    showResults();
  }
  svg.addEventListener("click",event => {
    if (dragged) { event.preventDefault(); dragged = false; return; }
    if (event.target.closest("a")) return;
    const id = event.target.closest("[data-node-id]")?.dataset.nodeId;
    select(id || null);
  });
  $("close").onclick = () => select(null);
  document.addEventListener("keydown",e => { if (e.key === "Escape" && active) select(null); });
  showResults(); fit();
})();
