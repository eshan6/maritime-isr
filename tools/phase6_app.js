// Maritime ISR — product surface. Vanilla JS, no framework.
const $ = (s, r=document) => r.querySelector(s);
const el = (t, c, h) => { const e=document.createElement(t); if(c)e.className=c; if(h!=null)e.innerHTML=h; return e; };
const fmtT = iso => { const d=new Date(iso); return d.toISOString().slice(0,16).replace('T',' ')+'Z'; };
const fmtD = iso => new Date(iso).toISOString().slice(0,10);

$('#asof').textContent = fmtT(SNAP.as_of);
$('#nav-alerts').textContent = SNAP.stats.open_alerts + ' open';
$('#nav-vessels').textContent = SNAP.stats.vessels;

// ---------- routing ----------
let mapInit = false;
function route(v){
  document.querySelectorAll('#nav a').forEach(a=>a.classList.toggle('on', a.dataset.v===v));
  document.querySelectorAll('.view').forEach(x=>x.classList.remove('on'));
  $('#v-'+v).classList.add('on');
  if(v==='map' && !mapInit){ initMap(); mapInit=true; }
  else if(v==='map'){ setTimeout(()=>MAP.invalidateSize(),50); }
}
document.querySelectorAll('#nav a').forEach(a=>a.onclick=()=>route(a.dataset.v));

// ---------- helpers ----------
const tag = (txt, cls) => `<span class="tag ${cls||'grey'}">${txt}</span>`;
const typeTag = t => tag(t.replace(/_/g,' '), TYPE_TAG[t]||'grey');
const dispTag = d => ({open:'amber',confirm:'red',dismiss:'grey',watch:'blue'}[d]||'grey');
const shortV = v => v ? v.replace('vessel:imo:','IMO ').replace('vessel:mmsi:','MMSI ') : '—';

// ---------- overview ----------
function renderOverview(){
  const s = SNAP.stats;
  const kpis = [
    ['Tracks (30d)', s.tracks, ''],
    ['Dark-vessel candidates', s.dark_candidates, s.dark_candidates?'red':''],
    ['Open alerts', s.open_alerts, s.open_alerts?'red':''],
    ['Vessels in graph', s.vessels, ''],
    ['Anomaly types live', s.anomaly_types_live+'/6', ''],
    ['Graph edges', s.edges, ''],
  ].map(([l,n,c])=>`<div class="kpi"><div class="n ${c}">${n}</div><div class="l">${l}</div></div>`).join('');

  // recent alerts (top 6 by score, open first)
  const al = [...SNAP.alerts].sort((a,b)=>(a.disposition==='open'?-1:0)-(b.disposition==='open'?-1:0)||b.score-a.score).slice(0,6);
  const alrows = al.map(a=>`<tr class="clk" onclick="openAlert('${a.alert_id}')">
      <td>${typeTag(a.type)}</td>
      <td class="mono">${a.mmsi?('MMSI '+a.mmsi):shortV(a.subject)}</td>
      <td>${a.score.toFixed(2)}</td>
      <td>${tag(a.disposition, dispTag(a.disposition))}</td>
      <td class="muted">${fmtT(a.ts)}</td></tr>`).join('');

  const byType = Object.entries(SNAP.alert_type_counts).sort((a,b)=>b[1]-a[1])
    .map(([t,n])=>`<tr><td>${typeTag(t)}</td><td style="text-align:right">${n}</td></tr>`).join('');

  $('#v-overview').innerHTML = `
    <h1>Operational overview</h1>
    <div class="crumb">Fusion picture for the Arabian Sea AOI, as of ${fmtT(SNAP.as_of)}.</div>
    <div class="kpis">${kpis}</div>
    <div class="row">
      <div class="card" style="flex:2;min-width:420px">
        <div class="hd">Priority alerts <a onclick="route('alerts')">View all</a></div>
        <table><thead><tr><th>Type</th><th>Subject</th><th>Score</th><th>Disposition</th><th>Detected</th></tr></thead>
        <tbody>${alrows||'<tr><td colspan=5 class="empty">No alerts.</td></tr>'}</tbody></table>
      </div>
      <div class="card" style="flex:1;min-width:240px">
        <div class="hd">Alerts by type</div>
        <table><tbody>${byType}</tbody></table>
      </div>
    </div>
    <div class="card">
      <div class="hd">System status</div>
      <div class="bd"><div class="kv">
        <div class="k">Pipeline version</div><div class="mono">${SNAP.pipeline_version}</div>
        <div class="k">AOI</div><div>5°N–25°N, 60°E–78°E (Arabian Sea v1)</div>
        <div class="k">Data source</div><div>Synthetic feed — proof of functionality, not live sensors</div>
        <div class="k">Snapshot generated</div><div class="mono">${fmtT(SNAP.generated_at)}</div>
      </div></div>
    </div>`;
}

