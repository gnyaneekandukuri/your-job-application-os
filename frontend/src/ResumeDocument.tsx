import { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import { Extension } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';

export type ResumeBlock={kind:'heading'|'contact'|'role'|'bullet'|'text';text:string;citations:{source_id:string;quote:string}[];reason:string};
const Metadata=Extension.create({name:'resumeMetadata',addGlobalAttributes(){return [{types:['paragraph'],attributes:{
 origin:{default:null,rendered:false},kind:{default:'text',renderHTML:attrs=>({'data-kind':attrs.kind})}
}}];}});
export function ResumeDocument({initial,disabled,onChange,onSelect}:{initial:ResumeBlock[];disabled:boolean;onChange:(blocks:ResumeBlock[])=>void;onSelect:(index:number)=>void}) {
 const editor=useEditor({
  shouldRerenderOnTransaction:true,
  extensions:[StarterKit.configure({heading:false,bulletList:false,orderedList:false,listItem:false,listKeymap:false,blockquote:false,codeBlock:false,horizontalRule:false,bold:false,italic:false,strike:false,code:false,link:false,underline:false,trailingNode:false}),Metadata],
  content:{type:'doc',content:initial.map((b,i)=>({type:'paragraph',attrs:{origin:i,kind:b.kind},content:b.text?[{type:'text',text:b.text}]:[]}))},
  editorProps:{attributes:{class:'resume-paper',role:'textbox','aria-label':'Resume document','aria-multiline':'true'},
   // Paste text only: outside HTML cannot inject source identifiers or formatting.
   handlePaste(view,event){const text=event.clipboardData?.getData('text/plain');if(text===undefined)return false;event.preventDefault();view.dispatch(view.state.tr.insertText(text));return true;}},
  onUpdate:({editor,transaction})=>{if(!transaction.docChanged)return;const next:ResumeBlock[]=[];editor.state.doc.forEach(node=>{const source=initial[node.attrs.origin];next.push({kind:node.attrs.kind,text:node.textBetween(0,node.content.size,'\n','\n'),citations:source?.citations||[],reason:source?.reason||'Added during editing; needs supporting evidence.'});});onChange(next);},
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
