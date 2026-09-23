'use strict';
const svg = document.querySelector('#chart');
const statusLine = document.querySelector('#chart-status');
const select = document.querySelector('#period');
const ns = 'http://www.w3.org/2000/svg';
let points = [], visible = [];
function el(tag, attrs, text) { const node = document.createElementNS(ns, tag); for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,v); if (text !== undefined) node.textContent = text; svg.append(node); return node; }
function draw() {
  svg.replaceChildren();
  const days = Number(select.value), last = points.at(-1);
  if (!last) { statusLine.textContent = 'Данные ещё не загружены. Выполните backfill на сервере.'; return; }
  const cutoff = Date.parse(last.date) - days * 86400000;
  visible = points.filter(p => !days || Date.parse(p.date) >= cutoff);
  const values = visible.map(p => p.value), low = Math.min(...values) - .003, high = Math.max(...values) + .003;
  const width = Math.max(270, svg.getBoundingClientRect().width), mobile = width < 600;
  const height = mobile ? 235 : 300, left = 58, right = width - 15, bottom = height - 38;
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const firstTime = Date.parse(visible[0].date), span = Math.max(1, Date.parse(last.date) - firstTime);
  const x = p => left + (Date.parse(p.date)-firstTime) / span * (right-left), y = value => bottom - (value-low)/(high-low)*(bottom-20);
  el('title', {}, 'Дневной справочный курс EUR/USD, ECB');
  const ticks = mobile ? 3 : 5;
  for (let i=0;i<ticks;i++) { const value = low+(high-low)*i/(ticks-1), pos = y(value); el('line',{x1:left,x2:right,y1:pos,y2:pos,class:'gridline'}); el('text',{x:left-8,y:pos+4,'text-anchor':'end'},value.toFixed(4)); }
  el('path',{d:visible.map((p,i)=>(i?'L':'M')+x(p).toFixed(2)+','+y(p.value).toFixed(2)).join(' '),class:'series-line'});
  for (const index of mobile ? [0,visible.length-1] : [0,Math.floor((visible.length-1)/2),visible.length-1]) {const p=visible[index];el('text',{x:x(p),y:height-8,'text-anchor':index===0?'start':index===visible.length-1?'end':'middle'},p.date);}
  const cursor=el('line',{x1:0,x2:0,y1:20,y2:bottom,class:'cursor',visibility:'hidden'}), dot=el('circle',{r:4,class:'chart-dot',visibility:'hidden'});
  svg.onpointermove = e => {const rect=svg.getBoundingClientRect(), px=(e.clientX-rect.left)/rect.width*width;const p=visible.reduce((best,p)=>Math.abs(x(p)-px)<Math.abs(x(best)-px)?p:best,visible[0]);cursor.setAttribute('x1',x(p));cursor.setAttribute('x2',x(p));cursor.setAttribute('visibility','visible');dot.setAttribute('cx',x(p));dot.setAttribute('cy',y(p.value));dot.setAttribute('visibility','visible');statusLine.textContent=p.date+' · '+p.value.toFixed(4)+' USD / EUR';};
  statusLine.textContent = last.date+' · '+last.value.toFixed(4)+' USD / EUR · '+visible.length+' наблюдений';
}
select.addEventListener('change',draw);
window.addEventListener('resize', draw);
fetch('/api/market').then(r=>{if(!r.ok)throw Error('HTTP '+r.status);return r.json();}).then(data=>{points=data.points;draw();const body=document.querySelector('#values');points.slice(-10).reverse().forEach(p=>{const tr=document.createElement('tr');[p.date,p.value.toFixed(4)].forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.append(td);});body.append(tr);});}).catch(()=>{statusLine.textContent='Не удалось загрузить данные. Обновите страницу или проверьте сервис на сервере.';});
