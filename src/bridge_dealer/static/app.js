// One set of controls is moved between desktop docks and mobile dialogs.
'use strict';
const $ = id => document.getElementById(id);
const SEATS = ['N', 'E', 'S', 'W'];
const NAMES = {N: '北家', E: '东家', S: '南家', W: '西家'};
const ENGLISH = {N: 'NORTH', E: 'EAST', S: 'SOUTH', W: 'WEST'};
const SUITS = ['♠', '♥', '♦', '♣'];
const STRAINS = ['S', 'H', 'D', 'C', 'NT'];
const RANKS = 'AKQJT98765432';
const MODES = {full: '全牌', condition: '条件牌', free: '不约束', exclude: '排除牌'};
const emptyHand = () => ({mode: 'free', cards: new Set(), excluded: new Set(), alsoExclude: false, keepKnown: false, shapeOverride: null, hcp: [0, 37], lengths: [[0, 13], [0, 13], [0, 13], [0, 13]]});
let hands = Object.fromEntries(SEATS.map(s => [s, emptyHand()]));
let selectedSeat = 'N', selectedStrains = new Set(STRAINS), contracts = [];
let contractLevels = Object.fromEntries(STRAINS.map(s => [s,3]));
let queryMode = 'estimate', activeJob = null, lastResult = null, lastSnapshot = null, startedAt = 0, timer = null;
let editorPane = 'main', conditionPane = 'points', pickerSuit = 0, contractPage = 0, resultPage = 0;
let toastTimer, resizeFrame;
const editor = document.querySelector('.editor'), contractSection = $('contract-section');
const wideScreen = matchMedia('(min-width:981px)');
const symbol = strain => strain === 'NT' ? 'NT' : SUITS[STRAINS.indexOf(strain)];
const redClass = strain => ['H', 'D'].includes(strain) ? 'red' : '';
const prettyCount = n => Number(n || 0).toLocaleString('zh-CN');
const canonical = value => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
const pct = n => n == null ? '—' : (100 * n).toFixed(1) + '%';
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 3500); }
function error(message) { $('editor-error').textContent = message; $('editor-error').hidden = !message; }
function changed() { if (lastResult) { $('stale-note').hidden = false; scheduleLayout(); } }
function effectiveCards(h) { return h.mode === 'full' || h.mode === 'condition' && h.keepKnown ? h.cards : new Set(); }
function effectiveExcluded(h) { return h.mode === 'exclude' || h.alsoExclude ? h.excluded : new Set(); }
function pbn(cards) { return SUITS.map((_, i) => [...cards].filter(c => Math.floor(c / 13) === i).sort((a, b) => a - b).map(c => RANKS[c % 13]).join('')).join('.'); }
function parsePbn(value) {
  if (typeof value !== 'string') throw Error('持牌和排除牌必须是 SHDC 点分字符串。');
  const suits = value.trim().toUpperCase().replaceAll('10', 'T').split('.');
  if (suits.length !== 4) throw Error('请按 ♠.♥.♦.♣ 输入四段花色；空门可留空或用 -。');
  const cards = new Set();
  suits.forEach((ranks, suit) => { if (ranks === '-') ranks = ''; for (const rank of ranks) { const r = RANKS.indexOf(rank); if (r < 0) throw Error('牌面只可使用 AKQJT98765432，10 用 T 表示。'); const id = suit * 13 + r; if (cards.has(id)) throw Error('同一家不能重复包含同一张牌。'); cards.add(id); } });
  return cards;
}
function allShapes(lengths) {
  const result = [];
  for (let s = lengths[0][0]; s <= lengths[0][1]; s++) for (let h = lengths[1][0]; h <= lengths[1][1]; h++) for (let d = lengths[2][0]; d <= lengths[2][1]; d++) { const c = 13 - s - h - d; if (c >= lengths[3][0] && c <= lengths[3][1]) result.push([s, h, d, c]); }
  return result;
}
function validateHand(h, seat) {
  if (h.mode === 'full' && h.cards.size !== 13) throw Error(`${NAMES[seat]}全牌需要 13 张，当前为 ${h.cards.size} 张。`);
  if (effectiveCards(h).size > 13) throw Error(`${NAMES[seat]}已知牌最多为 13 张。`);
  if (h.mode === 'condition') {
    if (!h.hcp.every(n => Number.isInteger(n) && n >= 0 && n <= 37) || h.hcp[0] > h.hcp[1]) throw Error(`${NAMES[seat]}点力须为 0–37 的整数范围，最小值不能大于最大值。`);
    if (!h.lengths.every(pair => pair.every(n => Number.isInteger(n) && n >= 0 && n <= 13) && pair[0] <= pair[1]) || !(h.shapeOverride || allShapes(h.lengths)).length) throw Error(`${NAMES[seat]}花色长度范围不成立，四门总长度必须能组成 13 张。`);
  }
  if ([...effectiveExcluded(h)].some(c => effectiveCards(h).has(c))) throw Error(`${NAMES[seat]}不能同时持有和排除同一张牌。`);
}
function conditions(source = hands) {
  const result = {}, owners = new Map();
  for (const seat of SEATS) {
    const h = source[seat]; validateHand(h, seat); const value = {}, known = effectiveCards(h);
    if (known.size) { value.predeal = pbn(known); for (const c of known) { if (owners.has(c)) throw Error(`${NAMES[seat]}与${NAMES[owners.get(c)]}持牌重复。`); owners.set(c, seat); } }
    if (h.mode === 'condition') { if (h.hcp[0] !== 0 || h.hcp[1] !== 37) value.hcp = [...h.hcp]; const shapes = h.shapeOverride || allShapes(h.lengths); if (shapes.length < 560) value.shapes = shapes; }
    const excluded = effectiveExcluded(h); if (excluded.size) value.exclude = pbn(excluded);
    if (Object.keys(value).length) result[seat] = value;
  }
  return result;
}
function importHands(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw Error('顶层必须是以 N、E、S、W 为键的条件对象。');
  const next = Object.fromEntries(SEATS.map(s => [s, emptyHand()]));
  const shape = v => {
    if (!Array.isArray(v) || v.length !== 4 || !v.every(n => Number.isInteger(n) && n >= 0 && n <= 13) || v.reduce((a,b) => a+b,0) !== 13) throw Error('每个牌型必须是 SHDC 顺序、总和为 13 的四个整数。');
    return [...v];
  };
  const permutations = v => v.length === 1 ? [v] : v.flatMap((n,i) => permutations(v.filter((_,j) => j !== i)).map(t => [n,...t]));
  for (const [seat,c] of Object.entries(raw)) {
    if (!SEATS.includes(seat)) throw Error(`未知方位 ${seat}；只接受 N、E、S、W。`);
    if (!c || typeof c !== 'object' || Array.isArray(c)) throw Error(`${NAMES[seat]}条件必须是对象。`);
    const unknown = Object.keys(c).filter(k => !['predeal','exclude','hcp','shape','shapes','shape_any'].includes(k));
    if (unknown.length) throw Error(`${NAMES[seat]}包含不支持的字段：${unknown.join('、')}。`);
    const h = next[seat], shapeKeys = ['shape','shapes','shape_any'].filter(k => k in c);
    if (shapeKeys.length > 1) throw Error(`${NAMES[seat]}只能指定 shape、shapes、shape_any 其中一项。`);
    if ('predeal' in c) h.cards = parsePbn(c.predeal);
    if (h.cards.size > 13) throw Error(`${NAMES[seat]}已知牌超过 13 张。`);
    if ('hcp' in c) {
      h.hcp = Number.isInteger(c.hcp) ? [c.hcp,c.hcp] : Array.isArray(c.hcp) && c.hcp.length === 2 ? [...c.hcp] : [NaN,NaN];
    }
    if (shapeKeys.length) {
      let shapes;
      if ('shape' in c) shapes = [shape(c.shape)];
      else if ('shape_any' in c) shapes = permutations(shape(c.shape_any));
      else { if (!Array.isArray(c.shapes) || !c.shapes.length) throw Error(`${NAMES[seat]}shapes 必须是非空牌型数组。`); shapes = c.shapes.map(shape); }
      h.shapeOverride = [...new Map(shapes.map(v => [v.join(','),v])).values()].sort((a,b) => { for(let i=0;i<4;i++) if(a[i]!==b[i])return a[i]-b[i];return 0; });
      h.lengths = SUITS.map((_,i) => [Math.min(...h.shapeOverride.map(v => v[i])),Math.max(...h.shapeOverride.map(v => v[i]))]);
    }
    if (shapeKeys.length || 'hcp' in c || h.cards.size && h.cards.size < 13) { h.mode = 'condition'; h.keepKnown = h.cards.size > 0; }
    else if (h.cards.size) h.mode = 'full';
    if ('exclude' in c) { h.excluded = parsePbn(c.exclude); if (h.mode === 'free') h.mode = 'exclude'; else h.alsoExclude = h.excluded.size > 0; }
    validateHand(h,seat);
  }
  conditions(next); // Validate the whole import before replacing any current hand.
  return next;
}
function renderTable() {
  let fixed = 0, free = 0;
  for (const seat of SEATS) {
    const h = hands[seat], excluded = effectiveExcluded(h), known = effectiveCards(h); fixed += known.size; let content = '', meta = '';
    if (h.mode === 'full') {
      content = '<div class="seat-hand">' + SUITS.map((s, i) => `<span><i class="${i === 1 || i === 2 ? 'red' : ''}">${s}</i><em>${[...h.cards].sort((a,b) => a-b).filter(c => Math.floor(c/13) === i).map(c => RANKS[c%13]).join(' ') || '—'}</em></span>`).join('') + '</div>';
      meta = `${h.cards.size} / 13 张 · ${[...h.cards].reduce((sum,c) => sum + Math.max(4-c%13,0), 0)} HCP`;
    } else if (h.mode === 'condition') {
      const shape = h.lengths.map((r,i) => `${SUITS[i]} ${r[0] === r[1] ? r[0] : r.join('–')}`).join('　');
      content = `<div class="seat-meta"><b>${h.hcp.join('–')} HCP</b><br>${shape}<br>${h.shapeOverride ? h.shapeOverride.length+' 种指定牌型' : '按条件均匀发牌'}</div>`;
      meta = [known.size ? `已知 ${known.size} 张` : '',excluded.size ? `排除 ${excluded.size} 张` : ''].filter(Boolean).join(' · ') || '花色与点力同时生效';
    } else if (excluded.size) {
      content = `<div class="seat-meta"><b>排除 ${excluded.size} 张牌</b><br>${SUITS.map((s,i) => `${s} ${[...excluded].filter(c => Math.floor(c/13) === i).length}`).join('　')}<br>其余合法持牌均可</div>`; meta = '明确不会持有';
    } else { free++; content = '<div class="seat-placeholder">♠ ♥ ♦ ♣</div>'; meta = '任意合法手牌'; }
    $('seat-' + seat).innerHTML = `<button class="seat-button ${seat === selectedSeat ? 'selected' : ''}" data-table-seat="${seat}" aria-label="编辑${NAMES[seat]}" aria-pressed="${seat === selectedSeat}"><div class="seat-head"><strong><b>${seat}</b>${NAMES[seat]}</strong><small>${MODES[h.mode]}</small></div>${content}<div class="seat-foot">${meta}</div></button>`;
  }
  $('deal-summary').textContent = `${fixed} 张已知牌 · ${free} 家不约束`;
}
function renderPicker(target, excluded) {
  const h = hands[selectedSeat], cards = excluded ? h.excluded : h.cards;
  const otherOwned = new Set(SEATS.filter(s => s !== selectedSeat).flatMap(s => [...effectiveCards(hands[s])]));
  const compact = editor.classList.contains('compact-editor');
  $(target).innerHTML = SUITS.map((s, i) => compact && i !== pickerSuit ? '' : `<div class="picker-row"><span class="${i === 1 || i === 2 ? 'red' : ''}">${s}</span>${[...RANKS].map((r, j) => { const c = i*13+j; const unavailable = !excluded && (otherOwned.has(c) || effectiveExcluded(h).has(c)) || excluded && effectiveCards(h).has(c); const chosen = cards.has(c); return `<button class="card ${i === 1 || i === 2 ? 'red' : ''} ${chosen ? excluded ? 'excluded' : 'chosen' : ''}" data-card="${c}" data-exclude="${excluded}" aria-label="${excluded ? '排除' : '持有'}${s}${r}" aria-pressed="${chosen}" ${unavailable && !chosen ? 'disabled' : ''}>${r}</button>`; }).join('')}</div>`).join('');
}
function renderEditor() {
  const h = hands[selectedSeat], hasKnown = h.mode === 'condition' && h.keepKnown, hasExclude = h.mode !== 'exclude' && h.alsoExclude;
  if (editorPane === 'known' && !hasKnown || editorPane === 'exclude' && !hasExclude) editorPane = 'main';
  editor.dataset.conditionPane = conditionPane;
  document.body.dataset.conditionPane = conditionPane;
  $('editor-title').innerHTML = `${NAMES[selectedSeat]} <span>${ENGLISH[selectedSeat]}</span>`; $('seat-badge').textContent = selectedSeat;
  $('player-dialog-title').textContent = `${NAMES[selectedSeat]} · 玩家设置`;
  for (const key of ['seat','mode','editorPane','conditionPane']) document.querySelectorAll(`[data-${key.replace(/[A-Z]/g,m=>'-'+m.toLowerCase())}]`).forEach(b => { if (b.tagName !== 'BUTTON') return; const v = b.dataset[key] === ({seat:selectedSeat,mode:h.mode,editorPane,conditionPane})[key]; b.classList.toggle('selected',v); b.setAttribute('aria-pressed',v); });
  $('editor-subtabs').hidden = !hasKnown && !hasExclude; $('known-tab').hidden = !hasKnown; $('excluded-tab').hidden = !hasExclude;
  $('condition-fields').hidden = h.mode !== 'condition' || editorPane !== 'main'; $('free-message').hidden = h.mode !== 'free' || editorPane !== 'main';
  $('hcp-min').value = h.hcp[0]; $('hcp-max').value = h.hcp[1];
  $('length-grid').innerHTML = SUITS.map((s,i) => `<label class="length-field"><span class="${i === 1 || i === 2 ? 'red' : ''}">${s}</span><input type="number" data-length="${i}" data-bound="0" min="0" max="13" value="${h.lengths[i][0]}" aria-label="${s}最少张数"><small>—</small><input type="number" data-length="${i}" data-bound="1" min="0" max="13" value="${h.lengths[i][1]}" aria-label="${s}最多张数"></label>`).join('');
  $('imported-shapes-note').hidden = !h.shapeOverride; $('imported-shapes-note').textContent = h.shapeOverride ? `已导入 ${h.shapeOverride.length} 种牌型；修改花色长度会替换此集合。` : '';
  const excluding = h.mode === 'exclude';
  $('cards-section').hidden = !(editorPane === 'known' || editorPane === 'main' && ['full','exclude'].includes(h.mode));
  $('cards-label').textContent = excluding ? '选择不会持有的牌' : hasKnown ? '编辑部分已知牌' : '选择全部 13 张牌';
  $('card-count').textContent = excluding ? `已排除 ${h.excluded.size} 张` : `${h.cards.size} / 13 张`;
  document.querySelectorAll('[data-picker-tabs]').forEach(el => el.innerHTML = SUITS.map((s,i) => `<button data-picker-suit="${i}" class="${i === pickerSuit ? 'selected' : ''} ${i===1||i===2?'red':''}" aria-label="编辑${s}花色" aria-pressed="${i===pickerSuit}">${s}</button>`).join(''));
  renderPicker('card-picker', excluding); renderPicker('exclude-picker',true);
  const text = pbn(excluding ? h.excluded : h.cards); $('pbn-input').value = text === '...' ? '' : text;
  $('exclusion-toggle-wrap').hidden = excluding; $('also-exclude').checked = h.alsoExclude;
  $('extra-exclusions').hidden = !hasExclude || editorPane !== 'exclude'; $('exclude-count').textContent = `${h.excluded.size} 张`;
  error('');
}
function render() { renderTable(); renderEditor(); scheduleLayout(); }
function selectSeat(s, fromTable = false) { selectedSeat = s; editorPane = 'main'; render(); if (fromTable && !wideScreen.matches) openDialog('player-dialog'); }
function openDialog(id) { const d = $(id); if (!d.open) d.showModal(); scheduleLayout(); }
function closeDialog(id) { if ($(id).open) $(id).close(); }
function adaptLayout() {
  const wide = wideScreen.matches, playerTarget = $(wide ? 'player-dock' : 'player-dialog-body'), contractTarget = $(wide ? 'contract-dock-body' : 'contract-dialog');
  if (wide) { closeDialog('player-dialog'); closeDialog('contract-dialog'); }
  if (editor.parentElement !== playerTarget) playerTarget.append(editor);
  if (contractSection.parentElement !== contractTarget) contractTarget.append(contractSection);
  editor.classList.toggle('dense-editor',editor.clientHeight>0&&editor.clientHeight<330);
  const compact = editor.clientHeight > 0 && editor.clientHeight < 470 || innerHeight <= 660;
  if (editor.classList.contains('compact-editor') !== compact) { editor.classList.toggle('compact-editor', compact); renderEditor(); }
  updateContracts(); renderResultPage();
}
function scheduleLayout() { cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(adaptLayout); }
window.addEventListener('resize',scheduleLayout);
new ResizeObserver(scheduleLayout).observe(editor);
new ResizeObserver(scheduleLayout).observe($('results-dialog'));
// Contracts remain editable in a dock or in the same modal controls.
const contractKey = c => `${c.declarer}:${c.level}:${c.strain}`;
function draftContracts() {return STRAINS.filter(s=>selectedStrains.has(s)).map(strain=>({declarer:$('declarer').value,level:contractLevels[strain],strain}));}
function selectedContracts() {return [...new Map([...contracts,...draftContracts()].map(c=>[contractKey(c),c])).values()];}
function renderContractBuilder() {
  $('strain-levels').innerHTML = STRAINS.map(s => `<div class="strain-level ${selectedStrains.has(s)?'selected':''}"><button data-strain="${s}" class="${redClass(s)}" aria-label="查询${symbol(s)}定约" aria-pressed="${selectedStrains.has(s)}"><b>${contractLevels[s]}</b>${symbol(s)}<span class="strain-check">${selectedStrains.has(s)?'✓':'−'}</span></button></div>`).join('');
  $('advanced-contract-levels').innerHTML = STRAINS.map(s => `<label class="${redClass(s)}">${symbol(s)}<select data-contract-level="${s}" aria-label="${symbol(s)}定约阶数">${[1,2,3,4,5,6,7].map(n=>`<option value="${n}" ${contractLevels[s]===n?'selected':''}>${n}阶</option>`).join('')}</select></label>`).join('');
}
function fillContractLevels(preset) {
  contractLevels = Object.fromEntries(STRAINS.map(s => [s,preset==='game' ? s==='NT'?3:['S','H'].includes(s)?4:5 : Number(preset)]));
  selectedStrains = new Set(STRAINS);renderContractBuilder();updateContracts();changed();
  document.querySelector('.extra-presets').open=false;
  toast('已选定五种花色，可关闭不需要的花色');
}
renderContractBuilder();
$('advanced-contract-levels').addEventListener('change',event=>{const s=event.target.dataset.contractLevel;if(s){contractLevels[s]=Number(event.target.value);renderContractBuilder();updateContracts();changed();}});
$('declarer').addEventListener('change',()=>{updateContracts();changed();});
function contractPageSize() { return wideScreen.matches ? 5 : innerHeight < 480 ? 4 : innerWidth <= 620 ? 4 : 8; }
function updateContracts() {
  const list = selectedContracts(), size = contractPageSize(), pages = Math.max(1,Math.ceil(list.length / size));
  contractPage = Math.max(0,Math.min(contractPage,pages-1));
  $('contract-list').innerHTML = list.slice(contractPage*size,(contractPage+1)*size).map((c,i) => `<div class="contract-chip"><strong class="${redClass(c.strain)}">${c.level}${symbol(c.strain)}</strong><span>${c.declarer}</span><button data-remove="${contractPage*size+i}" aria-label="移除${NAMES[c.declarer]}${c.level}${symbol(c.strain)}">×</button></div>`).join('');
  $('contract-count').textContent = `${list.length} 个`;
  $('contract-page').textContent = `${contractPage+1} / ${pages} · 共 ${list.length} 个`;
  $('contract-pagination').hidden=pages<=1;
  $('contract-prev').disabled = contractPage === 0; $('contract-next').disabled = contractPage === pages-1;
  $('default-note').textContent = contracts.length ? `共 ${list.length} 个定约，含已保留的对比组；当前选择直接参与结算。` : `当前 ${list.length} 个定约直接参与结算；追加对比可保留此组后继续调整。`;
}
function renderResultPage() {
  if (!lastResult) return;
  const wrap = document.querySelector('.result-table-wrap');
  const headerHeight = document.querySelector('.result-table thead').getBoundingClientRect().height || 32;
  const rowHeight = Math.max(innerHeight <= 480 ? 34 : innerHeight <= 660 ? 38 : 46, document.querySelector('#result-rows tr')?.getBoundingClientRect().height || 0);
  const size = Math.max(1, Math.floor((wrap.clientHeight-headerHeight-2)/rowHeight)), pages = Math.max(1,Math.ceil(lastResult.results.length/size));
  resultPage = Math.max(0,Math.min(resultPage,pages-1));
  $('result-rows').innerHTML = lastResult.results.slice(resultPage*size,(resultPage+1)*size).map(c=>`<tr><td class="result-contract"><span class="${redClass(c.strain)}">${c.level}${symbol(c.strain)}</span><small>${NAMES[c.declarer]}庄家</small></td><td><div class="rate-cell"><strong>${pct(c.probability)}</strong><span class="rate-track"><span class="rate-fill" style="width:${c.probability==null?0:c.probability*100}%"></span></span></div></td><td>${c.interval?lastResult.status==='exact'?'精确值':pct(c.interval[0])+' – '+pct(c.interval[1]):'—'}</td><td>${prettyCount(c.successes)} / ${prettyCount(c.samples)}</td></tr>`).join('');
  $('result-pagination').hidden = !lastResult.results.length;
  $('result-page').textContent = `${resultPage+1} / ${pages} · 共 ${lastResult.results.length} 个`;
  $('result-prev').disabled = resultPage === 0; $('result-next').disabled = resultPage === pages-1;
}
$('contract-prev').onclick = () => {contractPage--;updateContracts();};
$('contract-next').onclick = () => {contractPage++;updateContracts();};
$('result-prev').onclick = () => {resultPage--;renderResultPage();};
$('result-next').onclick = () => {resultPage++;renderResultPage();};
$('open-contracts').onclick = () => { if (!wideScreen.matches) openDialog('contract-dialog'); else { $('declarer').focus({preventScroll:true}); toast('在右侧定约设置中选择庄家、阶数和花色'); } };
$('open-results').onclick = () => openDialog('results-dialog');
$('open-settings').onclick = () => openDialog('settings-dialog');
document.querySelectorAll('dialog').forEach(d => d.addEventListener('click',e => { if (e.target === d) {const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();} }));
document.addEventListener('click', event => {
  const b = event.target.closest('button'); if (!b) return;
  if (b.dataset.close) {closeDialog(b.dataset.close);return;}
  if (b.dataset.seat || b.dataset.tableSeat) {selectSeat(b.dataset.seat || b.dataset.tableSeat,!!b.dataset.tableSeat);return;}
  if (b.dataset.editorPane) {editorPane=b.dataset.editorPane;renderEditor();return;}
  if (b.dataset.conditionPane) {conditionPane=b.dataset.conditionPane;renderEditor();return;}
  if (b.dataset.pickerSuit !== undefined) {pickerSuit=Number(b.dataset.pickerSuit);renderEditor();return;}
  const h = hands[selectedSeat];
  if (b.dataset.mode) {h.mode=b.dataset.mode;editorPane='main';changed();render();}
  if (b.dataset.card !== undefined) {
    const c=Number(b.dataset.card),excluded=b.dataset.exclude==='true',set=excluded?h.excluded:h.cards;
    if (set.has(c)) set.delete(c); else {if (!excluded && set.size>=13) return error('已知牌最多选择 13 张，请先取消一张。'); if(excluded && effectiveCards(h).has(c))return error('不能排除本家已持有的牌。'); set.add(c);}
    changed();render();
  }
  if (b.dataset.preset) {fillContractLevels(b.dataset.preset);return;}
  if (b.dataset.strain) {const s=b.dataset.strain;if(selectedStrains.has(s)){selectedStrains.delete(s);contracts=contracts.filter(c=>c.strain!==s);}else selectedStrains.add(s);renderContractBuilder();updateContracts();changed();}
  if (b.dataset.remove !== undefined) {const c=selectedContracts()[Number(b.dataset.remove)];contracts=contracts.filter(v=>contractKey(v)!==contractKey(c));if(draftContracts().some(v=>contractKey(v)===contractKey(c)))selectedStrains.delete(c.strain);renderContractBuilder();updateContracts();changed();scheduleLayout();}
  if (b.dataset.query) {queryMode=b.dataset.query;document.querySelectorAll('[data-query]').forEach(x=>{const v=x.dataset.query===queryMode;x.classList.toggle('selected',v);x.setAttribute('aria-pressed',v);});const exact=queryMode==='exact';for(const id of ['budget','max-samples','min-samples'])$(id).disabled=exact;$('mode-note').textContent=exact?'完成全部合法牌局后才返回真值；大空间可能耗时很长，可随时取消。':'多定约共用整次预算；复杂条件下样本可能少于目标。';changed();}
});
$('condition-fields').addEventListener('input',event=>{const h=hands[selectedSeat],t=event.target;if(t.id==='hcp-min')h.hcp[0]=t.value===''?NaN:Number(t.value);else if(t.id==='hcp-max')h.hcp[1]=t.value===''?NaN:Number(t.value);else if(t.dataset.length!==undefined){h.lengths[Number(t.dataset.length)][Number(t.dataset.bound)]=t.value===''?NaN:Number(t.value);h.shapeOverride=null;$('imported-shapes-note').hidden=true;}changed();renderTable();try{validateHand(h,selectedSeat);error('');}catch(e){error(e.message);}});
$('also-exclude').addEventListener('change',event=>{hands[selectedSeat].alsoExclude=event.target.checked;editorPane=event.target.checked?'exclude':'main';changed();render();});
$('apply-pbn').onclick=()=>{try{const h=hands[selectedSeat],cards=parsePbn($('pbn-input').value);if(h.mode!=='exclude'){if(cards.size>13)throw Error('已知牌最多 13 张。');for(const c of cards){if(effectiveExcluded(h).has(c))throw Error('这手牌包含本家已排除的牌。');for(const s of SEATS)if(s!==selectedSeat&&effectiveCards(hands[s]).has(c))throw Error(`这手牌与${NAMES[s]}的已知牌重复。`);}h.cards=cards;}else{if([...cards].some(c=>effectiveCards(h).has(c)))throw Error('不能排除已知牌。');h.excluded=cards;}changed();render();}catch(e){error(e.message);}};
$('pbn-input').addEventListener('keydown',e=>{if(e.key==='Enter')$('apply-pbn').click();});
$('add-contracts').onclick=()=>{if(!selectedStrains.size)return toast('请至少选择一种定约花色。');contracts=selectedContracts();contractPage=Math.ceil(contracts.length/contractPageSize())-1;updateContracts();changed();toast('已保留当前组，可调整阶数或切换庄家继续对比');scheduleLayout();};
$('default-contracts').onclick=()=>{contracts=[];contractLevels=Object.fromEntries(STRAINS.map(s=>[s,3]));selectedStrains=new Set(STRAINS);$('declarer').value='N';contractPage=0;renderContractBuilder();updateContracts();changed();};
$('reset').onclick=()=>{hands=Object.fromEntries(SEATS.map(s=>[s,emptyHand()]));editorPane='main';changed();render();toast('四家牌局已重置');};
$('load-demo').onclick=()=>{hands=Object.fromEntries(SEATS.map(s=>[s,emptyHand()]));hands.N.mode='full';hands.N.cards=parsePbn('QJ6.K652.J85.T98');selectedSeat='N';editorPane='main';$('default-contracts').click();changed();render();toast('已载入北家全牌，可直接结算五种花色');};
for(const id of ['budget','max-samples','min-samples','threads'])$(id).addEventListener('input',changed);
function downloadJson(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name+'-'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('copy-conditions').onclick=async()=>{try{const text=JSON.stringify(conditions(),null,2);try{await navigator.clipboard.writeText(text);toast('四家限制条件已复制');}catch{$('conditions-json').value=text;$('import-error').hidden=true;openDialog('conditions-dialog');$('conditions-json').select();toast('剪贴板不可用，已选中条件文本，可手动复制');}}catch(e){toast(e.message);}};
$('export-conditions').onclick=()=>{try{downloadJson(conditions(),'bridge-conditions');toast('限制条件已导出为 JSON');}catch(e){toast(e.message);}};
$('open-import').onclick=()=>{$('conditions-json').value='';$('import-error').hidden=true;openDialog('conditions-dialog');};
$('import-conditions').onclick=()=>{try{const text=$('conditions-json').value;if(text.length>131072)throw Error('条件文件不能超过 128 KB。');const next=importHands(JSON.parse(text));hands=next;editorPane='main';changed();render();closeDialog('conditions-dialog');toast('四家限制条件已导入');}catch(e){$('import-error').textContent=e instanceof SyntaxError?'JSON 格式不正确，请检查引号、逗号和括号。':e.message;$('import-error').hidden=false;}};
$('choose-conditions-file').onclick=()=>$('conditions-file').click();
$('conditions-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>131072)throw Error('条件文件不能超过 128 KB。');$('conditions-json').value=await file.text();$('import-error').hidden=true;toast('文件已读取，点击「导入条件」应用');}catch(e){$('import-error').textContent=e.message;$('import-error').hidden=false;}event.target.value='';};
async function api(path, body) {const response=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let data;try{data=await response.json();}catch{throw Error('服务返回异常，请确认本地后端仍在运行。');}if(!response.ok)throw Error(data.error||'请求失败');return data;}
function setBusy(busy){$('results-dot').hidden=!busy&&!lastResult;$('results-dot').classList.toggle('running',busy);$('analyze').disabled=busy;$('analyze').textContent=busy?'正在结算…':'开始结算 ↗';$('cancel').hidden=!busy;$('cancel').disabled=false;$('cancel').textContent='取消解析';$('run-caption').textContent=busy?'可继续编辑，修改将在下次提交时生效':'用计算，验证你的判断';}
function showQueryError(message){$('query-error').textContent=message;$('query-error').hidden=!message;}
function numericInput(id,min,max){const n=Number($(id).value);if(!Number.isInteger(n)||n<min||n>max)throw Error(`${$(id).closest('label').childNodes[0].textContent}须为 ${min}–${max} 的整数。`);return n;}
$('analyze').onclick=async()=>{
  if(activeJob)return;showQueryError('');
  try {
    const request={conditions:conditions(),contracts:selectedContracts(),exact:queryMode==='exact',budget_seconds:numericInput('budget',1,3600),max_samples:numericInput('max-samples',1,10000000),min_samples:numericInput('min-samples',1,10000000),threads:numericInput('threads',1,32)};
    if(!request.contracts.length)throw Error('请至少开启一种查询花色，或添加一个定约。');
    closeDialog('player-dialog');openDialog('results-dialog');setBusy(true);$('result-content').hidden=true;$('download').hidden=true;$('empty-results').hidden=true;$('progress').hidden=false;$('stale-note').hidden=true;$('progress-samples').textContent='已完成 0 副';$('progress-label').textContent=request.exact?'正在全枚举合法牌局…':'正在准备条件发牌…';$('progress-detail').textContent=`${request.contracts.length} 个定约共用样本`; $('progress-bar').removeAttribute('value');
    lastSnapshot=JSON.parse(JSON.stringify(request));startedAt=performance.now();lastResult=null;
    const data=await api('/api/jobs',request);activeJob=data.id;try{sessionStorage.setItem('bridge-active-job',data.id);}catch{}
    timer=setInterval(()=>{const t=(performance.now()-startedAt)/1000;$('progress-clock').textContent=t.toFixed(1)+'s';if(!request.exact)$('progress-bar').value=Math.min(98,t/request.budget_seconds*100);},100);

    poll(data.id);
  } catch(e){showQueryError(e.message);if(!activeJob){setBusy(false);$('progress').hidden=true;if(!lastResult)$('empty-results').hidden=false;}openDialog('results-dialog'); }
};
async function poll(id){
  if(activeJob!==id)return;
  try {const data=await api('/api/jobs/'+id);const p=data.progress||{};$('progress-samples').textContent=`已完成 ${prettyCount(p.samples)} 副`;if(p.strategy)$('progress-label').textContent=lastSnapshot.exact?'正在枚举并计算全部分布…':'正在发牌并计算定约…';if(data.state==='running'){setTimeout(()=>poll(id),500);return;}
    activeJob=null;try{sessionStorage.removeItem('bridge-active-job');}catch{}clearInterval(timer);setBusy(false);$('progress').hidden=true;if(data.result){lastResult=data.result;renderResults(data.result);}else{showQueryError(data.error||'解析未完成');$('empty-results').hidden=false;}
  }catch(e){showQueryError('无法读取进度：'+e.message+' 仍将尝试连接。');setTimeout(()=>poll(id),2000);}
}
$('cancel').onclick=async()=>{if(!activeJob)return;$('cancel').disabled=true;$('cancel').textContent='正在取消…';try{await api('/api/jobs/'+activeJob+'/cancel',{});}catch(e){showQueryError(e.message);$('cancel').disabled=false;}};
function renderResults(r){
  $('empty-results').hidden=true;$('result-content').hidden=false;$('download').hidden=false;$('results-dot').classList.remove('running');
  const labels={exact:'全枚举真值',estimated:'抽样估计',insufficient_samples:'样本未达目标',infeasible:'条件无解',error:'解析出错',cancelled:'已取消'};
  $('result-summary').innerHTML=`<span class="status-pill ${['insufficient_samples','cancelled','infeasible','error'].includes(r.status)?'warn':''}">${labels[r.status]||r.status}</span><span><strong>${prettyCount(r.samples)}</strong> 副已解析</span><span><strong>${r.elapsed_seconds.toFixed(1)}</strong> 秒</span><span>${r.results.length} 个定约${r.status==='exact'?' · 已遍历全部分布':''}</span>`;
  resultPage=0;renderResultPage();$('results-dot').hidden=false;
  const notes={exact:'全部合法分布已逐副计算，以上为所设条件下的精确成功率。',estimated:'以上为条件均匀抽样估计。95% 区间同时覆盖本次选定定约，并适用于按时间停止的查询。',insufficient_samples:'计算预算已用完或达到样本上限，样本少于目标；请结合区间判断，或提高预算继续研究。',infeasible:'没有满足所有条件的完整牌局。请检查各家点力、花色长度及排除牌；这不表示定约成功率为 0%。',error:'后端未能完成计算。',cancelled:'本次解析已取消。已完成的抽样仅供参考；未穷尽的枚举不能称为真值。'};
  $('result-note').textContent=notes[r.status]||'';
  if(r.method==='enumeration'&&r.status!=='exact'&&r.status!=='infeasible')$('result-note').textContent+=' 当前仅完成枚举前缀，显示的是总体概率上下界。';
  if(r.errors)showQueryError(r.errors.join('；'));
  // Changes made during the solve affect the next request, not the current result.
  try{const now={conditions:conditions(),contracts:selectedContracts(),exact:queryMode==='exact',budget_seconds:Number($('budget').value),max_samples:Number($('max-samples').value),min_samples:Number($('min-samples').value),threads:Number($('threads').value)};$('stale-note').hidden=canonical(now)===canonical(lastSnapshot);}catch{$('stale-note').hidden=false;}
  requestAnimationFrame(renderResultPage);
}
$('download').onclick=()=>{if(!lastResult)return;const blob=new Blob([JSON.stringify({request:lastSnapshot,result:lastResult},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='bridge-analysis-'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};

render();adaptLayout();
// Refresh recovery preserves partial predeals and exact shape unions.
async function restoreRunningJob() {
  let identity;
  try {identity=sessionStorage.getItem('bridge-active-job');}catch{}
  if(!identity){try{identity=(await api('/api/jobs')).active_id;}catch{}}
  if(!identity)return;
  try{
    const job=await api('/api/jobs/'+identity);if(activeJob)return;
    lastSnapshot=job.request;queryMode=lastSnapshot.exact?'exact':'estimate';contracts=lastSnapshot.contracts;const first=contracts[0];if(first){$('declarer').value=first.declarer;selectedStrains=new Set(contracts.filter(c=>c.declarer===first.declarer).map(c=>c.strain));for(const s of selectedStrains)contractLevels[s]=contracts.find(c=>c.declarer===first.declarer&&c.strain===s).level;}else selectedStrains=new Set();renderContractBuilder();
    hands=importHands(lastSnapshot.conditions);
    for(const [key,id] of [['budget_seconds','budget'],['max_samples','max-samples'],['min_samples','min-samples'],['threads','threads']])$(id).value=lastSnapshot[key];
    document.querySelector(`[data-query="${queryMode}"]`).click();render();updateContracts();openDialog('results-dialog');
    $('empty-results').hidden=true;
    if(job.state!=='running'){try{sessionStorage.removeItem('bridge-active-job');}catch{}if(job.result){lastResult=job.result;renderResults(job.result);}else showQueryError(job.error||'解析未完成');return;}
    activeJob=identity;setBusy(true);$('progress').hidden=false;startedAt=performance.now()-job.elapsed_seconds*1000;
    timer=setInterval(()=>{$('progress-clock').textContent=((performance.now()-startedAt)/1000).toFixed(1)+'s';},100);
    toast('已恢复正在运行的解析任务');poll(identity);
  }catch{try{sessionStorage.removeItem('bridge-active-job');}catch{}}
}
restoreRunningJob();
