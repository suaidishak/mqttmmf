import { PAGE_SIZE, formatNumber, unitText, series, segments, csv, validateData } from './model.mjs';
const $ = id => document.getElementById(id);
const state = { data: null, site: null, parameter: 'ghi', hours: 24, timezone: 'Asia/Kuala_Lumpur', page: 0, loading: false, failed: false };
const number = formatNumber;
function el(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function time(value, short = false) { return new Intl.DateTimeFormat('en-GB', { timeZone: state.timezone, ...(short ? { hour:'2-digit',minute:'2-digit' } : { day:'2-digit', month:'short', year:'numeric', hour:'2-digit',minute:'2-digit',second:'2-digit' }), hourCycle:'h23' }).format(new Date(value)); }
function site() { return state.data?.sites.find(s => s.siteCode === state.site); }
function points() { return series(site()?.captures || [], state.parameter, state.hours); }
function svg(tag, attributes = {}, text) { const node = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [key,value] of Object.entries(attributes)) node.setAttribute(key, value); if (text !== undefined) node.textContent = text; return node; }
function chart(points, compact = false) {
  const root = svg('svg', { viewBox: compact ? '0 0 100 32' : '0 0 1000 270', role:'img', 'aria-label':compact?'Recent parameter trend':`${state.parameter} arithmetic means over actual capture times` });
  if (!points.length) return root;
  const width = compact ? 100 : 1000, height = compact ? 32 : 270;
  const left = compact ? 2 : 66, right = compact ? 2 : 24, top = compact ? 3 : 18, bottom = compact ? 3 : 40;
  let lo = Math.min(...points.map(p=>p.averageValue)), hi = Math.max(...points.map(p=>p.averageValue));
  const padding = (hi-lo)*.14 || Math.abs(hi)*.1 || 1; lo -= padding; hi += padding;
  const first = Date.parse(points[0].capturedAt), last = Date.parse(points.at(-1).capturedAt);
  const x = p => first === last ? (left+width-right)/2 : left + (Date.parse(p.capturedAt)-first)/(last-first)*(width-left-right);
  const y = value => top + (hi-value)/(hi-lo)*(height-top-bottom);
  if (!compact) {
    root.append(svg('title', {}, `${state.parameter}: ${points.length} captured observations. See the history table for exact values.`));
    for(let i=0;i<5;i++){ const v=lo+(hi-lo)*i/4, yy=y(v); root.append(svg('line',{x1:left,y1:yy,x2:width-right,y2:yy,stroke:'#e7ece5','stroke-dasharray':'4 5'}),svg('text',{x:left-12,y:yy+4,'text-anchor':'end',fill:'#879186','font-size':11},number(v))); }
    const labels = first===last ? [first] : Array.from({length:5},(_,i)=>first+(last-first)*i/4);
    for (const t of labels) root.append(svg('text',{x:x({capturedAt:new Date(t).toISOString()}),y:height-12,'text-anchor':'middle',fill:'#879186','font-size':11}, (last-first>86400000?new Intl.DateTimeFormat('en-GB',{timeZone:state.timezone,day:'2-digit',month:'short'}).format(new Date(t))+' ':'')+time(t,true)));
  }
  for (const group of segments(points)) {
    if(group.length<2)continue;
    const line = group.map((p,i)=>`${i?'L':'M'}${x(p)},${y(p.averageValue)}`).join(' ');
    if(!compact)root.append(svg('path',{d:`${line} L${x(group.at(-1))},${height-bottom} L${x(group[0])},${height-bottom} Z`,fill:'#eef5ed'}));
    root.append(svg('path',{d:line,fill:'none',stroke:'#287950','stroke-width':compact?1.5:2.4,'stroke-linejoin':'round','stroke-linecap':'round'}));
  }
  for(const p of points){const dot=svg('circle',{cx:x(p),cy:y(p.averageValue),r:compact?1.7:4,fill:'#287950',stroke:'#fff','stroke-width':compact?0:1.7,...(!compact?{tabindex:0}: {})});if(!compact)dot.append(svg('title',{},`${time(p.capturedAt)} · ${number(p.averageValue)} · ${unitText(p)} · ${p.contributingStations}/${p.stationCount} stations`));root.append(dot);}
  return root;
}
function freshness() {
  const latest=site()?.captures.at(-1), badge=$('freshness');
  if(state.failed){badge.className='status error';badge.textContent=state.data?'Refresh failed':'Data unavailable';return;}
  if(!latest){badge.className='status';badge.textContent=state.loading?'Loading data':'No captures';return;}
  const age = Math.max(0,Date.now()-Date.parse(latest.capturedAt)), minutes=Math.floor(age/60000);
  badge.className='status '+(minutes<=30?'fresh':'stale');badge.textContent=minutes<=30?'Recent capture':`Capture ${minutes<60?minutes+'m':Math.floor(minutes/60)+'h'} old`;
  const note=$('notice');note.className='notice '+(minutes>30?'warning':'');note.textContent=(minutes>30?'The latest capture is delayed. ':'')+'Collection requests run every 15 minutes; GitHub scheduling can delay or skip runs. The dashboard checks for published updates every minute.';
}
function render() {
  const selected=site(), captures=selected?.captures||[], latest=captures.at(-1);
  $('captured').textContent=latest?time(latest.capturedAt):'No captures';$('stations').textContent=latest?latest.stationCount+' stations':'—';$('parameters').textContent=latest?latest.parameters.length+' parameters':'—';$('captures').textContent=captures.length+' captures';
  const codes=[...new Set(captures.flatMap(c=>c.parameters.map(p=>p.parameterCode)))].sort();
  if(!codes.includes(state.parameter))state.parameter=codes[0]||'';
  $('parameter').replaceChildren(...codes.map(c=>{const opt=el('option',c);opt.value=c;return opt;}));$('parameter').value=state.parameter;$('parameter').disabled=!codes.length;
  $('download-latest').disabled=!latest;$('download-history').disabled=!points().length;
  const featured=['ghi','poa','amb_temp','ws'].filter(c=>latest?.parameters.some(p=>p.parameterCode===c));
  for(const p of latest?.parameters||[])if(featured.length<4&&!featured.includes(p.parameterCode))featured.push(p.parameterCode);
  $('metric-cards').replaceChildren(...featured.map((code,i)=>{const p=latest.parameters.find(p=>p.parameterCode===code),card=el('article',undefined,'metric-card'),top=el('div',undefined,'metric-top');top.append(el('span',code),el('span',['☀','↗','◉','≈'][i],'metric-symbol'));const value=el('div',number(p.averageValue),'metric-value');value.append(el('small',p.unitConflict?'Mixed units':p.unit||'Unit not supplied'));const bottom=el('div',undefined,'metric-bottom'),spark=el('div',undefined,'spark');spark.append(chart(series(captures,code,24),true));bottom.append(el('span',p.contributingStations+' / '+latest.stationCount+' stations'),spark);card.append(top,value,bottom);return card;}));
  $('publication').textContent=`Published ${time(state.data.generatedAt)} · ${state.timezone==='UTC'?'UTC':'UTC+8'}\nLatest ${state.data.historyCaptureLimit} captures within ${state.data.historyDays} days. Older records remain in Supabase.`;
  freshness();renderLatest();renderTrend();
}
function renderLatest() {
  const latest=site()?.captures.at(-1), search=$('search').value.toLowerCase();
  const rows=(latest?.parameters||[]).filter(p=>p.parameterCode.toLowerCase().includes(search)).map(p=>{const tr=el('tr'),code=el('td'),button=el('button',p.parameterCode,'parameter-button');button.type='button';button.title='View '+p.parameterCode+' trend';button.addEventListener('click',()=>{state.parameter=p.parameterCode;state.page=0;$('parameter').value=state.parameter;renderTrend();document.querySelector('.trend-panel').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});});code.append(button);const units=el('td'),tag=el('span',unitText(p),'source-unit'+(p.unitConflict?' conflict':''));units.append(tag);const counts=el('td'),coverage=el('span',undefined,'coverage'),bar=el('span',undefined,'coverage-bar'),fill=el('span',undefined,'coverage-fill'); // Width uses an SVG, keeping the CSP free of inline styles.
    bar.append(svg('svg',{viewBox:'0 0 44 4',width:44,height:4}));bar.firstChild.append(svg('rect',{x:0,y:0,width:44*p.contributingStations/latest.stationCount,height:4,fill:'#6eaa86'}));coverage.append(bar,el('span',`${p.contributingStations} / ${latest.stationCount}`));counts.append(coverage);tr.append(code,el('td',number(p.averageValue),'number'),units,counts,el('td',`${p.stationsWithUnit} / ${p.contributingStations} labelled`));return tr;});
  if(!rows.length){const tr=el('tr'),td=el('td',search?'No matching parameters.':'No captured readings.','empty');td.colSpan=5;tr.append(td);rows.push(tr);}$('latest-rows').replaceChildren(...rows);
}
function renderTrend() {
  const data=points();const units=[...new Set(data.flatMap(p=>p.sourceUnits))];
  $('trend-description').textContent=state.parameter?`${state.parameter} · ${units.length>1?'Source unit labels vary; numeric values are shown without conversion.':units[0]||'Unit metadata not supplied.'}`:'No parameter history available.';
  $('trend-latest').textContent=data.length?number(data.at(-1).averageValue):'—';$('trend-min').textContent=data.length?number(Math.min(...data.map(p=>p.averageValue))):'—';$('trend-max').textContent=data.length?number(Math.max(...data.map(p=>p.averageValue))):'—';$('trend-count').textContent=data.length;
  $('chart').replaceChildren(data.length?chart(data):el('div','No observations for this parameter in the selected window.','no-chart'));
  $('history-description').textContent=`${state.parameter||'Parameter'} · ${state.hours} hours before the latest site capture · ${state.timezone==='UTC'?'UTC':'Malaysia time (UTC+8)'}`;
  $('download-history').disabled=!data.length;renderHistory();
}
function renderHistory() {
  const data=points().toReversed(),pages=Math.max(1,Math.ceil(data.length/PAGE_SIZE));state.page=Math.min(state.page,pages-1);
  const rows=data.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE).map(p=>{const tr=el('tr');tr.append(el('td',time(p.capturedAt)),el('td',p.parameterCode),el('td',number(p.averageValue),'number'),el('td',unitText(p)),el('td',`${p.contributingStations} / ${p.stationCount}`));return tr;});
  if(!rows.length){const tr=el('tr'),td=el('td','No observations in this window.','empty');td.colSpan=5;tr.append(td);rows.push(tr);}$('history-rows').replaceChildren(...rows);$('history-count').textContent=data.length+' observations';$('page-number').textContent=`${state.page+1} / ${pages}`;$('previous').disabled=!state.page;$('next').disabled=state.page>=pages-1;
}
async function load() {
  if(state.loading)return;state.loading=true;$('refresh').disabled=true;$('refresh').textContent='Refreshing…';
  try {
    const response=await fetch('./data/averages.json?t='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error('Unavailable');const data=validateData(await response.json());state.data=data;state.failed=false;
    if(!data.sites.some(s=>s.siteCode===state.site))state.site=data.sites[0]?.siteCode;
    $('site').replaceChildren(...data.sites.map(s=>{const opt=el('option',s.siteCode);opt.value=s.siteCode;return opt;}));$('site').value=state.site||'';$('site').disabled=data.sites.length<2;render();
  }catch{state.failed=true;freshness();$('notice').className='notice error';$('notice').textContent=state.data?'The latest publication could not be loaded. Previously loaded readings remain visible; use Refresh to retry.':'The data publication is unavailable. Use Refresh to retry.';}
  finally{state.loading=false;$('refresh').disabled=false;$('refresh').textContent='↻ Refresh';}
}
function download(rows, name) { const blob=new Blob(['\uFEFF'+csv(rows)],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),link=el('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000); }
const header=['site_code','captured_at_utc','parameter_code','average_value','unit_metadata','source_units','unit_conflict','contributing_stations','station_count','stations_with_unit'];
function csvRow(p,c) { return [state.site,c.capturedAt,p.parameterCode,p.averageValue,p.unit,p.sourceUnits.join(' | '),p.unitConflict,p.contributingStations,c.stationCount,p.stationsWithUnit]; }
$('refresh').addEventListener('click',load);$('search').addEventListener('input',renderLatest);
$('site').addEventListener('change',()=>{state.site=$('site').value;state.page=0;render();});$('parameter').addEventListener('change',()=>{state.parameter=$('parameter').value;state.page=0;renderTrend();});$('period').addEventListener('change',()=>{state.hours=Number($('period').value);state.page=0;renderTrend();});$('timezone').addEventListener('change',()=>{state.timezone=$('timezone').value;if(state.data)render();});
$('previous').addEventListener('click',()=>{state.page--;renderHistory();});$('next').addEventListener('click',()=>{state.page++;renderHistory();});
$('download-latest').addEventListener('click',()=>{const c=site()?.captures.at(-1);if(c)download([header,...c.parameters.map(p=>csvRow(p,c))],state.site+'-latest-averages.csv');});$('download-history').addEventListener('click',()=>download([header,...points().map(p=>csvRow(p,p))],state.site+'-'+state.parameter+'-history.csv'));
load();setInterval(()=>{if(!document.hidden)load();},60000);setInterval(()=>{if(!state.failed&&state.data)freshness();},30000);
