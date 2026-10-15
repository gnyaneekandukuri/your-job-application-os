import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../../.local/scan-tests/scanReportLogic.js',import.meta.url),'utf8');
const {summarizeScan,assessmentLabel,needsHelp,boardRoles}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const role=(fields={})=>({id:'1',company:'Example',title:'PM',stage:'Screening finished',priority:'High priority',...fields});
test('separates new, repeat and unknown without assuming missing flags mean repeat',()=>{
 const s=summarizeScan([role({newly_saved:true}),role({newly_saved:false}),role()]);
 assert.deepEqual([s.total,s.newRecorded,s.repeat,s.unknownNew],[3,1,1,1]);
});
test('existing completed analyses do not inflate scan completions; recommendation is independent',()=>{
 const s=summarizeScan([role({assessment:{state:'COMPLETE'}}),role({assessment_requested:true,assessment:{state:'COMPLETE',recommendation:null}}),role({assessment:{state:'STALE'}}),role()]);
 assert.equal(s.requested,1);assert.equal(s.completedRequests,1);assert.deepEqual(s.highStates,{COMPLETE:2,STALE:1,NOT_STARTED:1});
 assert.equal(assessmentLabel(role({assessment:{state:'COMPLETE'}})),'Existing completed analysis');
});
test('unresolved roles are not rejections and low reasons are counted independently',()=>{
 const s=summarizeScan([role({priority:'Needs information'}),role({priority:'Not screened'}),role({priority:'Low priority',reason:'Staff title'}),role({priority:'Low priority',reason:'Staff title'})]);
 assert.equal(s.needs,1);assert.equal(s.unscreened,1);assert.equal(s.low,2);assert.deepEqual(s.lowReasons,{'Staff title':2});
});

test('board membership overlaps without duplicating the overall role and unknown history stays unknown',()=>{
 const roles=[role({source_boards:['yc','linkedin']}),role({id:'2',source_boards:['yc']}),role({id:'3'})];
 assert.equal(boardRoles(roles,'yc').length,2);assert.equal(boardRoles(roles,'linkedin').length,1);assert.equal(summarizeScan(roles).total,3);
});
test('needs help separates low priority from unresolved or failed processing',()=>{
 assert.equal(needsHelp(role({priority:'Low priority'})),false);
 assert.equal(needsHelp(role({priority:'Not screened'})),true);
 assert.equal(needsHelp(role({assessment:{state:'ERROR'}})),true);
 assert.equal(needsHelp(role({assessment:{state:'COMPLETE'}})),false);
});
