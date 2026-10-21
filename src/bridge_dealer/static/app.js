// Compact settings expand next to their table card without leaving the deal.
'use strict';
const $ = id => document.getElementById(id);
const SEATS = ['N', 'E', 'S', 'W'];
const NAMES = {N: '北家', E: '东家', S: '南家', W: '西家'};
const SUITS = ['♠', '♥', '♦', '♣'];
const STRAINS = ['S', 'H', 'D', 'C', 'NT'];
const CONTRACT_ORDER = ['NT', 'S', 'H', 'D', 'C'];
const RANKS = 'AKQJT98765432';
const MODES = {full: '全牌', condition: '条件牌', free: '不约束', exclude: '排除牌'};
const emptyHand = () => ({mode: 'free', cards: new Set(), excluded: new Set(), alsoExclude: false, keepKnown: false, shapeOverride: null, hcp: [0, 37], lengths: [[0, 13], [0, 13], [0, 13], [0, 13]]});
let hands = Object.fromEntries(SEATS.map(s => [s, emptyHand()]));
let selectedSeat = 'N', selectedStrains = new Set(STRAINS), contracts = [];
let contractLevels = Object.fromEntries(STRAINS.map(s => [s,3]));
let queryMode = 'estimate', activeJob = null, lastResult = null, lastSnapshot = null, startedAt = 0, timer = null;
let editorPane = 'main', conditionPane = 'points', pickerSuit = 0, contractPage = 0, resultPage = 0;
let toastTimer, resizeFrame;
let tableMenu = null;
let resultsExpanded = false, queryBusy = false, querySamples = 0, queryError = '';
let handDialogSeat = 'N', handTemplates = [], templateBusy = false, pendingTemplateDelete = null, templateLoad = 0;
let numberWheel = null, numberGesture = null, suppressedNumberClick = null;
const editor = document.querySelector('.editor');
const bridgeTable = document.querySelector('.table-play-area');
const symbol = strain => strain === 'NT' ? 'NT' : SUITS[STRAINS.indexOf(strain)];
const redClass = strain => ['H', 'D'].includes(strain) ? 'red' : '';
const prettyCount = n => Number(n || 0).toLocaleString('zh-CN');
const canonical = value => JSON.stringify(value, (_, v) => v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
const pct = n => n == null ? '—' : (100 * n).toFixed(1) + '%';
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 3500); }
function error(message) { $('editor-error').textContent = message; $('editor-error').hidden = !message; }
function changed() { if (lastResult) $('stale-note').hidden = false; renderMiniResults(); scheduleLayout(); }
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
function singleHandCondition(seat = selectedSeat) {
  const source = Object.fromEntries(SEATS.map(s => [s, s === seat ? hands[s] : emptyHand()]));
  return conditions(source)[seat] || {};
}
function parseSingleHand(text) {
  if (text.length > 131072) throw Error('单家条件不能超过 128 KB。');
  let raw;
  try { raw = JSON.parse(text); } catch { throw Error('JSON 格式不正确，请检查引号、逗号和括号。'); }
  if (raw && typeof raw === 'object' && !Array.isArray(raw) && Object.keys(raw).some(s => SEATS.includes(s))) {
    const keys = Object.keys(raw);
    if (keys.length !== 1 || !SEATS.includes(keys[0])) throw Error('这里只接受单家条件；四家条件请使用牌桌上的全局导入。');
    raw = raw[keys[0]];
  }
  importHands({[handDialogSeat]: raw});
  return raw;
}
function applySingleHand(raw, seat) {
  const candidate = importHands({[seat]: raw})[seat];
  for (const other of SEATS) {
    if (other !== seat && [...effectiveCards(candidate)].some(c => effectiveCards(hands[other]).has(c))) throw Error(`这手牌与${NAMES[other]}的已知牌重复；其他三家条件已保留。`);
  }
  hands = {...hands, [seat]: candidate};
  editorPane = 'main'; changed(); render();
}
function handDialogError(id, message = '') { $(id).textContent = message; $(id).hidden = !message; }
function resetTemplateDelete() { pendingTemplateDelete = null; $('delete-hand-template').textContent = '删除模板'; }
function templateControls(busy = templateBusy) {
  templateBusy = busy;
  for (const id of ['hand-template-select','save-hand-template']) $(id).disabled = busy;
  for (const id of ['update-hand-template','delete-hand-template']) $(id).disabled = busy || !$('hand-template-select').value;
}
function renderHandTemplates(identity = '') {
  const select = $('hand-template-select'); select.replaceChildren(new Option(handTemplates.length ? '选择模板…' : '还没有保存的模板', ''));
  for (const entry of handTemplates) select.add(new Option(entry.name, entry.id));
  select.value = handTemplates.some(t => t.id === identity) ? identity : '';
  templateControls(); resetTemplateDelete();
}
async function loadHandTemplates() {
  const request = ++templateLoad; templateControls(true);
  try { const data = await api('/api/hand-templates'); if (request !== templateLoad) return; handTemplates = data.templates; renderHandTemplates(); }
  catch (e) { if (request === templateLoad) handDialogError('hand-template-error',e.message); }
  finally { if (request === templateLoad) templateControls(false); }
}
function openHandConditions(text = '', focusName = false) {
  handDialogSeat = selectedSeat;
  $('hand-conditions-title').textContent = `${NAMES[handDialogSeat]} · 单家条件与模板`;
  $('hand-conditions-description').textContent = `应用时仅替换${NAMES[handDialogSeat]}的限制条件，保留其他三家。保存模板不会修改牌桌。`;
  $('apply-hand-condition').textContent = `应用到${NAMES[handDialogSeat]} ↗`;
  $('hand-conditions-json').value = text; $('hand-template-name').value = '';
  handDialogError('hand-import-error'); handDialogError('hand-template-error'); renderHandTemplates();
  openDialog('hand-conditions-dialog');
  $(focusName ? 'hand-template-name' : 'hand-conditions-json').focus();
  loadHandTemplates();
}
async function copySingleHand(text, fromEditor = false) {
  try { await navigator.clipboard.writeText(text); toast('单家限制条件已复制，可导入其他玩家或保存为模板'); }
  catch { if (fromEditor) openHandConditions(text); $('hand-conditions-json').focus(); $('hand-conditions-json').select(); toast('剪贴板不可用，已选中单家条件，可手动复制'); }
}
function renderTable() {
  for (const seat of SEATS) {
    const h = hands[seat], excluded = effectiveExcluded(h), known = effectiveCards(h); let content = '', meta = '';
    if (h.mode === 'full') {
      content = '<div class="seat-hand">' + SUITS.map((s, i) => `<span><i class="${i === 1 || i === 2 ? 'red' : ''}">${s}</i><em>${[...h.cards].sort((a,b) => a-b).filter(c => Math.floor(c/13) === i).map(c => RANKS[c%13]).join(' ') || '—'}</em></span>`).join('') + '</div>';
      meta = `${h.cards.size} / 13 张 · ${[...h.cards].reduce((sum,c) => sum + Math.max(4-c%13,0), 0)} HCP`;
    } else if (h.mode === 'condition') {
      const shape = h.lengths.map((r,i) => `${SUITS[i]} ${r[0] === r[1] ? r[0] : r.join('–')}`).join('　');
      content = `<div class="seat-meta"><b>${h.hcp.join('–')} HCP</b><br>${shape}${h.shapeOverride ? '<br>'+h.shapeOverride.length+' 种指定牌型' : ''}</div>`;
      meta = [known.size ? `已知 ${known.size} 张` : '',excluded.size ? `排除 ${excluded.size} 张` : ''].filter(Boolean).join(' · ');
    } else if (excluded.size) {
      content = `<div class="seat-meta"><b>排除 ${excluded.size} 张牌</b><br>${SUITS.map((s,i) => `${s} ${[...excluded].filter(c => Math.floor(c/13) === i).length}`).join('　')}</div>`;
    } else { content = '<div class="seat-placeholder">♠ ♥ ♦ ♣</div>'; }
    const expanded = tableMenu === 'player' && seat === selectedSeat;
    $('seat-' + seat).innerHTML = `<button class="seat-button ${expanded ? 'selected' : ''}" data-table-seat="${seat}" aria-label="编辑${NAMES[seat]}" aria-expanded="${expanded}" aria-controls="player-menu"><div class="seat-head"><strong><b>${seat}</b>${NAMES[seat]}</strong><small>${MODES[h.mode]} <span class="seat-chevron">${expanded ? '⌃' : '⌄'}</span></small></div><div class="seat-content">${content}</div>${meta ? `<div class="seat-foot">${meta}</div>` : ''}</button>`;
  }
}
function renderPicker(target, excluded) {
  const h = hands[selectedSeat], cards = excluded ? h.excluded : h.cards;
  const otherOwned = new Set(SEATS.filter(s => s !== selectedSeat).flatMap(s => [...effectiveCards(hands[s])]));
  const compact = editor.classList.contains('compact-editor');
  $(target).innerHTML = SUITS.map((s, i) => compact && i !== pickerSuit ? '' : `<div class="picker-row"><span class="${i === 1 || i === 2 ? 'red' : ''}">${s}</span>${[...RANKS].map((r, j) => { const c = i*13+j; const unavailable = !excluded && (otherOwned.has(c) || effectiveExcluded(h).has(c)) || excluded && effectiveCards(h).has(c); const chosen = cards.has(c); return `<button class="card ${i === 1 || i === 2 ? 'red' : ''} ${chosen ? excluded ? 'excluded' : 'chosen' : ''}" data-card="${c}" data-exclude="${excluded}" aria-label="${excluded ? '排除' : '持有'}${s}${r}" aria-pressed="${chosen}" ${unavailable && !chosen ? 'disabled' : ''}>${r}</button>`; }).join('')}</div>`).join('');
}
// The sliders, precise fields, wheel and mouse scrubbing all update the same hand.
function rangeMarkup(kind,index,label,max) {
  const unit=kind==='hcp'?'点':'张',red=kind==='length'&&[1,2].includes(index)?'red':'';
  return `<div class="dual-range" data-range-kind="${kind}" data-index="${index}" data-max="${max}"><span class="range-name ${red}">${kind==='length'?label:''}</span><div class="range-track"><i class="range-fill"></i>${[0,1].map(bound=>`<button class="range-thumb" data-bound="${bound}" role="slider" aria-label="${label}${bound?'上限':'下限'}" aria-orientation="horizontal"></button>`).join('')}</div><output class="range-value" aria-label="${label}范围"></output><span class="range-unit">${unit}</span></div>`;
}
function syncConditionRanges() {
  const h=hands[selectedSeat];
  document.querySelectorAll('[data-number-kind]').forEach(t=>{const pair=t.dataset.numberKind==='hcp'?h.hcp:h.lengths[Number(t.dataset.index)];t.value=Number.isFinite(pair[Number(t.dataset.bound)])?pair[Number(t.dataset.bound)]:'';});
  document.querySelectorAll('.dual-range').forEach(el=>{
    const pair=el.dataset.rangeKind==='hcp'?h.hcp:h.lengths[Number(el.dataset.index)],max=Number(el.dataset.max);
    const safe=pair.map((n,i)=>Number.isFinite(n)?Math.max(0,Math.min(max,n)):i*max);
    el.style.setProperty('--lower',safe[0]/max*100+'%');el.style.setProperty('--upper',safe[1]/max*100+'%');
    el.classList.toggle('is-equal',safe[0]===safe[1]);
    el.querySelector('.range-value').textContent=pair.every(Number.isFinite)?pair[0]===pair[1]?String(pair[0]):pair.join('–'):'—';
    el.querySelectorAll('.range-thumb').forEach((t,i)=>{t.setAttribute('aria-valuemin',i?safe[0]:0);t.setAttribute('aria-valuemax',i?max:safe[1]);t.setAttribute('aria-valuenow',safe[i]);t.setAttribute('aria-valuetext',`${safe[i]}${el.dataset.rangeKind==='hcp'?'点':'张'}`);});
  });
}
function conditionEdited() {
  syncConditionRanges();changed();renderTable();
  try{validateHand(hands[selectedSeat],selectedSeat);error('');}catch(e){error(e.message);}
}
function numericSpec(t) {
  if(t.dataset.numberLevel){const s=t.dataset.numberLevel;return {value:contractLevels[s],min:1,max:7,label:`${symbol(s)} 定约阶数`};}
  const h=hands[selectedSeat],kind=t.dataset.numberKind,index=Number(t.dataset.index||0),bound=Number(t.dataset.bound);
  const pair=kind==='hcp'?h.hcp:h.lengths[index],max=kind==='hcp'?37:13,peer=pair[1-bound];
  return {value:pair[bound],min:bound&&Number.isFinite(peer)?Math.ceil(Math.max(0,Math.min(max,peer))):0,max:!bound&&Number.isFinite(peer)?Math.floor(Math.max(0,Math.min(max,peer))):max,label:`${kind==='hcp'?'点力':SUITS[index]+' 张数'}${bound?'上限':'下限'}`};
}
function syncLevelNumbers() {
  document.querySelectorAll('[data-number-level]').forEach(t=>{const s=t.dataset.numberLevel,n=contractLevels[s];if(t.tagName==='INPUT')t.value=n;else{t.querySelector('b').textContent=n;t.setAttribute('aria-label',`调整${symbol(s)}定约阶数，当前${n}阶`);}});
}
function writeNumber(t,value) {
  const spec=numericSpec(t),n=Math.max(spec.min,Math.min(spec.max,Math.round(value)));
  if(!Number.isFinite(n)||n===spec.value)return;
  if(t.dataset.numberLevel){contractLevels[t.dataset.numberLevel]=n;syncLevelNumbers();updateContracts();changed();return;}
  const h=hands[selectedSeat],pair=t.dataset.numberKind==='hcp'?h.hcp:h.lengths[Number(t.dataset.index)];
  pair[Number(t.dataset.bound)]=n;
  if(t.dataset.numberKind==='length'){h.shapeOverride=null;$('imported-shapes-note').hidden=true;}
  conditionEdited();
}
function rangeNumberTarget(el,bound) {
  return document.querySelector(`[data-number-kind="${el.dataset.rangeKind}"]${el.dataset.rangeKind==='length'?`[data-index="${el.dataset.index}"]`:''}[data-bound="${bound}"]`);
}
function closeNumberWheel(restoreFocus=false) {
  if(!numberWheel)return;
  const target=numberWheel.target;numberWheel=null;$('number-wheel').hidden=true;
  if(restoreFocus&&target.isConnected){const focus=target.tagName==='INPUT'?target.closest('details')?.querySelector('summary'):target;focus?.focus({preventScroll:true});}
}
function positionNumberWheel() {
  if(!numberWheel)return;
  if(!numberWheel.target.isConnected||!numberWheel.target.getClientRects().length)return closeNumberWheel();
  const el=$('number-wheel'),a=numberWheel.target.getBoundingClientRect(),b=bridgeTable.getBoundingClientRect(),gap=6;
  const x=Math.max(b.left+8,Math.min(a.left+a.width/2-el.offsetWidth/2,b.right-el.offsetWidth-8));
  const y=a.bottom+gap+el.offsetHeight<=b.bottom-8?a.bottom+gap:a.top-gap-el.offsetHeight;
  el.style.left=x+'px';el.style.top=Math.max(b.top+8,Math.min(y,b.bottom-el.offsetHeight-8))+'px';
}
function selectWheelValue(value,scroll=true) {
  if(!numberWheel)return;
  numberWheel.value=Math.max(numberWheel.min,Math.min(numberWheel.max,Math.round(value)));
  const list=$('number-wheel-options');
  list.querySelectorAll('[data-wheel-value]').forEach(t=>{const selected=Number(t.dataset.wheelValue)===numberWheel.value;t.classList.toggle('selected',selected);t.setAttribute('aria-selected',selected);if(selected)list.setAttribute('aria-activedescendant',t.id);});
  $('number-wheel-value').value=numberWheel.value;
  if(scroll)list.scrollTop=(numberWheel.value-numberWheel.min)*28;
}
function openNumberWheel(target) {
  if(numberWheel?.target===target)return;
  const spec=numericSpec(target);numberWheel={...spec,target,value:Number.isFinite(spec.value)?Math.max(spec.min,Math.min(spec.max,spec.value)):spec.min};
  $('number-wheel-title').textContent=spec.label;
  $('number-wheel-options').innerHTML=Array.from({length:spec.max-spec.min+1},(_,i)=>`<div id="wheel-option-${i}" role="option" data-wheel-value="${spec.min+i}">${spec.min+i}</div>`).join('');
  $('number-wheel').hidden=false;selectWheelValue(numberWheel.value);positionNumberWheel();$('number-wheel-options').focus({preventScroll:true});
}
function finishNumberGesture() {
  const g=numberGesture;if(!g)return;
  clearTimeout(g.timer);numberGesture=null;document.body.classList.remove('number-scrubbing');$('number-scrub-value').hidden=true;
  if(g.target.hasPointerCapture?.(g.id))g.target.releasePointerCapture(g.id);
  if(g.active)suppressedNumberClick={target:g.target,until:performance.now()+600};
}
// A track click picks the nearest endpoint; equal endpoints remain independently reachable.
let rangeGesture=null;
document.addEventListener('pointerdown',event=>{
  const range=event.target.closest('.dual-range');if(!range||event.button!==0||!event.isPrimary)return;
  const track=range.querySelector('.range-track'),thumb=event.target.closest('.range-thumb'),r=track.getBoundingClientRect(),max=Number(range.dataset.max);
  const x=Math.max(0,Math.min(max,(event.clientX-r.left)/r.width*max)),low=numericSpec(rangeNumberTarget(range,0)).value,high=numericSpec(rangeNumberTarget(range,1)).value;
  const bound=thumb?Number(thumb.dataset.bound):Math.abs(x-low)<=Math.abs(x-high)?0:1;
  event.preventDefault();closeNumberWheel();track.setPointerCapture(event.pointerId);range.querySelector(`[data-bound="${bound}"]`).focus({preventScroll:true});
  rangeGesture={track,target:rangeNumberTarget(range,bound),id:event.pointerId,max};
  if(!thumb)writeNumber(rangeGesture.target,x);
});
document.addEventListener('pointermove',event=>{
  if(!rangeGesture||event.pointerId!==rangeGesture.id)return;
  const r=rangeGesture.track.getBoundingClientRect();writeNumber(rangeGesture.target,(event.clientX-r.left)/r.width*rangeGesture.max);
});
function finishRangeGesture(event) {if(rangeGesture&&event.pointerId===rangeGesture.id){const {track,id}=rangeGesture;rangeGesture=null;if(track.hasPointerCapture(id))track.releasePointerCapture(id);}}
document.addEventListener('pointerup',finishRangeGesture);document.addEventListener('pointercancel',finishRangeGesture);
document.addEventListener('keydown',event=>{
  const input=event.target.closest('input[data-number-kind],input[data-number-level]');
  if(input&&event.altKey&&event.key==='ArrowDown'){event.preventDefault();openNumberWheel(input);return;}
  const thumb=event.target.closest('.range-thumb');if(!thumb)return;
  const target=rangeNumberTarget(thumb.closest('.dual-range'),Number(thumb.dataset.bound)),s=numericSpec(target);
  const values={ArrowLeft:s.value-1,ArrowDown:s.value-1,ArrowRight:s.value+1,ArrowUp:s.value+1,PageDown:s.value-5,PageUp:s.value+5,Home:s.min,End:s.max};
  if(event.key in values){event.preventDefault();writeNumber(target,values[event.key]);}
});
document.addEventListener('pointerdown',event=>{
  const t=event.target.closest('[data-number-kind],[data-number-level]');
  if(!t||event.button!==0||!event.isPrimary)return;
  if(numberWheel&&numberWheel.target!==t)closeNumberWheel();
  if(event.pointerType==='touch'||event.pointerType==='pen'||matchMedia('(pointer:coarse)').matches){if(t.tagName==='INPUT'){event.preventDefault();openNumberWheel(t);}return;}
  finishNumberGesture();
  const g={target:t,id:event.pointerId,x:event.clientX,y:event.clientY,value:numericSpec(t).value,active:false};
  g.timer=setTimeout(()=>{if(numberGesture!==g||!t.isConnected)return;closeNumberWheel();g.active=true;g.value=Number.isFinite(g.value)?g.value:numericSpec(t).min;t.setPointerCapture(g.id);document.body.classList.add('number-scrubbing');const r=t.getBoundingClientRect(),out=$('number-scrub-value');out.value=g.value+' ↕';out.style.left=r.left+r.width/2+'px';out.style.top=Math.max(8,r.top-31)+'px';out.hidden=false;},320);
  numberGesture=g;
});
document.addEventListener('pointermove',event=>{
  const g=numberGesture;if(!g||g.id!==event.pointerId)return;
  if(!g.active){if(Math.hypot(event.clientX-g.x,event.clientY-g.y)>6)finishNumberGesture();return;}
  event.preventDefault();writeNumber(g.target,g.value+Math.round((g.y-event.clientY)/8));$('number-scrub-value').value=numericSpec(g.target).value+' ↕';
},{passive:false});
document.addEventListener('pointerup',event=>{if(numberGesture?.id===event.pointerId)finishNumberGesture();});
document.addEventListener('pointercancel',event=>{if(numberGesture?.id===event.pointerId)finishNumberGesture();});
window.addEventListener('blur',()=>{finishNumberGesture();rangeGesture=null;closeNumberWheel();});
document.addEventListener('click',event=>{
  if(suppressedNumberClick&&performance.now()<suppressedNumberClick.until&&suppressedNumberClick.target.contains(event.target)){suppressedNumberClick=null;event.preventDefault();event.stopImmediatePropagation();return;}
  const target=event.target.closest('[data-number-level]');if(target?.tagName==='BUTTON'){event.preventDefault();openNumberWheel(target);}
},true);
$('number-wheel-options').addEventListener('scroll',()=>{if(numberWheel)selectWheelValue(numberWheel.min+$('number-wheel-options').scrollTop/28,false);});
$('number-wheel-options').addEventListener('click',event=>{const t=event.target.closest('[data-wheel-value]');if(t)selectWheelValue(Number(t.dataset.wheelValue));});
$('number-wheel-options').addEventListener('keydown',event=>{
  if(!numberWheel)return;
  const values={ArrowUp:numberWheel.value-1,ArrowDown:numberWheel.value+1,Home:numberWheel.min,End:numberWheel.max,PageUp:numberWheel.value-5,PageDown:numberWheel.value+5};
  if(event.key in values){event.preventDefault();selectWheelValue(values[event.key]);}
  if(event.key==='Enter'){event.preventDefault();$('number-wheel-apply').click();}
});
$('number-wheel-cancel').onclick=()=>closeNumberWheel(true);
$('number-wheel-apply').onclick=()=>{if(!numberWheel)return;const {target,value}=numberWheel;writeNumber(target,value);closeNumberWheel(true);};
for(const id of ['player-menu','contract-menu'])$(id).addEventListener('scroll',()=>{
  if(!numberWheel)return;
  const a=numberWheel.target.getBoundingClientRect(),m=$(id).getBoundingClientRect();
  if(a.bottom<m.top||a.top>m.bottom)closeNumberWheel();else positionNumberWheel();
});
function renderEditor() {
  const h = hands[selectedSeat], hasKnown = h.mode === 'condition' && h.keepKnown, hasExclude = h.mode !== 'exclude' && h.alsoExclude;
  if (editorPane === 'known' && !hasKnown || editorPane === 'exclude' && !hasExclude) editorPane = 'main';
  editor.dataset.conditionPane = conditionPane;
  document.body.dataset.conditionPane = conditionPane;
  $('editor-title').textContent = `${NAMES[selectedSeat]}条件`;
  for (const key of ['seat','mode','editorPane','conditionPane']) document.querySelectorAll(`[data-${key.replace(/[A-Z]/g,m=>'-'+m.toLowerCase())}]`).forEach(b => { if (b.tagName !== 'BUTTON') return; const v = b.dataset[key] === ({seat:selectedSeat,mode:h.mode,editorPane,conditionPane})[key]; b.classList.toggle('selected',v); b.setAttribute('aria-pressed',v); });
  $('editor-subtabs').hidden = !hasKnown && !hasExclude; $('known-tab').hidden = !hasKnown; $('excluded-tab').hidden = !hasExclude;
  $('condition-fields').hidden = h.mode !== 'condition' || editorPane !== 'main'; $('free-message').hidden = h.mode !== 'free' || editorPane !== 'main';
  closeNumberWheel();
  $('hcp-range').innerHTML = rangeMarkup('hcp',0,'点力',37);
  $('length-ranges').innerHTML = SUITS.map((s,i) => rangeMarkup('length',i,s,13)).join('');
  $('length-grid').innerHTML = SUITS.map((s,i) => `<label class="length-field"><span class="${i === 1 || i === 2 ? 'red' : ''}">${s}</span><input type="number" data-number-kind="length" data-index="${i}" data-bound="0" min="0" max="13" aria-label="${s}最少张数"><small>—</small><input type="number" data-number-kind="length" data-index="${i}" data-bound="1" min="0" max="13" aria-label="${s}最多张数"></label>`).join('');
  syncConditionRanges();
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
function selectSeat(s, fromTable = false) {
  if (fromTable && tableMenu === 'player' && selectedSeat === s) return closeTableMenu();
  selectedSeat = s; editorPane = 'main'; renderEditor(); showTableMenu('player');
}
function openDialog(id) { const d = $(id); if (!d.open) d.showModal(); scheduleLayout(); }
function closeDialog(id) { if ($(id).open) $(id).close(); }
function showTableMenu(kind) {
  closeNumberWheel();
  $('hand-tools-menu').open = false;
  tableMenu = kind;
  for (const name of ['player','contract']) $(name+'-menu').hidden = name !== kind;
  if (kind) $(kind+'-menu').scrollTop = 0;
  bridgeTable.dataset.settings = kind || '';
  $('open-contracts').setAttribute('aria-expanded',kind === 'contract');
  if (kind !== 'contract') document.querySelectorAll('#contract-menu details').forEach(d => d.open = false);
  renderTable(); scheduleLayout();
}
function closeTableMenu(restoreFocus = false) {
  const previous = tableMenu; showTableMenu(null);
  if (restoreFocus) (previous === 'player' ? document.querySelector(`#seat-${selectedSeat} button`) : $('open-contracts'))?.focus({preventScroll:true});
}
function positionTableMenu() {
  if (!tableMenu) return;
  const menu = $(tableMenu+'-menu');
  const anchor = tableMenu === 'player' ? document.querySelector(`#seat-${selectedSeat} button`) : $('open-contracts');
  const box = bridgeTable.getBoundingClientRect(), card = anchor.getBoundingClientRect();
  const width = Math.min(282,bridgeTable.clientWidth-16), gap = 6, edge = 8;
  const left = card.left-box.left-bridgeTable.clientLeft, top = card.top-box.top-bridgeTable.clientTop;
  const right = left+card.width, bottom = top+card.height, height = bridgeTable.clientHeight;
  menu.style.width = width+'px'; menu.style.maxHeight = Math.max(80,height-edge*2)+'px';
  const desired = Math.min(menu.scrollHeight+2,tableMenu === 'player' ? 300 : 320,height-edge*2);
  let x = left+card.width/2-width/2, y = bottom+gap, room = height-y-edge, placement = 'below';
  if (tableMenu === 'player' && selectedSeat === 'W' && bridgeTable.clientWidth-right-gap-edge >= width) {
    x = right+gap; y = top; room = height-y-edge; placement = 'right';
  } else if (tableMenu === 'player' && selectedSeat === 'E' && left-gap-edge >= width) {
    x = left-gap-width; y = top; room = height-y-edge; placement = 'left';
  } else if (top-gap-edge > room && !(tableMenu === 'player' && selectedSeat === 'N')) {
    room = top-gap-edge; y = top-gap-Math.min(desired,room); placement = 'above';
  }
  const available = tableMenu === 'player' ? Math.min(desired,room) : room < Math.min(170,desired) ? desired : Math.min(desired,room);
  menu.style.maxHeight = Math.max(80,available)+'px';
  const actual = menu.getBoundingClientRect().height;
  x = Math.max(edge,Math.min(x,bridgeTable.clientWidth-width-edge));
  y = Math.max(edge,Math.min(y,height-actual-edge));
  menu.style.left = x+'px'; menu.style.top = y+'px'; menu.dataset.placement = placement;
}
function adaptLayout() {
  if (!editor.classList.contains('compact-editor')) { editor.classList.add('compact-editor'); renderEditor(); }
  updateContracts(); renderResultPage(); positionTableMenu(); positionNumberWheel();
}
function scheduleLayout() { cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(adaptLayout); }
window.addEventListener('resize',scheduleLayout);
new ResizeObserver(scheduleLayout).observe(bridgeTable);
for (const id of ['player-menu','contract-menu']) new ResizeObserver(scheduleLayout).observe($(id));
new ResizeObserver(scheduleLayout).observe($('results-widget'));
// Presets and advanced levels share the same inline contract panel.
const contractKey = c => `${c.declarer}:${c.level}:${c.strain}`;
function draftContracts() {return STRAINS.filter(s=>selectedStrains.has(s)).map(strain=>({declarer:$('declarer').value,level:contractLevels[strain],strain}));}
function selectedContracts() {return [...new Map([...contracts,...draftContracts()].map(c=>[contractKey(c),c])).values()];}
function renderContractBuilder() {
  closeNumberWheel();
  $('strain-levels').innerHTML = STRAINS.map(s => `<div class="strain-level ${selectedStrains.has(s)?'selected':''}"><button class="level-number" data-number-level="${s}" aria-label="调整${symbol(s)}定约阶数" aria-haspopup="listbox" title="点按选择阶数；电脑长按上下拖动"><b>${contractLevels[s]}</b><small>阶</small></button><button data-strain="${s}" class="${redClass(s)}" aria-label="查询${symbol(s)}定约" aria-pressed="${selectedStrains.has(s)}">${symbol(s)}<span class="strain-check">${selectedStrains.has(s)?'✓':'−'}</span></button></div>`).join('');
  $('advanced-contract-levels').innerHTML = STRAINS.map(s => `<label class="${redClass(s)}">${symbol(s)}<input type="number" data-number-level="${s}" min="1" max="7" value="${contractLevels[s]}" aria-label="${symbol(s)}定约阶数"></label>`).join('');
  syncLevelNumbers();
}
function fillContractLevels(preset) {
  contractLevels = Object.fromEntries(STRAINS.map(s => [s,preset==='game' ? s==='NT'?3:['S','H'].includes(s)?4:5 : Number(preset)]));
  selectedStrains = new Set(STRAINS);renderContractBuilder();updateContracts();changed();
  document.querySelector('.extra-presets').open=false;
  toast('已选定五种花色，可关闭不需要的花色');
}
renderContractBuilder();
$('advanced-contract-levels').addEventListener('input',event=>{const t=event.target,n=Number(t.value);if(t.dataset.numberLevel&&t.value!==''&&Number.isInteger(n)&&n>=1&&n<=7)writeNumber(t,n);});
$('advanced-contract-levels').addEventListener('change',syncLevelNumbers);
$('declarer').addEventListener('change',()=>{updateContracts();changed();});
function contractPageSize() { return 5; }
function updateContracts() {
  const list = selectedContracts(), size = contractPageSize(), pages = Math.max(1,Math.ceil(list.length / size));
  contractPage = Math.max(0,Math.min(contractPage,pages-1));
  $('contract-list').innerHTML = list.slice(contractPage*size,(contractPage+1)*size).map((c,i) => `<div class="contract-chip"><strong class="${redClass(c.strain)}">${c.level}${symbol(c.strain)}</strong><span>${c.declarer}</span><button data-remove="${contractPage*size+i}" aria-label="移除${NAMES[c.declarer]}${c.level}${symbol(c.strain)}">×</button></div>`).join('');
  $('contract-count').textContent = `${list.length} 个`;
  $('contract-page').textContent = `${contractPage+1} / ${pages} · 共 ${list.length} 个`;
  $('contract-pagination').hidden=pages<=1;
  $('contract-prev').disabled = contractPage === 0; $('contract-next').disabled = contractPage === pages-1;
  $('default-note').textContent = contracts.length ? `共 ${list.length} 个定约，含已保留的对比组；当前选择直接参与结算。` : `当前 ${list.length} 个定约直接参与结算；追加对比可保留此组后继续调整。`;
  renderMiniResults();
}
function setResultsExpanded(expanded, restoreFocus = false) {
  resultsExpanded = expanded;
  $('results-widget').dataset.expanded = String(expanded);
  $('result-details').hidden = !expanded; $('result-mini').hidden = expanded;
  for (const id of ['open-results','mini-results']) $(id).setAttribute('aria-expanded',String(expanded));
  $('open-results').setAttribute('aria-label',expanded ? '收起解析详情' : '展开解析详情');
  $('result-toggle').textContent = expanded ? '⌃' : '⌄';
  if (restoreFocus) $('open-results').focus({preventScroll:true});
  scheduleLayout();
}
function renderMiniResults() {
  const r = lastResult;
  const prefix = r?.method === 'enumeration' && !['exact','infeasible'].includes(r.status);
  // An unfinished census has bounds, not a known population success rate.
  const candidates = (r?.results || []).filter(c => c.samples > 0 && (Number.isFinite(c.probability) || prefix && c.interval)).sort(contractPriority);
  const score = c => Number.isFinite(c.probability) ? c.probability : c.successes / c.samples;
  const best = candidates.reduce((top,c) => !top || score(c) > score(top) ? c : top,null);
  renderContractIcon(best);
  const shortPercent = value => (value*100).toFixed(1).replace(/\.0$/,'');
  if (best) {
    const value = prefix ? `${shortPercent(best.interval[0])}–${shortPercent(best.interval[1])}%` : `${shortPercent(best.probability)}%`;
    $('mini-result-list').innerHTML = `<span class="mini-result"><span class="mini-contract"><b class="${redClass(best.strain)}">${best.level}${symbol(best.strain)}</b><small>${NAMES[best.declarer]}</small></span><strong class="${prefix?'probability-bound':''}">${value}</strong></span>`;
  } else {
    $('mini-result-list').innerHTML = `<span class="mini-no-contracts">${queryBusy ? '等待首批结果…' : r ? '暂无有效胜率' : selectedContracts().length ? '待计算' : '请选择查询定约'}</span>`;
  }
  const labels = {running:prefix?'枚举中':'估计中',exact:'精确值',estimated:'抽样估计',insufficient_samples:'样本不足',infeasible:'条件无解',error:'解析出错',cancelled:'已取消'};
  $('result-state').textContent = queryError ? '解析异常' : queryBusy ? lastSnapshot?.exact?'枚举中':'计算中' : r ? labels[r.status] || r.status : '待计算';
  $('results-widget').classList.toggle('is-running',queryBusy);
  $('mini-compute-state').textContent = queryBusy ? '后台计算中' : r?.status === 'cancelled' ? '计算已取消' : r ? '计算已结束' : queryError ? '计算已停止' : '尚未开始';
  $('mini-compute-samples').textContent = `已解析 ${prettyCount(queryBusy ? querySamples : r?.samples ?? querySamples)} 副`;
  const stale = r && !$('stale-note').hidden;
  const note = queryError ? '解析异常 · 展开查看' : stale ? prefix?'条件已改 · 概率上下界':'条件已改 · 上次结果' : prefix ? '已解析排名 · 概率上下界' : '';
  $('mini-result-note').textContent = note; $('mini-result-note').hidden = !note;
}
function contractPriority(a,b) { return CONTRACT_ORDER.indexOf(a.strain) - CONTRACT_ORDER.indexOf(b.strain); }
function renderContractIcon(best) {
  // Edited targets preview the next query; only matching results choose the winner.
  const current = best && $('stale-note').hidden ? best : selectedContracts().sort(contractPriority)[0];
  const declarer = current?.declarer || $('declarer').value || 'N';
  const label = current ? `${current.level}${symbol(current.strain)}` : '—';
  const button = $('open-contracts'), value = $('compass-contract');
  value.textContent = label;
  value.classList.toggle('red',!!current && !!redClass(current.strain));
  button.dataset.declarer = declarer;
  button.setAttribute('aria-label',`展开定约设置，${NAMES[declarer]}${current ? label : '，未选择定约'}`);
  button.title = `${NAMES[declarer]}${current ? label : '，未选择定约'} · 点击设置定约`;
}
function renderResultPage() {
  if (!lastResult) return;
  const size = 5, pages = Math.max(1,Math.ceil(lastResult.results.length/size));
  resultPage = Math.max(0,Math.min(resultPage,pages-1));
  $('result-rows').innerHTML = lastResult.results.slice(resultPage*size,(resultPage+1)*size).map(c=>`<tr><td class="result-contract"><span class="${redClass(c.strain)}">${c.level}${symbol(c.strain)}</span><small>${NAMES[c.declarer]}庄家</small></td><td><div class="rate-cell"><strong>${pct(c.probability)}</strong><span class="rate-track"><span class="rate-fill" style="width:${c.probability==null?0:c.probability*100}%"></span></span></div></td><td>${c.interval?lastResult.status==='exact'?'精确值':pct(c.interval[0])+' – '+pct(c.interval[1]):'—'}</td><td>${prettyCount(c.successes)} / ${prettyCount(c.samples)}</td></tr>`).join('');
  $('result-pagination').hidden = pages <= 1;
  $('result-page').textContent = `${resultPage+1} / ${pages} · 共 ${lastResult.results.length} 个`;
  $('result-prev').disabled = resultPage === 0; $('result-next').disabled = resultPage === pages-1;
}
$('contract-prev').onclick = () => {contractPage--;updateContracts();};
$('contract-next').onclick = () => {contractPage++;updateContracts();};
$('result-prev').onclick = () => {resultPage--;renderResultPage();};
$('result-next').onclick = () => {resultPage++;renderResultPage();};
$('open-contracts').onclick = () => tableMenu === 'contract' ? closeTableMenu() : showTableMenu('contract');
$('open-results').onclick = $('mini-results').onclick = () => setResultsExpanded(!resultsExpanded);
$('table-tools-menu').addEventListener('click',event => {
  if (event.target.closest('button')) {
    $('table-tools-menu').open = false;
    if ($('table-tools-menu').contains(document.activeElement)) $('table-tools-menu').querySelector('summary').focus({preventScroll:true});
  }
});
$('hand-tools-menu').addEventListener('toggle',scheduleLayout);
$('hand-tools-menu').addEventListener('click',event => {
  if (event.target.closest('.hand-tools button')) {
    $('hand-tools-menu').open = false;
    if ($('hand-tools-menu').contains(document.activeElement)) $('hand-tools-menu').querySelector('summary').focus({preventScroll:true});
  }
});
document.addEventListener('pointerdown',event => {
  if (numberWheel&&!event.target.closest('#number-wheel,[data-number-kind],[data-number-level]'))closeNumberWheel();
  if (!event.target.closest('#table-tools-menu')) $('table-tools-menu').open = false;
  if (!event.target.closest('#hand-tools-menu')) $('hand-tools-menu').open = false;
  if (tableMenu && !event.target.closest('.table-menu,.seat-button,#open-contracts,#results-widget,#number-wheel,dialog')) closeTableMenu();
});
document.addEventListener('keydown',event => {
  if(event.key==='Escape'&&numberWheel){event.preventDefault();closeNumberWheel(true);}
  else if (event.key === 'Escape' && $('table-tools-menu').open && !document.querySelector('dialog[open]')) { event.preventDefault(); $('table-tools-menu').open = false; $('table-tools-menu').querySelector('summary').focus({preventScroll:true}); }
  else if (event.key === 'Escape' && $('hand-tools-menu').open && !document.querySelector('dialog[open]')) { event.preventDefault(); $('hand-tools-menu').open = false; $('hand-tools-menu').querySelector('summary').focus({preventScroll:true}); }
  else if (event.key === 'Escape' && resultsExpanded && $('compute-menu').open && !document.querySelector('dialog[open]')) { event.preventDefault(); $('compute-menu').open = false; $('open-settings').focus({preventScroll:true}); }
  else if (event.key === 'Escape' && tableMenu && !document.querySelector('dialog[open]')) { event.preventDefault(); closeTableMenu(true); }
  else if (event.key === 'Escape' && resultsExpanded && !document.querySelector('dialog[open]')) { event.preventDefault(); setResultsExpanded(false,true); }
});
document.querySelectorAll('dialog').forEach(d => d.addEventListener('click',e => { if (e.target === d) {const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();} }));
document.addEventListener('click', event => {
  const b = event.target.closest('button'); if (!b) return;
  if (b.dataset.close) {closeDialog(b.dataset.close);return;}
  if (b.hasAttribute('data-close-table')) {closeTableMenu(true);return;}
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
  if (b.dataset.query) {queryMode=b.dataset.query;document.querySelectorAll('[data-query]').forEach(x=>{const v=x.dataset.query===queryMode;x.classList.toggle('selected',v);x.setAttribute('aria-pressed',v);});const exact=queryMode==='exact';for(const id of ['budget','max-samples'])$(id).disabled=exact;$('mode-note').textContent=exact?'达到目标后每秒展示枚举进展；穷尽才是真值，不限时、可取消。':'达到目标即展示，每秒更新；后台继续至样本上限或时间预算。';changed();}
});
$('condition-fields').addEventListener('input',event=>{
  const h=hands[selectedSeat],t=event.target;if(!t.dataset.numberKind)return;
  const pair=t.dataset.numberKind==='hcp'?h.hcp:h.lengths[Number(t.dataset.index)],bound=Number(t.dataset.bound),value=t.value===''?NaN:Number(t.value);
  if(Object.is(pair[bound],value))return;
  pair[bound]=value;if(t.dataset.numberKind==='length'){h.shapeOverride=null;$('imported-shapes-note').hidden=true;}
  conditionEdited();
});
document.querySelectorAll('.range-more').forEach(d=>d.addEventListener('toggle',()=>{
  scheduleLayout();
  if(d.open)requestAnimationFrame(()=>{const menu=$('player-menu'),bottom=d.getBoundingClientRect().bottom,edge=menu.getBoundingClientRect().bottom-8;if(bottom>edge)menu.scrollTop+=bottom-edge;});
}));
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
$('copy-hand').onclick = async () => { try { await copySingleHand(JSON.stringify(singleHandCondition(),null,2),true); } catch (e) { error(e.message); } };
$('import-hand').onclick = () => openHandConditions();
$('open-hand-templates').onclick = () => {
  let text = '', message = '';
  try { text = JSON.stringify(singleHandCondition(),null,2); } catch (e) { message = e.message + ' 可选择已保存模板替换本家。'; }
  openHandConditions(text,true); handDialogError('hand-import-error',message);
};
$('copy-hand-json').onclick = async () => {
  try { const text = JSON.stringify(parseSingleHand($('hand-conditions-json').value),null,2); $('hand-conditions-json').value = text; await copySingleHand(text); handDialogError('hand-import-error'); }
  catch (e) { handDialogError('hand-import-error',e.message); }
};
$('apply-hand-condition').onclick = () => {
  try { applySingleHand(parseSingleHand($('hand-conditions-json').value),handDialogSeat); closeDialog('hand-conditions-dialog'); toast(`已替换${NAMES[handDialogSeat]}条件，其他三家保持原样`); }
  catch (e) { handDialogError('hand-import-error',e.message); }
};
$('hand-template-select').onchange = () => {
  const entry = handTemplates.find(t => t.id === $('hand-template-select').value);
  $('hand-template-name').value = entry ? entry.name : '';
  if (entry) { $('hand-conditions-json').value = JSON.stringify(entry.condition,null,2); handDialogError('hand-import-error'); }
  handDialogError('hand-template-error'); templateControls(); resetTemplateDelete();
};
for (const id of ['hand-template-name','hand-conditions-json']) $(id).addEventListener('input',() => { resetTemplateDelete(); handDialogError(id === 'hand-template-name' ? 'hand-template-error' : 'hand-import-error'); });
async function saveHandTemplate(update) {
  if (templateBusy) return;
  try {
    const condition = parseSingleHand($('hand-conditions-json').value), name = $('hand-template-name').value.trim();
    if (!name) throw Error('请先填写模板名称。');
    const identity = update ? $('hand-template-select').value : '';
    if (update && !identity) throw Error('请先选择需要更新的模板。');
    templateControls(true); handDialogError('hand-template-error');
    const data = await api('/api/hand-templates' + (identity ? '/' + identity : ''),{name,condition});
    const entry = data.template;
    handTemplates = update ? handTemplates.map(t => t.id === entry.id ? entry : t) : [...handTemplates,entry];
    renderHandTemplates(entry.id); $('hand-template-name').value = entry.name;
    $('hand-conditions-json').value = JSON.stringify(entry.condition,null,2);
    handDialogError('hand-import-error'); toast(update ? '模板名称与条件已更新并保存到本机' : '新模板已保存到本机');
  } catch (e) { handDialogError('hand-template-error',e.message); }
  finally { templateControls(false); }
}
$('save-hand-template').onclick = () => saveHandTemplate(false);
$('update-hand-template').onclick = () => saveHandTemplate(true);
$('delete-hand-template').onclick = async () => {
  const identity = $('hand-template-select').value;
  if (!identity || templateBusy) return;
  if (pendingTemplateDelete !== identity) { pendingTemplateDelete = identity; $('delete-hand-template').textContent = '确认删除'; return; }
  templateControls(true); handDialogError('hand-template-error');
  try { await api('/api/hand-templates/' + identity + '/delete',{}); handTemplates = handTemplates.filter(t => t.id !== identity); renderHandTemplates(); $('hand-template-name').value = ''; toast('本地模板已删除，牌桌条件已保留'); }
  catch (e) { handDialogError('hand-template-error',e.message); }
  finally { templateControls(false); resetTemplateDelete(); }
};
async function api(path, body) {const response=await fetch(path,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let data;try{data=await response.json();}catch{throw Error('服务返回异常，请确认本地后端仍在运行。');}if(!response.ok)throw Error(data.error||'请求失败');return data;}
function setBusy(busy){queryBusy=busy;$('analyze').disabled=busy;$('analyze').textContent=busy?'正在结算…':'开始结算 ↗';$('cancel').hidden=!busy;$('cancel').disabled=false;$('cancel').textContent='取消解析';$('run-caption').textContent=busy?'可继续编辑，修改将在下次提交时生效':'用计算，验证你的判断';renderMiniResults();}
function showQueryError(message){queryError=message;$('query-error').textContent=message;$('query-error').hidden=!message;renderMiniResults();}
function numericInput(id,min,max){const n=Number($(id).value);if(!Number.isInteger(n)||n<min||n>max)throw Error(`${$(id).closest('label').childNodes[0].textContent}须为 ${min}–${max} 的整数。`);return n;}
$('analyze').onclick=async()=>{
  if(activeJob)return;showQueryError('');
  try {
    const request={conditions:conditions(),contracts:selectedContracts(),exact:queryMode==='exact',budget_seconds:numericInput('budget',1,3600),max_samples:numericInput('max-samples',1,10000000),min_samples:numericInput('min-samples',1,10000000),threads:numericInput('threads',1,32)};
    if(!request.contracts.length)throw Error('请至少开启一种查询花色，或添加一个定约。');
    lastSnapshot=JSON.parse(JSON.stringify(request));lastResult=null;querySamples=0;setBusy(true);$('result-content').hidden=true;$('download').hidden=true;$('empty-results').hidden=true;$('progress').hidden=false;$('stale-note').hidden=true;$('progress-samples').textContent='已完成 0 副';$('progress-label').textContent=request.exact?'正在全枚举合法牌局…':'正在准备条件发牌…';$('progress-detail').textContent=`${request.contracts.length} 个定约共用样本`; $('progress-bar').removeAttribute('value');
    startedAt=performance.now();resultPage=0;renderMiniResults();
    const data=await api('/api/jobs',request);activeJob=data.id;try{sessionStorage.setItem('bridge-active-job',data.id);}catch{}
    timer=setInterval(()=>{const t=(performance.now()-startedAt)/1000;$('progress-clock').textContent=t.toFixed(1)+'s';if(!request.exact)$('progress-bar').value=Math.min(98,t/request.budget_seconds*100);},100);

    poll(data.id);
  } catch(e){showQueryError(e.message);if(!activeJob){setBusy(false);$('progress').hidden=true;if(!lastResult)$('empty-results').hidden=false;}renderMiniResults(); }
};
async function poll(id){
  if(activeJob!==id)return;
  try {const data=await api('/api/jobs/'+id);if(activeJob!==id)return;const p=data.progress||{};querySamples=p.samples||0;showQueryError('');$('progress-samples').textContent=`已完成 ${prettyCount(p.samples)} 副`;if(p.strategy)$('progress-label').textContent=lastSnapshot.exact?'正在枚举并计算全部分布…':'正在发牌并计算定约…';if(data.state==='running'){
      if(data.result){const updated=!lastResult||lastResult.elapsed_seconds!==data.result.elapsed_seconds;lastResult=data.result;if(updated)renderResults(data.result);$('progress-label').textContent=lastSnapshot.exact?'后台继续全枚举 · 每秒更新':'后台继续计算 · 每秒更新';}
      setTimeout(()=>poll(id),1000);return;
    }
    activeJob=null;try{sessionStorage.removeItem('bridge-active-job');}catch{}clearInterval(timer);setBusy(false);$('progress').hidden=true;if(data.result){lastResult=data.result;renderResults(data.result);}else{showQueryError(data.error||'解析未完成');$('empty-results').hidden=false;}
  }catch(e){showQueryError('无法读取进度：'+e.message+' 仍将尝试连接。');setTimeout(()=>poll(id),2000);}
}
$('cancel').onclick=async()=>{if(!activeJob)return;$('cancel').disabled=true;$('cancel').textContent='正在取消…';try{await api('/api/jobs/'+activeJob+'/cancel',{});}catch(e){showQueryError(e.message);$('cancel').disabled=false;}};
function renderResults(r){
  $('empty-results').hidden=true;$('result-content').hidden=false;$('download').hidden=false;
  const labels={running:r.method==='enumeration'?'枚举进行中':'抽样估计 · 计算中',exact:'全枚举真值',estimated:'抽样估计',insufficient_samples:'样本未达目标',infeasible:'条件无解',error:'解析出错',cancelled:'已取消'};
  $('result-summary').innerHTML=`<span class="status-pill ${['insufficient_samples','cancelled','infeasible','error'].includes(r.status)?'warn':''}">${labels[r.status]||r.status}</span><span><strong>${prettyCount(r.samples)}</strong> 副已解析</span><span><strong>${r.elapsed_seconds.toFixed(1)}</strong> 秒</span><span>${r.results.length} 个定约${r.status==='exact'?' · 已遍历全部分布':''}</span>`;
  renderResultPage();
  const notes={exact:'全部合法分布已逐副计算，以上为所设条件下的精确成功率。',estimated:'以上为条件均匀抽样估计。95% 区间同时覆盖本次选定定约，并适用于按时间停止的查询。',insufficient_samples:'计算预算已用完或达到样本上限，样本少于目标；请结合区间判断，或提高预算继续研究。',infeasible:'没有满足所有条件的完整牌局。请检查各家点力、花色长度及排除牌；这不表示定约成功率为 0%。',error:'后端未能完成计算。',cancelled:'本次解析已取消。已完成的抽样仅供参考；未穷尽的枚举不能称为真值。'};
  $('result-note').textContent=notes[r.status]||'';
  if(r.running)$('result-note').textContent=r.method==='enumeration'?'已达到目标样本，后台继续枚举，每秒更新；可随时取消。':'已达到目标样本，后台继续计算，每秒更新，直到样本上限或时间预算。95% 区间同时覆盖所选定约及各次更新。';
  if(r.method==='enumeration'&&r.status!=='exact'&&r.status!=='infeasible')$('result-note').textContent+=' 当前仅完成枚举前缀，显示的是总体概率上下界。';
  if(r.errors)showQueryError(r.errors.join('；'));
  // Changes made during the solve affect the next request, not the current result.
  try{const now={conditions:conditions(),contracts:selectedContracts(),exact:queryMode==='exact',budget_seconds:Number($('budget').value),max_samples:Number($('max-samples').value),min_samples:Number($('min-samples').value),threads:Number($('threads').value)};$('stale-note').hidden=canonical(now)===canonical(lastSnapshot);}catch{$('stale-note').hidden=false;}
  renderMiniResults();requestAnimationFrame(renderResultPage);
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
    document.querySelector(`[data-query="${queryMode}"]`).click();render();updateContracts();
    $('empty-results').hidden=true;
    if(job.state!=='running'){try{sessionStorage.removeItem('bridge-active-job');}catch{}if(job.result){lastResult=job.result;renderResults(job.result);}else showQueryError(job.error||'解析未完成');return;}
    activeJob=identity;querySamples=job.progress?.samples||0;setBusy(true);$('progress').hidden=false;startedAt=performance.now()-job.elapsed_seconds*1000;
    if(job.result){lastResult=job.result;renderResults(job.result);}
    $('progress-bar').removeAttribute('value');
    timer=setInterval(()=>{$('progress-clock').textContent=((performance.now()-startedAt)/1000).toFixed(1)+'s';},100);
    toast('已恢复正在运行的解析任务');poll(identity);
  }catch{try{sessionStorage.removeItem('bridge-active-job');}catch{}}
}
restoreRunningJob();
