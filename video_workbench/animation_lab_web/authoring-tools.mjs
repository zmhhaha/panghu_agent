// Drawing engines and observed authoring dependencies are separate filters.
const LABELS = Object.freeze({animejs:'Anime.js', manim:'Manim'});

export function observedTools(value) {
  if (value?.engine_status !== 'rendered' || !Array.isArray(value.authoring_tools)) return [];
  const found = new Map();
  for (const row of value.authoring_tools) {
    if (!row || !Object.hasOwn(LABELS,row.id) || row.status !== 'loaded'
        || typeof row.version !== 'string' || !row.version
        || row.evidence_scope !== 'render_loaded_dependency_not_visual_application_or_acceptance') continue;
    found.set(row.id,{id:row.id,label:LABELS[row.id],version:row.version});
  }
  return [...found.values()].sort((a,b)=>a.id.localeCompare(b.id));
}

export function toolSearchText(value) {
  return observedTools(value).map(row=>row.id+' '+row.label).join(' ').toLowerCase();
}

export function matchesAuthoringTool(value,selected='all') {
  return selected === 'all' || observedTools(value).some(row=>row.id===selected);
}

export function renderAuthoringBadges(doc,value) {
  return observedTools(value).map(row=>{
    const badge=doc.createElement('span');
    badge.className='engine-badge'; badge.dataset.authoringTool=row.id;
    badge.textContent=row.label+' '+row.version;
    badge.title='本样片渲染时已加载的创作工具；加载不代表效果已获认可。';
    return badge;
  });
}

export function resourceAuthoringRequest(fields,durationSeconds) {
  if (fields.use_manim_resource !== 'on') return {};
  const seconds=Number(fields.manim_resource_seconds),frames=seconds*24;
  if (!Number.isFinite(seconds) || seconds<.5 || seconds>2.5 || !Number.isInteger(frames)
      || !Number.isFinite(durationSeconds) || seconds>durationSeconds) {
    throw Error('Manim 片段时长需为 0.5 至 2.5 秒，且不能超过样片时长。');
  }
  return {resource_authoring:{kind:'manim',width:640,height:360,frame_count:frames,
    global_start_frame:0,seed:17}};
}

export function mountAuthoringControls(doc,onFilter) {
  const engine=doc.getElementById('gallery-engine'),form=doc.getElementById('experiment-form');
  let select,checkbox,seconds,note;
  if (engine?.parentNode?.parentNode?.append) {
    const label=doc.createElement('label'); label.textContent='创作工具';
    label.htmlFor='gallery-authoring-tool'; select=doc.createElement('select'); select.id='gallery-authoring-tool';
    for (const [value,title] of [['all','全部'],['animejs','Anime.js'],['manim','Manim']]) {
      const option=doc.createElement('option');option.value=value;option.textContent=title;select.append(option);
    }
    select.value='all';select.addEventListener('change',onFilter);label.append(select);engine.parentNode.parentNode.append(label);
  }
  if (form?.append) {
    const section=doc.createElement('details');section.className='form-details';
    const summary=doc.createElement('summary');summary.textContent='创作工具（可选）';
    note=doc.createElement('p');note.className='small muted';
    const label=doc.createElement('label');checkbox=doc.createElement('input');
    checkbox.type='checkbox';checkbox.name='use_manim_resource';checkbox.disabled=true;
    const caption=doc.createElement('span');caption.textContent='使用 Manim 制作数学或几何动画片段';
    label.append(checkbox,caption);
    const length=doc.createElement('label');length.textContent='Manim 片段时长（秒）';
    seconds=doc.createElement('input');seconds.type='number';seconds.name='manim_resource_seconds';
    seconds.min='.5';seconds.max='2.5';seconds.step='.25';seconds.value='2';seconds.disabled=true;
    length.append(seconds);checkbox.addEventListener('change',()=>{seconds.disabled=checkbox.disabled||!checkbox.checked;});
    const help=doc.createElement('p');help.className='small muted';
    help.textContent='Manim 片段由 Agent 创作，与其他画面共同组成完整样片。HTML/SVG、Canvas 和 Three.js 仍负责画面绘制。';
    section.append(summary,note,label,length,help);
    const footer=form.querySelector?.('.form-footer');
    if (footer?.before) footer.before(section);else form.append(section);
  }
  return {
    get selected(){return select?.value??'all';},
    resetFilter(){if(select)select.value='all';},
    resetExperiment(resource){
      if(!checkbox)return;
      checkbox.checked=resource?.kind==='manim'&&Number.isInteger(resource.frame_count);
      if(checkbox.checked)seconds.value=String(resource.frame_count/24);
      seconds.disabled=checkbox.disabled||!checkbox.checked;
    },
    update(availability,busy){
      if(!checkbox)return;
      const manim=availability?.resource_authoring?.manim?.enabled===true;
      checkbox.disabled=!manim||availability?.enabled!==true||busy;
      seconds.disabled=checkbox.disabled||!checkbox.checked;
      const anime=Boolean(availability?.authoring_libraries?.animejs);
      note.textContent=(anime?'Anime.js 已开放，Agent 可按目标选用。':'Anime.js 暂未开放生成。')
        +(manim?'Manim 片段已开放。':'Manim 片段暂未开放生成。');
    }
  };
}