// ---------- map + replay ----------
let MAP, trackLayer, contactLayer, frameLayer, playing=false, playTimer=null;
function initMap(){
  MAP = L.map('map', {zoomControl:true}).setView([15.5, 69], 6);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    {attribution:'&copy; OpenStreetMap, &copy; CARTO', subdomains:'abcd', maxZoom:12}).addTo(MAP);
  const a=SNAP.aoi;
  L.rectangle([[a.lat_min,a.lon_min],[a.lat_max,a.lon_max]],
    {color:'#1a5fb4',weight:1,fill:false,dashArray:'4 4'}).addTo(MAP);
  SNAP.receivers.forEach(r=>L.circle([r.lat,r.lon],{radius:r.radius_km*1000,
    color:'#9aa3ad',weight:1,fill:false,dashArray:'3 5'}).addTo(MAP).bindTooltip(r.name+' receiver'));

  trackLayer = L.layerGroup().addTo(MAP);
  SNAP.tracks.forEach(t=>{
    L.polyline(t.pts.map(p=>[p[0],p[1]]),{color:'#2563eb',weight:1,opacity:.35})
      .addTo(trackLayer).bindTooltip('MMSI '+t.mmsi);
  });
  contactLayer = L.layerGroup().addTo(MAP);
  SNAP.matched.forEach(m=>L.circleMarker([m.lat,m.lon],{radius:3,color:'#0f7b43',
    weight:1,fillColor:'#0f7b43',fillOpacity:.7}).addTo(contactLayer)
    .bindTooltip('Matched — MMSI '+m.mmsi));
  SNAP.dark.forEach(d=>L.circleMarker([d.lat,d.lon],{radius:7,color:'#dc2626',weight:2,
    fillColor:'#dc2626',fillOpacity:.35}).addTo(contactLayer).bindPopup(
    `<b style="color:#dc2626">Dark-vessel candidate</b><br>Length ≈ ${d.length_m} m · score ${d.score}<br>
     <span class="muted">${fmtT(d.ts)} · ${d.scene_id}</span>`));
  frameLayer = L.layerGroup().addTo(MAP);

  $('#legend').innerHTML = `
    <div class="li"><span class="sw" style="background:#2563eb"></span>AIS track</div>
    <div class="li"><span class="sw" style="background:#0f7b43"></span>Matched contact</div>
    <div class="li"><span class="sw" style="background:#dc2626"></span>Dark-vessel candidate</div>
    <div class="li"><span class="sw sq" style="background:#f59e0b"></span>Replay position</div>`;

  const r=$('#scrubr');
  r.oninput=()=>showFrame(+r.value);
  $('#playbtn').onclick=togglePlay;
  showFrame(59);
}
function showFrame(i){
  const f = SNAP.frames[i];
  frameLayer.clearLayers();
  f.pos.forEach(p=>L.circleMarker([p[0],p[1]],{radius:3.5,color:'#b45309',weight:1,
    fillColor:'#f59e0b',fillOpacity:.9}).addTo(frameLayer));
  $('#scrubt').textContent = fmtT(new Date(f.t*1000).toISOString());
  $('#scrubn').textContent = f.pos.length+' vessels';
  $('#scrubr').value = i;
}
function togglePlay(){
  playing=!playing; $('#playbtn').innerHTML = playing?'&#10073;&#10073;':'&#9654;';
  if(playing){ playTimer=setInterval(()=>{ let i=(+$('#scrubr').value+1)%60; showFrame(i);
    if(i===59){/*loop*/} },400); } else clearInterval(playTimer);
}

