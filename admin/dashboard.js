'use strict';

const $ = id => document.getElementById(id);
const number = new Intl.NumberFormat('vi-VN');
const time = new Intl.DateTimeFormat('vi-VN', {dateStyle:'short',timeStyle:'medium',timeZone:'Asia/Bangkok'});
const clock = new Intl.DateTimeFormat('vi-VN', {hour:'2-digit',minute:'2-digit',timeZone:'Asia/Bangkok'});
const teams = {cat:'Mèo',enemy:'Quái',none:'Chưa chọn'};
const actions = {spawn:'Triệu hồi',fire_cat_cannon:'Pháo Mèo',fire_enemy_cannon:'Pháo Quái',
  change_map:'Đổi bản đồ',finish_battle_cat:'Mèo kết liễu',finish_battle_enemy:'Quái kết liễu',
  heal_base:'Hồi căn cứ',buff_team:'Tăng sức mạnh'};
let page = 0, senderPage = 0, viewer = '', viewerName = '', data, busy = false, dirty = false;
let online = false, timer;

function elt(tag, text = '', className = '') {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}
const fmt = value => value == null ? '—' : number.format(value);
const dateTime = value => time.format(value * 1000);
const username = row => row.username ? '@' + row.username : '—';

function filters() {
  const params = new URLSearchParams({page,sender_page:senderPage});
  for (const id of ['scope','session','date','team','search','sort']) {
    if ($(id).value) params.set(id,$(id).value);
  }
  if (viewer) params.set('viewer',viewer);
  return params;
}

function connection() {
  const states = {connected:'TikTok đã kết nối',connecting:'Đang nối TikTok',offline:'Tài khoản chưa LIVE',
    disconnected:'TikTok mất kết nối',unconfigured:'Chưa cấu hình TikTok'};
  $('connection').textContent = online
    ? data?.mode === 'mock' ? '● Mô phỏng' : '● ' + (states[data?.connection] ?? 'Đang tải')
    : 'Mất kết nối · đang thử lại';
  $('connection').dataset.online = online && (data?.mode === 'mock' || data?.connection === 'connected');
}

async function refresh() {
  dirty = true;
  if (busy) return;
  busy = true;
  try {
    while (dirty) {
      dirty = false;
      const params = filters();
      const response = await fetch('/api/report?' + params);
      if (!response.ok) throw new Error(response.status);
      const next = await response.json();
      if (params.toString() !== filters().toString()) continue;
      clearTimeout(timer);
      data = next;
      render();
    }
  } catch {
    online = false;
    connection();
    clearTimeout(timer);
    timer = setTimeout(refresh,1500);
  } finally {
    busy = false;
  }
}

function metric(label, value, sub = '') {
  const card = elt('div','','metric');
  card.append(elt('span',label),elt('strong',fmt(value)));
  if (sub) card.append(elt('small',sub));
  return card;
}

function bars(id, entries) {
  const container = $(id);
  if (!entries.length) {
    container.replaceChildren(elt('p','Chưa có dữ liệu','empty'));
    return;
  }
  const list = elt('div','','bars');
  const max = Math.max(1,...entries.map(row => row.value));
  for (const entry of entries) {
    const row = elt('div','','bar-row');
    const bar = elt('meter');
    bar.max = max;
    bar.value = entry.value;
    bar.setAttribute('aria-label',entry.label);
    row.append(elt('span',entry.label,entry.team || ''),bar,elt('strong',fmt(entry.value)));
    list.append(row);
  }
  container.replaceChildren(list);
}

