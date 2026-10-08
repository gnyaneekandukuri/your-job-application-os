import test from 'node:test';
import assert from 'node:assert/strict';
import {Schema} from '@tiptap/pm/model';
import {EditorState} from '@tiptap/pm/state';
import {uniqueResumeOrigins,paragraphText} from '../.resume-tests/resumeDocumentLogic.js';
import {changedFromMaster} from '../.resume-tests/resumeChanges.js';
const schema=new Schema({nodes:{doc:{content:'paragraph+'},paragraph:{content:'text*',attrs:{origin:{default:null},manualId:{default:null}}},text:{}},marks:{resumeRight:{}}});
const paragraph=(text,origin)=>schema.node('paragraph',{origin},text?schema.text(text):undefined);
test('Enter assigns a new identity that stays stable during typing',()=>{
 let counter=0;
 let state=EditorState.create({schema,doc:schema.node('doc',null,[paragraph('Original point',0),paragraph('Other point',1)]),plugins:[uniqueResumeOrigins(()=>String(++counter))]});
 state=state.applyTransaction(state.tr.split(9)).state;
 assert.equal(state.doc.child(0).attrs.origin,0);
 assert.equal(state.doc.child(1).attrs.origin,null);
 const newId=state.doc.child(1).attrs.manualId;assert.match(newId,/^manual:/);
 assert.equal(state.doc.child(2).attrs.origin,1);
 state=state.applyTransaction(state.tr.insertText('x',11)).state;
 assert.equal(state.doc.child(1).attrs.manualId,newId);
 assert.equal(state.doc.child(2).attrs.origin,1);
});
test('New paragraph cannot masquerade as first master point',()=>{
 let state=EditorState.create({schema,doc:schema.node('doc',null,[paragraph('Name',0)]),plugins:[uniqueResumeOrigins(()=> 'new')]});
 state=state.applyTransaction(state.tr.insert(state.doc.content.size,paragraph('New',null))).state;
 assert.equal(state.doc.child(1).attrs.origin,null);assert.equal(state.doc.child(1).attrs.manualId,'manual:new');
});
test('Highlight comparison agrees on unchanged, rewritten, added and style-changed paragraphs',()=>{
 const original={text:'A supported point',kind:'bullet'};
 assert.equal(changedFromMaster({...original},original),false);
 assert.equal(changedFromMaster({...original,text:'A revised point'},original),true);
 assert.equal(changedFromMaster({...original,kind:'text'},original),true);
 assert.equal(changedFromMaster(original,undefined),true);
});
test('Aligned role and date reconstruct the original tab without false highlighting',()=>{
 const node=schema.node('paragraph',{},[schema.text('Product Manager'),schema.text('2025',[schema.mark('resumeRight')])]);
 assert.equal(paragraphText(node),'Product Manager\t2025');
 assert.equal(changedFromMaster({text:paragraphText(node),kind:'role'},{text:'Product Manager\t2025',kind:'role'}),false);
});