// ---------- alerts ----------
function renderAlerts(){
  const rows = [...SNAP.alerts].sort((a,b)=>(a.disposition==='open'?-1:0)-(b.disposition==='open'?-1:0)||b.score-a.score)
    .map(a=>`<tr class="clk" onclick="openAlert('${a.alert_id}')">
      <td>${typeTag(a.type)}</td>
      <td class="mono">${a.mmsi?('MMSI '+a.mmsi):shortV(a.subject)}</td>
      <td>${a.score.toFixed(2)}</td>
      <td>${a.evidence.length} steps</td>
      <td>${tag(a.disposition, dispTag(a.disposition))}</td>
      <td class="muted">${fmtT(a.ts)}</td></tr>`).join('');
  $('#v-alerts').innerHTML = `
    <h1>Alert queue</h1>
    <div class="crumb">${SNAP.alerts.length} alerts · ${SNAP.stats.open_alerts} open for review. Click any alert for its evidence chain.</div>
    <div class="card"><table>
      <thead><tr><th>Type</th><th>Subject</th><th>Score</th><th>Evidence</th><th>Disposition</th><th>Detected</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}
function openAlert(id){
  const a = SNAP.alerts.find(x=>x.alert_id===id);
  route('alerts');
  const chain = a.evidence.map(c=>`<div class="step">
      <span class="mono">${shortV(c.src)||c.src}</span>
      &nbsp;—[<span class="edge">${c.edge}</span>${c.confidence!=null?' '+(+c.confidence).toFixed(2):''}]&rarr;&nbsp;
      <span class="mono">${shortV(c.dst)||c.dst}</span>
      <span class="muted"> · ${c.source||''}</span></div>`).join('');
  const propRows = Object.entries(a.props||{}).map(([k,v])=>
    `<div class="k">${k}</div><div>${typeof v==='object'?JSON.stringify(v):v}</div>`).join('');
  $('#v-alerts').innerHTML = `
    <span class="back" onclick="renderAlerts()">&larr; Back to queue</span>
    <h1>${typeTag(a.type)} &nbsp;${a.mmsi?('MMSI '+a.mmsi):shortV(a.subject)}</h1>
    <div class="crumb">Alert ${a.alert_id} · detected ${fmtT(a.ts)} · confidence ${a.confidence.toFixed(2)}</div>
    <div class="split">
      <div class="card"><div class="hd">Evidence chain</div>
        <div class="bd"><div class="chain">${chain}</div>
        <div class="muted" style="margin-top:8px;font-size:11px">Confidence is the weakest link in the chain.</div></div></div>
      <div class="card"><div class="hd">Details</div>
        <div class="bd"><div class="kv">
          <div class="k">Rule</div><div>${a.rule}</div>
          <div class="k">Subject</div><div class="mono">${a.subject}</div>
          ${propRows}
        </div>
        <div style="margin-top:14px" class="dispbtns">
          <button class="btn pri" onclick="disp('${id}','confirm')">Confirm</button>
          <button class="btn" onclick="disp('${id}','dismiss')">Dismiss</button>
          <button class="btn" onclick="disp('${id}','watch')">Watch</button>
        </div>
        <div class="muted" style="margin-top:8px;font-size:11px">Current: ${tag(a.disposition, dispTag(a.disposition))} — dispositions feed the detector-tuning loop.</div>
        ${a.mmsi?`<div style="margin-top:12px"><a onclick="openEntityByMmsi(${a.mmsi})">Open vessel entity page &rarr;</a></div>`:''}
        </div></div>
    </div>
    <div class="card"><div class="hd">Report</div>
      <div class="bd"><button class="btn" onclick="genReport('${id}')">Generate incident report</button>
      <span class="muted" style="margin-left:10px">One-click PDF/text summary with imagery reference, chain, and confidence.</span></div></div>`;
}
// in-memory disposition (session only — no backend in the demo)
function disp(id, label){
  const a = SNAP.alerts.find(x=>x.alert_id===id); a.disposition=label;
  $('#nav-alerts').textContent = SNAP.alerts.filter(x=>x.disposition==='open').length+' open';
  openAlert(id);
}

// ---------- vessels ----------
function renderVessels(){
  const rows = Object.values(SNAP.entities)
    .sort((a,b)=>b.risk.risk_score-a.risk.risk_score)
    .map(e=>{
      const rc = e.risk.risk_score>=.4?'red':e.risk.risk_score>=.25?'amber':'grey';
      const idc = e.identity_history.filter(i=>i.closed).length;
      return `<tr class="clk" onclick="openEntity('${e.vessel_id}')">
        <td class="mono">${e.mmsi?('MMSI '+e.mmsi):shortV(e.vessel_id)}</td>
        <td>${e.props.name||'—'}</td>
        <td>${e.props.flag||'—'}</td>
        <td>${e.n_tracks}</td>
        <td>${idc?tag(idc+' change'+(idc>1?'s':''),'amber'):'<span class="muted">—</span>'}</td>
        <td>${e.alerts.length?tag(e.alerts.length,'red'):'<span class="muted">0</span>'}</td>
        <td>${tag(e.risk.risk_score.toFixed(3), rc)}</td></tr>`;
    }).join('');
  $('#v-vessels').innerHTML = `
    <h1>Vessels</h1>
    <div class="crumb">${Object.keys(SNAP.entities).length} vessel entities with track, identity, or alert history. Sorted by risk.</div>
    <div class="card"><table>
      <thead><tr><th>MMSI</th><th>Name</th><th>Flag</th><th>Tracks</th><th>Identity changes</th><th>Alerts</th><th>Risk</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}
