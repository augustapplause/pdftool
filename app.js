import { APP_VERSION, TOOL_LABELS } from './config.js';
import { state, resetDocument } from './state.js';
import { openPdf } from './pdf.js';
import { main, toast } from './ui.js';
import { renderRedact } from './redact.js';
import { renderXlsx } from './xlsx-tool.js';
import { renderText } from './text.js';
import { renderRotate } from './rotate.js';
import { renderAnnotate } from './annotate.js';
import { renderSplit } from './split.js';

const renderers = { redact: renderRedact, xlsx: renderXlsx, text: renderText, rotate: renderRotate, annotate: renderAnnotate, split: renderSplit };
document.getElementById('app-version').textContent = APP_VERSION;

async function renderActiveTool(){
  if(!state.activeTool)return;
  try{await renderers[state.activeTool]();}catch(error){console.error(error);main.innerHTML='';const p=document.createElement('p');p.className='status warning';p.textContent=`Unable to render ${TOOL_LABELS[state.activeTool]}: ${error.message}`;main.append(p);}
}

function updateNavigation(){
  document.querySelectorAll('.nav-button').forEach((button)=>button.classList.toggle('active',button.dataset.tool===state.activeTool));
  document.getElementById('selected-function').textContent=state.activeTool?`Selected: ${TOOL_LABELS[state.activeTool]}`:'Select a function to begin.';
  document.getElementById('reset-tool').hidden=!state.activeTool;
}

document.querySelectorAll('.nav-button').forEach((button)=>button.addEventListener('click',()=>{
  const next=button.dataset.tool;if(next===state.activeTool)return;
  window.dispatchEvent(new Event('pdf-tools-tool-change'));
  resetDocument(false);state.activeTool=next;updateNavigation();renderActiveTool();
}));

document.getElementById('reset-tool').addEventListener('click',()=>{window.dispatchEvent(new Event('pdf-tools-tool-change'));resetDocument(true);renderActiveTool();});

main.addEventListener('change',async(event)=>{
  if(event.target.id!=='pdf-upload')return;
  const file=event.target.files?.[0];if(!file)return;
  if(file.type!=='application/pdf'&&!file.name.toLowerCase().endsWith('.pdf')){toast('Please select a PDF file.');return;}
  try{
    const opened=await openPdf(file);state.file=file;state.bytes=opened.bytes;state.pdf=opened.pdf;state.pageCount=opened.pageCount;state.pageIndex=0;
    toast(`Loaded PDF with ${state.pageCount} page(s).`);await renderActiveTool();
  }catch(error){console.error(error);toast(`Unable to open PDF: ${error.message}`,7000);}
});

window.addEventListener('pdf-page-change',renderActiveTool);
updateNavigation();
