// Run: node tests/browser_zo.cjs. Exercises actual sorting/rendering/QSY/NEXT code.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('pouncer/web.py','utf8').replace(/\r\n/g,'\n');
let created=0;
function element(tag='div') {
  created++;
  const el={tag,children:[],style:{},dataset:{},hidden:false,scrollWidth:80,clientWidth:90,
    _class:'',parentNode:null,innerHTML:'',textContent:'',offsetHeight:50,
    addEventListener(){}, get className(){return this._class},set className(v){this._class=v},
    insertBefore(child,before){child.remove(); const i=before?this.children.indexOf(before):-1;
      this.children.splice(i<0?this.children.length:i,0,child);child.parentNode=this;},
    appendChild(child){this.insertBefore(child,null)},
    remove(){if(this.parentNode){const a=this.parentNode.children;a.splice(a.indexOf(this),1);this.parentNode=null}},
    querySelectorAll(selector){return this.children.filter(x=>x.tag==='button'&&x.classList.contains('tile'))}};
  el.classList={contains:c=>el._class.split(' ').includes(c),
    add:c=>{if(!el.classList.contains(c))el._class+=' '+c},
    remove:c=>{el._class=el._class.split(' ').filter(x=>x!==c).join(' ')},
    toggle(c,on){if(on)el.classList.add(c);else el.classList.remove(c)}};
  return el;
}
const els=new Map();
const get=id=>{if(!els.has(id))els.set(id,element());return els.get(id)};
const grid=get('grid'), header=get('header');
const noop=()=>{};
const c=vm.createContext({console,Date,URLSearchParams,window:{innerWidth:320},
  document:{body:element(),getElementById:get,createElement:element,
    querySelector:()=>header,querySelectorAll:()=>[]},
  grid,metaEl:get('meta'),bandEl:get('band'),logWarnEl:get('logwarn'),logMsgEl:get('logmsg'),
  snrSheet:get('snrsheet'),sortKey:'snr',freqFrom:false,dxFirst:false,multFirst:false,rareFirst:false,
  lastData:{},myRadio:2,views:{},selKey:'',calm:false,calmSig:'',calmOrder:[],calmSorted:0,
  calmNew:new Set(),lastTouch:0,CALM_RESORT_MS:15000,CALM_HOLD_MS:3000,
  lastWpm:{},tuneStep:25,PAGE_VERSION:'test',tiles:new Map(),zoHeads:[],tilesLayout:'',emptyEl:null,shown:[],
  renderFkeys:noop,cwCheck:noop,labelTune:noop,syncHeaderHeight:noop,labelControl:noop,tapResult:noop,
  saveViews:noop,showMinSnr:noop,markSort:noop,checkAlerts:noop,attachPress:noop,
  esc:s=>String(s??''),calmKey:s=>s.call+'|'+Math.round(s.freq/1000),spotKey:s=>s.call+'|'+s.freq,
  isRare:s=>s.rare===true,rareKey:s=>s.run??999,
  refresh:noop,toast:()=>({}),flash:noop,
  controlAllowed:false,canControl:()=>c.controlAllowed,pin:null,PIN_MS:60000,
  rp:params=>new URLSearchParams({...params,radio:c.myRadio}).toString(),
  requests:[],fetch:async url=>{c.requests.push(url);return {json:async()=>({ok:false,error:'test'})}}});
function code(a,b){const start=source.indexOf(a);assert.ok(start>=0,a);const end=source.indexOf(b,start);assert.ok(end>start,b);return source.slice(start,end)}
vm.runInContext(code('  function zoGroup(s)','  // Classification is supplied'),c);
vm.runInContext(code('  function cqText(m)','  function markSort()'),c);
vm.runInContext(code('  function applyPin(list)','  async function reconnect()'),c);
vm.runInContext(code('  function render(data)','  const zoHeads ='),c);
vm.runInContext(code("  document.getElementById('next').onclick",'  function syncHeaderHeight()'),c);
const s=(call,urgency,snr,freq=7020)=>({call,urgency,snr,freq,mode:'CW',time:'1200Z',
  urgency_reasons:['Test history'],urgency_confidence:'limited',mult:0,dx:false});
let data={zo_mode:true,radio:2,radios:[1,2],band:'40m',radio_band:'40m',freq:7020,modes:['CW'],
  spots:[s('N1','normal',40),s('U2','urgent',20),s('X1','unknown',99),s('M1','medium',30),s('U1','urgent',50)]};
