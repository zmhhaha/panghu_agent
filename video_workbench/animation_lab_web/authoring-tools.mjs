// Creation capabilities can share a browser engine. Motion libraries are a
// separate layer; a loaded dependency never establishes aesthetic acceptance.
const LABELS = Object.freeze({animejs:'Anime.js', gsap:'GSAP', manim:'Manim', 'native-js':'原生 JavaScript'});
const CREATION_ENGINES = Object.freeze(['html-svg','canvas','three']);
const CREATION_LABELS = Object.freeze({'html-svg':'HTML/SVG',canvas:'Canvas 2D',three:'Three.js'});
const MOTION_TOOLS = Object.freeze(['native-js','gsap','animejs']);
const DEPENDENCY_TOOLS = Object.freeze(['manim','gsap','animejs']);
const SHA256 = /^[0-9a-f]{64}$/;

export function toolLayer(id) {
  return id === 'manim' ? 'creation' : MOTION_TOOLS.includes(id) ? 'motion' : null;
}

export function observedTools(value) {
  if (value?.engine_status !== 'rendered' || !Array.isArray(value.authoring_tools)) return [];
  const found = new Map();
  for (const row of value.authoring_tools) {
    if (!row || !DEPENDENCY_TOOLS.includes(row.id) || row.status !== 'loaded'
        || typeof row.version !== 'string' || !row.version
        || row.evidence_scope !== 'render_loaded_dependency_not_visual_application_or_acceptance') continue;
    found.set(row.id,{id:row.id,label:LABELS[row.id],version:row.version});
  }
  return [...found.values()].sort((a,b)=>a.id.localeCompare(b.id));
}

export function observedMotionOrchestration(value) {
  if (value?.engine_status !== 'rendered' || !Array.isArray(value.motion_orchestration)) return [];
  const found = new Map();
  for (const row of value.motion_orchestration) {
    if (!row || !MOTION_TOOLS.includes(row.id)
        || typeof row.source_sha256 !== 'string' || !SHA256.test(row.source_sha256)
        || typeof row.media_sha256 !== 'string' || !SHA256.test(row.media_sha256)
        || typeof row.render_operation_id !== 'string' || !row.render_operation_id.trim()) continue;
    const native = row.id === 'native-js';
    if (native ? row.status !== 'source_verified'
        || row.evidence_scope !== 'source_inspection_not_visual_acceptance'
      : row.status !== 'render_observed'
        || row.evidence_scope !== 'render_loaded_dependency_not_visual_acceptance'
        || typeof row.version !== 'string' || !row.version.trim()) continue;
    found.set(row.id,{...row,label:LABELS[row.id]});
  }
  return [...found.values()].sort((a,b)=>MOTION_TOOLS.indexOf(a.id)-MOTION_TOOLS.indexOf(b.id));
}

export function toolSearchText(value) {
  return [...observedTools(value).filter(row=>toolLayer(row.id)==='creation'),
    ...observedMotionOrchestration(value)].map(row=>row.id+' '+row.label).join(' ').toLowerCase();
}

export function matchesAuthoringTool(value,selected='all') {
  return selected === 'all' || MOTION_TOOLS.includes(selected)
    && observedMotionOrchestration(value).some(row=>row.id===selected);
}

export function matchesVisualCreation(value,selected='all') {
  if (selected === 'all') return true;
  if (selected === 'manim') return observedTools(value).some(row=>row.id==='manim');
  const engine = value?.engine_status === 'rendered' && CREATION_ENGINES.includes(value.engine)
    ? value.engine : 'unknown';
  return selected === engine;
}

export function renderAuthoringBadges(doc,value,layer='motion') {
  const rows=layer==='motion'?observedMotionOrchestration(value)
    :observedTools(value).filter(row=>toolLayer(row.id)===layer);
  return rows.map(row=>{
    const badge=doc.createElement('span');
    badge.className='engine-badge'; badge.dataset.authoringTool=row.id; badge.dataset.capabilityLayer=layer;
    badge.textContent=row.label;
    if(layer==='motion') {
      badge.dataset.evidenceStatus=row.status;
      badge.title=row.label+(row.version?' '+row.version:'')+'；'
        +(row.id==='native-js'?'源码已核验时间驱动的画面更新':'源码已核验实质调用，渲染已加载固定版本依赖')
        +'；不代表连续动作或视觉质量已获认可。';
    }else {
      badge.title=row.label+' '+row.version+'；画面创作资源；渲染时已加载，不代表效果已获认可。';
    }
    return badge;
  });
}

export function renderCapabilityLabels(doc,value,noSample=false) {
  const root=doc.createElement('div');root.className='card-engine capability-labels';
  const confirmed=!noSample&&value?.engine_status==='rendered'&&CREATION_ENGINES.includes(value.engine);
  const creation=[],motion=confirmed?renderAuthoringBadges(doc,value):[];
  if(confirmed) {
    const engine=doc.createElement('span');
    engine.className='engine-badge engine-'+value.engine;engine.dataset.renderEngine=value.engine;
    engine.dataset.capabilityLayer='creation';engine.textContent=CREATION_LABELS[value.engine];
    engine.title='画面创作：'+engine.textContent+'；已核实的样片记录。';
    creation.push(engine,...renderAuthoringBadges(doc,value,'creation'));
  }
  for(const [layer,label,badges]of [['creation','画面创作',creation],['motion','动作编排',motion]]) {
    const row=doc.createElement('div'),caption=doc.createElement('span'),values=doc.createElement('span');
    row.className='capability-label-row';row.dataset.capabilityLayer=layer;
    caption.className='capability-label-title';caption.textContent=label+'：';
    values.className='capability-label-values';
    if(badges.length) {
      for(const [index,badge]of badges.entries()) {
        if(index) {
          const separator=doc.createElement('span');separator.className='capability-label-separator';
          separator.textContent='\u00a0';values.append(separator);
        }
        values.append(badge);
      }
    }else {
      const pending=doc.createElement('span');pending.className='capability-label-placeholder';
      pending.textContent=confirmed?'未记录':'待确认';
      pending.title=confirmed?'缺少可核对的动作编排证据，不代表样片没有动画。':'尚无已核实样片，该能力待确认。';
      values.append(pending);
    }
    row.append(caption,values);root.append(row);
  }
  return root;
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
    const label=doc.createElement('label'); label.textContent='动作编排';
    label.htmlFor='gallery-authoring-tool'; select=doc.createElement('select'); select.id='gallery-authoring-tool';
    for (const [value,title] of [['all','全部'],...MOTION_TOOLS.map(id=>[id,LABELS[id]])]) {
      const option=doc.createElement('option');option.value=value;option.textContent=title;select.append(option);
    }
    select.value='all';select.addEventListener('change',onFilter);label.append(select);engine.parentNode.parentNode.append(label);
  }
  if (form?.append) {
    const section=doc.createElement('details');section.className='form-details';
    const summary=doc.createElement('summary');summary.textContent='Manim 画面（可选）';
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
    help.textContent='画面创作可组合 HTML/SVG、Canvas、Three.js 和 Manim；GSAP、Anime.js 属于动作编排。Manim 片段由 Agent 创作后加入完整样片。';
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
      note.textContent=(manim?'Manim 画面已开放。':'Manim 画面暂未开放生成。')
        +(anime?'Anime.js 动作编排已开放，Agent 可按目标选用。':'Anime.js 动作编排暂未开放生成。');
    }
  };
}
