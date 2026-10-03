import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
// Import compiled pure logic without requiring package metadata in private output.
const compiled=await readFile(new URL('../../.local/gap-tests/gapReviewLogic.js',import.meta.url),'utf8');
const {buildGapReview}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'));
const req=(id,category='requirement')=>({id,category,interpretation:'Build AI products',quote:'Build AI products for enterprise customers.'});
const evidence={source_title:'Example project',source_path:'story.md',start_line:3,quote:'I owned the workflow design.'};
const match=(alignment='partial',confidence='high',passages=[evidence])=>({judgment:{support:'transferable',assessment:{alignment,confidence},limitation:'Deployment ownership is not established.',clarification:'Did you own deployment, and in which project?'},passages});

test('absence and low confidence never become an experience gap or blocker',()=>{
 const gaps=buildGapReview([req('a'),req('b')],{a:match('insufficient_evidence','low',[]),b:match('partial','low')},null);
 assert.ok(gaps.every(g=>g.kind==='documentation'));
 assert.ok(gaps.every(g=>g.impact.includes('does not prove a missing skill')));
});
test('strong transferable work has no gap; supported partial work has coverage gap',()=>{
 const results={a:match('strong'),b:match()};const before=JSON.stringify(results);
 const gaps=buildGapReview([req('a'),req('b')],results,null);
 assert.equal(gaps.length,1);assert.equal(gaps[0].kind,'experience');
 assert.equal(gaps[0].question,results.b.judgment.clarification);
 assert.deepEqual(gaps[0].evidence,[evidence]);assert.equal(JSON.stringify(results),before);
});
test('ambiguity and preferences do not become required blockers',()=>{
 const gaps=buildGapReview([req('a','unclear'),req('b','preference')],{a:match(),b:match()},null);
 assert.equal(gaps[0].kind,'clarification');
 assert.ok(gaps[1].impact.includes('not a mandatory'));
 assert.ok(gaps.every(g=>g.kind!=='blocker'));
});
test('only sourced incompatible constraints are blockers, ahead of core gaps',()=>{
 const pursuit={constraints:[{kind:'h1b_transfer',finding:'unknown',quote:'',reason:'Not stated'},
 {kind:'location',finding:'incompatible',quote:'Onsite in London',reason:'Outside preferred country'},
 {kind:'core_coding',finding:'incompatible',quote:'',reason:'Unsupported conflict'}],core_evidence:[{requirement_id:'core'}]};
 const gaps=buildGapReview([req('other'),req('core','responsibility')],{other:match(),core:match()},pursuit);
 assert.equal(gaps.filter(g=>g.kind==='blocker').length,1);
 assert.equal(gaps[0].id,'constraint:location');assert.equal(gaps[1].id,'core');
 assert.equal(gaps.find(g=>g.id==='constraint:h1b_transfer').kind,'clarification');
});
test('pending rows generate no invented gaps',()=>assert.deepEqual(buildGapReview([req('pending')],{},null),[]));

test('tentative strong judgments need clarification, not a missing-skill label',()=>{
 const item=match('strong');item.judgment.assessment.review_required=true;
 const gaps=buildGapReview([req('a')],{a:item},null);
 assert.equal(gaps.length,1);assert.equal(gaps[0].kind,'clarification');
 assert.equal(gaps[0].label,'Tentative classification');
 item.judgment.assessment.user_confirmed=true;
 assert.deepEqual(buildGapReview([req('a')],{a:item},null),[]);
});
