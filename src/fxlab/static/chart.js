'use strict';
const svg = document.querySelector('#chart');
const statusLine = document.querySelector('#chart-status');
const select = document.querySelector('#period');
const series = document.querySelector('#series');
const ns = 'http://www.w3.org/2000/svg';
let points = [], visible = [], requestNumber = 0;
function el(tag, attrs, text) { const node = document.createElementNS(ns, tag); for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,v); if (text !== undefined) node.textContent = text; svg.append(node); return node; }
function draw() {
  svg.replaceChildren();
  const days = Number(select.value), last = points.at(-1);
  if (!last) { statusLine.textContent = 'Данные ещё не загружены. Выполните backfill на сервере.'; return; }
  const cutoff = Date.parse(last.date) - days * 86400000;
  visible = points.filter(p => !days || Date.parse(p.date) >= cutoff);
  const values = visible.filter(p=>p.complete!==false).map(p => p.value);
  if(!values.length){ statusLine.textContent='В выбранном периоде нет полных наблюдений.'; return; }
  const low = Math.min(...values) - .003, high = Math.max(...values) + .003;
  const width = Math.max(270, svg.getBoundingClientRect().width), mobile = width < 600;
  const height = mobile ? 235 : 300, left = 58, right = width - 15, bottom = height - 38;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const firstTime = Date.parse(visible[0].date), span = Math.max(1, Date.parse(last.date) - firstTime);
  const x = p => left + (Date.parse(p.date)-firstTime) / span * (right-left), y = value => bottom - (value-low)/(high-low)*(bottom-20);
  el('title', {}, document.querySelector('#series-description').textContent);
  const ticks = mobile ? 3 : 5;
  for (let i=0;i<ticks;i++) { const value = low+(high-low)*i/(ticks-1), pos = y(value); el('line',{x1:left,x2:right,y1:pos,y2:pos,class:'gridline'}); el('text',{x:left-8,y:pos+4,'text-anchor':'end'},value.toFixed(4)); }
  let line='', previous=null;
  for (const p of visible) { if(p.complete===false){previous=null;continue;} const gap=series.value==='h1' && previous && Date.parse(p.date)-Date.parse(previous.date)>3600000; line+=(previous&&!gap?' L':' M')+x(p).toFixed(2)+','+y(p.value).toFixed(2);previous=p; }
  el('path',{d:line,class:'series-line'});
  for (const index of mobile ? [0,visible.length-1] : [0,Math.floor((visible.length-1)/2),visible.length-1]) {const p=visible[index];el('text',{x:x(p),y:height-8,'text-anchor':index===0?'start':index===visible.length-1?'end':'middle'},p.date.slice(0,10));}
  const cursor=el('line',{x1:0,x2:0,y1:20,y2:bottom,class:'cursor',visibility:'hidden'}), dot=el('circle',{r:4,class:'chart-dot',visibility:'hidden'});
  svg.onpointermove = e => {const rect=svg.getBoundingClientRect(), px=(e.clientX-rect.left)/rect.width*width;const p=visible.reduce((best,p)=>Math.abs(x(p)-px)<Math.abs(x(best)-px)?p:best,visible[0]);cursor.setAttribute('x1',x(p));cursor.setAttribute('x2',x(p));cursor.setAttribute('visibility','visible');dot.setAttribute('cx',x(p));dot.setAttribute('cy',y(p.value));dot.setAttribute('visibility',p.complete===false?'hidden':'visible');statusLine.textContent=p.date+' · '+p.value.toFixed(4)+' USD / EUR'+(p.complete===false?' · неполная сессия':'');};
  statusLine.textContent = last.date.replace('+00:00','Z')+' · '+last.value.toFixed(4)+' USD / EUR · '+visible.length+' наблюдений';
}
select.addEventListener('change',draw);
window.addEventListener('resize', draw);
async function loadSeries(){
  const request=++requestNumber, kind=series.value;
  svg.replaceChildren(); points=[]; document.querySelector('#values').replaceChildren();
  statusLine.textContent='Загрузка графика…';document.querySelector('#coverage').textContent='Загрузка покрытия…';
  const descriptions={d1:'Закрытие дневной сессии · Dukascopy Bid · 17:00 New York',h1:'Закрытие часовой свечи · Dukascopy Bid · UTC',ecb:'Дневной справочный курс ECB · USD за 1 EUR'};
  document.querySelector('#series-description').textContent=descriptions[kind];
  svg.setAttribute('aria-label', descriptions[kind]);
  document.querySelector('#series-note').textContent=kind==='ecb'?'Справочный курс ECB — не торговая свеча и не цена исполнения. Исторические версии и время доступности не подтверждены.':'Линия показывает Close. Пропуски не заполняются; неполные D1 скрыты на графике. Исторические версии не подтверждены, доступность предполагается через 60 секунд после закрытия бара. Bid не включает спред.';
  document.querySelector('#download-series').href='/download/'+(kind==='ecb'?'ecb_eurusd.parquet':'eurusd_'+kind+'.parquet');
  try {
    const response=await fetch(kind==='ecb'?'/api/market':'/api/bars?frequency='+kind);
    if(!response.ok)throw Error('HTTP '+response.status);
    const data=await response.json();if(request!==requestNumber)return;
    points=data.points;draw();
    document.querySelector('#coverage').textContent=points.length?points[0].date.slice(0,10)+' — '+points.at(-1).date.slice(0,10)+' · '+points.length+' наблюдений':'Нет данных';
    const body=document.querySelector('#values');points.slice(-10).reverse().forEach(p=>{const tr=document.createElement('tr');[p.date.replace('+00:00','Z'),p.value.toFixed(4)+(p.complete===false?' · неполная сессия':'')].forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});body.append(tr);});
  }catch(error){if(request===requestNumber){statusLine.textContent='Не удалось загрузить данные. Обновите страницу или проверьте сервис на сервере.';document.querySelector('#coverage').textContent='Загрузка не выполнена';}}
}
series.addEventListener('change',loadSeries);
loadSeries();