function lineChart(id, series, key, color) {
  const canvas = $(id);
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  const scale = devicePixelRatio || 1;
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext('2d');
  ctx.scale(scale,scale);
  const values = series.filter(row => row[key] != null);
  ctx.font = '12px Segoe UI';
  ctx.fillStyle = '#c4b7cf';
  if (!values.length) {
    ctx.fillText('Chưa có dữ liệu',10,26);
    return;
  }
  const max = Math.max(1,...values.map(row => row[key]));
  const minTime = series[0].time;
  const span = series.at(-1).time-minTime;
  const left = Math.max(45,ctx.measureText(fmt(max)).width+9), right = width-14, bottom = height-25;
  for (let i=0;i<3;i++) {
    const y = 10+(bottom-10)*i/2;
    ctx.fillText(fmt(Math.round(max*(1-i/2))),0,y+4);
    ctx.strokeStyle = '#433750';
    ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(right,y); ctx.stroke();
  }
  ctx.fillText(clock.format(minTime*1000),left,height-5);
  if (span) {
    const end = clock.format((minTime+span)*1000);
    ctx.fillText(end,right-ctx.measureText(end).width,height-5);
  }
  const point = row => [span ? left+(right-left)*(row.time-minTime)/span : (left+right)/2,
    bottom-(bottom-10)*row[key]/max];
  ctx.lineWidth = 2;
  ctx.strokeStyle = ctx.fillStyle = color;
  let drawing = false;
  ctx.beginPath();
  for (const row of series) {
    if (row[key] == null) {
      drawing = false;
      continue;
    }
    const [x,y] = point(row);
    if (drawing) ctx.lineTo(x,y);
    else ctx.moveTo(x,y);
    drawing = true;
  }
  ctx.stroke();
  for (const row of values) {
    ctx.beginPath(); ctx.arc(...point(row),2.7,0,Math.PI*2); ctx.fill();
  }
}

function tableRows(id, rows, columns) {
  if (rows.length) {
    $(id).replaceChildren(...rows);
  } else {
    const tr = elt('tr');
    const td = elt('td','Chưa có dữ liệu theo bộ lọc','empty');
    td.colSpan = columns;
    tr.append(td);
    $(id).replaceChildren(tr);
  }
}

function rowOf(values) {
  const tr = elt('tr');
  for (const value of values) {
    const td = elt('td');
    if (value instanceof Node) td.append(value);
    else td.textContent = value;
    tr.append(td);
  }
  return tr;
}

