import { useEffect } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import { Extension, Mark } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import StarterKit from '@tiptap/starter-kit';
import {addedWordRanges,changedFromMaster} from './resumeChanges';
import {uniqueResumeOrigins,paragraphText} from './resumeDocumentLogic';

export type ResumeBlock={change_type?:string;jd_quote?:string;item_id?:string;format?:{font_family:string;font_size:number;bold:boolean;italic:boolean;align:string};kind:'heading'|'contact'|'role'|'bullet'|'text';text:string;citations:{source_id:string;quote:string}[];reason:string};
type BlockFormat={font_family:string;font_size:number;bold:boolean;italic:boolean;align:string;space_before?:number|null;space_after?:number|null;rule?:string|null;indent?:number|null;hanging?:number|null};
// Mirror the master's Word layout: spacing, section rules and bullet indents (Word's default bullet when unknown).
function formatStyle(f:BlockFormat,kind:string){
 const css=[`font-family:${f.font_family}`,`font-size:${f.font_size}pt`,`font-weight:${f.bold?700:400}`,`font-style:${f.italic?'italic':'normal'}`,`text-align:${f.align}`];
 if(f.space_before!=null)css.push(`margin-top:${f.space_before}pt`);
 if(f.space_after!=null)css.push(`margin-bottom:${f.space_after}pt`);
 if(kind==='heading')css.push(f.rule==='below'?'border-bottom:1px solid #444;border-top:0':f.rule==='above'?'border-top:1.5px solid #222;border-bottom:0':'border:0','text-transform:none','letter-spacing:normal','padding-top:0','padding-bottom:0');
 if(kind==='bullet'){const indent=f.indent||36;css.push(`padding-left:${indent}pt`,`--bullet-at:${Math.max(0,indent-(f.hanging??18))}pt`);}
 return css.join(';')+';';
}
const Metadata=Extension.create({name:'resumeMetadata',addGlobalAttributes(){return [{types:['paragraph'],attributes:{
 origin:{default:null,rendered:false},manualId:{default:null,rendered:false},format:{default:null,renderHTML:attrs=>attrs.format?{style:formatStyle(attrs.format,attrs.kind)}:{}},kind:{default:'text',renderHTML:attrs=>({'data-kind':attrs.kind})}
}}];}});
const RightField=Mark.create({name:'resumeRight',renderHTML(){return ['span',{class:'resume-right-field'},0];}});
export type KeywordSuggestion={id:string;item_id:string;keywords:string[];question:string;kept?:boolean};
// Keep/undo controls for a job keyword already placed in this point; not part of the editable text.
function keywordControl(s:KeywordSuggestion,onDecide:(id:string,decision:'accept'|'reject')=>void){
 const box=document.createElement('span');box.className='keyword-inline';box.contentEditable='false';
 const label=document.createElement('span');label.textContent=`${s.kept?'Added job keyword':'Job keyword'}: ${s.keywords.join(', ')}${s.question?` · ${s.question}`:''}`;box.append(label);
 // A kept edit was proven by the candidate's own words; it only offers undo.
 for(const [decision,mark,name] of (s.kept?[['reject','✕','Undo']]:[['accept','✓','Keep'],['reject','✕','Undo']]) as [('accept'|'reject'),string,string][]){
  const button=document.createElement('button');button.type='button';button.textContent=mark;button.title=`${name} this keyword edit`;button.setAttribute('aria-label',`${name} job keyword ${s.keywords.join(', ')}`);
  button.onmousedown=e=>e.preventDefault();button.onclick=e=>{e.preventDefault();onDecide(s.id,decision);};box.append(button);
 }
 return box;
}
export function ResumeDocument({initial,master,disabled,onChange,onSelect,suggestions=[],onDecide}:{initial:ResumeBlock[];master?:ResumeBlock[];disabled:boolean;onChange:(blocks:ResumeBlock[])=>void;onSelect:(index:number)=>void;suggestions?:KeywordSuggestion[];onDecide?:(id:string,decision:'accept'|'reject')=>void}) {
 const changes=Extension.create({name:'resumeChanges',addProseMirrorPlugins(){return [new Plugin({key:new PluginKey('resumeChanges'),props:{decorations(state){
  if(!master&&!suggestions.length)return DecorationSet.empty;
  const marks:Decoration[]=[];
  state.doc.forEach((node,pos)=>{
   const source=initial[node.attrs.origin];const original=master?.find(b=>b.item_id===source?.item_id);
   const text=paragraphText(node);
   if(master&&changedFromMaster({text,kind:node.attrs.kind},original)){
    // Words added to the candidate's own point are green; a rewritten, replaced or new point is yellow.
    const added=original&&original.kind===node.attrs.kind?addedWordRanges(original.text.replace(/\t/g,''),node.textContent):null;
    if(added)for(const [from,to] of added)marks.push(Decoration.inline(pos+1+from,pos+1+to,{class:'resume-added-words',title:'Words added to your point'}));
    else marks.push(Decoration.node(pos,pos+node.nodeSize,{class:'resume-changed',title:original?'This point was rewritten':'Added point'}));
   }
   const suggestion=suggestions.find(s=>s.item_id===source?.item_id);
   if(!suggestion||!onDecide)return;
   for(const word of suggestion.keywords){const at=node.textContent.toLowerCase().indexOf(word.toLowerCase());if(at>=0)marks.push(Decoration.inline(pos+1+at,pos+1+at+word.length,{class:'keyword-inline-word'}));}
   marks.push(Decoration.widget(pos+node.nodeSize-1,()=>keywordControl(suggestion,onDecide),{side:1,ignoreSelection:true,key:suggestion.id}));
  });return DecorationSet.create(state.doc,marks);
 }}})];}});
 const editor=useEditor({
  shouldRerenderOnTransaction:true,
  extensions:[StarterKit.configure({heading:false,bulletList:false,orderedList:false,listItem:false,listKeymap:false,blockquote:false,codeBlock:false,horizontalRule:false,bold:false,italic:false,strike:false,code:false,link:false,underline:false,trailingNode:false}),Metadata,RightField,changes,Extension.create({name:'resumeIdentity',addProseMirrorPlugins(){return [uniqueResumeOrigins()];}})],
  content:{type:'doc',content:initial.map((b,i)=>({type:'paragraph',attrs:{origin:i,kind:b.kind,format:b.format||null},content:b.text?b.text.split('\t').filter(Boolean).map((text,j)=>({type:'text',text,marks:j?[{type:'resumeRight'}]:[]})):[]}))},
  editorProps:{attributes:{class:'resume-paper',role:'textbox','aria-label':'Resume document','aria-multiline':'true'},
   // Paste text only: outside HTML cannot inject source identifiers or formatting.
   handlePaste(view,event){const text=event.clipboardData?.getData('text/plain');if(text===undefined)return false;event.preventDefault();view.dispatch(view.state.tr.insertText(text));return true;}},
  onUpdate:({editor,transaction})=>{if(!transaction.docChanged)return;const next:ResumeBlock[]=[];editor.state.doc.forEach(node=>{const source=initial[node.attrs.origin];next.push({...source,item_id:source?.item_id||node.attrs.manualId,format:node.attrs.format||source?.format,kind:node.attrs.kind,text:paragraphText(node),citations:source?.citations||[],change_type:source&&paragraphText(node)===source.text?source.change_type:'manual',jd_quote:source&&paragraphText(node)===source.text?source.jd_quote:'',reason:source&&paragraphText(node)===source.text?source.reason:'Manually edited; review evidence and JD alignment again.'});});onChange(next);},
  onSelectionUpdate:({editor})=>onSelect(editor.state.selection.$from.index(0)),
 });
 useEffect(()=>{editor?.setEditable(!disabled, false);},[editor,disabled]);
 if(!editor)return null;
 return <div className="resume-document-container"><div className="resume-format-toolbar" aria-label="Document formatting">
  <button disabled={disabled||!editor.can().undo()} onClick={()=>editor.chain().focus().undo().run()}>Undo</button>
  <button disabled={disabled||!editor.can().redo()} onClick={()=>editor.chain().focus().redo().run()}>Redo</button>
  <label>Paragraph style<select disabled={disabled} value={editor.getAttributes('paragraph').kind||'text'} onChange={e=>editor.chain().focus().updateAttributes('paragraph',{kind:e.target.value,format:{font_family:'Times New Roman',font_size:10,italic:false,...editor.getAttributes('paragraph').format,bold:['heading','role'].includes(e.target.value),align:e.target.value==='contact'?'center':'left'}}).run()}>
   <option value="text">Text</option><option value="contact">Name / contact</option><option value="heading">Section heading</option><option value="role">Company / role</option><option value="bullet">Bullet</option>
  </select></label><span>Click the resume to edit</span>
 </div><EditorContent editor={editor}/></div>;
}
