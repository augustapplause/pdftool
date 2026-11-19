import { state } from './state.js';
import { makeToolShell, button, status, toast, createDownload } from './ui.js';
import { renderPage } from './pdf.js';
import { loadGlobalScript } from './vendor.js';

const NS = 'http://www.w3.org/2000/svg';
const TOOL_NAMES = ['select', 'text', 'image', 'rectangle', 'circle', 'line', 'arrow', 'pen'];
let keydownController;
const COLORS = ['#000000', '#ffffff', '#d00000', '#e56b00', '#f2c500', '#008a2e', '#00a7b5', '#0057d9', '#7a32a8', '#7a4a20', '#777777'];

function svgEl(tag, attrs = {}) { const el = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v))); return el; }
function objectBounds(o) {
  if (o.type === 'line' || o.type === 'arrow') return { x: Math.min(o.x1, o.x2), y: Math.min(o.y1, o.y2), w: Math.max(1, Math.abs(o.x2 - o.x1)), h: Math.max(1, Math.abs(o.y2 - o.y1)) };
  if (o.type === 'pen') { const xs = o.points.map(p => p.x), ys = o.points.map(p => p.y); return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(1, Math.max(...xs) - Math.min(...xs)), h: Math.max(1, Math.max(...ys) - Math.min(...ys)) }; }
  return { x: o.x, y: o.y, w: Math.max(1, o.w), h: Math.max(1, o.h) };
}
function copy(o) { return JSON.parse(JSON.stringify(o)); }
function dataUrlToBytes(dataUrl) { const encoded=dataUrl.split(',')[1]||''; const binary=atob(encoded); const bytes=new Uint8Array(binary.length); for(let i=0;i<binary.length;i+=1)bytes[i]=binary.charCodeAt(i); return bytes; }
function saveHistory(page) {
  const data = state.toolState.annotate; const objects = data.objects.get(page) || [];
  if (!data.history.has(page)) data.history.set(page, []);
  const history = data.history.get(page); history.push(copy(objects)); if (history.length > 30) history.shift();
}
function colorRgb(hex, rgbFactory) { const n = hex.replace('#', ''); return rgbFactory(parseInt(n.slice(0,2),16)/255, parseInt(n.slice(2,4),16)/255, parseInt(n.slice(4,6),16)/255); }
function rotateScreenPoint(point, center, angle) { const r=angle*Math.PI/180,c=Math.cos(r),s=Math.sin(r),dx=point.x-center.x,dy=point.y-center.y; return {x:center.x+dx*c-dy*s,y:center.y+dx*s+dy*c}; }