function openEntityByMmsi(m){
  const e = Object.values(SNAP.entities).find(x=>x.mmsi===m);
  if(e) openEntity(e.vessel_id); else route('vessels');
}
function openEntity(vid){
  const e = SNAP.entities[vid]; if(!e){route('vessels');return;}
  route('vessels');
  const rs = e.risk, C = rs.components;
  const max = Math.max(rs.risk_score, .001);
  const segs = Object.entries(C).map(([k,c])=>c.weighted>0?
    `<div class="risk-seg" style="width:${100*c.weighted/max}%;background:${RCOL[k]}" title="${k} ${c.weighted.toFixed(3)}"></div>`:'').join('');
  const compRows = Object.entries(C).map(([k,c])=>`<div class="risk-row">
      <span>${k.replace(/_/g,' ')}</span>
      <div class="risk-bar"><div class="risk-seg" style="width:${100*c.value}%;background:${RCOL[k]}"></div></div>
      <span class="mono">${c.value.toFixed(2)}×${c.weight}</span></div>`).join('');
  const idRows = e.identity_history.map(i=>`<tr>
      <td>${i.kind}</td><td class="mono">${i.value}</td>
      <td>${i.closed?tag('former','grey'):tag('current','blue')}</td></tr>`).join('');
  const nb = e.neighborhood.map(n=>`<div class="step">
      <span class="mono">${shortV(n.src)||n.src.slice(0,22)}</span>
      —[<span class="edge">${n.edge}</span> ${n.confidence.toFixed(2)}]&rarr;
      <span class="mono">${shortV(n.dst)||n.dst.slice(0,22)}</span>
      ${n.closed?tag('closed','grey'):''}</div>`).join('');
  const alertRows = e.alerts.map(id=>{const a=SNAP.alerts.find(x=>x.alert_id===id);
    return a?`<tr class="clk" onclick="openAlert('${id}')"><td>${typeTag(a.type)}</td>
      <td>${a.score.toFixed(2)}</td><td>${tag(a.disposition,dispTag(a.disposition))}</td>
      <td class="muted">${fmtT(a.ts)}</td></tr>`:'';}).join('');
  const fp = e.props.fingerprint||{};
  $('#v-vessels').innerHTML = `
    <span class="back" onclick="renderVessels()">&larr; Back to vessels</span>
    <h1>${e.props.name||'Unknown vessel'} <span class="muted mono" style="font-size:14px">${e.mmsi?('MMSI '+e.mmsi):''}</span></h1>
    <div class="crumb">${shortV(e.vessel_id)} · flag ${e.props.flag||'—'} · ${e.n_tracks} track(s) · risk ${rs.risk_score.toFixed(3)}</div>
    <div class="split">
      <div class="card"><div class="hd">Risk score — ${rs.risk_score.toFixed(3)}</div>
        <div class="bd"><div class="risk-bar" style="height:22px;margin-bottom:12px">${segs}</div>${compRows}
        <div class="muted" style="margin-top:8px;font-size:11px">Score = weighted sum of named components. Decomposable by design.</div></div></div>
      <div class="card"><div class="hd">Identity history</div>
        <table><thead><tr><th>Field</th><th>Value</th><th>Status</th></tr></thead>
        <tbody>${idRows||'<tr><td colspan=3 class="muted">No registry identity.</td></tr>'}</tbody></table></div>
    </div>
    <div class="split">
      <div class="card"><div class="hd">Behavioral fingerprint</div>
        <div class="bd"><div class="kv">
          <div class="k">Mean speed</div><div>${fp.sog_mean!=null?fp.sog_mean.toFixed(1)+' kn':'—'}</div>
          <div class="k">90th-pct speed</div><div>${fp.sog_p90!=null?fp.sog_p90.toFixed(1)+' kn':'—'}</div>
          <div class="k">Heading change rate</div><div>${fp.heading_change_rate!=null?fp.heading_change_rate.toFixed(1)+'°/min':'—'}</div>
          <div class="k">Loiter episodes</div><div>${fp.n_loiter_episodes??'—'}</div>
          <div class="k">Port calls</div><div>${(fp.port_calls&&fp.port_calls.length)?fp.port_calls.join(', '):'—'}</div>
        </div></div></div>
      <div class="card"><div class="hd">Alerts</div>
        <table><tbody>${alertRows||'<tr><td class="muted">No alerts.</td></tr>'}</tbody></table></div>
    </div>
    <div class="card"><div class="hd">Graph neighborhood</div>
      <div class="bd"><div class="chain">${nb||'<span class="muted">No edges.</span>'}</div></div></div>
    <div class="card"><div class="hd">Report</div><div class="bd">
      <button class="btn" onclick="genVesselReport('${vid}')">Generate vessel report</button></div></div>`;
}