function calls(){return Array.from(c.shown,x=>x.call)}
(async()=>{
  c.render(data);
  assert.equal(c.columns(),3); // narrow phones retain all columns
  assert.deepEqual(calls(),['U1','U2','M1','N1','X1']);
  assert.equal(c.tiles.get('U1|7').el.style.gridColumn,'1');
  assert.equal(c.tiles.get('M1|7').el.style.gridColumn,'2');
  assert.equal(c.tiles.get('N1|7').el.style.gridColumn,'3');
  assert.ok(+c.tiles.get('X1|7').el.style.gridRow > +c.tiles.get('N1|7').el.style.gridRow);
  assert.equal(c.zoHeads[2].textContent,'Normal (2)');
  const baseline=created;
  for(let i=0;i<40;i++)c.render(data);
  assert.equal(created,baseline,'refresh must reuse DOM tiles and headings');
  assert.equal(c.requests.length,0,'rendering cannot tune');
  c.sortKey='freq'; data.spots[0].freq=7001; data.spots[1].freq=7030; data.spots[4].freq=7010;
  c.render(data);assert.deepEqual(calls().slice(0,2),['U1','U2']);
  c.sortKey='time';data.spots[1].time='1210Z';c.render(data);assert.equal(calls()[0],'U2');
  c.multFirst=true;data.spots[4].mult=2;c.render(data);assert.equal(calls()[0],'U1');
  c.dxFirst=true;data.spots[1].dx=true;c.render(data);assert.equal(calls()[0],'U1'); // mult wins inside urgent
  assert.equal(c.zoGroup(c.shown[2]),'medium');
  c.calm=true;c.calmOrder=[];c.render(data);const stable=calls();
  data.spots[1].snr=100;c.lastTouch=Date.now();c.render(data);assert.deepEqual(calls(),stable);
  const oldUrgent=c.tiles.get('U1|7').el;
  data.spots[4].urgency='normal';c.render(data);assert.equal(oldUrgent.style.gridColumn,'3');
  assert.equal(c.tiles.get('U1|7').el,oldUrgent);
  await c.qsy(c.shown[0],c.tiles.get(c.calmKey(c.shown[0])).el);
  assert.equal(c.requests.length,0,'look-only must still block QSY');
  c.controlAllowed=true;
  get('next').onclick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(c.requests.length,1);
  assert.ok(c.requests[0].includes('radio=2'));
  assert.ok(c.tiles.get(c.calmKey(c.shown[0])).el.classList.contains('sel'));
  assert.ok(!c.zoHeads.some(h=>h.classList.contains('sel')),'NEXT must select a tile, not a heading');
  data.zo_mode=false;c.render(data);assert.equal(c.columns(),1);
  assert.ok(c.zoHeads.every(h=>!h.parentNode));
  assert.ok(Array.from(c.tiles.values()).every(t=>t.el.style.gridColumn===''));
  data.zo_mode=true;data.spots=[];c.render(data);
  assert.equal(c.zoHeads[0].textContent,'\\u26a1 Urgent (0)');
  assert.equal(c.zoHeads[1].textContent,'Medium (0)');
  assert.equal(c.zoHeads[2].textContent,'Normal (0)');
  assert.equal(c.zoHeads[3].parentNode,null);
  assert.equal(c.columns(),3);

  // Exercise refresh(), not just render(): a full response received during a
  // hold must be rendered after release even if every later response is same.
  c.rp=()=>new URLSearchParams();c.document.hidden=false;
  Object.assign(c,{pressing:false,location:{reload:noop}});
  vm.runInContext(code("  let inFlight = false", "  document.addEventListener('visibilitychange'"),c);
  for (const hold of ['pointer','remove','debug','calm']) {
    for (const enabled of [true,false]) {
      c.calm=false;c.pressing=false;
      get('rmsheet').classList.remove('show');get('dbgsheet').classList.remove('show');
      data={...data,zo_mode:!enabled,spots:[s('HELD','urgent',30)]};c.render(data);
      const previous=grid.style.gridTemplateColumns;
      c.pressing=hold==='pointer';
      if(hold==='remove')get('rmsheet').classList.add('show');
      if(hold==='debug')get('dbgsheet').classList.add('show');
      c.calm=hold==='calm';c.lastTouch=Date.now();
      vm.runInContext("lastTag='';lastQuery='';pendingRender=false",c);
      let replies=[{...data,zo_mode:enabled,spots:[s('HELD','normal',30)],tag:'held'},
                   {same:true},{same:true},{same:true}];
      let fetches=[];c.fetch=async url=>{fetches.push(url);return {json:async()=>replies.shift()}};
      await c.refresh();assert.equal(grid.style.gridTemplateColumns,previous,hold+' holds rendering');
      await c.refresh();assert.equal(grid.style.gridTemplateColumns,previous,hold+' still holds on same');
      c.pressing=false;c.lastTouch=0;
      get('rmsheet').classList.remove('show');get('dbgsheet').classList.remove('show');
      await c.refresh();
      assert.equal(grid.style.gridTemplateColumns,'repeat('+(enabled?3:1)+', minmax(0, 1fr))',hold+' applies deferred toggle');
      if(enabled)assert.equal(c.tiles.get('HELD|7').el.style.gridColumn,'3','latest classification applied');
      assert.equal(vm.runInContext('pendingRender',c),false);
      const count=created;await c.refresh();assert.equal(created,count);
      assert.ok(fetches.every(url=>url.startsWith('/api/spots?')),'refresh never sends controls');
    }
  }
  console.log('ZO browser: columns, sorting, calm list, NEXT/QSY permissions, reuse and toggling passed');
})().catch(e=>{console.error(e);process.exitCode=1});
