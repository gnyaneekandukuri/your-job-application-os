import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const compiled=await readFile(new URL('../../.local/queue-tests/assessmentQueue.js',import.meta.url),'utf8');
const {queueGroup,nextAction,analysisFilter,processingStatus,recentResultsFirst}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const role=(assessment)=>({job_id:'1',company:'Example',title:'PM',url:'',assessment:{state:'COMPLETE',score:76,recommendation:'CASUAL',...assessment}});
test('recommendations never count as candidate choices',()=>{assert.equal(queueGroup(role({})),'decision');assert.equal(queueGroup(role({recommendation:'ATTACK_HARD'})),'decision');});
test('only current chosen pursuits enter shortlist',()=>{assert.equal(queueGroup(role({choice:'CASUAL'})),'shortlist');assert.equal(queueGroup(role({choice:'ATTACK_HARD'})),'shortlist');assert.equal(queueGroup(role({choice:'SKIP'})),'skipped');});
test('outdated and failed work cannot enter shortlist',()=>{for(const state of ['STALE','ERROR','INTERRUPTED','NOT_STARTED'])assert.equal(queueGroup(role({state,choice:'ATTACK_HARD'})),'attention');assert.equal(queueGroup(role({choice:'CASUAL',listing_changed:true})),'decision');});
test('next action explains changed and unfinished work',()=>{assert.equal(nextAction(role({listing_changed:true})),'Review changed job description');assert.equal(nextAction(role({state:'STALE'})),'Review outdated assessment');assert.equal(queueGroup(role({state:'RUNNING'})),'running');});

test('active recommendation filters exclude passed choices',()=>{
 assert.equal(analysisFilter(role({}), 'worth'),true);
 assert.equal(analysisFilter(role({recommendation:'SKIP'}),'worth'),false);
 assert.equal(analysisFilter(role({choice:'SKIP'}),'worth'),false);
 assert.equal(analysisFilter(role({listing_changed:true}),'worth'),true);
});
test('negative recommendations and unfinished work remain accessible separately',()=>{
 assert.equal(analysisFilter(role({recommendation:'SKIP'}),'consider_passing'),true);
 assert.equal(analysisFilter(role({recommendation:'SKIP'}),'all'),true);
 for(const state of ['STALE','ERROR','RUNNING','NOT_STARTED']) {
  assert.equal(analysisFilter(role({state,score:null}),'all'),false);
  assert.ok(processingStatus(role({state})));
 }
 // Outdated results that kept a score stay visible under All (tagged Outdated), never under Worth applying.
 assert.equal(analysisFilter(role({state:'STALE',score:61}),'all'),true);
 assert.equal(analysisFilter(role({state:'STALE',score:61}),'worth'),false);

});
test('evidence review is visible but never labeled worth applying',()=>{
 const r=role({recommendation:'REVIEW',score:27,provisional:true});
 assert.equal(analysisFilter(r,'worth'),false);
 assert.equal(analysisFilter(r,'review'),true);
 assert.equal(analysisFilter(r,'all'),true);
});

test('completed scores remain visible when the recommendation failed',()=>{
 const r=role({recommendation:null,recommendation_error:'Request too large'});
 assert.equal(analysisFilter(r,'all'),true);
 assert.equal(analysisFilter(r,'worth'),false);
 assert.equal(analysisFilter(r,'unavailable'),true);
});

test('completed review recommendations are not processing failures',()=>{
 assert.equal(processingStatus(role({recommendation:'REVIEW'})),null);
 assert.equal(processingStatus(role({state:'PAUSED'})),'Paused');
 assert.equal(processingStatus(role({state:'STALE'})),'Outdated');
 assert.equal(processingStatus(role({recommendation:null})),'Recommendation unavailable');
});
test('recommendation groups and passed view partition completed results',()=>{
 for(const recommendation of ['CASUAL','ATTACK_HARD','REVIEW','SKIP',null]) {
  const r=role({recommendation,choice:'SKIP'});
  assert.equal(['worth','review','consider_passing','unavailable','passed'].filter(f=>analysisFilter(r,f)).length,1);
 }
});

test('most recently analyzed first, then score, then scan recency',()=>{
 const old={...role({score:99,assessed_at:'2026-10-10T12:00:00Z'}),job_id:'old',last_scan_id:44};
 const seen={...role({score:70,assessed_at:'2026-10-06T10:00:00Z'}),job_id:'seen',last_scan_id:45};
 const fresh={...seen,job_id:'fresh',assessment:{...seen.assessment,score:60,assessed_at:'2026-10-10T11:00:00Z'}};
 const manual={...old,job_id:'manual',last_scan_id:null};
 const unknown={...seen,job_id:'unknown',assessment:{...seen.assessment,score:null}};
 assert.deepEqual([manual,old,fresh,unknown,seen].sort(recentResultsFirst).map(r=>r.job_id),['old','manual','fresh','seen','unknown']);
 assert.equal(seen.assessment.assessed_at,'2026-10-06T10:00:00Z');
});

 test('passing hides a completed role from active views and reconsider restores it',()=>{
 const passed=role({choice:'SKIP'});
 for(const filter of ['all','worth','review','consider_passing','unavailable'])assert.equal(analysisFilter(passed,filter),false);
 assert.equal(analysisFilter(passed,'passed'),true);
 assert.equal(analysisFilter(role({choice:'CASUAL'}),'all'),true);
 assert.equal(analysisFilter(role({choice:'CASUAL'}),'passed'),false);
 });
