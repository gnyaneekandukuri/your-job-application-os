import { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import { Extension, Mark } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import StarterKit from '@tiptap/starter-kit';

export type ResumeBlock={item_id?:string;format?:{font_family:string;font_size:number;bold:boolean;italic:boolean;align:string};kind:'heading'|'contact'|'role'|'bullet'|'text';text:string;citations:{source_id:string;quote:string}[];reason:string};
const Metadata=Extension.create({name:'resumeMetadata',addGlobalAttributes(){return [{types:['paragraph'],attributes:{
 origin:{default:null,rendered:false},format:{default:null,renderHTML:attrs=>attrs.format?{style:`font-family:${attrs.format.font_family};font-size:${attrs.format.font_size}pt;font-weight:${attrs.format.bold?700:400};font-style:${attrs.format.italic?'italic':'normal'};text-align:${attrs.format.align};`}:{}},kind:{default:'text',renderHTML:attrs=>({'data-kind':attrs.kind})}
}}];}});
const RightField=Mark.create({name:'resumeRight',renderHTML(){return ['span',{class:'resume-right-field'},0];}});
export function ResumeDocument({initial,master,disabled,onChange,onSelect}:{initial:ResumeBlock[];master?:ResumeBlock[];disabled:boolean;onChange:(blocks:ResumeBlock[])=>void;onSelect:(index:number)=>void}) {
 const changes=Extension.create({name:'resumeChanges',addProseMirrorPlugins(){return [new Plugin({key:new PluginKey('resumeChanges'),props:{decorations(state){
  if(!master)return DecorationSet.empty;
  const marks:Decoration[]=[];
  state.doc.forEach((node,pos)=>{
   const source=initial[node.attrs.origin];const original=master.find(b=>b.item_id===source?.item_id);
   let text='',right=false;node.forEach(child=>{const next=child.marks.some(m=>m.type.name==='resumeRight');if(next&&!right)text+='\t';right=next;text+=child.isText?child.text||'':'\n';});
   if(!original||text!==original.text||node.attrs.kind!==original.kind)marks.push(Decoration.node(pos,pos+node.nodeSize,{class:'resume-changed',title:original?'Changed from master':'Added point'}));
  });return DecorationSet.create(state.doc,marks);
 }}})];}});
 const editor=useEditor({
  shouldRerenderOnTransaction:true,
  extensions:[StarterKit.configure({heading:false,bulletList:false,orderedList:false,listItem:false,listKeymap:false,blockquote:false,codeBlock:false,horizontalRule:false,bold:false,italic:false,strike:false,code:false,link:false,underline:false,trailingNode:false}),Metadata,RightField,changes],
  content:{type:'doc',content:initial.map((b,i)=>({type:'paragraph',attrs:{origin:i,kind:b.kind,format:b.format||null},content:b.text?b.text.split('\t').filter(Boolean).map((text,j)=>({type:'text',text,marks:j?[{type:'resumeRight'}]:[]})):[]}))},
  editorProps:{attributes:{class:'resume-paper',role:'textbox','aria-label':'Resume document','aria-multiline':'true'},
   // Paste text only: outside HTML cannot inject source identifiers or formatting.
   handlePaste(view,event){const text=event.clipboardData?.getData('text/plain');if(text===undefined)return false;event.preventDefault();view.dispatch(view.state.tr.insertText(text));return true;}},
  onUpdate:({editor,transaction})=>{if(!transaction.docChanged)return;const next:ResumeBlock[]=[];editor.state.doc.forEach(node=>{const source=initial[node.attrs.origin];let text='',right=false;node.forEach(child=>{const isRight=child.marks.some(m=>m.type.name==='resumeRight');if(isRight&&!right)text+='\t';right=isRight;text+=child.isText?child.text||'':'\n';});next.push({...source,kind:node.attrs.kind,text,citations:source?.citations||[],reason:source?.reason||'Added during editing; needs supporting evidence.'});});onChange(next);},
  onSelectionUpdate:({editor})=>onSelect(editor.state.selection.$from.index(0)),
 });
 useEffect(()=>{editor?.setEditable(!disabled, false);},[editor,disabled]);
 if(!editor)return null;
 return <div className="resume-document-container"><div className="resume-format-toolbar" aria-label="Document formatting">
  <button disabled={disabled||!editor.can().undo()} onClick={()=>editor.chain().focus().undo().run()}>Undo</button>
  <button disabled={disabled||!editor.can().redo()} onClick={()=>editor.chain().focus().redo().run()}>Redo</button>
  <label>Paragraph style<select disabled={disabled} value={editor.getAttributes('paragraph').kind||'text'} onChange={e=>editor.chain().focus().updateAttributes('paragraph',{kind:e.target.value}).run()}>
   <option value="text">Text</option><option value="contact">Name / contact</option><option value="heading">Section heading</option><option value="role">Company / role</option><option value="bullet">Bullet</option>
  </select></label><span>Click the resume to edit</span>
 </div><EditorContent editor={editor}/></div>;
}
