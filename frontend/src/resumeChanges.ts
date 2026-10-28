export function changedFromMaster(block:{text:string;kind:string},original?:{text:string;kind:string}){
 return !original||block.text!==original.text||block.kind!==original.kind;
}

// Character ranges of words added to the original, when the edit only adds words (every original word kept, in order).
// Returns null for rewrites, replacements and removals, which are shown as a changed paragraph instead.
export function addedWordRanges(original:string,text:string):[number,number][]|null{
 const words=(s:string)=>[...s.matchAll(/\S+/g)].map(m=>({start:m.index!,end:m.index!+m[0].length,key:m[0].toLowerCase().replace(/^[^\p{L}\p{N}$~]+|[^\p{L}\p{N}%+]+$/gu,'')}));
 const a=words(original),b=words(text);
 if(!a.length||b.length<=a.length)return null;
 // Longest common subsequence of words; a pure addition keeps all of the original.
 const table=Array.from({length:a.length+1},()=>new Array<number>(b.length+1).fill(0));
 for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)table[i][j]=a[i].key===b[j].key?table[i+1][j+1]+1:Math.max(table[i+1][j],table[i][j+1]);
 if(table[0][0]!==a.length)return null;
 const added:[number,number][]=[];let i=0;
 for(let j=0;j<b.length;j++){
  if(i<a.length&&a[i].key===b[j].key&&table[i][j]===table[i+1][j+1]+1){i++;continue;}
  const last=added[added.length-1];
  if(last&&text.slice(last[1],b[j].start).trim()==='')last[1]=b[j].end;else added.push([b[j].start,b[j].end]);
 }
 return added;
}
