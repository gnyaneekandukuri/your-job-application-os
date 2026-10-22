import {useEffect,useState} from 'react';

type SheetsState={automatic_sync?:{pending:boolean;due_at:number|null;error:string|null;syncing:boolean};configured:boolean;connected:boolean;target?:{id:string;title:string;url:string}|null;last_sync?:{at:string;added:number;updated:number;total:number}|null};
export function GoogleSheetsSync({changeKey=''}:{changeKey?:string}){
 const [state,setState]=useState<SheetsState|null>(null),[expanded,setExpanded]=useState(false),[busy,setBusy]=useState(false),[waiting,setWaiting]=useState(false),[url,setUrl]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 async function request(action='',body?:unknown){
  const r=await fetch('/api/applications/sheets'+action,action?{method:'POST',headers:{'Content-Type':'application/json','X-Local-Review':'1'},body:JSON.stringify(body??{})}:undefined);
  const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Google Sheets request failed. Please try again.');return data;
 }
 useEffect(()=>{let live=true;const refresh=()=>{void request().then(s=>{if(live)setState(s);}).catch(()=>{});};refresh();window.addEventListener('focus',refresh);return()=>{live=false;window.removeEventListener('focus',refresh);};},[changeKey]);
 useEffect(()=>{setUrl(state?.target?.url||'');},[state?.target?.url]);
 useEffect(()=>{if(!waiting)return;let live=true;const started=Date.now();const timer=setInterval(()=>{if(Date.now()-started>600000){setWaiting(false);setNotice('If you connected Google, press Refresh connection.');return;}void request().then(s=>{if(live&&s.connected){setState(s);setWaiting(false);setNotice('Google connected. Choose your destination spreadsheet below.');}}).catch(()=>{});},2500);return()=>{live=false;clearInterval(timer);};},[waiting]);
 async function act(action:string,body?:unknown){setBusy(true);setError('');setNotice('');try{const s=await request(action,body);setState(s);if(action==='/sync')setNotice(`Synced: ${s.last_sync.added} added, ${s.last_sync.updated} updated. No duplicate rows added.`);if(action==='/target')setNotice('Destination saved. Press Sync to Google Sheets to send your applications.');}catch(e){setError(String(e));}finally{setBusy(false);}}
 async function connect(){
  const popup=window.open('about:blank','job-review-google-connect');
  setBusy(true);setError('');setNotice('');
  try{const result=await request('/connect');if(popup){popup.opener=null;popup.location.href=result.url;}else{throw Error('Allow popups for this local app, then press Connect Google again.');}setWaiting(true);}
  catch(e){popup?.close();setError(String(e));}finally{setBusy(false);}
 }
 async function upload(file:File){
  if(file.size>12000){setError('Choose the small OAuth client JSON downloaded from Google Cloud.');return;}
  setBusy(true);setError('');setNotice('');
  try{const s=await request('/configure',{credentials_json:await file.text()});setState(s);setWaiting(false);setNotice('OAuth client saved locally. Connect Google next.');}catch(e){setError(String(e));}finally{setBusy(false);}
 }
 return <section className="sheets-sync" aria-label="Google Sheets sync">
  <div className="applications-heading"><div><strong>Google Sheets</strong><p>{state?.target?<a href={state.target.url} target="_blank" rel="noreferrer">{state.target.title} ↗</a>:state?.connected?'Connected · choose a spreadsheet':'Keep a copy of your applications in Google Sheets.'}</p></div>
   <div className="sheets-actions"><button disabled={busy} onClick={()=>setExpanded(!expanded)}>{expanded?'Hide settings':state?.target?'Sheet settings':'Set up Google Sheets'}</button>{state?.target&&<button className="primary" disabled={busy||!state.connected} onClick={()=>void act('/sync')}>{busy?'Working…':'Sync to Google Sheets'}</button>}</div>
  </div>
  {notice&&<p role="status">{notice}</p>}{error&&<p role="alert">{error}</p>}
  {state?.automatic_sync?.error&&<p role="alert" className="error">Automatic sync failed: {state.automatic_sync.error} Use Sync to Google Sheets to retry.</p>}
  {state?.connected&&state.target&&state.automatic_sync?.pending&&!state.automatic_sync.error&&<p>{state.automatic_sync.syncing?'Syncing applications…':`Automatic sync scheduled for ${new Date(state.automatic_sync.due_at!*1000).toLocaleString()}. Sync manually to send them sooner.`}</p>}
  <p>New applications and edits sync one hour after the first unsynced change. A successful manual sync clears the pending batch.</p>
  {state?.last_sync&&<p>Last synced {new Date(state.last_sync.at).toLocaleString()} · {state.last_sync.total} applications.</p>}
  {expanded&&<div className="sheets-settings">
   {!state?.configured&&<><h3>One-time Google setup</h3><ol><li>In <a href="https://console.cloud.google.com/apis/library/sheets.googleapis.com" target="_blank" rel="noreferrer">Google Cloud</a>, choose or create a project and enable the Google Sheets API.</li><li>Configure Google Auth Platform for your account. If the app is in testing, add your Google email as a test user.</li><li>Create an OAuth client with application type <strong>Desktop app</strong>, then download its JSON file.</li></ol><label>OAuth client JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void upload(file);}}/></label><p>This file and your connection tokens stay in private local storage and are excluded from Git.</p></>}
   {state?.configured&&!state.connected&&<><p>Google requests permission to read and edit your spreadsheets. This app uses only the spreadsheet you select and its “Job Review applications” tab.</p><button disabled={busy} onClick={()=>void connect()}>{waiting?'Connect Google again':'Connect Google'}</button>{waiting&&<p role="status">Finish signing in in the Google tab, then return here.</p>}<button disabled={busy} onClick={()=>void act('')}>Refresh connection</button></>}
   {state?.connected&&<><label>Destination spreadsheet URL<input type="url" value={url} disabled={busy} onChange={e=>setUrl(e.target.value)} placeholder="https://docs.google.com/spreadsheets/d/…/edit"/></label><div className="sheets-actions"><button disabled={busy||!url.trim()} onClick={()=>void act('/target',{url})}>Save destination</button><button disabled={busy} onClick={()=>{setWaiting(false);void act('/disconnect');}}>Disconnect locally</button></div></>}
   <p>Sync sends all application tracker rows, including notes. It creates a “Job Review applications” tab if needed and updates its rows by Application ID. Keep that ID column intact. Edits to synced fields in Sheets are replaced by the app’s values on the next sync; extra columns and other tabs are left alone. No resumes or stories are uploaded. No AI usage.</p>
  </div>}
 </section>;
}
