// Change citation text nodes only. Code, existing links and source definitions
// retain their original Markdown meaning and never become citation controls.
interface MarkdownNode {type:string;value?:string;url?:string;children?:MarkdownNode[];}
export function remarkCitations(){
  return (tree:MarkdownNode)=>{
    function walk(node:MarkdownNode){
      if(['link','linkReference','definition','code','inlineCode','html'].includes(node.type)||!node.children)return;
      node.children=node.children.flatMap(child=>{
        if(child.type!=='text'||!child.value){walk(child);return [child];}
        const pieces:MarkdownNode[]=[];let cursor=0;
        for(const match of child.value.matchAll(/\[(\d+)\]/g)){
          const at=match.index!;if(at>cursor)pieces.push({type:'text',value:child.value.slice(cursor,at)});
          pieces.push({type:'link',url:`#source-${match[1]}`,children:[{type:'text',value:`[${match[1]}]`}]});cursor=at+match[0].length;
        }
        if(!cursor)return [child];if(cursor<child.value.length)pieces.push({type:'text',value:child.value.slice(cursor)});return pieces;
      });
    }
    walk(tree);
  };
}
