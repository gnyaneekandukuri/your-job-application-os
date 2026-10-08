import { Plugin, PluginKey } from '@tiptap/pm/state';
import type { Node as ProseMirrorNode } from '@tiptap/pm/model';

// Enter inherits paragraph attributes. A second paragraph must not inherit the
// same master identity and citation bundle as the first one.
export function uniqueResumeOrigins(makeId:()=>string=()=>crypto.randomUUID()) {
 return new Plugin({key:new PluginKey('uniqueResumeOrigins'),appendTransaction(transactions,_old,state){
  if(!transactions.some(t=>t.docChanged))return null;
  const seen=new Set<number>();const manualIds=new Set<string>();const tr=state.tr;
  state.doc.forEach((node,pos)=>{
   const origin=node.attrs.origin;
   const valid=Number.isInteger(origin)&&origin>=0&&!seen.has(origin);
   if(valid){seen.add(origin);return;}
   const manualId=node.attrs.manualId;
   if(origin!==null||!manualId||manualIds.has(manualId)){
    const id='manual:'+makeId();manualIds.add(id);
    tr.setNodeMarkup(pos,undefined,{...node.attrs,origin:null,manualId:id});
   }else manualIds.add(manualId);
  });return tr.docChanged?tr:null;
 }});
}
export function paragraphText(node:ProseMirrorNode){
 let text='',right=false;
 node.forEach(child=>{const next=child.marks.some(m=>m.type.name==='resumeRight');if(next&&!right)text+='\t';right=next;text+=child.isText?child.text||'':'\n';});
 return text;
}
