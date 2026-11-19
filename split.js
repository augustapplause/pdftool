import { state } from './state.js';
import { makeToolShell, button, status, toast, createDownload } from './ui.js';
import { renderPage, extractPages } from './pdf.js';

export async function renderSplit(){
  makeToolShell('split');if(!state.pdf)return;
  const main=document.getElementById('main-content');
  const intro=document.createElement('p');intro.textContent='Scroll left and right through the preview and check the pages to include. Use the output-order strip below the previews to move selected pages left or right.';main.append(intro);
  const toolbar=document.createElement('div');toolbar.className='toolbar';
  const data=state.toolState.split;
  const pageStrip=document.createElement('div');pageStrip.className='page-strip';
  const statusNode=status(`${data.selected.length} of ${state.pageCount} pages selected.`);
  const selectAll=button('Select all',()=>{data.selected=Array.from({length:state.pageCount},(_,i)=>i);data.order=[...data.selected];draw();});
  const clearAll=button('Clear all',()=>{data.selected=[];data.order=[];draw();});
  toolbar.append(selectAll,clearAll,statusNode);main.append(toolbar,pageStrip);
  const orderPanel=document.createElement('div');orderPanel.className='order-panel';orderPanel.style.marginTop='12px';
  const orderHeader=document.createElement('div');orderHeader.className='tool-header';const title=document.createElement('strong');title.textContent='Selected pages in output order';orderHeader.append(title,button('Restore original order',()=>{data.order=[...data.selected].sort((a,b)=>a-b);draw();}));
  const orderStrip=document.createElement('div');orderStrip.className='order-strip';orderPanel.append(orderHeader,orderStrip);main.append(orderPanel);
  const footer=document.createElement('div');footer.className='toolbar';footer.append(button('Generate Excerpt PDF',async()=>{if(!data.order.length)return;try{const bytes=await extractPages(state.bytes,data.order);const base=(state.file?.name||'document.pdf').replace(/\.pdf$/i,'')||'document';createDownload(bytes,`${base}_excerpt.pdf`,'application/pdf');toast('Excerpt PDF generated.');}catch(e){toast(`Could not split PDF: ${e.message}`);}},'primary'));main.append(footer);
  const previews=[];for(let i=0;i<state.pageCount;i++){const card=document.createElement('div');card.className='page-card';const render=await renderPage(state.pdf,i,.72,0);previews.push({card,render});const label=document.createElement('label');label.className='page-choice';const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=data.selected.includes(i);checkbox.onchange=()=>{if(checkbox.checked){if(!data.selected.includes(i))data.selected.push(i);if(!data.order.includes(i))data.order.push(i);}else{data.selected=data.selected.filter(v=>v!==i);data.order=data.order.filter(v=>v!==i);}data.selected.sort((a,b)=>a-b);draw();};const text=document.createElement('span');text.textContent=`Include page ${i+1}`;label.append(checkbox,text);card.append(render.canvas,label);pageStrip.append(card);}
  function draw(){
    previews.forEach(({card},i)=>{card.classList.toggle('selected',data.selected.includes(i));card.querySelector('input').checked=data.selected.includes(i);});
    statusNode.textContent=`${data.selected.length} of ${state.pageCount} page${state.pageCount===1?'':'s'} selected.`;orderStrip.replaceChildren();
    if(!data.order.length){const empty=document.createElement('div');empty.className='empty-order';empty.textContent='Select one or more pages above.';orderStrip.append(empty);}
    data.order.forEach((p,index)=>{const card=document.createElement('div');card.className='order-card';card.append(button('◀',()=>{if(index>0){[data.order[index-1],data.order[index]]=[data.order[index],data.order[index-1]];draw();}}));const label=document.createElement('div');label.className='order-label';label.textContent=`Page ${p+1}`;card.append(label,button('▶',()=>{if(index<data.order.length-1){[data.order[index+1],data.order[index]]=[data.order[index],data.order[index+1]];draw();}}));card.children[0].disabled=index===0;card.children[2].disabled=index===data.order.length-1;orderStrip.append(card);});
  }
  draw();
}
