'use strict';
const chart=document.querySelector('#policy-chart'),statusNode=document.querySelector('#policy-status'),ns='http://www.w3.org/2000/svg';
function add(tag,attrs,text){const node=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);if(text!==undefined)node.textContent=text;chart.append(node);return node;}
function stepCoords(points,key,x,y,reverse=false){const ordered=reverse?[...points].reverse():points,result=[[x(ordered[0]),y(ordered[0][key])]];for(let i=1;i<ordered.length;i++){result.push([x(ordered[i]),y(ordered[i-1][key])],[x(ordered[i]),y(ordered[i][key])]);}return result;}
function path(coords){return coords.map((point,index)=>(index?'L':'M')+point[0].toFixed(2)+','+point[1].toFixed(2)).join(' ');}
async function render(){
  try{
    const source=document.querySelector('#policy-source')?.value||'fomc',response=await fetch('/api/policy?source='+source);if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json(),points=data.points;
    chart.replaceChildren();if(!points.length){statusNode.textContent='Данные решений ещё не загружены.';return;}
    const width=Math.max(270,chart.getBoundingClientRect().width),mobile=width<600,height=mobile?235:300,left=54,right=width-15,bottom=height-38;
    const isFomc=data.source==='fomc',title=isFomc?'Целевой диапазон ставки FOMC':'Ставки ECB';chart.setAttribute('viewBox',`0 0 ${width} ${height}`);chart.setAttribute('aria-label',title+' по времени официального решения');add('title',{},title);
    const low=Math.min(...points.map(point=>point.lower)),high=Math.max(...points.map(point=>point.upper)),pad=Math.max(.15,(high-low)*.1),floor=Math.max(0,low-pad),ceiling=high+pad;
    const first=Date.parse(points[0].published_at),span=Math.max(1,Date.parse(points.at(-1).published_at)-first),x=point=>left+(Date.parse(point.published_at)-first)/span*(right-left),y=value=>bottom-(value-floor)/(ceiling-floor)*(bottom-20);
    for(let i=0;i<(mobile?3:5);i++){const value=floor+(ceiling-floor)*i/(mobile?2:4),pos=y(value);add('line',{x1:left,x2:right,y1:pos,y2:pos,class:'gridline'});add('text',{x:left-8,y:pos+4,'text-anchor':'end'},value.toFixed(2)+'%');}
    const upper=stepCoords(points,'upper',x,y),lower=stepCoords(points,'lower',x,y,true),band=[...upper,...lower].map(point=>point.map(value=>value.toFixed(2)).join(',')).join(' ');
    add('polygon',{points:band,class:'policy-band'});add('path',{d:path(stepCoords(points,'midpoint',x,y)),class:'policy-line'});
    for(const point of points)add('circle',{cx:x(point),cy:y(point.midpoint),r:3,class:'policy-dot'});
    for(const index of mobile?[0,points.length-1]:[0,Math.floor((points.length-1)/2),points.length-1]){const point=points[index];add('text',{x:x(point),y:height-8,'text-anchor':index===0?'start':index===points.length-1?'end':'middle'},point.date);}
    const cursor=add('line',{x1:0,x2:0,y1:20,y2:bottom,class:'cursor',visibility:'hidden'});
    chart.onpointermove=event=>{const rect=chart.getBoundingClientRect(),px=(event.clientX-rect.left)/rect.width*width,point=points.reduce((best,item)=>Math.abs(x(item)-px)<Math.abs(x(best)-px)?item:best,points[0]);cursor.setAttribute('x1',x(point));cursor.setAttribute('x2',x(point));cursor.setAttribute('visibility','visible');statusNode.textContent=`${point.date} · ${point.lower.toFixed(2)}–${point.upper.toFixed(2)}% · ${point.change_bp===null?'нет предыдущего':point.change_bp.toFixed(0)+' bp'} · available неизвестно`;};
    document.querySelector('#policy-note').textContent=isFomc?'Полоса показывает границы целевого диапазона FOMC, линия — midpoint. Ось X использует время официальной публикации. График не означает, что запись уже была получена нашей системой.':'Полоса показывает corridor от deposit facility до marginal lending facility, линия — deposit facility rate. Ось X использует время официальной публикации. График не означает, что запись уже была получена нашей системой.';
    statusNode.textContent=`${points[0].date} — ${points.at(-1).date} · ${points.length} решений · ${isFomc?'FOMC':'ECB'}`;
  }catch(error){statusNode.textContent='Не удалось загрузить решения. Обновите страницу или проверьте сервис.';}
}
let timer;window.addEventListener('resize',()=>{clearTimeout(timer);timer=setTimeout(render,120);});render();
document.querySelector('#policy-source')?.addEventListener('change',render);
