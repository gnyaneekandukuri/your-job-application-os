import {useEffect, useState} from 'react';
import './applicationStats.css';
type Stats = {
 primary: {
  hs_interviews: {interviews:number;hs_applications:number;rate:number;weekly:{week_start:string;hs_applications:number;interviews:number}[]};
  applied_within_6h: {within_6h:number;measurable:number;unknown:number;rate:number;weekly:{week_start:string;within_6h:number;measurable:number;unknown:number}[]};
 };
 funnel: {label:string;count:number;percent:number}[];
 approaches: {label:string;applied:number;screens:number;screen_rate:number;rejection_rate:number}[];
 weekly: {week_start:string;count:number}[];
 withdrawn:number;
 unknown_statuses:Record<string,number>;
};
export function ApplicationStats(){
 const [stats,setStats]=useState<Stats|null>(null),[error,setError]=useState('');
 useEffect(()=>{
  const controller=new AbortController();
  let loading=false;
  // Focus refreshes must not stack slow requests on the server.
  async function load(){if(loading)return;loading=true;try{
   const response=await fetch('/api/applications/stats',{signal:controller.signal});
   if(!response.ok)throw Error('Could not load stats.');
   const data:Stats=await response.json();setStats(data);setError('');
  }catch(e){if(!controller.signal.aborted)setError(String(e));}finally{loading=false;}}
  void load();window.addEventListener('focus',load);
  return()=>{controller.abort();window.removeEventListener('focus',load);};
 },[]);
 if(error)return <p role="alert">{error} Reopen Stats to retry.</p>;
 if(!stats)return <p role="status">Loading stats…</p>;
 const hs=stats.primary.hs_interviews, timing=stats.primary.applied_within_6h;
 const maximum=Math.max(1,...stats.weekly.map(w=>w.count));
 const unknown=Object.values(stats.unknown_statuses).reduce((sum,n)=>sum+n,0);
 return <section className="application-stats" aria-label="Application stats">
  <div className="stats-primary">
   <section className="stats-card stats-headline"><h2>Interview calls from HS resume</h2><strong>{hs.interviews}</strong><span>{hs.interviews} of {hs.hs_applications} HS applications · {hs.rate}%</span><p className="stats-note">ATS-HS (untailored) applications currently Interviewing or Offer.</p></section>
   <section className="stats-card stats-headline"><h2>Applied within 6h of posting</h2><strong>{timing.within_6h}</strong><span>{timing.within_6h} of {timing.measurable} measurable · {timing.unknown} unknown · {timing.rate}%</span><p className="stats-note">Uses recorded time as the applied-time proxy and absolute board posting times. Missing or imprecise times and applications recorded before posting are unknown.</p></section>
  </div>
  <section className="stats-box"><h2>Headline metrics by week <small>Monday start · applied date</small></h2>{stats.weekly.length?<table><thead><tr>{['Week starting','HS applications','HS interviews','Within 6h','Measurable','Unknown'].map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{timing.weekly.map(row=>{const h=hs.weekly.find(w=>w.week_start===row.week_start);return <tr key={row.week_start}><th scope="row">{row.week_start}</th><td>{h?.hs_applications??0}</td><td>{h?.interviews??0}</td><td>{row.within_6h}</td><td>{row.measurable}</td><td>{row.unknown}</td></tr>;})}</tbody></table>:<p>No applications yet.</p>}</section>
  <div className="stats-funnel">{stats.funnel.map(card=><div className="stats-card" key={card.label}><span>{card.label}</span><strong>{card.count}</strong><small>{card.percent}% of Applied</small></div>)}</div>
  <section className="stats-box"><h2>What's working</h2><table><thead><tr>{['Approach','Applied','Screens','Screen rate','Rejection rate'].map(label=><th key={label}>{label}</th>)}</tr></thead><tbody>{stats.approaches.map(row=><tr key={row.label}><th scope="row">{row.label}</th><td>{row.applied}</td><td>{row.screens}</td><td>{row.applied?`${row.screen_rate}%`:'—'}</td><td>{row.applied?`${row.rejection_rate}%`:'—'}</td></tr>)}</tbody></table><p className="stats-note">Screens includes Interview and Offer. Resume and priority rows overlap.</p></section>
  <section className="stats-box"><h2>Applications per week <small>Monday start · applied date</small></h2>{stats.weekly.length?<div className="stats-chart" role="list" aria-label="Weekly application counts">{stats.weekly.map((week,index)=><div className="stats-week" role="listitem" key={week.week_start} title={`Week of ${week.week_start}: ${week.count} applications`} aria-label={`Week of ${week.week_start}: ${week.count} applications`}><span className="stats-bar-space"><span className="stats-bar" style={{height:`${week.count/maximum*100}%`}} /></span><small>{index%Math.max(1,Math.ceil(stats.weekly.length/12))===0?week.week_start.slice(5):' '}</small></div>)}</div>:<p>No applications yet. Your saved applications will appear here.</p>}</section>
  <p className="stats-note">All saved applications. Current statuses only; earlier stages are not recorded. Screen has no separate status yet.{stats.withdrawn>0&&` ${stats.withdrawn} withdrawn included in Applied only.`}{unknown>0&&` ${unknown} unrecognized statuses included in Applied only.`}</p>
 </section>;
}
