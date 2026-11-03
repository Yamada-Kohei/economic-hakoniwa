// 手引き「産業のつながり」の図と表（/help/chain/）。データは server/web/chain_map.py が作る
(function(){
const D=JSON.parse(document.getElementById('chain-data').textContent);
const G=D.goods, F=D.facs, S=D.sinks;
const fmt=v=>Number(v).toLocaleString('ja-JP',{maximumFractionDigits:2});
const tierCls=g=>({1:'raw',2:'mid',3:'fin'})[G[g].tier]||'fin';
const tierName={raw:'原料',mid:'中間財',fin:'最終財'};
const gname=g=>G[g]?G[g].n:g;
const isArms=g=>G[g]&&G[g].arms;

// 図の列（並べ方だけ。中身はデータから）
const COLS=D.cols;
const COLHEAD=['原料','一次加工','加工','組み立て','完成品','使い道'];
const colOf={}; COLS.forEach((c,i)=>c.forEach(g=>colOf[g]=i));

// 線: 施設の作り方の「使う物 → 作る物」
const isExtra=r=>r.id==='automated'||r.grade>0;
const EDGES=new Map();
const has=(list,g)=>list.some(x=>x[0]===g);
for(const f of F){
  const rs=f.r.filter(r=>r.out.length);
  const outs=new Set(rs.flatMap(r=>r.out.map(x=>x[0])));
  for(const o of outs){
    const base=rs.filter(r=>!isExtra(r)&&has(r.out,o));
    const all=rs.filter(r=>has(r.out,o));
    const ins=new Set(all.flatMap(r=>r.in.map(x=>x[0])));
    for(const i of ins){
      const solid=base.length>0&&base.every(r=>has(r.in,i));
      const onlyAuto=all.filter(r=>has(r.in,i)).every(r=>r.id==='automated');
      const k=i+'>'+o;
      const e=EDGES.get(k)||{i,o,solid:false,auto:true,via:new Set()};
      e.solid=e.solid||solid; e.auto=e.auto&&onlyAuto; e.via.add(f.n);
      EDGES.set(k,e);
    }
  }
}
// 作るのに電力が要る品目
const needsPower=new Set([...EDGES.values()].filter(e=>e.i==='electricity').map(e=>e.o));

const opt={arms:false,power:false,auto:false};
let sel=null; // {type:'g'|'s', id}

const NW=104,NH=28,GAPY=8,COLW=142,PADX=14,PADT=34;
const svgNS='http://www.w3.org/2000/svg';
const el=(t,a={},p)=>{const n=document.createElementNS(svgNS,t);for(const k in a)n.setAttribute(k,a[k]);if(p)p.appendChild(n);return n;};

function visibleGoods(){
  return COLS.map(c=>c.filter(g=>G[g]&&(opt.arms||!isArms(g))));
}
function visEdges(){
  return [...EDGES.values()].filter(e=>{
    if(!opt.arms&&(isArms(e.i)||isArms(e.o)))return false;
    if(e.i==='electricity'&&!opt.power)return false;
    if(e.auto&&!opt.auto)return false;
    return colOf[e.i]!==undefined&&colOf[e.o]!==undefined;
  });
}
function visSinks(){return S.filter(s=>opt.arms||s.id!=='army');}

let POS={}, svg;
function draw(){
  const cols=visibleGoods(), sinks=visSinks();
  const rows=Math.max(...cols.map(c=>c.length),sinks.length);
  const H=PADT+rows*(NH+GAPY)+12, W=PADX*2+5*COLW+NW;
  POS={};
  const chart=document.getElementById('chain-chart'); chart.innerHTML='';
  svg=el('svg',{viewBox:`0 0 ${W} ${H}`,width:W,height:H,role:'img','aria-label':'品目どうしのつながりの図'},chart);
  COLHEAD.forEach((h,i)=>el('text',{x:PADX+i*COLW+NW/2,y:20,'text-anchor':'middle',class:'colhead'},svg).textContent=h);
  const place=(list,ci,key)=>{
    const off=PADT+(rows-list.length)*(NH+GAPY)/2;
    list.forEach((id,j)=>POS[key+id]={x:PADX+ci*COLW,y:off+j*(NH+GAPY)});
  };
  cols.forEach((c,i)=>place(c,i,'g:'));
  place(sinks.map(s=>s.id),5,'s:');
  const eg=el('g',{},svg), ng=el('g',{},svg);
  const ve=visEdges();
  for(const e of ve){
    const a=POS['g:'+e.i], b=POS['g:'+e.o]; if(!a||!b)continue;
    let d;
    const ay=a.y+NH/2, by=b.y+NH/2;
    if(colOf[e.o]>colOf[e.i]){const x1=a.x+NW,x2=b.x,m=(x2-x1)*.5;d=`M${x1},${ay} C${x1+m},${ay} ${x2-m},${by} ${x2},${by}`;}
    else if(colOf[e.o]<colOf[e.i]){const x1=a.x,x2=b.x+NW,m=Math.max(30,(x1-x2)*.5);d=`M${x1},${ay} C${x1-m},${ay} ${x2+m},${by} ${x2},${by}`;}
    else{const x=a.x+NW,bul=26+Math.abs(by-ay)*.12;d=`M${x},${ay} C${x+bul},${ay} ${x+bul},${by} ${x},${by}`;}
    const p=el('path',{d,class:'edge'+(e.solid?'':' opt')+(e.i==='electricity'?' pow':''),'marker-end':''},eg);
    p.dataset.i=e.i;p.dataset.o=e.o;
  }
  for(const s of sinks){
    const b=POS['s:'+s.id];
    for(const g of s.g){
      const a=POS['g:'+g]; if(!a)continue;
      const x1=a.x+NW,x2=b.x,ay=a.y+NH/2,by=b.y+NH/2,m=(x2-x1)*.4;
      const p=el('path',{d:`M${x1},${ay} C${x1+m},${ay} ${x2-m},${by} ${x2},${by}`,class:'edge sinkE'},eg);
      p.dataset.g=g;p.dataset.s=s.id;
    }
  }
  const mk=(key,label,cls,extra)=>{
    const p=POS[key]; const g=el('g',{class:'node '+cls,transform:`translate(${p.x},${p.y})`,tabindex:0,role:'button','aria-label':label},ng);
    el('rect',{width:NW,height:NH,rx:5},g);
    el('text',{x:9,y:NH/2+5,'font-size':13},g).textContent=label;
    if(extra)extra(g);
    g.dataset.key=key;
    const go=()=>{const [t,id]=key.split(':');select(sel&&sel.id===id?null:{type:t,id});};
    g.addEventListener('click',go);
    g.addEventListener('keydown',ev=>{if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();go();}});
    return g;
  };
  cols.flat().forEach(gid=>mk('g:'+gid,gname(gid),tierCls(gid),g=>{
    if(needsPower.has(gid)&&!opt.power)el('text',{x:NW-14,y:NH/2+4,class:'bolt'},g).textContent='⚡';
  }));
  sinks.forEach(s=>mk('s:'+s.id,s.n,'sink'));
  applySel();
}

function closure(start,dir,ve){
  const out=new Set(), st=[start];
  while(st.length){const x=st.pop();for(const e of ve){const [a,b]=dir==='up'?[e.o,e.i]:[e.i,e.o];if(a===x&&!out.has(b)&&b!==start){out.add(b);st.push(b);}}}
  return out;
}
function applySel(){
  if(!svg)return;
  svg.classList.toggle('dim',!!sel);
  svg.querySelectorAll('.node').forEach(n=>n.classList.remove('sel','up','down','hit'));
  svg.querySelectorAll('.edge').forEach(p=>p.classList.remove('up','down','on'));
  if(!sel){renderPanel();return;}
  const ve=visEdges();
  if(sel.type==='g'){
    const up=closure(sel.id,'up',ve), down=closure(sel.id,'down',ve);
    svg.querySelectorAll('.node').forEach(n=>{const k=n.dataset.key;
      if(k==='g:'+sel.id)n.classList.add('sel');
      else if(up.has(k.slice(2))&&k.startsWith('g:'))n.classList.add('up');
      else if(down.has(k.slice(2))&&k.startsWith('g:'))n.classList.add('down');});
    svg.querySelectorAll('.edge:not(.sinkE)').forEach(p=>{const i=p.dataset.i,o=p.dataset.o;
      if((o===sel.id||up.has(o))&&up.has(i))p.classList.add('up');
      else if((i===sel.id||down.has(i))&&down.has(o))p.classList.add('down');});
    svg.querySelectorAll('.edge.sinkE').forEach(p=>{if(p.dataset.g===sel.id){p.classList.add('on');const n=svg.querySelector(`[data-key="s:${p.dataset.s}"]`);n&&n.classList.add('hit');}});
    renderPanel(up,down);
  }else{
    const s=S.find(x=>x.id===sel.id);
    svg.querySelector(`[data-key="s:${s.id}"]`).classList.add('sel');
    svg.querySelectorAll('.edge.sinkE').forEach(p=>{if(p.dataset.s===s.id){p.classList.add('on');const n=svg.querySelector(`[data-key="g:${p.dataset.g}"]`);n&&n.classList.add('hit');}});
    renderPanel();
  }
}
function select(s){sel=s;applySel();}

const gspan=(g,q)=>`<span class="g${g==='electricity'?' e':''}">${gname(g)}${q!==undefined?` <span class="num">${fmt(q)}</span>${G[g]?G[g].u:''}`:''}</span>`;
const chip=g=>`<button type="button" class="chip ${tierCls(g)}" data-g="${g}">${gname(g)}</button>`;
const recName=(f,r)=>r.rn||(f.r.length>1?r.id:'');
function renderPanel(up,down){
  const P=document.getElementById('chain-panel');
  if(!sel){P.innerHTML='<p class="hint">図の品目か使い道を押すと、ここに作る施設・使う施設・さかのぼると要る原料が出ます。下の表の品目名からも選べます。</p>';return;}
  if(sel.type==='s'){
    const s=S.find(x=>x.id===sel.id);
    P.innerHTML=`<div class="title"><b>${s.n}</b><span class="tag sink">使い道</span></div><p class="small">${s.note}</p><div class="chips">${s.g.filter(g=>opt.arms||!isArms(g)).map(chip).join('')}</div>`;
    bindChips(P);return;
  }
  const g=sel.id, info=G[g], cls=tierCls(g);
  const makers=[],users=[];
  for(const f of F)for(const r of f.r){
    const o=r.out.find(x=>x[0]===g); if(o)makers.push({f,r});
    const i=r.in.find(x=>x[0]===g); if(i)users.push({f,r,q:i[1]});
  }
  const mk=makers.map(({f,r})=>`<li><b>${f.n}</b>${recName(f,r)?`（${recName(f,r)}）`:''}: ${r.in.length?r.in.map(x=>gspan(x[0],x[1])).join('・'):'材料なし'} → ${r.out.map(x=>gspan(x[0],x[1])).join('・')}${r.res?' ・鉱脈が要る':''}</li>`).join('')||'<li>どの施設も作らない</li>';
  const byFac=new Map();
  users.forEach(u=>{const k=u.f.n;const cur=byFac.get(k)||[];cur.push(u);byFac.set(k,cur);});
  const us=[...byFac.entries()].map(([n,list])=>{
    const outs=[...new Set(list.flatMap(u=>u.r.out.map(x=>gname(x[0]))))];
    const qs=[...new Set(list.map(u=>fmt(u.q)))];
    return `<li><b>${n}</b>: ${qs.join(' / ')}${info.u}${outs.length?` → ${outs.join('・')}`:'（教材として使う）'}</li>`;}).join('')||'<li>工場の材料には使わない</li>';
  const sk=S.filter(s=>s.g.includes(g)).map(s=>s.n).join('、')||'なし';
  const raws=[...(up||[])].filter(x=>G[x]&&G[x].tier===1);
  P.innerHTML=`<div class="title"><b>${info.n}</b><span class="tag ${cls}">${tierName[cls]}</span><span class="small">単位 ${info.u||'—'}</span></div>
  <div class="kv">
    <div>作る施設</div><div><ul>${mk}</ul></div>
    <div>材料に使う施設</div><div><ul>${us}</ul></div>
    <div>最後に使う所</div><div>${sk}</div>
    <div>さかのぼると要る原料</div><div class="chips">${raws.length?raws.map(chip).join(''):'<span class="small">なし（これ自体が原料、または材料なしで作る）</span>'}</div>
    <div>これを使って作る物</div><div class="chips">${down&&down.size?[...down].map(chip).join(''):'<span class="small">なし</span>'}</div>
  </div>
  <p class="small">今の図に出している線（兵器・電力・自動化の切り替え）でたどっています。</p>`;
  bindChips(P);
}
function bindChips(root){root.querySelectorAll('.chip[data-g],.g-link').forEach(b=>b.addEventListener('click',()=>{
  const g=b.dataset.g; if(isArms(g)&&!opt.arms){opt.arms=true;document.getElementById('chain-arms').checked=true;draw();}
  select({type:'g',id:g}); document.getElementById('chain-chart').scrollIntoView({behavior:'smooth',block:'start'});
}));}

// 産業ごとの表
const ORDER=['農業','鉱業','エネルギー','鉄鋼','非鉄','石油','食品','繊維','消費財','機械','電子機器','自動車','造船','兵器','商業','教育'];
const groupOf=f=>f.ind||({commerce:'商業',education:'教育'})[f.cat]||'その他';
function renderTable(){
  const groups=new Map();
  F.forEach(f=>{const k=groupOf(f);(groups.get(k)||groups.set(k,[]).get(k)).push(f);});
  const keys=[...groups.keys()].sort((a,b)=>(ORDER.indexOf(a)+99)%99-(ORDER.indexOf(b)+99)%99);
  const link=(g,q)=>`<button type="button" class="chip ${tierCls(g)} g-link" data-g="${g}" style="padding:0 6px">${gname(g)}${q!==undefined?` <span class="num">${fmt(q)}</span>${G[g].u}`:''}</button>`;
  const recHtml=(f,r)=>{
    const lab=recName(f,r);
    const lab2=r.labor<1?`人手 ${Math.round(r.labor*100)}%`:'';
    return `<div class="rec">${lab||lab2||r.grow?`<span class="rn">${[lab,lab2,r.grow?`収穫まで ${r.grow} か月`:''].filter(Boolean).join(' ・ ')}</span>`:''}
      <div class="flow">${r.in.length?r.in.map(x=>link(x[0],x[1])).join(''):'<span class="small">材料なし</span>'}<span class="arrow">→</span>${r.out.length?r.out.map(x=>link(x[0],x[1])).join(''):'<span class="small">教育の水準</span>'}</div></div>`;
  };
  document.getElementById('chain-table').innerHTML=keys.map(k=>`<div class="ind"><h3>${k}</h3>${groups.get(k).map(f=>{
    const st=f.staff||{}; const total=Object.values(st).reduce((a,b)=>a+b,0);
    const place=[f.terrain.join('・'),f.coastal?'海に面する':'',f.water?'水が集まる所':'',f.zone==='commercial'?'商業地':'',f.r.some(r=>r.res)?'鉱脈が要る':''].filter(Boolean).join('・');
    const main=f.r.filter(r=>!r.grade), grades=f.r.filter(r=>r.grade);
    return `<div class="card"><div class="head"><b>${f.n}</b><span class="small num">人手 ${fmt(total)}${st.engineer?`（技術者 ${fmt(st.engineer)}）`:''}</span></div>
      <div class="meta"><span>場所: ${place||'—'}</span><span>建てる: ${f.turns||'—'} か月${f.mat.length?'・'+f.mat.map(x=>`${gname(x[0])} ${fmt(x[1])}${G[x[0]].u}`).join('・'):''}</span></div>
      ${main.map(r=>recHtml(f,r)).join('')}
      ${grades.length?`<details><summary>機械化・近代化した農場の作り方（${grades.length}）</summary>${grades.map(r=>recHtml(f,r)).join('')}</details>`:''}
    </div>`;}).join('')}</div>`).join('');
  bindChips(document.getElementById('chain-table'));
}
function renderSinks(){
  document.getElementById('chain-sinks').innerHTML=S.map(s=>`<div><b>${s.n}</b><span class="small">${s.note}</span><div class="chips">${s.g.map(chip).join('')}</div></div>`).join('');
  bindChips(document.getElementById('chain-sinks'));
}

document.getElementById('chain-arms').addEventListener('change',e=>{opt.arms=e.target.checked;if(sel&&!opt.arms&&(isArms(sel.id)||sel.id==='army'))sel=null;draw();});
document.getElementById('chain-power').addEventListener('change',e=>{opt.power=e.target.checked;draw();});
document.getElementById('chain-auto').addEventListener('change',e=>{opt.auto=e.target.checked;draw();});
const chartEl=document.getElementById('chain-chart'), fitBtn=document.getElementById('chain-fit');
fitBtn.addEventListener('click',()=>{const on=chartEl.classList.toggle('fit');fitBtn.textContent=on?'元の大きさで見る':'画面の幅に合わせる';});
document.getElementById('chain-clear').addEventListener('click',()=>select(null));
if(window.innerWidth>=900){chartEl.classList.add('fit');fitBtn.textContent='元の大きさで見る';}
draw(); renderTable(); renderSinks();
})();