// ---------- risk board ----------
function renderRisk(){
  const max = Math.max(...SNAP.risk_board.map(r=>r.score), .001);
  const rows = SNAP.risk_board.map(r=>{
    const segs = Object.entries(r.components).map(([k,c])=>c.weighted>0?
      `<div class="risk-seg" style="width:${100*c.weighted/max}%;background:${RCOL[k]}" title="${k}"></div>`:'').join('');
    const e = SNAP.entities[r.vessel];
    return `<tr class="clk" onclick="openEntity('${r.vessel}')">
      <td class="mono">${r.mmsi?('MMSI '+r.mmsi):shortV(r.vessel)}</td>
      <td>${e?(e.props.name||'—'):'—'}</td>
      <td style="width:45%"><div class="risk-bar">${segs}</div></td>
      <td class="mono">${r.score.toFixed(3)}</td></tr>`;
  }).join('');
  const leg = Object.entries(RCOL).map(([k,c])=>
    `<span style="margin-right:14px"><span class="tag" style="background:${c};color:#fff">${k.replace(/_/g,' ')}</span></span>`).join('');
  $('#v-risk').innerHTML = `
    <h1>Risk board</h1>
    <div class="crumb">Composite per-vessel risk, ranked. Each bar decomposes into named contributions.</div>
    <div style="margin-bottom:12px">${leg}</div>
    <div class="card"><table>
      <thead><tr><th>MMSI</th><th>Name</th><th>Risk composition</th><th>Score</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

// ---------- reports ----------
function reportText(a){
  const lines = [];
  lines.push('MARITIME ISR — INCIDENT REPORT');
  lines.push('='.repeat(52));
  lines.push('Classification: UNCLASSIFIED // SYNTHETIC DEMO DATA');
  lines.push('Generated: '+fmtT(new Date().toISOString()));
  lines.push('Alert ID: '+a.alert_id);
  lines.push('');
  lines.push('SUMMARY');
  lines.push('  Anomaly type : '+a.type.replace(/_/g,' '));
  lines.push('  Subject      : '+(a.mmsi?('MMSI '+a.mmsi):a.subject));
  lines.push('  Detected     : '+fmtT(a.ts));
  lines.push('  Confidence   : '+a.confidence.toFixed(2)+' (weakest link in chain)');
  lines.push('');
  lines.push('EVIDENCE CHAIN');
  a.evidence.forEach((c,i)=>lines.push('  '+(i+1)+'. '+(c.src||'')+' -['+c.edge+
    (c.confidence!=null?' '+(+c.confidence).toFixed(2):'')+']-> '+(c.dst||'')+
    (c.source?'   ['+c.source+']':'')));
  lines.push('');
  if(a.props && Object.keys(a.props).length){
    lines.push('DETAILS');
    Object.entries(a.props).forEach(([k,v])=>lines.push('  '+k+': '+(typeof v==='object'?JSON.stringify(v):v)));
    lines.push('');
  }
  lines.push('IMAGERY REFERENCE');
  lines.push('  SAR scene: '+(a.props.scene_id||'see track archive'));
  lines.push('  (chip attached in full product; omitted in text export)');
  lines.push('');
  lines.push('CONFIDENCE STATEMENT');
  lines.push('  This alert was produced by automated fusion of open AIS and');
  lines.push('  SAR-class detections. All data is synthetic. Disposition: '+a.disposition+'.');
  return lines.join('\n');
}
function download(name, text){
  const b=new Blob([text],{type:'text/plain'}); const u=URL.createObjectURL(b);
  const a=el('a'); a.href=u; a.download=name; a.click(); URL.revokeObjectURL(u);
}
function genReport(id){ const a=SNAP.alerts.find(x=>x.alert_id===id);
  const t=reportText(a); showReportPreview('Incident report — '+a.alert_id, t, 'maritime_isr_incident_'+id+'.txt'); }
function genVesselReport(vid){ const e=SNAP.entities[vid];
  const L=[];
  L.push('MARITIME ISR — VESSEL REPORT');
  L.push('='.repeat(52));
  L.push('Classification: UNCLASSIFIED // SYNTHETIC DEMO DATA');
  L.push('Vessel: '+(e.props.name||'Unknown')+'  ('+shortV(e.vessel_id)+')');
  L.push('MMSI: '+(e.mmsi||'—')+'   Flag: '+(e.props.flag||'—'));
  L.push('Risk score: '+e.risk.risk_score.toFixed(3));
  L.push('');
  L.push('RISK DECOMPOSITION');
  Object.entries(e.risk.components).forEach(([k,c])=>
    L.push('  '+k.padEnd(24)+' '+c.value.toFixed(2)+' x '+c.weight+' = '+c.weighted.toFixed(3)));
  L.push('');
  L.push('IDENTITY HISTORY');
  e.identity_history.forEach(i=>L.push('  '+i.kind+': '+i.value+(i.closed?' (former)':' (current)')));
  L.push('');
  L.push('ALERTS: '+e.alerts.length);
  showReportPreview('Vessel report — '+(e.props.name||vid), L.join('\n'), 'maritime_isr_vessel_'+(e.mmsi||'x')+'.txt');
}
function showReportPreview(title, text, fname){
  route('reports');
  $('#v-reports').innerHTML = `
    <h1>${title}</h1>
    <div class="crumb">Preview of the one-click report. In the full product this renders as PDF with the SAR chip and track plot.</div>
    <div class="card"><div class="hd">Report preview
      <button class="btn pri" onclick="download('${fname}', document.getElementById('rpt').textContent)">Download</button></div>
      <div class="bd"><pre id="rpt" style="white-space:pre-wrap;font-family:'SF Mono',Menlo,monospace;font-size:12px;margin:0;color:#1f2933">${text.replace(/</g,'&lt;')}</pre></div></div>`;
}
function renderReports(){
  $('#v-reports').innerHTML = `
    <h1>Reports</h1>
    <div class="crumb">Generate an incident report from any alert, or a vessel report from any entity page.</div>
    <div class="card"><div class="hd">Recent alerts</div>
    <table><thead><tr><th>Type</th><th>Subject</th><th>Score</th><th></th></tr></thead><tbody>${
      SNAP.alerts.slice(0,12).map(a=>`<tr><td>${typeTag(a.type)}</td>
        <td class="mono">${a.mmsi?('MMSI '+a.mmsi):shortV(a.subject)}</td><td>${a.score.toFixed(2)}</td>
        <td><button class="btn" onclick="genReport('${a.alert_id}')">Generate</button></td></tr>`).join('')
    }</tbody></table></div>`;
}

// ---------- provenance ----------
function renderProvenance(){
  $('#v-provenance').innerHTML = `
    <h1>Provenance & methodology</h1>
    <div class="crumb">Every figure in this console traces to the pipeline that produced it.</div>
    <div class="card"><div class="hd">Data lineage</div><div class="bd"><div class="kv">
      <div class="k">AIS ingest</div><div>Synthetic 30-day NMEA feed → canonical position schema (Phase 0)</div>
      <div class="k">SAR detection</div><div>Synthetic scenes, CFAR + classifier equivalent (Phase 1)</div>
      <div class="k">Tracks</div><div>Kalman-smoothed, gap-classified (Phase 2)</div>
      <div class="k">Fusion</div><div>SAR↔AIS association, dark-vessel cascade (Phase 3)</div>
      <div class="k">Graph</div><div>Object graph, ownership + sanctions, identity persistence (Phase 4)</div>
      <div class="k">Anomalies</div><div>Six precision-gated detectors + risk scoring (Phase 5)</div>
    </div></div></div>
    <div class="card"><div class="hd">Honesty statement</div><div class="bd">
      <p style="margin:0 0 8px">All data in this console is <b>synthetic</b>. No live sensor feeds, subscriptions, or
      real vessel data are connected. Every metric is measured on a deterministic synthetic suite with injected
      ground truth. Real-world accuracy will differ and must be re-measured on the deploy host.</p>
      <p style="margin:0" class="muted">This is a decision-support layer and proof of functionality.</p>
    </div></div>`;
}

// ---------- init all ----------
renderOverview(); renderAlerts(); renderVessels(); renderRisk(); renderReports(); renderProvenance();
