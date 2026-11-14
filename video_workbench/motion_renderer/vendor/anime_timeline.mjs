// Anime.js is a paused authoring library. HyperFrames owns the only clock.
import {createTimeline, engine, stagger, svg, eases} from './anime.esm.min.js';

engine.pause();
engine.useDefaultMainLoop = false;
engine.pauseOnDocumentHidden = false;
engine.timeUnit = 'ms';

export {stagger, svg, eases};
export const ANIMEJS_VERSION = '4.5.0';
const check = (value, code) => { if (!value) throw Error(code); };
function parameters(value) {
  check(value && typeof value === 'object' && !Array.isArray(value), 'anime_parameters_invalid');
  // Callbacks may depend on which frames were visited. Draw from tweened state
  // after renderAt instead. Easing and value functions remain author-controlled.
  for (const key of Object.keys(value))
    check(!/^on[A-Z]/.test(key) && !['autoplay','loop','alternate','reversed','playbackRate',
      'playbackEase','frameRate','composition','then'].includes(key), 'anime_autonomous_parameter');
  for (const nested of Object.values(value)) {
    if (Array.isArray(nested)) {
      for (const row of nested) if (row && typeof row === 'object') parameters(row);
    } else if (nested && typeof nested === 'object') parameters(nested);
  }
  return value;
}

export function createFrameTimeline({fps, startFrame = 0, durationFrames, defaults = {ease:'linear'}} = {}) {
  check(Number.isInteger(fps) && fps > 0 && fps <= 120 && Number.isInteger(startFrame)
    && startFrame >= 0 && Number.isInteger(durationFrames) && durationFrames > 0,
    'anime_frame_clock_invalid');
  parameters(defaults);
  const endMilliseconds = durationFrames * 1000 / fps;
  const timeline = createTimeline({autoplay:false, loop:0, defaults:{...defaults}});
  let disposed = false, rendered = false;
  const active = () => check(!disposed, 'anime_timeline_disposed');
  const api = {
    add(targets, values, position = 0) {
      active(); check(!rendered, 'anime_timeline_already_rendered'); parameters(values);
      check(typeof position === 'string' || Number.isFinite(position), 'anime_position_invalid');
      // Never use string selectors: modules must stay within their mount root.
      check(typeof targets !== 'string' && targets != null, 'anime_scoped_targets_required');
      timeline.add(targets, values, position);
      timeline.pause();
      check(Number.isFinite(timeline.duration) && timeline.duration <= endMilliseconds + 1e-6,
        'anime_timeline_exceeds_frame_window');
      return api;
    },
    label(name, position) {
      active(); check(!rendered, 'anime_timeline_already_rendered');
      check(typeof name === 'string' && /^[A-Za-z][\w-]*$/.test(name)
        && Number.isFinite(position) && position >= 0 && position <= endMilliseconds, 'anime_label_invalid');
      timeline.label(name, position); return api;
    },
    renderAt(context) {
      active();
      check(context && Number.isInteger(context.global_frame) && context.global_frame >= 0
        && Number.isInteger(context.time?.numerator) && Number.isInteger(context.time?.denominator)
        && context.time.denominator > 0
        && context.time.numerator * fps === context.global_frame * context.time.denominator,
        'anime_frame_clock_invalid');
      rendered = true;
      const milliseconds = Math.max(0, Math.min(endMilliseconds,
        (context.global_frame - startFrame) * 1000 / fps));
      timeline.pause().seek(milliseconds, true);
      check(timeline.paused && !engine.reqId && !engine.useDefaultMainLoop, 'anime_clock_not_paused');
      return milliseconds;
    },
    dispose() {
      if (disposed) return;
      timeline.revert(); timeline.cancel();
      disposed = true;
    },
  };
  return Object.freeze(api);
}
