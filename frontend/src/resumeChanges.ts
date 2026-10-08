export function changedFromMaster(block:{text:string;kind:string},original?:{text:string;kind:string}){
 return !original||block.text!==original.text||block.kind!==original.kind;
}
