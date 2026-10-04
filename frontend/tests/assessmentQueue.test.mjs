import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const compiled=await readFile(new URL('../../.local/queue-tests/assessmentQueue.js',import.meta.url),'utf8');
const {queueGroup,nextAction,analysisFilter}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const role=(assessment)=>({job_id:'1',company:'Example',title:'PM',url:'',assessment:{state:'COMPLETE',score:76,recommendation:'CASUAL',...assessment}});
test('recommendations never count as candidate choices',()=>{assert.equal(queueGroup(role({})),'decision');assert.equal(queueGroup(role({recommendation:'ATTACK_HARD'})),'decision');});
test('only current chosen pursuits enter shortlist',()=>{assert.equal(queueGroup(role({choice:'CASUAL'})),'shortlist');assert.equal(queueGroup(role({choice:'ATTACK_HARD'})),'shortlist');assert.equal(queueGroup(role({choice:'SKIP'})),'skipped');});
test('outdated and failed work cannot enter shortlist',()=>{for(const state of ['STALE','ERROR','INTERRUPTED','NOT_STARTED'])assert.equal(queueGroup(role({state,choice:'ATTACK_HARD'})),'attention');assert.equal(queueGroup(role({choice:'CASUAL',listing_changed:true})),'decision');});
test('next action explains changed and unfinished work',()=>{assert.equal(nextAction(role({listing_changed:true})),'Review changed job description');assert.equal(nextAction(role({state:'STALE'})),'Review outdated assessment');assert.equal(queueGroup(role({state:'RUNNING'})),'running');});

test('worth applying filter contains only current positive recommendations',()=>{
 assert.equal(analysisFilter(role({}), 'worth'),true);
 assert.equal(analysisFilter(role({recommendation:'SKIP'}),'worth'),false);
 assert.equal(analysisFilter(role({choice:'SKIP'}),'worth'),false);
 assert.equal(analysisFilter(role({listing_changed:true}),'worth'),false);
});
test('negative recommendations and unfinished work remain accessible separately',()=>{
 assert.equal(analysisFilter(role({recommendation:'SKIP'}),'consider_passing'),true);
 assert.equal(analysisFilter(role({recommendation:'SKIP'}),'all'),true);
 for(const state of ['STALE','ERROR','RUNNING','NOT_STARTED']) {
  assert.equal(analysisFilter(role({state}),'all'),false);
  assert.equal(analysisFilter(role({state}),'issues'),true);
 }
 assert.equal(analysisFilter(role({choice:'SKIP'}),'passed'),true);
});
test('evidence review is visible but never labeled worth applying',()=>{
 const r=role({recommendation:'REVIEW',score:27,provisional:true});
 assert.equal(analysisFilter(r,'worth'),false);
 assert.equal(analysisFilter(r,'issues'),true);
 assert.equal(analysisFilter(r,'all'),true);
});

test('completed scores remain visible when the recommendation failed',()=>{
 const r=role({recommendation:null,recommendation_error:'Request too large'});
 assert.equal(analysisFilter(r,'all'),true);
 assert.equal(analysisFilter(r,'worth'),false);
 assert.equal(analysisFilter(r,'issues'),true);
});
