import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

// Exercise the component's real event handlers with a small hook host.
const compiled=await readFile(new URL('../../.local/application-tests/AppliedJobs.js',import.meta.url),'utf8');
let state=[],refs=[],effects=[],cursor=0,refCursor=0;
const hooks={useState(initial){const i=cursor++;if(!(i in state))state[i]=initial;return [state[i],value=>{state[i]=typeof value==='function'?value(state[i]):value;}];},useRef(initial){const i=refCursor++;return refs[i]??= {current:initial};},useEffect(fn){effects.push(fn);}};
globalThis.applicationTestHooks=hooks;
const source=compiled.replace(/import .* from ['"]\.\/GoogleSheetsSync['"];?/, 'const GoogleSheetsSync=()=>null;')
 .replace(/import .* from ['"]\.\/AddExternalApplication['"];?/, 'const AddExternalApplication=()=>null;')
 .replace(/import .* from ['"]react['"];?/, 'const {useState,useRef,useEffect}=globalThis.applicationTestHooks;')
 .replace(/from ["']react\/jsx-runtime["']/,`from ${JSON.stringify(import.meta.resolve('react/jsx-runtime'))}`);
const {AppliedJobs}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
function render(){cursor=refCursor=0;effects=[];return AppliedJobs();}
function nodes(tree){if(!tree||typeof tree!=='object')return [];if(Array.isArray(tree))return tree.flatMap(nodes);return [tree,...nodes(tree.props?.children)];}
function find(tree,type,predicate=()=>true){return nodes(tree).find(n=>n.type===type&&predicate(n.props));}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
// Local date: the page's default "Last 7 days" filter uses the local calendar day, not UTC.
const row={id:'one',revision:'v1',company:'Example',title:'PM',applied_date:new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10),status:'Applied',notes:'',follow_up_date:null,boards:[],resume_revision:'',recorded_without_resume:true};

test('Update immediately displays saved fields and counts, and ignores an older list response',async()=>{
 state=[];refs=[];
 let gets=0,resolveOld,resolveRefresh;
 const updated={...row,revision:'v2',status:'Rejected',notes:'Closed',follow_up_date:'2026-11-01'};
 globalThis.fetch=async(url,options)=>{
  if(options?.method==='POST'){assert.deepEqual(JSON.parse(options.body),{revision:'v1',status:'Rejected',notes:'Closed',follow_up_date:'2026-11-01'});return {ok:true,json:async()=>updated};}
  gets++;
  if(gets===2)return new Promise(resolve=>{resolveOld=resolve;});
  if(gets===3)return new Promise(resolve=>{resolveRefresh=resolve;});
  return {ok:true,json:async()=>[row]};
 };
 globalThis.window={addEventListener(){},removeEventListener(){},setInterval(){return 1;},clearInterval(){}};
 render();effects[0]();await tick();
 let tree=render();
 find(tree,'button',p=>p.children==='Update').props.onClick();
 tree=render();
 find(find(tree,'dialog'),'select').props.onChange({target:{value:'Rejected'}});tree=render();
 find(tree,'textarea').props.onChange({target:{value:'Closed'}});tree=render();
 find(tree,'input',p=>p.type==='date').props.onChange({target:{value:'2026-11-01'}});tree=render();
 // Start a background fetch before saving, then let it return stale data afterward.
 effects[0]();await tick();
 find(tree,'form').props.onSubmit({preventDefault(){}});await tick();
 tree=render();
 assert.equal(find(tree,'span',p=>p.className==='application-status application-status-rejected').props.children,'Rejected');
 assert.ok(find(tree,'td',p=>p.children==='Closed'));
 assert.ok(find(tree,'td',p=>p.children==='2026-11-01'));
 assert.deepEqual(find(tree,'option',p=>p.value==='Rejected').props.children,['Rejected',' (',1,')']);
 resolveOld({ok:true,json:async()=>[row]});await tick();
 assert.ok(find(render(),'span',p=>p.children==='Rejected'));
 resolveRefresh({ok:true,json:async()=>[updated]});await tick();
 tree=render();
 find(tree,'select',p=>p.value==='All statuses').props.onChange({target:{value:'Applied'}});
 assert.ok(find(render(),'p',p=>p.children==='No applications match these filters.'));
});

test('failed saves keep the dialog open and display the server error',async()=>{
 state=[];refs=[];
 globalThis.fetch=async(url,options)=>options?.method==='POST'?{ok:false,json:async()=>({detail:'Reload before saving.'})}:{ok:true,json:async()=>[row]};
 render();effects[0]();await tick();let tree=render();
 find(tree,'button',p=>p.children==='Update').props.onClick();tree=render();
 find(tree,'form').props.onSubmit({preventDefault(){}});await tick();tree=render();
 assert.ok(find(tree,'form'));
 assert.match(find(tree,'p',p=>p.role==='alert').props.children[0],/Reload before saving/);
 assert.ok(find(tree,'span',p=>p.children==='Applied'));
});
