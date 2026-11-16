// Drive genuine, unmodified compiler pages with the developer's global clock.
const config = JSON.parse(document.querySelector('#player').dataset.config);
const clips = config.clips.map(clip => {
  const frame = document.createElement('iframe');
  frame.title = clip.label;
  frame.src = clip.url;
  frame.setAttribute('sandbox', 'allow-scripts allow-same-origin');
  document.querySelector('#player').append(frame);
  return { ...clip, frame };
});
let ready = false;
let active = -1;
let current = 0;
const parentOrigin = location.origin;
const report = value => parent.postMessage(value, parentOrigin);

function fit() {
  const scale = document.documentElement.clientWidth / 1280;
  for (const clip of clips) clip.frame.style.transform = `scale(${scale})`;
}

function show(time, requestId) {
  const number = Number(time);
  if (!Number.isFinite(number)) return;
  current = Math.max(0, Math.min(config.duration, number));
  let index = clips.findIndex(clip => current < clip.offset + clip.duration);
  if (index < 0) index = clips.length - 1;
  // Set a newly selected page explicitly even when returning backwards.
  // All pages remain loaded, so a seek never waits for a new document.
  for (let i = 0; i < clips.length; i += 1) {
    const clip = clips[i];
    clip.frame.hidden = i !== index;
    if (i === index) {
      clip.frame.contentWindow.__timelines['video-motion'].seek(
        Math.max(0, Math.min(clip.duration, current - clip.offset)), false,
      );
    }
  }
  active = index;
  report({ type: 'animation-lab:state', time: current, ready: true,
    duration: config.duration, clip_id: clips[active].id, requestId });
}

window.addEventListener('message', event => {
  if (event.source !== parent || event.origin !== parentOrigin || !ready) return;
  if (event.data?.type === 'animation-lab:seek') show(event.data.time, event.data.requestId);
});
window.addEventListener('resize', fit);

try {
  await Promise.all(clips.map(clip => new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('预览资源加载超时')), 20000);
    clip.frame.addEventListener('load', async () => {
      try {
        const win = clip.frame.contentWindow;
        if (!win.__videoMotion?.ready || !win.__timelines?.['video-motion']) {
          throw new Error('编译画面未能初始化');
        }
        await win.__videoMotion.ready;
        if (!win.document.fonts.check('16px VideoNoto')) throw new Error('指定字体未能加载');
        clearTimeout(timeout);
        resolve();
      } catch (error) {
        clearTimeout(timeout);
        reject(error);
      }
    }, { once: true });
  })));
  ready = true;
  fit();
  show(0);
  report({ type: 'animation-lab:ready', duration: config.duration });
} catch (error) {
  document.querySelector('#error').hidden = false;
  document.querySelector('#error').textContent = error.message;
  report({ type: 'animation-lab:error', message: error.message });
}
