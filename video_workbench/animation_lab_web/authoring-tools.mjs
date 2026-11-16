// Creation capabilities can share a browser engine. Motion libraries are a
// separate layer; a loaded dependency never establishes aesthetic acceptance.
const LABELS = Object.freeze({animejs:'Anime.js', gsap:'GSAP', manim:'Manim', 'native-js':'原生 JavaScript'});
const CREATION_ENGINES = Object.freeze(['html-svg','canvas','three']);
const CREATION_LABELS = Object.freeze({'html-svg':'HTML/SVG',canvas:'Canvas 2D',three:'Three.js'});
const MOTION_TOOLS = Object.freeze(['native-js','gsap','animejs']);
const DEPENDENCY_TOOLS = Object.freeze(['manim','gsap','animejs']);
const CREATION_FIELDS = Object.freeze({'html-svg':'creation_html_svg',canvas:'creation_canvas',three:'creation_three',manim:'creation_manim'});
const MOTION_FIELDS = Object.freeze({'native-js':'motion_native_js',gsap:'motion_gsap',animejs:'motion_animejs'});
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
  const selected=name=>fields[name]==='on'||fields[name]===true;
  const creation=Object.entries(CREATION_FIELDS).filter(([,name])=>selected(name)).map(([id])=>id);
  const manimOnly=creation.length===1&&creation[0]==='manim';
  const motion=manimOnly?[]:Object.entries(MOTION_FIELDS).filter(([,name])=>selected(name)).map(([id])=>id);
  // Each checkbox has its own name, so Object.fromEntries(new FormData(form))
  // preserves combinations without relying on repeated-key form fields.
  const request=creation.length||motion.length?{capability_selection:{creation,motion}}:{};
  if (!creation.includes('manim')&&fields.use_manim_resource!=='on') return request;
  const seconds=Number(fields.manim_resource_seconds),frames=seconds*24;
  if (!Number.isFinite(seconds) || seconds<.5 || seconds>2.5 || !Number.isInteger(frames)
      || !Number.isFinite(durationSeconds) || seconds>durationSeconds) {
    throw Error('Manim 片段时长需为 0.5 至 2.5 秒，且不能超过样片时长。');
  }
  return {...request,resource_authoring:{kind:'manim',width:640,height:360,frame_count:frames,
    global_start_frame:0,seed:17}};
}

