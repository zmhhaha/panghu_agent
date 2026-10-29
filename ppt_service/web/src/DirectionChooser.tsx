import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { ArrowDown, Check, ChevronRight, Palette, Sparkles, X } from 'lucide-react';
import type { Direction, Plan } from './domain';
import { presentDirection } from './directionPresentation';
import './direction-choice.css';

interface Props {
 directions: Direction[];
 selectedId: string;
 onSelect: (id: string) => void;
 disabled: boolean;
 recommendation?: Plan['recommendation'];
 renderDetails: (direction: Direction) => ReactNode;
}

function StyleSample({direction}:{direction:Direction}) {
 const presentation=presentDirection(direction);
 const {colors,layout}=presentation;
 const style={
  '--sample-bg':colors.background??'#f3f4f2',
  '--sample-ink':colors.foreground??'#303d37',
  '--sample-accent':colors.accent??'#748078',
  '--sample-surface':colors.surface??'transparent',
 } as CSSProperties;
 return <span className={`direction-sample sample-${layout}`} style={style} aria-hidden="true">
  <span className="sample-topline"><span>{presentation.styleLabels[0]??'内容与风格'}</span><span>01 / {String(presentation.pageCount).padStart(2,'0')}</span></span>
  <span className="sample-heading">{direction.pages[0]?.title||direction.title}</span>
  <span className="sample-accent"/>
  <span className="sample-sections">{direction.pages.slice(1,4).map(page=><span key={page.number}><span>{String(page.number).padStart(2,'0')}</span><span>{page.title}</span></span>)}</span>
  <span className="sample-footer">{presentation.modeLabels[0]??'内容安排'}</span>
 </span>;
}

export function DirectionChooser({directions,selectedId,onSelect,disabled,recommendation,renderDetails}:Props) {
 const [inspectedId,setInspectedId]=useState<string|null>(null);
 const inspector=useRef<HTMLElement|null>(null);
 useEffect(()=>{
  if(inspectedId)inspector.current?.scrollIntoView?.({block:'start',behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
 },[inspectedId]);
 const inspected=directions.find(direction=>direction.id===inspectedId);
 const selected=directions.find(direction=>direction.id===selectedId);
 return <section className="direction-choice" aria-label="比较设计方案">
  <div className="direction-choice-intro"><div><h3>哪一种，更像你想要的演示？</h3><p>先比较风格，再按需查看内容安排。</p></div><span><Palette size={15}/>风格示意 · 实际页面将在确认后制作</span></div>
  <div className="direction-choice-grid">{directions.map((direction,index)=>{
   const presentation=presentDirection(direction);
   const chosen=selectedId===direction.id;
   const recommended=recommendation?.direction_id===direction.id;
   const inputId=`direction-choice-${index}`;
   return <article key={direction.id} className={`direction-option ${chosen?'is-chosen':''}`} aria-label={direction.title}>
    <div className="direction-option-top"><span>方案 {String(index+1).padStart(2,'0')}</span>{recommended&&<span className="direction-recommended"><Sparkles size={12}/>推荐</span>}</div>
    <label className="direction-sample-label" htmlFor={inputId}><StyleSample direction={direction}/><span className="sr-only">查看风格并选择{direction.title}</span></label>
    <div className="direction-option-body">
     <div className="direction-option-title"><h3>{direction.title}</h3><span>{presentation.pageCount} 页</span></div>
     <div className="direction-style-labels">{[...presentation.styleLabels,...presentation.modeLabels].slice(0,3).map((value,i)=><span key={`${i}-${value}`}>{value}</span>)}</div>
     <p className="direction-short-description">{presentation.summary||'可展开查看本方案的内容安排与设计说明。'}</p>
     <div className="direction-palette-row">{presentation.palette.length?<><ul className="direction-palette" aria-label={`${direction.title}的配色`}>{presentation.palette.map(color=><li key={color.key}><span style={{backgroundColor:color.color}} title={`${color.label} ${color.color}`}><span className="sr-only">{color.label} {color.color}</span></span></li>)}</ul><span>{presentation.paletteName||'方案配色'}</span></>:<span className="direction-no-palette">本方案尚未提供明确配色</span>}</div>
     <label className={`direction-select ${chosen?'is-chosen':''}`} htmlFor={inputId}>
      <input id={inputId} type="radio" name="direction" aria-label={`选择${direction.title}`} checked={chosen} disabled={disabled} onChange={()=>onSelect(direction.id)}/>
      <span>{chosen?'已选择这个方案':'选择这个方案'}</span>{chosen?<Check size={16}/>:<ChevronRight size={16}/>} 
     </label>
     <button className="direction-inspect" aria-expanded={inspectedId===direction.id} aria-controls="direction-inspector" onClick={()=>setInspectedId(inspectedId===direction.id?null:direction.id)}>查看内容与完整方案 <ArrowDown size={14}/></button>
     {recommended&&recommendation?.reason&&<details className="direction-reason"><summary>为什么推荐这个方案</summary><p>{recommendation.reason}</p></details>}
    </div>
   </article>;
  })}</div>
  <div className="direction-choice-status"><p aria-live="polite">{selected?<><Check size={16}/><span>当前选择：<strong>{selected.title}</strong></span></>:<span>还未选择，点击上方任一方案即可。</span>}</p>{recommendation&&<small>系统推荐仅供参考，不代表用户确认</small>}</div>
  {inspected&&<section ref={inspector} id="direction-inspector" className="direction-inspector" aria-label={`${inspected.title}的完整方案`}>
   <div className="direction-inspector-heading"><div><span className="eyebrow">方案详情</span><h3>{inspected.title}</h3><p>{selectedId===inspected.id?'这也是你当前选择的方案。':'正在查看此方案；展开详情不会改变你的选择。'}</p></div><button className="secondary compact" onClick={()=>setInspectedId(null)}><X size={14}/>收起详情</button></div>
   {renderDetails(inspected)}
  </section>}
 </section>;
}
