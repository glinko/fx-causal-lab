'use strict';
const chart=document.querySelector('#positioning-chart'), statusNode=document.querySelector('#positioning-status'), ns='http://www.w3.org/2000/svg';
function add(tag,attrs,text){const node=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);if(text!==undefined)node.textContent=text;chart.append(node);return node;}
function pathFor(points,key,x,y){let result='';for(const point of points)result+=(result?' L':' M')+x(point).toFixed(2)+','+y(point[key]).toFixed(2);return result;}
async function render(){
  try{
    const response=await fetch('/api/positioning');if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json(),points=data.points;
    chart.replaceChildren();if(!points.length){statusNode.textContent='Данные CFTC ещё не загружены.';return;}
    const width=Math.max(270,chart.getBoundingClientRect().width),mobile=width<600,height=mobile?235:300,left=64,right=width-15,bottom=height-38;
    chart.setAttribute('viewBox',`0 0 ${width} ${height}`);add('title',{},'Чистые позиции CFTC по отчётной дате');
    const values=points.flatMap(point=>[point.asset_manager,point.leveraged]),low=Math.min(...values),high=Math.max(...values),pad=Math.max(1,(high-low)*.08),floor=low-pad,ceiling=high+pad;
    const first=Date.parse(points[0].date),span=Math.max(1,Date.parse(points.at(-1).date)-first),x=point=>left+(Date.parse(point.date)-first)/span*(right-left),y=value=>bottom-(value-floor)/(ceiling-floor)*(bottom-20);
    for(let i=0;i<(mobile?3:5);i++){const value=floor+(ceiling-floor)*i/(mobile?2:4),pos=y(value);add('line',{x1:left,x2:right,y1:pos,y2:pos,class:'gridline'});add('text',{x:left-8,y:pos+4,'text-anchor':'end'},Math.round(value/1000)+'k');}
    add('line',{x1:left,x2:right,y1:y(0),y2:y(0),class:'zero-line'});add('path',{d:pathFor(points,'asset_manager',x,y),class:'position-asset'});add('path',{d:pathFor(points,'leveraged',x,y),class:'position-leveraged'});
    for(const index of mobile?[0,points.length-1]:[0,Math.floor((points.length-1)/2),points.length-1]){const point=points[index];add('text',{x:x(point),y:height-8,'text-anchor':index===0?'start':index===points.length-1?'end':'middle'},point.date);}
    const cursor=add('line',{x1:0,x2:0,y1:20,y2:bottom,class:'cursor',visibility:'hidden'});
    chart.onpointermove=event=>{const rect=chart.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*width,point=points.reduce((best,item)=>Math.abs(x(item)-px)<Math.abs(x(best)-px)?item:best,points[0]);cursor.setAttribute('x1',x(point));cursor.setAttribute('x2',x(point));cursor.setAttribute('visibility','visible');statusNode.textContent=`${point.date} · asset ${Math.round(point.asset_manager).toLocaleString('ru-RU')} · leveraged ${Math.round(point.leveraged).toLocaleString('ru-RU')} · available ${point.available_at||'неизвестно'}`;};
    statusNode.textContent=`${points[0].date} — ${points.at(-1).date} · ${points.length} отчётов`;
  }catch(error){statusNode.textContent='Не удалось загрузить позиционирование. Обновите страницу или проверьте сервис.';}
}
window.addEventListener('resize',render);render();