export function mountAuthoringControls(doc,onFilter) {
  const engine=doc.getElementById('gallery-engine'),form=doc.getElementById('experiment-form');
  const choices={creation:new Map(),motion:new Map()},selectionNotes={};
  let select,seconds,length,note,availability=null,busy=false,collaboration=false;
  const selectedIds=layer=>[...choices[layer]].filter(([,input])=>input.checked).map(([id])=>id);
  const isManimOnly=()=>{
    const creation=selectedIds('creation');
    return creation.length===1&&creation[0]==='manim';
  };
  const refreshSelection=()=>{
    const manimOnly=isManimOnly();
    if(manimOnly)for(const input of choices.motion.values()) {
      input.checked=false;input.disabled=true;input.title='由 Manim 自身编排动画，无需浏览器动作编排。';
    }
    for(const layer of ['creation','motion']) {
      if(selectionNotes[layer])selectionNotes[layer].textContent=layer==='motion'&&manimOnly
        ?'由 Manim 自身编排动画。'
        :selectedIds(layer).length?'Agent 从选中能力中选择或组合使用。'
          :'自动选择：由 Agent 根据表达目标决定。';
    }
    const manim=choices.creation.get('manim');
    if(length)length.hidden=!manim?.checked;
    if(seconds)seconds.disabled=!manim?.checked||manim.disabled;
  };
  const applyAvailability=()=>{
    const enabled=availability?.enabled===true;
    const engines=Array.isArray(availability?.allowed_engines)?availability.allowed_engines:['html-svg'];
    const manim=availability?.resource_authoring?.manim?.enabled===true;
    const library=id=>{
      const entry=availability?.authoring_libraries?.[id];
      return entry&&typeof entry==='object'&&entry.enabled!==false
        &&typeof entry.version==='string'&&Boolean(entry.version.trim());
    };
    const motions=Array.isArray(availability?.motion_tools)?availability.motion_tools
      :['native-js',...(Object.hasOwn(availability?.authoring_libraries??{},'gsap')
        ?library('gsap')?['gsap']:[]:engines.includes('html-svg')?['gsap']:[]),
      ...(library('animejs')?['animejs']:[])];
    for(const [layer,entries]of Object.entries(choices))for(const [id,input]of entries) {
      const admitted=layer==='creation'?(id==='manim'?manim:engines.includes(id)):motions.includes(id);
      input.disabled=!enabled||busy||!admitted||(id==='manim'&&collaboration);
      input.title=id==='manim'&&collaboration?'Manim 预制资源暂未接入协作创作。'
        :!admitted?'该能力暂未开放生成。':'';
    }
    const manimInput=choices.creation.get('manim');
    if(collaboration&&manimInput)manimInput.checked=false;
    if(note)note.textContent=collaboration
      ?'协作创作可使用已开放的 HTML/SVG、Canvas 2D 和 Three.js；Manim 预制资源暂未接入协作。'
      :'勾选项限定本次可用能力；Agent 先决定实际采用或组合的能力，仅在采用 Manim 时制作资源。Manim 片段为 24 fps、0.5 至 2.5 秒。动作编排选项仅用于浏览器画面，Manim 使用自身的动画 API。';
    refreshSelection();
  };
  if (engine?.parentNode?.parentNode?.append) {
    const label=doc.createElement('label'); label.textContent='动作编排';
    label.htmlFor='gallery-authoring-tool'; select=doc.createElement('select'); select.id='gallery-authoring-tool';
    for (const [value,title] of [['all','全部'],...MOTION_TOOLS.map(id=>[id,LABELS[id]])]) {
      const option=doc.createElement('option');option.value=value;option.textContent=title;select.append(option);
    }
    select.value='all';select.addEventListener('change',onFilter);label.append(select);engine.parentNode.parentNode.append(label);
  }
  if (form?.append) {
    const section=doc.createElement('section');section.className='authoring-capabilities';
    const help=doc.createElement('p');help.className='small muted';
    help.textContent='两组默认自动选择。可分别勾选一项或多项，让 Agent 从选中的能力中选择或组合；画面创作与动作编排可搭配使用。';
    const groups=doc.createElement('div');groups.className='form-row authoring-capability-groups';
    for(const [layer,title,fields]of [['creation','画面创作',CREATION_FIELDS],['motion','动作编排',MOTION_FIELDS]]) {
      const group=doc.createElement('fieldset');group.className='form-details authoring-capability-group';
      group.dataset.capabilityLayer=layer;
      const legend=doc.createElement('legend');legend.textContent=title;group.append(legend);
      const automatic=doc.createElement('p');automatic.className='small muted';
      selectionNotes[layer]=automatic;group.append(automatic);
      for(const [id,name]of Object.entries(fields)) {
        const label=doc.createElement('label');label.className='checkbox-label';
        const input=doc.createElement('input');input.type='checkbox';input.name=name;
        input.id='experiment-'+name.replaceAll('_','-');input.disabled=true;
        const caption=doc.createElement('span');caption.textContent=layer==='creation'?(CREATION_LABELS[id]??LABELS[id]):LABELS[id];
        label.htmlFor=input.id;label.append(input,caption);group.append(label);
        choices[layer].set(id,input);input.addEventListener('change',applyAvailability);
      }
      if(layer==='creation') {
        length=doc.createElement('label');length.textContent='Manim 片段时长（秒）';length.hidden=true;
        seconds=doc.createElement('input');seconds.type='number';seconds.name='manim_resource_seconds';
        seconds.min='.5';seconds.max='2.5';seconds.step='.25';seconds.value='2';seconds.disabled=true;
        length.append(seconds);group.append(length);
      }
      groups.append(group);
    }
    note=doc.createElement('p');note.className='small muted';
    section.append(help,groups,note);
    const footer=form.querySelector?.('.form-footer');
    if (footer?.before) footer.before(section);else form.append(section);
    applyAvailability();
  }
  return {
    get selected(){return select?.value??'all';},
    get requestFields(){
      // FormData omits disabled controls. Preserve a requested capability that
      // lost admission so the server rejects it rather than silently choosing.
      const fields={};
      for(const [layer,entries]of Object.entries(choices))for(const [id,input]of entries) {
        if(layer==='motion'&&isManimOnly())continue;
        if(input.checked)fields[(layer==='creation'?CREATION_FIELDS:MOTION_FIELDS)[id]]='on';
      }
      if(choices.creation.get('manim')?.checked)fields.manim_resource_seconds=seconds.value;
      return fields;
    },
    resetFilter(){if(select)select.value='all';},
    resetExperiment(resource,selection,context){
      if(!seconds)return;
      if(context&&Object.hasOwn(context,'collaboration'))collaboration=context.collaboration===true;
      for(const [layer,entries]of Object.entries(choices))for(const [id,input]of entries) {
        input.checked=Array.isArray(selection?.[layer])&&selection[layer].includes(id);
      }
      if(resource?.kind==='manim'&&Number.isInteger(resource.frame_count))choices.creation.get('manim').checked=true;
      seconds.value=resource?.kind==='manim'&&Number.isInteger(resource.frame_count)?String(resource.frame_count/24):'2';
      applyAvailability();
    },
    update(nextAvailability,nextBusy,context){
      availability=nextAvailability;busy=Boolean(nextBusy);
      if(context&&Object.hasOwn(context,'collaboration'))collaboration=context.collaboration===true;
      applyAvailability();
    }
  };
}