function render() {
  connection();
  const selection = $('session').value;
  $('session').replaceChildren(new Option('Theo phạm vi',''),
    ...data.sessions.map(row => new Option(dateTime(row.started) + ' · ' + (row.username || 'Mock') + ' · ' + row.room.slice(0,10),row.id)));
  $('session').value = data.sessions.some(row => row.id === selection) ? selection : '';
  const summary = data.overview;
  const countSkills = (...keys) => data.skills.filter(row => keys.includes(row.label)).reduce((sum,row) => sum+row.value,0);
  $('overview').replaceChildren(
    metric('Đơn vị quà',summary.quantity),metric('Sự kiện quà',summary.events),
    metric('Diamonds ước tính',summary.diamonds,summary.unknown ? fmt(summary.unknown)+' quà chưa rõ giá trị' : ''),
    metric('Người gửi duy nhất',summary.viewers),metric('Quà hỗ trợ Mèo',summary.cat),metric('Quà hỗ trợ Quái',summary.enemy),
    metric('Lượt bắn pháo',countSkills('fire_cat_cannon','fire_enemy_cannon')),
    metric('Lượt đổi bản đồ',countSkills('change_map')),
    metric('Lượt kết liễu',countSkills('finish_battle_cat','finish_battle_enemy')),
    metric('Đã thực thi',summary.executed),metric('Thất bại / từ chối',summary.failed),metric('Đang chờ',summary.pending));
  lineChart('quantity-chart',data.series,'quantity','#cfb2ff');
  lineChart('diamonds-chart',data.series,'diamonds','#f4c58c');
  const assigned = summary.cat+summary.enemy;
  bars('teams-chart',assigned ? ['cat','enemy'].map(team =>
    ({label:teams[team] + ' · ' + Math.round(summary[team]*100/assigned) + '%',value:summary[team],team})) : []);
  bars('gifts-chart',data.gifts);
  bars('skills-chart',data.skills.map(row => ({...row,label:actions[row.label] ?? row.label})));
  const ranking = data.rankings[$('ranking').value];
  $('ranking-list').replaceChildren(...(ranking.length ? ranking.map((row,i) => {
    const card = elt('div','','rank');
    card.append(elt('span',(i+1)+'. '+(row.username ? username(row) : row.nickname || 'Người xem')),elt('strong',fmt(row.value)));
    return card;
  }) : [elt('p','Chưa có dữ liệu','empty')]));
  tableRows('senders',data.senders.map((row,i) => {
    const button = elt('button','Xem chi tiết');
    button.onclick = () => {
      viewer = row.viewer;
      viewerName = row.username ? username(row) : row.nickname || 'Người xem';
      page = senderPage = 0;
      refresh();
      $('gifts-heading').scrollIntoView({block:'start'});
    };
    const diamonds = elt('span',fmt(row.diamonds));
    if (row.unknown) diamonds.append(elt('small',fmt(row.unknown)+' chưa rõ'));
    return rowOf([senderPage*25+i+1,username(row),row.nickname ?? '—',teams[row.team] ?? '—',fmt(row.quantity),
      diamonds,dateTime(row.last_sent),button]);
  }),8);
  tableRows('events',data.events.map(row => {
    const gift = elt('span',row.name || 'Quà chưa rõ tên');
    if (row.icon) {
      const img = elt('img');
      img.src = row.icon;
      img.alt = '';
      img.loading = 'lazy';
      img.referrerPolicy = 'no-referrer';
      img.className = 'gift-icon';
      img.onerror = () => img.remove();
      gift.prepend(img);
    }
    gift.append(elt('small',row.gift_id));
    const status = elt('span',row.status,'status');
    status.dataset.status = row.status;
    if (row.detail) status.append(elt('small',row.detail));
    const ids = elt('span',row.session.slice(-14));
    ids.title = row.session;
    if (row.round_id) {
      const round = elt('small',row.round_id.slice(0,12));
      round.title = row.round_id;
      ids.append(round);
    }
    return rowOf([dateTime(row.received),row.username ? username(row) : row.nickname || '—',gift,fmt(row.quantity),
      fmt(row.diamond_value),fmt(row.diamonds),teams[row.team] || '—',actions[row.action] || '—',status,ids]);
  }),10);
  $('gifts-heading').textContent = viewer ? 'Lịch sử quà · '+viewerName : 'Lịch sử quà';
  $('clear-viewer').hidden = !viewer;
  $('event-page').textContent = 'Trang '+(page+1);
  $('sender-page').textContent = 'Trang '+(senderPage+1);
  $('event-prev').disabled = page === 0;
  $('sender-prev').disabled = senderPage === 0;
  $('event-next').disabled = data.events.length < 50;
  $('sender-next').disabled = data.senders.length < 25;
  for (const kind of ['senders','gifts','sessions']) $('export-'+kind).href = '/api/export/'+kind+'?'+filters();
}

function changed() { page = senderPage = 0; refresh(); }
$('scope').onchange = () => { $('session').value = ''; $('date').value = ''; changed(); };
for (const id of ['session','date','team','sort']) $(id).onchange = changed;
let searchTimer;
$('search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(changed,220); };
$('ranking').onchange = () => { if (data) render(); };
$('clear-viewer').onclick = () => { viewer = ''; viewerName = ''; changed(); };
$('event-prev').onclick = () => { page--; refresh(); };
$('event-next').onclick = () => { page++; refresh(); };
$('sender-prev').onclick = () => { senderPage--; refresh(); };
$('sender-next').onclick = () => { senderPage++; refresh(); };
window.addEventListener('resize',() => { if (data) render(); });
const updates = new EventSource('/api/updates');
let heartbeatTimeout;
function heartbeat() {
  if (!navigator.onLine) return;
  if (!online) refresh();
  online = true;
  connection();
  clearTimeout(heartbeatTimeout);
  heartbeatTimeout = setTimeout(disconnected,2500);
}
function disconnected() {
  online = false;
  connection();
}
updates.onopen = heartbeat;
updates.onmessage = () => { heartbeat(); if (navigator.onLine) refresh(); };
updates.addEventListener('heartbeat',heartbeat);
updates.onerror = disconnected;
window.addEventListener('offline',disconnected);
window.addEventListener('online',refresh);
refresh();