export async function renderAnnotate() {
  makeToolShell('annotate'); if (!state.pdf) return;
  const page = state.pageIndex; const data = state.toolState.annotate; const settings = data.settings;
  if (!data.objects.has(page)) data.objects.set(page, []);
  if (!data.history.has(page)) data.history.set(page, []);
  if (!data.selected.has(page)) data.selected.set(page, null);
  const objects = data.objects.get(page);
  const toolbar = document.createElement('div'); toolbar.className = 'annotation-toolbar';
  const group = () => { const el = document.createElement('div'); el.className = 'group'; toolbar.append(el); return el; };
  const addBtn = (parent, label, onClick, active = false, disabled = false) => { const b = button(label, onClick); if (active) b.classList.add('active'); b.disabled = disabled; parent.append(b); return b; };
  let g = group();
  addBtn(g, 'Previous page', () => { state.pageIndex = Math.max(0, page - 1); window.dispatchEvent(new Event('pdf-page-change')); }, false, page === 0);
  const pageSelect = document.createElement('select'); pageSelect.className = 'page-select';
  for (let i=0;i<state.pageCount;i++){const op=document.createElement('option');op.value=String(i);op.textContent=`Page ${i+1} of ${state.pageCount}`;op.selected=i===page;pageSelect.append(op);} pageSelect.onchange=()=>{state.pageIndex=Number(pageSelect.value);window.dispatchEvent(new Event('pdf-page-change'));}; g.append(pageSelect);
  addBtn(g, 'Next page', () => { state.pageIndex = Math.min(state.pageCount - 1, page + 1); window.dispatchEvent(new Event('pdf-page-change')); }, false, page === state.pageCount - 1);
  g = group();
  TOOL_NAMES.forEach((name) => addBtn(g, name[0].toUpperCase()+name.slice(1), () => { settings.tool = name; updateToolbar(); }, settings.tool === name, name === 'image' && !data.activeImageId));
  g = group();
  COLORS.forEach((color) => { const b = document.createElement('button'); b.className = 'button'; b.style.cssText = `width:25px;height:25px;padding:0;background:${color};border:1px solid #777`; b.title = color; b.onclick = () => { settings.color = color; applySetting(); updateToolbar(); }; g.append(b); });
  const custom = document.createElement('input'); custom.type='color'; custom.value=settings.color; custom.title='Custom colour'; custom.oninput=()=>{settings.color=custom.value;applySetting();}; g.append(custom);
  g = group();
  const thickness = document.createElement('label'); thickness.className='control-label'; thickness.append('Thickness'); const range=document.createElement('input'); range.type='range'; range.min='1'; range.max='18'; range.value=String(settings.thickness); range.oninput=()=>{settings.thickness=Number(range.value);if(selected())selected().thickness=settings.thickness;draw();}; thickness.append(range); g.append(thickness);
  const font = document.createElement('select'); ['Arial','Sans Serif'].forEach(v=>{const op=document.createElement('option');op.value=v;op.textContent=v;op.selected=v===settings.font;font.append(op);}); font.onchange=()=>{settings.font=font.value;applySetting();}; g.append(font);
  const size = document.createElement('label'); size.className='control-label'; size.append('Size'); const sizeInput=document.createElement('input'); sizeInput.type='range'; sizeInput.min='8'; sizeInput.max='96'; sizeInput.value=String(settings.fontSize); sizeInput.oninput=()=>{settings.fontSize=Number(sizeInput.value);}; size.append(sizeInput); g.append(size);
  [['bold','B'],['italic','I'],['underline','U']].forEach(([key,label])=>addBtn(g,label,()=>{settings[key]=!settings[key];applySetting();updateToolbar();},settings[key]));
  g = group(); addBtn(g,'Delete',deleteSelected); addBtn(g,'Duplicate',duplicateSelected); addBtn(g,'Undo',undo); addBtn(g,'Clear page',()=>{if(objects.length){saveHistory(page);objects.splice(0);data.selected.set(page,null);draw();}});
  const stageWrap = document.createElement('div'); stageWrap.className='canvas-scroll';
  const stage = document.createElement('div'); stage.className='canvas-stage'; stageWrap.append(stage);
  const rendered = await renderPage(state.pdf,page,1.5,0); rendered.canvas.className='preview-canvas'; stage.append(rendered.canvas);
  const svg=svgEl('svg',{width:rendered.canvas.width,height:rendered.canvas.height,viewBox:`0 0 ${rendered.canvas.width} ${rendered.canvas.height}`}); stage.append(svg);
  const helper=document.createElement('p');helper.className='muted';helper.textContent='Use the toolbar above the PDF. Select, move, resize and rotate objects. Press Delete to remove the selected object; Ctrl+D duplicates it; Ctrl+Z undoes the last edit.';
  const actions=document.createElement('div');actions.className='toolbar';
  const upload=document.createElement('input');upload.type='file';upload.accept='image/png,image/jpeg,image/bmp,image/gif,image/webp,image/svg+xml';upload.className='file-input';upload.style.maxWidth='380px';
  const uploadLabel=document.createElement('label');uploadLabel.className='control-label';uploadLabel.append('Upload an image to insert ',upload);
  upload.addEventListener('change',async()=>{const file=upload.files?.[0];if(!file)return;try{const bitmap=await createImageBitmap(file);const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;canvas.getContext('2d').drawImage(bitmap,0,0);const dataUrl=canvas.toDataURL('image/png');const id=`img_${Date.now()}`;data.images.set(id,{name:file.name,width:canvas.width,height:canvas.height,dataUrl,original:dataUrl});data.activeImageId=id;settings.tool='image';updateToolbar();draw();toast(`Image ready: ${file.name}`);}catch(e){toast(`Could not load image: ${e.message}`);}});
  const bgPanel=document.createElement('div');bgPanel.className='toolbar';
  const bgText=document.createElement('span');bgText.className='muted';bgText.textContent='Background removal (colour key): click the uploaded image to sample its background, then adjust tolerance.'; bgPanel.append(bgText);
  const bgPreview=document.createElement('canvas');bgPreview.width=220;bgPreview.height=140;bgPreview.style.cssText='max-width:220px;max-height:140px;object-fit:contain;border:1px solid #999;cursor:crosshair;background:repeating-conic-gradient(#ddd 0% 25%,#fff 0% 50%) 50%/14px 14px'; bgPanel.append(bgPreview);
  const bgColor=document.createElement('input');bgColor.type='color';bgColor.value='#ffffff';bgColor.title='Sampled background colour';bgPanel.append(bgColor);
  bgPreview.addEventListener('click',(event)=>{const asset=data.images.get(data.activeImageId);if(!asset)return;const img=new Image();img.onload=()=>{const rect=bgPreview.getBoundingClientRect();const x=Math.min(img.width-1,Math.max(0,Math.floor((event.clientX-rect.left)/rect.width*img.width)));const y=Math.min(img.height-1,Math.max(0,Math.floor((event.clientY-rect.top)/rect.height*img.height)));const c=document.createElement('canvas');c.width=1;c.height=1;c.getContext('2d').drawImage(img,x,y,1,1,0,0,1,1);const rgba=c.getContext('2d').getImageData(0,0,1,1).data;bgColor.value='#'+[rgba[0],rgba[1],rgba[2]].map(v=>v.toString(16).padStart(2,'0')).join('');};img.src=asset.original;});
  const tolerance=document.createElement('input');tolerance.type='range';tolerance.min='0';tolerance.max='180';tolerance.value='32';bgPanel.append(tolerance);
  const applyBg=button('Apply transparency',()=>{const asset=data.images.get(data.activeImageId);if(!asset)return;const image=new Image();image.onload=()=>{const c=document.createElement('canvas');c.width=image.width;c.height=image.height;const ctx=c.getContext('2d');ctx.drawImage(image,0,0);const pixels=ctx.getImageData(0,0,c.width,c.height);const target=bgColor.value.match(/[0-9a-f]{2}/gi).map(v=>parseInt(v,16));const tol=Number(tolerance.value),feather=Math.max(4,Math.min(24,tol*.35));for(let i=0;i<pixels.data.length;i+=4){const d=Math.hypot(pixels.data[i]-target[0],pixels.data[i+1]-target[1],pixels.data[i+2]-target[2]);if(d<=tol)pixels.data[i+3]=0;else if(d<tol+feather)pixels.data[i+3]=Math.round(pixels.data[i+3]*(d-tol)/feather);}ctx.putImageData(pixels,0,0);asset.dataUrl=c.toDataURL('image/png');draw();toast('Image transparency applied.');};image.src=asset.original;});
  bgPanel.append(applyBg,button('Restore original image',()=>{const asset=data.images.get(data.activeImageId);if(asset){asset.dataUrl=asset.original;draw();}}));
  const counts = document.createElement('div'); counts.className='status info';
  const exportBtn=button('Generate Annotated PDF',exportAnnotated,'primary'); actions.append(uploadLabel, bgPanel, exportBtn);
  const main = document.getElementById('main-content'); main.append(toolbar,helper,stageWrap,counts,actions);

  function selected(){return objects.find(o=>o.id===data.selected.get(page));}
  function applySetting(){const o=selected();if(!o)return;saveHistory(page);o.color=settings.color;if(o.type==='text'){o.bold=settings.bold;o.italic=settings.italic;o.underline=settings.underline;o.font=settings.font;}else o.thickness=settings.thickness;draw();}
  function updateToolbar(){toolbar.querySelectorAll('button').forEach(b=>{const name=b.textContent.toLowerCase();if(TOOL_NAMES.includes(name)){b.classList.toggle('active',name===settings.tool);b.disabled=name==='image'&&!data.activeImageId;}});const asset=data.images.get(data.activeImageId);if(asset){const image=new Image();image.onload=()=>{const ctx=bgPreview.getContext('2d');ctx.clearRect(0,0,bgPreview.width,bgPreview.height);const scale=Math.min(bgPreview.width/image.width,bgPreview.height/image.height);const w=image.width*scale,h=image.height*scale;bgPreview.width=Math.max(1,Math.round(w));bgPreview.height=Math.max(1,Math.round(h));ctx.drawImage(image,0,0,bgPreview.width,bgPreview.height);};image.src=asset.original;}}
  function deleteSelected(){const id=data.selected.get(page);if(!id)return;saveHistory(page);const idx=objects.findIndex(o=>o.id===id);if(idx>=0)objects.splice(idx,1);data.selected.set(page,null);draw();}
  function duplicateSelected(){const o=selected();if(!o)return;saveHistory(page);const dup=copy(o);dup.id=`ann_${Date.now()}`;if(dup.type==='line'||dup.type==='arrow'){dup.x1+=12;dup.y1+=12;dup.x2+=12;dup.y2+=12;}else if(dup.type==='pen')dup.points=dup.points.map(p=>({x:p.x+12,y:p.y+12}));else{dup.x+=12;dup.y+=12;}objects.push(dup);data.selected.set(page,dup.id);draw();}
  function undo(){const history=data.history.get(page);if(!history.length)return;const prior=history.pop();objects.splice(0,objects.length,...prior);data.selected.set(page,null);draw();}
  function draw(){
    svg.replaceChildren();
    for(const o of objects){const attrs={stroke:o.color||'#000','stroke-width':o.thickness||2,fill:'none','vector-effect':'non-scaling-stroke'};let node;
      if(o.type==='rectangle')node=svgEl('rect',{x:o.x,y:o.y,width:o.w,height:o.h,...attrs});
      else if(o.type==='circle')node=svgEl('ellipse',{cx:o.x+o.w/2,cy:o.y+o.h/2,rx:Math.abs(o.w/2),ry:Math.abs(o.h/2),...attrs});
      else if(o.type==='line')node=svgEl('line',{x1:o.x1,y1:o.y1,x2:o.x2,y2:o.y2,...attrs,'stroke-linecap':'round'});
      else if(o.type==='arrow'){const dx=o.x2-o.x1,dy=o.y2-o.y1,len=Math.max(1,Math.hypot(dx,dy)),ux=dx/len,uy=dy/len;const bx=o.x2-ux*Math.max(10,(o.thickness||2)*5),by=o.y2-uy*Math.max(10,(o.thickness||2)*5);node=svgEl('path',{d:`M ${o.x1} ${o.y1} L ${bx} ${by} M ${o.x2} ${o.y2} L ${bx-uy*8} ${by+ux*8} M ${o.x2} ${o.y2} L ${bx+uy*8} ${by-ux*8}`,...attrs});}
      else if(o.type==='pen')node=svgEl('polyline',{points:o.points.map(p=>`${p.x},${p.y}`).join(' '),...attrs,'stroke-linecap':'round','stroke-linejoin':'round'});
      else if(o.type==='text'){node=svgEl('text',{x:o.x,y:o.y+(o.fontSize||18),fill:o.color||'#000','font-family':'Arial','font-size':o.fontSize||18,'font-weight':o.bold?'bold':'normal','font-style':o.italic?'italic':'normal','text-decoration':o.underline?'underline':'none'});String(o.text||'').split('\n').forEach((line,i)=>{const t=svgEl('tspan',{x:o.x,dy:i===0?0:(o.fontSize||18)*1.2});t.textContent=line;node.append(t);});}
      else if(o.type==='image'){const asset=data.images.get(o.assetId);if(asset)node=svgEl('image',{x:o.x,y:o.y,width:o.w,height:o.h,href:asset.dataUrl,preserveAspectRatio:'none'});}
      if(node){const bb=objectBounds(o);node.setAttribute('transform',`rotate(${o.rotation||0} ${bb.x+bb.w/2} ${bb.y+bb.h/2})`);node.dataset.id=o.id;svg.append(node);}
    }
    if(draft){let node;if(draft.type==='rectangle')node=svgEl('rect',{x:draft.x,y:draft.y,width:draft.w,height:draft.h,stroke:draft.color, 'stroke-width':draft.thickness,fill:'none'});else if(draft.type==='circle')node=svgEl('ellipse',{cx:draft.x+draft.w/2,cy:draft.y+draft.h/2,rx:Math.abs(draft.w/2),ry:Math.abs(draft.h/2),stroke:draft.color,'stroke-width':draft.thickness,fill:'none'});else if(draft.type==='line'||draft.type==='arrow')node=svgEl('line',{x1:draft.x1,y1:draft.y1,x2:draft.x2,y2:draft.y2,stroke:draft.color,'stroke-width':draft.thickness});else if(draft.type==='pen')node=svgEl('polyline',{points:draft.points.map(p=>`${p.x},${p.y}`).join(' '),stroke:draft.color,'stroke-width':draft.thickness,fill:'none'});if(node)svg.append(node);}
    const sel=selected();if(sel){const b=objectBounds(sel);svg.append(svgEl('rect',{x:b.x-5,y:b.y-5,width:b.w+10,height:b.h+10,fill:'none',stroke:'#1677ff','stroke-width':1.5,'stroke-dasharray':'6 4','pointer-events':'none'}));[['nw',b.x,b.y],['ne',b.x+b.w,b.y],['sw',b.x,b.y+b.h],['se',b.x+b.w,b.y+b.h]].forEach(([corner,x,y])=>{const h=svgEl('rect',{x:x-5,y:y-5,width:10,height:10,fill:'#fff',stroke:'#1677ff','stroke-width':2,'data-handle':corner});svg.append(h);});const handle=svgEl('circle',{cx:b.x+b.w/2,cy:b.y-26,r:7,fill:'#fff',stroke:'#1677ff','stroke-width':2,'data-rotate':'1'});svg.append(handle);}
    counts.textContent=`Annotations saved - ${Array.from({length:state.pageCount},(_,i)=>`Page ${i+1}: ${(data.objects.get(i)||[]).length}`).join(', ')}`;
  }
  let draft=null, action=null;
  const getPoint=(event)=>{const r=svg.getBoundingClientRect();return{x:event.clientX-r.left,y:event.clientY-r.top};};
  let activeTextEditor = null;
  function beginInlineText(point) {
    if (activeTextEditor) activeTextEditor.finish(true);
    const input = document.createElement('textarea');
    input.className = 'inline-text';
    input.rows = 1;
    input.spellcheck = false;
    const fs = Number(settings.fontSize || 18);
    Object.assign(input.style, {
      left: `${point.x}px`, top: `${point.y}px`, fontSize: `${fs}px`,
      fontFamily: settings.font === 'Arial' ? 'Arial' : 'sans-serif',
      fontWeight: settings.bold ? 'bold' : 'normal',
      fontStyle: settings.italic ? 'italic' : 'normal',
      textDecoration: settings.underline ? 'underline' : 'none',
      color: settings.color, width: '2px', height: `${Math.ceil(fs * 1.3)}px`
    });
    stage.append(input);
    let finished = false;
    const measure = () => {
      const lines = input.value.split('\n');
      const longest = lines.reduce((a,b)=>a.length>=b.length?a:b,'');
      input.style.width = `${Math.max(2, Math.ceil(longest.length * fs * .68) + 8)}px`;
      input.style.height = `${Math.max(Math.ceil(fs * 1.3), Math.ceil(lines.length * fs * 1.2))}px`;
    };
    const finish = (save) => {
      if (finished) return;
      finished = true;
      const text = input.value;
      input.remove();
      activeTextEditor = null;
      if (save && text.trim()) {
        saveHistory(page);
        const lines = text.split('\n');
        const longest = lines.reduce((a,b)=>a.length>=b.length?a:b,'');
        const object = {
          id: `ann_${Date.now()}`, type: 'text', x: point.x, y: point.y,
          w: Math.max(20, longest.length * fs * .68), h: Math.max(fs * 1.35, lines.length * fs * 1.2),
          text, fontSize: fs, font: settings.font, color: settings.color,
          bold: settings.bold, italic: settings.italic, underline: settings.underline,
          rotation: 0, thickness: settings.thickness
        };
        objects.push(object);
        data.selected.set(page, object.id);
        settings.tool = 'select';
        updateToolbar();
      }
      draw();
    };
    activeTextEditor = { input, finish };
    input.addEventListener('pointerdown', (event) => event.stopPropagation());
    input.addEventListener('input', measure);
    input.addEventListener('keydown', (event) => {
      event.stopPropagation();
      if (event.key === 'Escape') { event.preventDefault(); finish(false); }
      else if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); finish(true); }
    });
    input.addEventListener('blur', () => queueMicrotask(() => {
      if (activeTextEditor?.input === input) finish(true);
    }));
    measure();
    requestAnimationFrame(() => input.focus({ preventScroll: true }));
  }
  svg.addEventListener('pointerdown',(event)=>{
    const p=getPoint(event);const target=event.target;const handle=target.dataset.handle;const rotate=target.dataset.rotate;
    if(handle||rotate){const o=selected();if(!o)return;saveHistory(page);action={kind:handle?'resize':'rotate',corner:handle,original:copy(o),start:p,center:{x:objectBounds(o).x+objectBounds(o).w/2,y:objectBounds(o).y+objectBounds(o).h/2},startAngle:Math.atan2(p.y-(objectBounds(o).y+objectBounds(o).h/2),p.x-(objectBounds(o).x+objectBounds(o).w/2))};svg.setPointerCapture(event.pointerId);return;}
    const id=target.dataset.id;
    if(settings.tool==='select'){
      if(id){data.selected.set(page,id);const o=selected();saveHistory(page);action={kind:'move',original:copy(o),start:p};svg.setPointerCapture(event.pointerId);}else data.selected.set(page,null);draw();return;
    }
    if(settings.tool==='text'){beginInlineText(p);return;}
    if(settings.tool==='image'){const asset=data.images.get(data.activeImageId);if(!asset){toast('Upload an image above the editor first.');return;}const maxW=Math.min(280,rendered.canvas.width*.42),ratio=asset.width/asset.height,w=maxW,h=w/ratio;saveHistory(page);const o={id:`ann_${Date.now()}`,type:'image',assetId:data.activeImageId,x:p.x-w/2,y:p.y-h/2,w,h,rotation:0,thickness:settings.thickness};objects.push(o);data.selected.set(page,o.id);settings.tool='select';updateToolbar();draw();return;}
    if(['rectangle','circle','line','arrow'].includes(settings.tool)){saveHistory(page);draft=['line','arrow'].includes(settings.tool)?{id:`draft`,type:settings.tool,x1:p.x,y1:p.y,x2:p.x,y2:p.y,color:settings.color,thickness:settings.thickness}:{id:'draft',type:settings.tool,x:p.x,y:p.y,w:1,h:1,color:settings.color,thickness:settings.thickness};action={kind:'draw',start:p};svg.setPointerCapture(event.pointerId);draw();return;}
    if(settings.tool==='pen'){saveHistory(page);draft={id:'draft',type:'pen',points:[p],color:settings.color,thickness:settings.thickness};action={kind:'pen'};svg.setPointerCapture(event.pointerId);draw();}
  });
  svg.addEventListener('pointermove',(event)=>{
    if(!action)return;const p=getPoint(event);
    if(action.kind==='draw'){if(draft.type==='line'||draft.type==='arrow'){draft.x2=p.x;draft.y2=p.y;}else{draft.x=Math.min(action.start.x,p.x);draft.y=Math.min(action.start.y,p.y);draft.w=Math.abs(p.x-action.start.x);draft.h=Math.abs(p.y-action.start.y);}draw();return;}
    if(action.kind==='pen'){draft.points.push(p);draw();return;}
    const o=selected();if(!o)return;
    if(action.kind==='move'){const dx=p.x-action.start.x,dy=p.y-action.start.y;const orig=action.original;if(o.type==='line'||o.type==='arrow'){o.x1=orig.x1+dx;o.y1=orig.y1+dy;o.x2=orig.x2+dx;o.y2=orig.y2+dy;}else if(o.type==='pen')o.points=orig.points.map(q=>({x:q.x+dx,y:q.y+dy}));else{o.x=orig.x+dx;o.y=orig.y+dy;} }
    else if(action.kind==='rotate'){const currentAngle=Math.atan2(p.y-action.center.y,p.x-action.center.x);o.rotation=(action.original.rotation||0)+(currentAngle-action.startAngle)*180/Math.PI;}
    else if(action.kind==='resize'){const b=objectBounds(action.original);let x1=b.x,y1=b.y,x2=b.x+b.w,y2=b.y+b.h;if(action.corner.includes('n'))y1=p.y;if(action.corner.includes('s'))y2=p.y;if(action.corner.includes('w'))x1=p.x;if(action.corner.includes('e'))x2=p.x;const nx=Math.min(x1,x2),ny=Math.min(y1,y2),nw=Math.max(5,Math.abs(x2-x1)),nh=Math.max(5,Math.abs(y2-y1));if(o.type==='pen'){const sx=nw/b.w,sy=nh/b.h;o.points=action.original.points.map(q=>({x:nx+(q.x-b.x)*sx,y:ny+(q.y-b.y)*sy}));}else if(o.type==='line'||o.type==='arrow'){const sx=nw/b.w,sy=nh/b.h;o.x1=nx+(action.original.x1-b.x)*sx;o.y1=ny+(action.original.y1-b.y)*sy;o.x2=nx+(action.original.x2-b.x)*sx;o.y2=ny+(action.original.y2-b.y)*sy;}else if(o.type==='image'){const scale=Math.max(.05,Math.max(nw/b.w,nh/b.h));o.w=Math.max(8,action.original.w*scale);o.h=Math.max(8,action.original.h*scale);o.x=action.corner.includes('w')?b.x+b.w-o.w:b.x;o.y=action.corner.includes('n')?b.y+b.h-o.h:b.y;}else{o.x=nx;o.y=ny;o.w=nw;o.h=nh;if(o.type==='text')o.fontSize=Math.max(4,action.original.fontSize*Math.max(nw/b.w,nh/b.h));}}
    draw();
  });
  svg.addEventListener('pointerup',()=>{if(!action)return;if((action.kind==='draw'||action.kind==='pen')&&draft){objects.push(draft);data.selected.set(page,draft.id);draft=null;}action=null;draw();});
  if (keydownController) keydownController.abort();
  keydownController = new AbortController();
  window.addEventListener('keydown',(event)=>{if(event.key==='Delete'&&selected())deleteSelected();else if(event.key==='Escape'){data.selected.set(page,null);draw();}else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z')undo();else if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='d')duplicateSelected();},{signal:keydownController.signal});
  window.addEventListener('pdf-tools-tool-change',()=>keydownController?.abort(),{signal:keydownController.signal});
  async function exportAnnotated(){
    try {
      const PDFLib = await loadGlobalScript('PDFLib', 'https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js');
      const { PDFDocument, StandardFonts, rgb, degrees } = PDFLib;
      const doc = await PDFDocument.load(state.bytes);
      const fontRegular = await doc.embedFont(StandardFonts.Helvetica);
      const fontBold = await doc.embedFont(StandardFonts.HelveticaBold);
      const fontItalic = await doc.embedFont(StandardFonts.HelveticaOblique);
      const fontBoldItalic = await doc.embedFont(StandardFonts.HelveticaBoldOblique);

      for (let p = 0; p < doc.getPageCount(); p += 1) {
        const pdfPage = doc.getPage(p);
        const jsPage = await state.pdf.getPage(p + 1);
        const viewport = jsPage.getViewport({ scale: 1.5, rotation: jsPage.rotate || 0 });
        const toPdf = (point) => {
          const [x, y] = viewport.convertToPdfPoint(point.x, point.y);
          return { x, y };
        };
        const items = data.objects.get(p) || [];

        for (const object of items) {
          const color = colorRgb(object.color || '#000000', rgb);
          const lineWidth = Math.max(0.5, Number(object.thickness || 2) / 1.5);
          const bounds = objectBounds(object);
          const center = { x: bounds.x + bounds.w / 2, y: bounds.y + bounds.h / 2 };
          const rotate = (point) => rotateScreenPoint(point, center, Number(object.rotation || 0));
          const drawSegment = (a, b, width = lineWidth) => pdfPage.drawLine({ start: toPdf(a), end: toPdf(b), thickness: width, color });

          if (object.type === 'rectangle') {
            const corners = [
              {x:object.x,y:object.y},{x:object.x+object.w,y:object.y},
              {x:object.x+object.w,y:object.y+object.h},{x:object.x,y:object.y+object.h}
            ].map(rotate);
            for (let i = 0; i < corners.length; i += 1) drawSegment(corners[i], corners[(i + 1) % corners.length]);
          } else if (object.type === 'circle') {
            const centerPdf = toPdf(rotate(center));
            const xEdge = toPdf(rotate({x:center.x+object.w/2,y:center.y}));
            const yEdge = toPdf(rotate({x:center.x,y:center.y+object.h/2}));
            const xVector = {x:xEdge.x-centerPdf.x,y:xEdge.y-centerPdf.y};
            const yVector = {x:yEdge.x-centerPdf.x,y:yEdge.y-centerPdf.y};
            const angle = Math.atan2(xVector.y, xVector.x) * 180 / Math.PI;
            pdfPage.drawEllipse({
              x:centerPdf.x,y:centerPdf.y,
              xScale:Math.hypot(xVector.x,xVector.y),
              yScale:Math.hypot(yVector.x,yVector.y),
              borderColor:color,borderWidth:lineWidth,rotate:degrees(angle)
            });
          } else if (object.type === 'line' || object.type === 'arrow') {
            const startPoint = rotate({x:object.x1,y:object.y1});
            const endPoint = rotate({x:object.x2,y:object.y2});
            drawSegment(startPoint, endPoint);
            if (object.type === 'arrow') {
              const dx=endPoint.x-startPoint.x,dy=endPoint.y-startPoint.y;
              const length=Math.max(.001,Math.hypot(dx,dy)),ux=dx/length,uy=dy/length;
              const headLength=Math.min(length*.65,Math.max(10,Number(object.thickness||2)*5.2));
              const headHalf=Math.max(5,Number(object.thickness||2)*2.8);
              const bx=endPoint.x-ux*headLength,by=endPoint.y-uy*headLength;
              const px=-uy,py=ux;
              const points=[
                {x:bx+px*headHalf,y:by+py*headHalf},endPoint,
                {x:bx-px*headHalf,y:by-py*headHalf}
              ];
              drawSegment(points[0],points[1],Math.max(.5,lineWidth*.8));
              drawSegment(points[1],points[2],Math.max(.5,lineWidth*.8));
            }
          } else if (object.type === 'pen') {
            const points=object.points.map(rotate);
            for(let i=1;i<points.length;i+=1)drawSegment(points[i-1],points[i]);
          } else if (object.type === 'text') {
            const font=object.bold&&object.italic?fontBoldItalic:object.bold?fontBold:object.italic?fontItalic:fontRegular;
            const fontSize=Math.max(4,Number(object.fontSize||18)/1.5);
            const baselineDisplay=rotate({x:object.x,y:object.y+Number(object.fontSize||18)});
            const baseline=toPdf(baselineDisplay);
            const directionPoint=toPdf(rotate({x:object.x+10,y:object.y+Number(object.fontSize||18)}));
            const angle=Math.atan2(directionPoint.y-baseline.y,directionPoint.x-baseline.x)*180/Math.PI;
            pdfPage.drawText(String(object.text||''),{x:baseline.x,y:baseline.y,size:fontSize,font,color,rotate:degrees(angle),lineHeight:fontSize*1.2});
            if(object.underline){
              const start=rotate({x:object.x,y:object.y+Number(object.fontSize||18)+2});
              const end=rotate({x:object.x+Math.max(Number(object.w||0),font.widthOfTextAtSize(String(object.text||''),fontSize)*1.5),y:object.y+Number(object.fontSize||18)+2});
              drawSegment(start,end,Math.max(.5,lineWidth*.45));
            }
          } else if (object.type === 'image') {
            const asset=data.images.get(object.assetId);
            if(asset){
              const image=await doc.embedPng(dataUrlToBytes(asset.dataUrl));
              const centerPdf=toPdf(rotate(center));
              const xEdge=toPdf(rotate({x:center.x+object.w/2,y:center.y}));
              const yEdge=toPdf(rotate({x:center.x,y:center.y+object.h/2}));
              const vx={x:xEdge.x-centerPdf.x,y:xEdge.y-centerPdf.y};
              const vy={x:yEdge.x-centerPdf.x,y:yEdge.y-centerPdf.y};
              const width=Math.max(1,Math.hypot(vx.x,vx.y)*2);
              const height=Math.max(1,Math.hypot(vy.x,vy.y)*2);
              const angle=Math.atan2(vx.y,vx.x);
              // drawImage rotates around its local lower-left origin. Offset
              // that origin so the transformed image remains centred correctly.
              const halfX=width/2,halfY=height/2;
              const origin={
                x:centerPdf.x-(halfX*Math.cos(angle)-halfY*Math.sin(angle)),
                y:centerPdf.y-(halfX*Math.sin(angle)+halfY*Math.cos(angle))
              };
              pdfPage.drawImage(image,{x:origin.x,y:origin.y,width,height,rotate:degrees(angle*180/Math.PI)});
            }
          }
        }
      }
      const bytes=await doc.save();
      const base=(state.file?.name||'document.pdf').replace(/\.pdf$/i,'')||'document';
      createDownload(bytes,`${base}_annotated.pdf`,'application/pdf');
      toast('Annotated PDF generated.');
    } catch(error) { console.error(error); toast(`Annotation export failed: ${error.message}`,7000); }
  }
  draw();
}
