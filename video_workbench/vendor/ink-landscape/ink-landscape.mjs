// src/brush-state.ts
var channels = ["pressure", "side", "angle", "dryness", "inkLoad"];
function copyStrokeBrush(brush) {
  if (!brush || brush.version !== 1) throw new RangeError("Unsupported brush version");
  if (!Array.isArray(brush.controls) || brush.controls.length > 128) throw new RangeError("brush.controls supports at most 128 controls");
  let previous = -1;
  const controls = brush.controls.map((control) => {
    if (!control || !Number.isFinite(control.at) || control.at < 0 || control.at > 1 || control.at <= previous) {
      throw new RangeError("brush control positions must be strictly increasing in 0..1");
    }
    previous = control.at;
    const result = { at: control.at };
    for (const key of channels) {
      const value = control[key];
      if (value === void 0) continue;
      if (typeof value !== "number" || !Number.isFinite(value) || key !== "angle" && (value < 0 || value > 1)) {
        throw new RangeError(`brush.${key} must be finite${key === "angle" ? "" : " and in 0..1"}`);
      }
      result[key] = value;
    }
    return Object.freeze(result);
  });
  return Object.freeze({ version: 1, controls: Object.freeze(controls) });
}
function brushStateAt(brush, at2, defaults3) {
  const result = { ...defaults3 };
  for (const key of channels) {
    let leftAt = 0, leftValue = defaults3[key], rightAt = 1, rightValue = defaults3[key];
    for (const control of brush.controls) {
      const value = control[key];
      if (value === void 0) continue;
      if (control.at <= at2) {
        leftAt = control.at;
        leftValue = value;
      }
      if (control.at >= at2) {
        rightAt = control.at;
        rightValue = value;
        break;
      }
    }
    const fraction = rightAt === leftAt ? 0 : Math.max(0, Math.min(1, (at2 - leftAt) / (rightAt - leftAt)));
    const eased = fraction * fraction * (3 - 2 * fraction);
    result[key] = leftValue * (1 - eased) + rightValue * eased;
  }
  return result;
}

// src/core.ts
var TAU = Math.PI * 2;
var EPSILON = 1e-9;
var MAX_GENERATED_MARKS = 1e4;
var MAX_RESAMPLED_POINTS = 1e5;
function finite(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(`${name} must be a finite number`);
  }
  return value;
}
function positive(value, name) {
  finite(value, name);
  if (value <= 0) throw new RangeError(`${name} must be greater than zero`);
  return value;
}
function bounded(value, name, min = 0, max = 1) {
  finite(value, name);
  if (value < min || value > max) throw new RangeError(`${name} must be between ${min} and ${max}`);
  return value;
}
function integer(value, name) {
  finite(value, name);
  if (!Number.isSafeInteger(value)) throw new RangeError(`${name} must be a safe integer`);
  return value;
}
function seedValue(value) {
  return integer(value, "seed") >>> 0;
}
function text(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${name} must be a nonempty string`);
  return value;
}
function hashText(value) {
  let hash8 = 2166136261;
  for (let i = 0; i < value.length; i++) {
    hash8 = Math.imul(hash8 ^ value.charCodeAt(i), 16777619);
  }
  return hash8 >>> 0;
}
function createRandom(seed) {
  let state = seedValue(seed);
  return () => {
    state = state + 1831565813 >>> 0;
    let word = state;
    word = Math.imul(word ^ word >>> 15, word | 1);
    word ^= word + Math.imul(word ^ word >>> 7, word | 61);
    return ((word ^ word >>> 14) >>> 0) / 4294967296;
  };
}
function copyPoint(point2, name) {
  if (!point2 || typeof point2 !== "object") throw new TypeError(`${name} must be a point`);
  return Object.freeze({ x: finite(point2.x, `${name}.x`), y: finite(point2.y, `${name}.y`) });
}
function copyInkPoint(point2, name) {
  const coordinates = copyPoint(point2, name);
  return point2.pressure === void 0 ? coordinates : Object.freeze({
    ...coordinates,
    pressure: bounded(point2.pressure, `${name}.pressure`)
  });
}
function pathLength(points) {
  let length = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    length += Math.hypot(b.x - a.x, b.y - a.y);
  }
  return length;
}
function copyPath(points) {
  if (!Array.isArray(points) || points.length < 2) throw new RangeError("A stroke requires at least two points");
  const copy = points.map((point2, i) => copyInkPoint(point2, `points[${i}]`));
  const length = pathLength(copy);
  if (!Number.isFinite(length) || length <= EPSILON) throw new RangeError("A stroke must have finite, nonzero length");
  return Object.freeze(copy);
}
function signedArea(points) {
  let twiceArea = 0;
  const origin = points[0];
  for (let i = 1; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    twiceArea += (a.x - origin.x) * (b.y - origin.y) - (a.y - origin.y) * (b.x - origin.x);
  }
  return twiceArea / 2;
}
function copyPolygon(points, name = "outline") {
  if (!Array.isArray(points) || points.length < 3) throw new RangeError(`${name} requires at least three points`);
  const copy = points.map((point2, i) => copyPoint(point2, `${name}[${i}]`));
  const area = signedArea(copy);
  if (!Number.isFinite(area) || Math.abs(area) <= EPSILON) throw new RangeError(`${name} must have finite, nonzero area`);
  return Object.freeze(copy);
}
function pointInPolygon(point2, polygon3) {
  copyPoint(point2, "point");
  if (!Array.isArray(polygon3) || polygon3.length < 3) throw new RangeError("polygon requires at least three points");
  let inside2 = false;
  for (let i = 0, j = polygon3.length - 1; i < polygon3.length; j = i++) {
    const a = polygon3[j];
    const b = polygon3[i];
    finite(a.x, "polygon.x");
    finite(a.y, "polygon.y");
    finite(b.x, "polygon.x");
    finite(b.y, "polygon.y");
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const cross = (point2.x - a.x) * dy - (point2.y - a.y) * dx;
    const edgeLength = Math.hypot(dx, dy);
    if (edgeLength > 0 && Math.abs(cross) <= EPSILON * Math.max(1, edgeLength) && point2.x >= Math.min(a.x, b.x) - EPSILON && point2.x <= Math.max(a.x, b.x) + EPSILON && point2.y >= Math.min(a.y, b.y) - EPSILON && point2.y <= Math.max(a.y, b.y) + EPSILON) return true;
    if (a.y > point2.y !== b.y > point2.y && point2.x < (b.x - a.x) * (point2.y - a.y) / (b.y - a.y) + a.x) inside2 = !inside2;
  }
  return inside2;
}
function resamplePath(points, spacing) {
  positive(spacing, "spacing");
  const source = copyPath(points);
  const total = pathLength(source);
  const steps = Math.ceil(total / spacing);
  if (steps + 1 > MAX_RESAMPLED_POINTS) throw new RangeError("Resampling would exceed 100000 points; increase spacing");
  const result = [{ ...source[0] }];
  let segment = 1;
  let walked = 0;
  for (let sample = 1; sample < steps; sample++) {
    const distance = sample * spacing;
    while (segment < source.length - 1) {
      const a2 = source[segment - 1];
      const b2 = source[segment];
      const length2 = Math.hypot(b2.x - a2.x, b2.y - a2.y);
      if (walked + length2 >= distance && length2 > 0) break;
      walked += length2;
      segment++;
    }
    const a = source[segment - 1];
    const b = source[segment];
    const length = Math.hypot(b.x - a.x, b.y - a.y);
    const u = length === 0 ? 1 : Math.min(1, Math.max(0, (distance - walked) / length));
    const point2 = {
      x: a.x + (b.x - a.x) * u,
      y: a.y + (b.y - a.y) * u,
      pressure: (a.pressure ?? 1) + ((b.pressure ?? 1) - (a.pressure ?? 1)) * u
    };
    result.push(point2);
  }
  result.push({ ...source[source.length - 1] });
  return result;
}
function timing(options) {
  const start = finite(options.start ?? 0, "start");
  const duration = finite(options.duration ?? 0, "duration");
  if (start < 0 || duration < 0) throw new RangeError("start and duration must be nonnegative");
  if (!Number.isFinite(start + duration)) throw new RangeError("start + duration must be finite");
  return { start, duration };
}
function strokeMark(id, points, options, fallbackSeed) {
  const nib = options.nib ?? "round";
  if (nib !== "round" && nib !== "flat") throw new RangeError("nib must be round or flat");
  if (options.smoothing !== void 0 && options.smoothing !== "linear" && options.smoothing !== "catmull-rom") {
    throw new RangeError("smoothing must be linear or catmull-rom");
  }
  const mark2 = {
    kind: "stroke",
    id: text(id, "id"),
    points: copyPath(points),
    width: positive(options.width ?? 8, "width"),
    ink: text(options.ink ?? "#20251f", "ink"),
    opacity: bounded(options.opacity ?? 0.85, "opacity"),
    dryness: bounded(options.dryness ?? 0.25, "dryness"),
    nib,
    tipAngle: finite(options.tipAngle ?? 0, "tipAngle"),
    ...options.smoothing === void 0 ? {} : { smoothing: options.smoothing },
    seed: seedValue(options.seed ?? fallbackSeed),
    ...timing(options),
    ...options.clip === void 0 ? {} : { clip: copyPolygon(options.clip, "clip") },
    ...options.brush === void 0 ? {} : { brush: copyStrokeBrush(options.brush) }
  };
  return Object.freeze(mark2);
}
function createInkScene(options) {
  const width = positive(options.width, "width");
  const height = positive(options.height, "height");
  const seed = seedValue(options.seed ?? 1);
  const background = text(options.background ?? "#f2efe5", "background");
  const marks = [];
  const ids = /* @__PURE__ */ new Set();
  function availableId(kind) {
    let serial = marks.length + 1;
    while (ids.has(`${kind}-${serial}`)) serial++;
    return `${kind}-${serial}`;
  }
  function assertUnused(id) {
    text(id, "id");
    if (ids.has(id)) throw new RangeError(`Duplicate mark id: ${id}`);
  }
  const builder = {
    stroke(points, strokeOptions = {}) {
      const id = strokeOptions.id ?? availableId("stroke");
      assertUnused(id);
      const mark2 = strokeMark(id, points, strokeOptions, seed ^ hashText(id));
      marks.push(mark2);
      ids.add(id);
      return mark2;
    },
    wash(outline, washOptions = {}) {
      const id = washOptions.id ?? availableId("wash");
      assertUnused(id);
      const passes = integer(washOptions.passes ?? 4, "passes");
      bounded(passes, "passes", 1, 32);
      const mark2 = Object.freeze({
        kind: "wash",
        id,
        outline: copyPolygon(outline),
        ink: text(washOptions.ink ?? "#30372e", "ink"),
        opacity: bounded(washOptions.opacity ?? 0.22, "opacity"),
        roughness: bounded(washOptions.roughness ?? 0.45, "roughness"),
        passes,
        seed: seedValue(washOptions.seed ?? seed ^ hashText(id)),
        ...timing(washOptions)
      });
      marks.push(mark2);
      ids.add(id);
      return mark2;
    },
    cun(outline, cunOptions = {}) {
      const region = copyPolygon(outline);
      const technique = cunOptions.technique ?? "pima";
      if (technique !== "pima" && technique !== "axe") throw new RangeError("technique must be pima or axe");
      const count = integer(cunOptions.count ?? 56, "count");
      bounded(count, "count", 0, MAX_GENERATED_MARKS);
      const length = positive(cunOptions.length ?? Math.min(width, height) * 0.16, "length");
      const prefix2 = text(cunOptions.idPrefix ?? availableId(technique), "idPrefix");
      const baseSeed = seedValue(cunOptions.seed ?? seed ^ hashText(prefix2));
      const random = createRandom(baseSeed);
      const baseDirection = copyPoint(cunOptions.direction ?? { x: 0.25, y: 1 }, "direction");
      if (Math.hypot(baseDirection.x, baseDirection.y) <= EPSILON) throw new RangeError("direction must be nonzero");
      if (cunOptions.flow !== void 0 && typeof cunOptions.flow !== "function") throw new TypeError("flow must be a function");
      strokeMark(`${prefix2}-validation`, [{ x: 0, y: 0 }, { x: 0, y: 1 }], cunOptions, baseSeed);
      for (let i = 0; i < count; i++) assertUnused(`${prefix2}-${i + 1}`);
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const point2 of region) {
        minX = Math.min(minX, point2.x);
        maxX = Math.max(maxX, point2.x);
        minY = Math.min(minY, point2.y);
        maxY = Math.max(maxY, point2.y);
      }
      const generated = [];
      let attempts = 0;
      const attemptLimit = Math.max(2e3, count * 200);
      while (generated.length < count) {
        if (++attempts > attemptLimit) throw new RangeError("Could not sample this region within the bounded attempt budget; simplify or split the polygon");
        const origin = { x: minX + random() * (maxX - minX), y: minY + random() * (maxY - minY) };
        if (!pointInPolygon(origin, region)) continue;
        const index = generated.length;
        const markLength = length * (0.45 + random() * 0.75);
        const bend = (random() - 0.5) * markLength * (technique === "pima" ? 0.28 : 0.08);
        const steps = technique === "pima" ? 8 : 4;
        const phase = random() * TAU;
        const path2 = [];
        let x = origin.x;
        let y = origin.y;
        for (let step = 0; step <= steps; step++) {
          const t = step / steps;
          const direction = copyPoint(cunOptions.flow === void 0 ? baseDirection : cunOptions.flow(Object.freeze({ x, y })), "flow direction");
          const magnitude = Math.hypot(direction.x, direction.y);
          if (!Number.isFinite(magnitude) || magnitude <= EPSILON) throw new RangeError("flow direction must have finite, nonzero length");
          const dx = direction.x / magnitude;
          const dy = direction.y / magnitude;
          const offset = Math.sin(t * Math.PI) * bend + Math.sin(t * TAU + phase) * markLength * 0.018;
          const pressure = technique === "pima" ? 0.12 + 0.76 * Math.sin(Math.PI * (0.08 + t * 0.86)) : 0.08 + 0.9 * Math.pow(1 - t, 0.7);
          path2.push({ x: x - dy * offset, y: y + dx * offset, pressure });
          x += dx * markLength / steps;
          y += dy * markLength / steps;
        }
        const id = `${prefix2}-${index + 1}`;
        const markOptions = {
          ...cunOptions,
          width: cunOptions.width ?? (technique === "pima" ? 2.2 : 10),
          dryness: cunOptions.dryness ?? (technique === "pima" ? 0.62 : 0.38),
          nib: cunOptions.nib ?? (technique === "pima" ? "round" : "flat"),
          tipAngle: cunOptions.tipAngle ?? Math.atan2(baseDirection.y, baseDirection.x) - 0.55,
          seed: (baseSeed ^ hashText(id)) >>> 0,
          clip: region
        };
        generated.push(strokeMark(id, path2, markOptions, baseSeed));
      }
      for (const mark2 of generated) {
        marks.push(mark2);
        ids.add(mark2.id);
      }
      return Object.freeze(generated);
    },
    snapshot() {
      return Object.freeze({ version: 1, width, height, seed, background, marks: Object.freeze([...marks]) });
    }
  };
  return Object.freeze(builder);
}

// src/path.ts
var EPSILON2 = 1e-9;
var MAX_STEPS = 8192;
var TABLE_BUDGET = 32768;
function finite2(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function sourcePoints(points) {
  if (!Array.isArray(points) || points.length < 2) throw new RangeError("A stroke requires at least two points");
  const result = [];
  let length = 0;
  for (const point2 of points) {
    if (!point2 || typeof point2 !== "object") throw new TypeError("points must contain points");
    const copy = {
      x: finite2(point2.x, "point.x"),
      y: finite2(point2.y, "point.y"),
      pressure: finite2(point2.pressure ?? 1, "point.pressure")
    };
    if (copy.pressure < 0 || copy.pressure > 1) throw new RangeError("point.pressure must be in 0..1");
    const previous = result[result.length - 1];
    const step = previous ? Math.hypot(copy.x - previous.x, copy.y - previous.y) : 0;
    length += step;
    if (!Number.isFinite(length)) throw new RangeError("Path length must be finite");
    if (previous && step < EPSILON2) result[result.length - 1] = copy;
    else result.push(copy);
  }
  return result;
}
function tangent(previous, point2, next) {
  const ax = point2.x - previous.x, ay = point2.y - previous.y;
  const bx = next.x - point2.x, by = next.y - point2.y;
  const before = Math.sqrt(Math.hypot(ax, ay));
  const after = Math.sqrt(Math.hypot(bx, by));
  return {
    x: ax / before - (ax + bx) / (before + after) + bx / after,
    y: ay / before - (ay + by) / (before + after) + by / after
  };
}
function segments(points, smooth) {
  return points.slice(1).map((b, index) => {
    const a = points[index];
    const delta = { x: b.x - a.x, y: b.y - a.y };
    const knot = Math.sqrt(Math.hypot(delta.x, delta.y));
    const before = smooth && index > 0 ? tangent(points[index - 1], a, b) : void 0;
    const after = smooth && index + 2 < points.length ? tangent(a, b, points[index + 2]) : void 0;
    return {
      a,
      b,
      m0: before ? { x: before.x * knot, y: before.y * knot } : delta,
      m1: after ? { x: after.x * knot, y: after.y * knot } : delta
    };
  });
}
function evaluate(segment, t, smooth) {
  const { a, b, m0, m1 } = segment;
  const dx = b.x - a.x, dy = b.y - a.y;
  if (!smooth) return {
    x: a.x + dx * t,
    y: a.y + dy * t,
    pressure: a.pressure + (b.pressure - a.pressure) * t,
    dx,
    dy
  };
  const t2 = t * t, t3 = t2 * t;
  const position = 3 * t2 - 2 * t3;
  const left = t3 - 2 * t2 + t;
  const right = t3 - t2;
  const slope = 6 * t - 6 * t2;
  const leftSlope = 3 * t2 - 4 * t + 1;
  const rightSlope = 3 * t2 - 2 * t;
  const scale = Math.max(Math.abs(dx), Math.abs(dy), Math.abs(m0.x), Math.abs(m0.y), Math.abs(m1.x), Math.abs(m1.y));
  return {
    x: a.x + dx * position + m0.x * left + m1.x * right,
    y: a.y + dy * position + m0.y * left + m1.y * right,
    pressure: a.pressure + (b.pressure - a.pressure) * position,
    dx: dx / scale * slope + m0.x / scale * leftSlope + m1.x / scale * rightSlope,
    dy: dy / scale * slope + m0.y / scale * leftSlope + m1.y / scale * rightSlope
  };
}
function sampleStrokePath(points, options = {}) {
  const spacing = finite2(options.spacing ?? 2, "spacing");
  if (spacing <= 0) throw new RangeError("spacing must be positive");
  const smoothing = options.smoothing ?? "linear";
  if (smoothing !== "linear" && smoothing !== "catmull-rom") throw new RangeError("smoothing must be linear or catmull-rom");
  const source = sourcePoints(points);
  if (source.length === 1) return [{ ...source[0], distance: 0, nx: 0, ny: 1 }];
  const smooth = smoothing === "catmull-rom" && source.length > 2;
  const curve = segments(source, smooth);
  const divisions = smooth ? Math.max(1, Math.min(24, Math.floor(TABLE_BUDGET / curve.length))) : 1;
  const table = [{ distance: 0, segment: 0, t: 0 }];
  let total = 0;
  let previous = source[0];
  for (let index = 0; index < curve.length; index++) {
    for (let step = 1; step <= divisions; step++) {
      const t = step / divisions;
      const point2 = evaluate(curve[index], t, smooth);
      total += Math.hypot(point2.x - previous.x, point2.y - previous.y);
      if (!Number.isFinite(total)) throw new RangeError("Interpolated path length must be finite");
      table.push({ distance: total, segment: index, t });
      previous = point2;
    }
  }
  const steps = Math.min(MAX_STEPS, Math.max(1, Math.ceil(total / spacing)));
  const result = [];
  let cursor = 1;
  for (let i = 0; i <= steps; i++) {
    const distance = total * (i / steps);
    while (cursor < table.length - 1 && table[cursor].distance < distance) cursor++;
    const left = table[cursor - 1], right = table[cursor];
    const fraction = right.distance > left.distance ? (distance - left.distance) / (right.distance - left.distance) : 0;
    const low = left.segment === right.segment ? left.t : 0;
    const t = low + (right.t - low) * fraction;
    const segment = curve[right.segment];
    const point2 = evaluate(segment, t, smooth);
    if (!Number.isFinite(point2.x) || !Number.isFinite(point2.y)) throw new RangeError("Interpolated path coordinates must be finite");
    let dx = point2.dx, dy = point2.dy;
    let magnitude = Math.hypot(dx, dy);
    if (magnitude < EPSILON2) {
      dx = segment.b.x - segment.a.x;
      dy = segment.b.y - segment.a.y;
      magnitude = Math.hypot(dx, dy);
    }
    const endpoint = i === 0 ? source[0] : i === steps ? source[source.length - 1] : void 0;
    result.push({
      x: endpoint?.x ?? point2.x,
      y: endpoint?.y ?? point2.y,
      pressure: endpoint?.pressure ?? Math.max(0, Math.min(1, point2.pressure)),
      distance,
      nx: -dy / magnitude,
      ny: dx / magnitude
    });
  }
  return result;
}

// src/perturbation.ts
var clamp = (value) => Math.max(0, Math.min(1, value));
var ease = (t) => t * t * t * (t * (t * 6 - 15) + 10);
function finite3(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function positive2(value, name) {
  if (finite3(value, name) <= 0) throw new RangeError(`${name} must be positive`);
  return value;
}
function bounded2(value, name) {
  if (finite3(value, name) < 0 || value > 1) throw new RangeError(`${name} must be in 0..1`);
  return value;
}
function seedValue2(seed) {
  if (!Number.isSafeInteger(finite3(seed, "seed"))) throw new RangeError("seed must be a safe integer");
  return seed >>> 0;
}
function optionsObject(options, fields7) {
  if (!options || typeof options !== "object" || Array.isArray(options)) throw new TypeError("options must be an object");
  for (const key of Object.keys(options)) if (!fields7.includes(key)) throw new RangeError(`Unsupported perturbation option: ${key}`);
}
function hash(seed, lattice) {
  let value = (seed ^ Math.imul(lattice, 2654435761)) >>> 0;
  value = Math.imul(value ^ value >>> 16, 2146121005);
  value = Math.imul(value ^ value >>> 15, 2221713035);
  return ((value ^ value >>> 16) >>> 0) / 4294967296;
}
function createNoise1D(seed) {
  const key = seedValue2(seed);
  return (position) => {
    finite3(position, "noise position");
    if (Math.abs(position) > Number.MAX_SAFE_INTEGER - 1) throw new RangeError("noise position exceeds safe lattice range");
    const left = Math.floor(position), t = position - left;
    const a = (hash(key, left) * 2 - 1) * t;
    const b = (hash(key, left + 1) * 2 - 1) * (t - 1);
    return Math.max(-1, Math.min(1, 2 * (a + (b - a) * ease(t))));
  };
}
function varyPressure(base, variance, options = {}) {
  bounded2(base, "base pressure");
  bounded2(variance, "variance");
  optionsObject(options, ["seed", "at", "wavelength"]);
  const noise6 = createNoise1D(options.seed ?? 0), at2 = finite3(options.at ?? 0, "at");
  const wavelength = positive2(options.wavelength ?? 1, "wavelength");
  const position = at2 / wavelength;
  const value = 0.74 * noise6(position + 17.173) + 0.26 * noise6(position * 3.17 + 61.71);
  return clamp(base * (1 + variance * value));
}
function pathData(points) {
  if (!Array.isArray(points) || points.length < 2 || points.length > 1e5) throw new RangeError("A perturbation path needs 2..100000 points");
  const copies = points.map((point2) => {
    if (!point2 || typeof point2 !== "object") throw new TypeError("path must contain points");
    return {
      x: finite3(point2.x, "point.x"),
      y: finite3(point2.y, "point.y"),
      ...point2.pressure === void 0 ? {} : { pressure: bounded2(point2.pressure, "point.pressure") }
    };
  });
  const distances = [0];
  for (let i = 1; i < copies.length; i++) {
    const a = copies[i - 1], b = copies[i];
    distances.push(finite3(distances[i - 1] + Math.hypot(b.x - a.x, b.y - a.y), "path length"));
  }
  return { points: copies, distances, length: distances.at(-1) };
}
function freezePoints(points) {
  return Object.freeze(points.map((point2) => Object.freeze(point2)));
}
function jitterPath(points, intensity, options = {}) {
  if (finite3(intensity, "intensity") < 0) throw new RangeError("intensity must be nonnegative");
  optionsObject(options, ["seed", "wavelength", "tangentRatio", "preserveEndpoints"]);
  const key = seedValue2(options.seed ?? 0), wavelength = positive2(options.wavelength ?? 12, "wavelength");
  const tangentRatio = bounded2(options.tangentRatio ?? 0.15, "tangentRatio");
  const preserve = options.preserveEndpoints ?? true;
  if (typeof preserve !== "boolean") throw new TypeError("preserveEndpoints must be boolean");
  const source = pathData(points), noise6 = createNoise1D(key), tangentNoise = createNoise1D(key ^ 1374496523);
  const forward = [], backward = [];
  let direction;
  for (let i = 0; i < source.points.length; i++) {
    if (i) {
      const a = source.points[i - 1], b = source.points[i], dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
      if (length > 0) direction = { x: dx / length, y: dy / length };
    }
    backward.push(direction);
  }
  direction = void 0;
  for (let i = source.points.length - 1; i >= 0; i--) {
    if (i + 1 < source.points.length) {
      const a = source.points[i], b = source.points[i + 1], dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
      if (length > 0) direction = { x: dx / length, y: dy / length };
    }
    forward[i] = direction;
  }
  return freezePoints(source.points.map((point2, i) => {
    if (intensity === 0 || source.length === 0 || preserve && (i === 0 || i === source.points.length - 1)) return point2;
    const before = backward[i] ?? forward[i] ?? { x: 1, y: 0 }, after = forward[i] ?? backward[i] ?? before;
    const x = before.x + after.x, y = before.y + after.y, magnitude = Math.hypot(x, y);
    const tx = magnitude > 1e-9 ? x / magnitude : after.x, ty = magnitude > 1e-9 ? y / magnitude : after.y;
    const distance = source.distances[i], at2 = distance / wavelength;
    const normal = 0.74 * noise6(at2 + 17.173) + 0.26 * noise6(at2 * 3.17 + 61.71);
    const tangent3 = tangentNoise(at2 * 0.83 + 29.37) * tangentRatio;
    const envelope = preserve ? ease(Math.min(1, distance / wavelength, (source.length - distance) / wavelength)) : 1;
    const scale = intensity * envelope / Math.max(1, Math.hypot(normal, tangent3));
    return {
      ...point2,
      x: finite3(point2.x + (-ty * normal + tx * tangent3) * scale, "perturbed x"),
      y: finite3(point2.y + (tx * normal + ty * tangent3) * scale, "perturbed y")
    };
  }));
}
function splitBristles(path2, count, options = {}) {
  if (!Number.isInteger(finite3(count, "count")) || count < 1 || count > 64) throw new RangeError("count must be an integer in 1..64");
  optionsObject(options, ["seed", "spread", "width", "opacity", "variance", "spacing"]);
  const seed = seedValue2(options.seed ?? 0), width = positive2(options.width ?? 2, "width");
  const spread2 = finite3(options.spread ?? 12, "spread");
  if (spread2 < 0) throw new RangeError("spread must be nonnegative");
  const opacity = bounded2(options.opacity ?? 0.8, "opacity"), variance = bounded2(options.variance ?? 0.3, "variance");
  const spacing = positive2(options.spacing ?? 2, "spacing"), source = pathData(path2);
  const zeroPositions = new Set(source.points.flatMap((point2, i) => point2.pressure === 0 && source.length > 0 ? [source.distances[i] / source.length] : []));
  const budget = Math.min(4092, Math.floor(1e5 / count) - 4) - zeroPositions.size;
  if (budget < 2) throw new RangeError("Too many pressure-lift anchors for the split source budget");
  const effectiveSpacing = Math.max(spacing, source.length / budget);
  const samples = sampleStrokePath(source.points, { spacing: effectiveSpacing });
  const total = samples.at(-1).distance, result = [];
  if (total === 0) throw new RangeError("A split bundle needs a path with nonzero length");
  const pointAt = (at2) => {
    const position = at2 * (samples.length - 1), index = Math.min(samples.length - 2, Math.floor(position)), t = position - index;
    const a = samples[index], b = samples[index + 1];
    const distance = at2 * total;
    let low = 0, high = source.distances.length - 1;
    while (low < high) {
      const middle = low + high >>> 1;
      if (source.distances[middle] < distance) low = middle + 1;
      else high = middle;
    }
    const previous = Math.max(0, low - 1), span = source.distances[low] - source.distances[previous];
    const fraction = span > 0 ? (distance - source.distances[previous]) / span : 1;
    const pressure = zeroPositions.has(at2) ? 0 : (source.points[previous].pressure ?? 1) * (1 - fraction) + (source.points[low].pressure ?? 1) * fraction;
    return {
      x: a.x + (b.x - a.x) * t,
      y: a.y + (b.y - a.y) * t,
      pressure,
      nx: a.nx + (b.nx - a.nx) * t,
      ny: a.ny + (b.ny - a.ny) * t
    };
  };
  for (let strand = 0; strand < count; strand++) {
    const rng = createRandom(seed ^ Math.imul(strand + 1, 2654435761));
    const noise6 = createNoise1D(seed ^ Math.imul(strand + 1, 1374496523));
    const center = strand === 0, side = strand % 2 ? -1 : 1;
    const rank = Math.ceil(strand / 2) / Math.max(1, Math.ceil((count - 1) / 2));
    const lateral = center ? 0 : side * spread2 * 0.5 * rank * (1 + (rng() * 2 - 1) * variance);
    const start = center ? 0 : rng() * 0.27, end = center ? 1 : 1 - rng() * 0.25;
    const gap = !center && rng() < 0.68, gapAt = 0.35 + rng() * 0.3, gapSize = 0.035 + rng() * 0.08;
    const ranges = gap ? [[start, gapAt - gapSize], [gapAt + gapSize, end]] : [[start, end]];
    const strandWidth = width * (center ? 1 : Math.max(0.08, Math.min(0.88, (0.38 + rng() * 0.28) * (1 + (rng() * 2 - 1) * variance))));
    const strandOpacity = opacity * (center ? 1 : Math.max(0.08, Math.min(0.94, (0.56 + rng() * 0.25) * (1 + (rng() * 2 - 1) * variance))));
    const tilt = (rng() * 2 - 1) * spread2 * 0.33;
    ranges.forEach(([from, to], part) => {
      const steps = Math.max(1, Math.ceil((to - from) * (samples.length - 1)));
      const positions = [.../* @__PURE__ */ new Set([
        ...Array.from({ length: steps + 1 }, (_, i) => from + (to - from) * (i / steps)),
        ...[...zeroPositions].filter((t) => t >= from && t <= to)
      ])].sort((a, b) => a - b);
      const points = positions.map((t) => {
        const u = (t - from) / (to - from), point2 = pointAt(t);
        const gather = 0.12 + 0.88 * Math.pow(Math.max(0, 4 * t * (1 - t)), 0.72);
        const offset = lateral * gather + (center ? 0.025 : 0.13) * spread2 * noise6(t * 6.7 + 19.173) + tilt * (t - 0.5);
        const taper = center ? 1 : 0.12 + 0.88 * Math.pow(Math.max(0, 4 * u * (1 - u)), 0.6);
        const pressure = varyPressure(point2.pressure, variance, { seed: seed ^ strand, at: t, wavelength: 0.16 }) * taper;
        return { x: finite3(point2.x + point2.nx * offset, "bristle x"), y: finite3(point2.y + point2.ny * offset, "bristle y"), pressure };
      });
      result.push(Object.freeze({
        id: `bristle-${strand}${gap ? `-part-${part}` : ""}`,
        points: freezePoints(points),
        width: finite3(strandWidth, "bristle width"),
        opacity: clamp(strandOpacity)
      }));
    });
  }
  if (result.reduce((sum, item) => sum + item.points.length, 0) > 1e5) throw new RangeError("Split source exceeds the point budget");
  return Object.freeze(result);
}

// src/natural.ts
function finite4(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function unit(value, name) {
  if (finite4(value, name) < 0 || value > 1) throw new RangeError(`${name} must be in 0..1`);
  return value;
}
function seedValue3(seed) {
  if (!Number.isSafeInteger(finite4(seed, "seed"))) throw new RangeError("seed must be a safe integer");
  return seed >>> 0;
}
function coordinate(value) {
  if (Math.abs(finite4(value, "noise coordinate")) > Number.MAX_SAFE_INTEGER - 1) throw new RangeError("Noise coordinate exceeds the safe lattice");
  return value;
}
var ease2 = (t) => t * t * t * (t * (t * 6 - 15) + 10);
function hash2(seed, x, y) {
  let n = (seed ^ Math.imul(x, 2654435761) ^ Math.imul(y, 2246822519)) >>> 0;
  n = Math.imul(n ^ n >>> 16, 2146121005);
  n = Math.imul(n ^ n >>> 15, 2221713035);
  return (n ^ n >>> 16) >>> 0;
}
function gradient(h, x, y) {
  switch (h & 7) {
    case 0:
      return x;
    case 1:
      return -x;
    case 2:
      return y;
    case 3:
      return -y;
    case 4:
      return (x + y) * Math.SQRT1_2;
    case 5:
      return (x - y) * Math.SQRT1_2;
    case 6:
      return (-x + y) * Math.SQRT1_2;
    default:
      return (-x - y) * Math.SQRT1_2;
  }
}
function noise2D(x, y, seed) {
  const ix = Math.floor(x), iy = Math.floor(y), tx = x - ix, ty = y - iy, u = ease2(tx), v = ease2(ty);
  const a = gradient(hash2(seed, ix, iy), tx, ty), b = gradient(hash2(seed, ix + 1, iy), tx - 1, ty);
  const c = gradient(hash2(seed, ix, iy + 1), tx, ty - 1), d = gradient(hash2(seed, ix + 1, iy + 1), tx - 1, ty - 1);
  return Math.max(-1, Math.min(1, ((a + (b - a) * u) * (1 - v) + (c + (d - c) * u) * v) * Math.SQRT2));
}
function getNoise1D(x, seed = 0) {
  return createNoise1D(seed)(x);
}
function getNoise2D(x, y, seed = 0) {
  return noise2D(coordinate(x), coordinate(y), seedValue3(seed));
}
function getFractalNoise2D(x, y, seed = 0, octaves = 4) {
  coordinate(x);
  coordinate(y);
  const key = seedValue3(seed);
  if (!Number.isInteger(octaves) || octaves < 1 || octaves > 8) throw new RangeError("octaves must be in 1..8");
  let value = 0, weight = 0, amplitude = 1, frequency = 1;
  for (let octave = 0; octave < octaves; octave++) {
    value += amplitude * noise2D(coordinate(x * frequency), coordinate(y * frequency), key ^ Math.imul(octave, 1374496523));
    weight += amplitude;
    amplitude *= 0.5;
    frequency *= 2;
  }
  return value / weight;
}
function accumulateEdge(alpha, thickness) {
  unit(alpha, "alpha");
  unit(thickness, "thickness");
  if (thickness === 0 || alpha <= 0 || alpha >= thickness) return 0;
  const t = alpha / thickness;
  return Math.min(1, 16 * t * t * (1 - t) * (1 - t));
}
function poissonDiskSampling(width, height, r, options = {}) {
  for (const [name, value] of [["width", width], ["height", height], ["r", r]]) {
    if (finite4(value, name) <= 0) throw new RangeError(`${name} must be positive`);
  }
  if (!options || typeof options !== "object" || Array.isArray(options)) throw new TypeError("options must be an object");
  for (const key of Object.keys(options)) if (!["seed", "attempts", "maxPoints"].includes(key)) throw new RangeError(`Unsupported sampling option: ${key}`);
  const seed = seedValue3(options.seed ?? 0), attempts = options.attempts ?? 30, maximum = options.maxPoints ?? 4096;
  if (!Number.isInteger(attempts) || attempts < 1 || attempts > 64) throw new RangeError("attempts must be in 1..64");
  if (!Number.isInteger(maximum) || maximum < 1 || maximum > 1e5) throw new RangeError("maxPoints must be in 1..100000");
  const cell = r * Math.SQRT1_2;
  const columns = Math.ceil(width / cell), rows = Math.ceil(height / cell);
  if (cell === 0 || !Number.isSafeInteger(columns) || !Number.isSafeInteger(rows) || columns > Number.MAX_SAFE_INTEGER - 4 || rows > Number.MAX_SAFE_INTEGER - 4) throw new RangeError("Sampling grid exceeds safe coordinates");
  const rng = createRandom(seed), points = [], active = [], grid = /* @__PURE__ */ new Map();
  const keyAt = (x, y) => `${x},${y}`;
  const insert = (point2) => {
    const index = points.length, key = keyAt(Math.floor(point2.x / cell), Math.floor(point2.y / cell));
    points.push(Object.freeze(point2));
    active.push(index);
    const indices = grid.get(key) ?? [];
    indices.push(index);
    grid.set(key, indices);
  };
  insert({ x: rng() * width, y: rng() * height });
  while (active.length && points.length < maximum) {
    const activeIndex = Math.floor(rng() * active.length), center = points[active[activeIndex]];
    let accepted = false;
    for (let attempt = 0; attempt < attempts; attempt++) {
      const angle = rng() * Math.PI * 2, radius = r * Math.sqrt(1 + 3 * rng());
      const point2 = { x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius };
      if (!(point2.x >= 0 && point2.x < width && point2.y >= 0 && point2.y < height)) continue;
      const cx = Math.floor(point2.x / cell), cy = Math.floor(point2.y / cell);
      let separated = true;
      for (let dy = -2; dy <= 2 && separated; dy++) for (let dx = -2; dx <= 2 && separated; dx++) {
        for (const index of grid.get(keyAt(cx + dx, cy + dy)) ?? []) {
          const other = points[index];
          if (Math.hypot(point2.x - other.x, point2.y - other.y) < r) {
            separated = false;
            break;
          }
        }
      }
      if (separated) {
        insert(point2);
        accepted = true;
        break;
      }
    }
    if (!accepted) {
      active[activeIndex] = active[active.length - 1];
      active.pop();
    }
  }
  return Object.freeze(points);
}

// src/brush-contact.ts
function sampleBrushContact(mark2, options = {}) {
  if (!Number.isFinite(mark2.width) || mark2.width <= 0) throw new RangeError("stroke.width must be positive");
  if (!Number.isFinite(mark2.dryness) || mark2.dryness < 0 || mark2.dryness > 1) throw new RangeError("stroke.dryness must be in 0..1");
  const brush = copyStrokeBrush(mark2.brush ?? { version: 1, controls: [] });
  const sampled = sampleStrokePath(mark2.points, { spacing: options.spacing ?? Math.max(0.65, Math.min(2, mark2.width * 0.05)), smoothing: mark2.smoothing ?? "linear" });
  const total = sampled.at(-1).distance;
  const positionAt = (distance) => {
    let low = 0, high = sampled.length - 1;
    while (low < high) {
      const middle = low + high >>> 1;
      if (sampled[middle].distance < distance) low = middle + 1;
      else high = middle;
    }
    if (low === 0) return sampled[0];
    const a = sampled[low - 1], b = sampled[low];
    const fraction = (distance - a.distance) / (b.distance - a.distance);
    const nx = a.nx * (1 - fraction) + b.nx * fraction, ny = a.ny * (1 - fraction) + b.ny * fraction;
    const magnitude = Math.hypot(nx, ny);
    return {
      x: a.x * (1 - fraction) + b.x * fraction,
      y: a.y * (1 - fraction) + b.y * fraction,
      pressure: a.pressure * (1 - fraction) + b.pressure * fraction,
      distance,
      nx: magnitude > 1e-8 ? nx / magnitude : b.nx,
      ny: magnitude > 1e-8 ? ny / magnitude : b.ny
    };
  };
  const anchored = new Map(sampled.map((sample) => [sample.distance, sample]));
  for (const control of brush.controls) {
    const distance = total * control.at;
    if (!anchored.has(distance)) anchored.set(distance, positionAt(distance));
  }
  const path2 = [...anchored.values()].sort((a, b) => a.distance - b.distance);
  return path2.map((sample) => {
    const state = brushStateAt(
      brush,
      total === 0 ? 0 : sample.distance / total,
      { pressure: 1, side: 0, angle: Math.PI / 2, dryness: mark2.dryness, inkLoad: 1 }
    );
    const pressure = sample.pressure * state.pressure;
    const reach = Math.min(total * 0.04, mark2.width * 0.35);
    const before = positionAt(Math.max(0, sample.distance - reach));
    const after = positionAt(Math.min(total, sample.distance + reach));
    const dx = after.x - before.x, dy = after.y - before.y, magnitude = Math.hypot(dx, dy);
    const nx = magnitude > 1e-8 ? -dy / magnitude : sample.nx, ny = magnitude > 1e-8 ? dx / magnitude : sample.ny;
    const across = Math.sin(state.angle);
    const halfWidth2 = mark2.width * 0.5 * Math.pow(pressure, 0.72) * (1 + state.side * 0.48 * Math.abs(across));
    const offset = halfWidth2 * state.side * 0.66 * across;
    const centerX = sample.x + nx * offset, centerY = sample.y + ny * offset;
    if (![
      centerX,
      centerY,
      halfWidth2,
      centerX + nx * halfWidth2,
      centerY + ny * halfWidth2,
      centerX - nx * halfWidth2,
      centerY - ny * halfWidth2
    ].every(Number.isFinite)) throw new RangeError("Brush contact coordinates exceed finite range");
    return {
      ...sample,
      nx,
      ny,
      pressure,
      centerX,
      centerY,
      halfWidth: halfWidth2,
      side: state.side,
      angle: state.angle,
      dryness: state.dryness,
      inkLoad: state.inkLoad,
      leftX: centerX + nx * halfWidth2,
      leftY: centerY + ny * halfWidth2,
      rightX: centerX - nx * halfWidth2,
      rightY: centerY - ny * halfWidth2
    };
  });
}

// src/canvas-contact.ts
var cache = /* @__PURE__ */ new WeakMap();
var clamp2 = (value, low = 0, high = 1) => Math.max(low, Math.min(high, value));
function hash3(seed, index, channel) {
  let value = ((seed | 0) ^ Math.imul(index | 0, 2654435761) ^ Math.imul(channel, 2246822519)) >>> 0;
  value = Math.imul(value ^ value >>> 16, 2146121005);
  value = Math.imul(value ^ value >>> 15, 2221713035);
  return ((value ^ value >>> 16) >>> 0) / 4294967296;
}
function noise(seed, at2, channel) {
  const index = Math.floor(at2), t = at2 - index, eased = t * t * (3 - 2 * t);
  return hash3(seed, index, channel) * (1 - eased) + hash3(seed, index + 1, channel) * eased;
}
function split(points, alpha) {
  const result = [];
  let run = [], previous;
  for (const point2 of points) {
    if (point2.left + point2.right > 1e-8) {
      if (run.length === 0 && previous) run.push(previous);
      run.push(point2);
    } else if (run.length) {
      run.push(point2);
      result.push({ alpha, points: run });
      run = [];
    }
    previous = point2;
  }
  if (run.length) result.push({ alpha, points: run });
  return result;
}
function releaseFilaments(mark2, samples) {
  const last = samples.at(-1), length = last.distance;
  if (mark2.width < 8 || length < Math.max(24, mark2.width * 6) || last.pressure > 0.08 || last.inkLoad <= 0.15 || mark2.opacity === 0) return [];
  const station2 = (distance) => {
    let low = 0, high = samples.length - 1;
    while (low < high) {
      const middle = low + high >>> 1;
      if (samples[middle].distance < distance) low = middle + 1;
      else high = middle;
    }
    return samples[low];
  };
  const lookback = Math.min(length * 0.4, Math.max(mark2.width * 3, length * 0.18));
  const release = samples.filter((sample) => sample.distance >= length - lookback);
  const peak = Math.max(...release.map((sample) => sample.pressure));
  if (peak < 0.15 || last.pressure > peak * 0.28 || peak - last.pressure < 0.12) return [];
  const tail = release.filter((sample) => sample.distance >= length - Math.min(lookback * 0.3, mark2.width * 0.45));
  if (tail.some((sample, index) => index > 0 && sample.pressure > tail[index - 1].pressure + 0.012)) return [];
  const before = samples[Math.max(0, samples.length - 3)];
  const dx = last.x - before.x, dy = last.y - before.y, magnitude = Math.hypot(dx, dy);
  const approachStart = station2(length - lookback), approachEnd = station2(length - lookback * 0.45);
  const ax = approachEnd.x - approachStart.x, ay = approachEnd.y - approachStart.y, approachLength = Math.hypot(ax, ay);
  if (magnitude < 1e-8 || approachLength < 1e-8 || (dx * ax + dy * ay) / (magnitude * approachLength) < 0.65) return [];
  const tx = dx / magnitude, ty = dy / magnitude, nx = -ty, ny = tx;
  const revealReach = Math.min(3, mark2.width * 0.08, length * 0.012);
  const anchor = station2(length - revealReach);
  if (anchor.inkLoad <= 0.15) return [];
  const count = 1 + (hash3(mark2.seed, 0, 1100) > 0.76 ? 1 : 0), result = [];
  for (let fiber = 0; fiber < count; fiber++) {
    const extension = Math.min(7, mark2.width * 0.2, length * 0.015) * (0.35 + hash3(mark2.seed, fiber, 1101) * 0.65);
    const side = hash3(mark2.seed, fiber, 1102) * 2 - 1;
    const sway = Math.min(1, mark2.width * 0.03) * side;
    const rootOffset = Math.min(0.65, anchor.halfWidth * 0.35) * side;
    const startX = anchor.centerX + nx * rootOffset, startY = anchor.centerY + ny * rootOffset;
    const endX = last.x + tx * extension + nx * sway, endY = last.y + ty * extension + ny * sway;
    const controlX = last.x + tx * extension * 0.2 - nx * sway * 0.3;
    const controlY = last.y + ty * extension * 0.2 - ny * sway * 0.3;
    const points = Array.from({ length: 9 }, (_, index) => {
      const t = index / 8, u = 1 - t;
      const vx = 2 * (controlX - startX) * u + 2 * (endX - controlX) * t;
      const vy = 2 * (controlY - startY) * u + 2 * (endY - controlY) * t;
      const speed = Math.hypot(vx, vy), radius = Math.min(0.22, Math.max(0.1, mark2.width * 45e-4)) * Math.pow(u, 1.5);
      return {
        x: startX * u * u + 2 * controlX * u * t + endX * t * t,
        y: startY * u * u + 2 * controlY * u * t + endY * t * t,
        nx: speed > 1e-8 ? -vy / speed : nx,
        ny: speed > 1e-8 ? vx / speed : ny,
        left: radius,
        right: radius,
        distance: length - revealReach + t * revealReach
      };
    });
    result.push({ points, alpha: mark2.opacity * Math.min(last.inkLoad, anchor.inkLoad) * (0.065 + hash3(mark2.seed, fiber, 1103) * 0.045) });
  }
  return result;
}
function prepare(mark2) {
  const immutable = Object.isFrozen(mark2) && Object.isFrozen(mark2.points) && mark2.points.every(Object.isFrozen) && !!mark2.brush && Object.isFrozen(mark2.brush) && Object.isFrozen(mark2.brush.controls) && mark2.brush.controls.every(Object.isFrozen);
  const stored = immutable ? cache.get(mark2) : void 0;
  if (stored) return stored;
  const samples = sampleBrushContact(mark2), bands = [];
  const length = samples.at(-1).distance;
  const wetBody = samples.map((sample) => {
    const fineScale = Math.max(1.4, mark2.width * 0.075);
    const broadScale = Math.max(5, mark2.width * 0.38);
    const leftGrain = noise(mark2.seed, sample.distance / fineScale, 600);
    const rightGrain = noise(mark2.seed, sample.distance / fineScale + 7.3, 601);
    const leftBody = noise(mark2.seed, sample.distance / broadScale, 620);
    const rightBody = noise(mark2.seed, sample.distance / broadScale + 3.7, 621);
    const side = sample.side * Math.sin(sample.angle);
    const roughness = (0.035 + sample.pressure * 0.075 + sample.dryness * 0.1) * Math.min(1, mark2.width / 5);
    const leftRough = roughness * (1 + Math.max(0, -side) * 2.1);
    const rightRough = roughness * (1 + Math.max(0, side) * 2.1);
    const contact = clamp2(1 - Math.pow(sample.dryness / 0.76, 1.18)) * sample.inkLoad;
    const left = clamp2(contact * (1 - leftRough * (0.25 + leftGrain * 0.75) - (1 - leftBody) * 0.1));
    const right = clamp2(contact * (1 - rightRough * (0.25 + rightGrain * 0.75) - (1 - rightBody) * 0.1));
    return {
      x: sample.centerX,
      y: sample.centerY,
      nx: sample.nx,
      ny: sample.ny,
      left: sample.halfWidth * left,
      right: sample.halfWidth * right,
      distance: sample.distance
    };
  });
  const count = 7 + Math.floor(hash3(mark2.seed, 0, 700) * 3);
  const weights = Array.from({ length: count }, (_, group) => 0.35 + hash3(mark2.seed, group, 701) * 1.3);
  const sum = weights.reduce((a, b) => a + b, 0);
  let before = 0;
  for (let fiber = 0; fiber < count * 3; fiber++) {
    const group = Math.floor(fiber / 3), branch = fiber % 3;
    if (branch === 0 && group > 0) before += weights[group - 1];
    const centerOfTuft = -1 + (before + weights[group] * 0.5) / sum * 2;
    const offset = clamp2(centerOfTuft + (branch === 0 ? 0 : (hash3(mark2.seed, fiber, 702) - 0.5) * 0.19), -0.96, 0.96);
    const breadth = branch === 0 ? weights[group] / sum * (0.76 + hash3(mark2.seed, group, 703) * 0.74) : (0.055 + hash3(mark2.seed, fiber, 704) * 0.24) / count;
    const phase = hash3(mark2.seed, group, 705) * 17;
    const points = samples.map((sample, index) => {
      const along = sample.distance / Math.max(3, mark2.width * (0.14 + hash3(mark2.seed, group, 706) * 0.45));
      const shared = noise(mark2.seed, along * 0.65 + phase, 710 + group);
      const local = noise(mark2.seed, along * 1.7 + phase, 800 + fiber);
      const slow = noise(mark2.seed, along * 0.27 + phase, 900 + group);
      const contact = local * 0.33 + shared * 0.5 + slow * 0.17;
      const rupture = noise(mark2.seed, sample.distance / Math.max(3, mark2.width * 0.3), 720);
      const roughSide = Math.max(0, -offset * sample.side * Math.sin(sample.angle));
      const threshold = 0.025 + Math.pow(sample.dryness, 1.25) * 0.52 + (1 - sample.inkLoad) * 0.48 + sample.dryness * clamp2(0.48 - rupture) * 0.5 + roughSide * 0.14;
      const amount = clamp2((contact - threshold) / 0.19);
      const taper = amount * amount * (3 - 2 * amount);
      const wander = ((slow - 0.5) * 0.24 + (shared - 0.5) * 0.12) * Math.min(1, mark2.width / 5);
      const center = clamp2(offset + wander, -0.99, 0.99) * sample.halfWidth;
      const endReach = Math.min(length * 0.08, mark2.width * 0.2);
      const entryDistance = hash3(mark2.seed, group, 730) * endReach;
      const finishDistance = hash3(mark2.seed, group, 731) * endReach;
      const ends = endReach > 0 ? clamp2((sample.distance + endReach - entryDistance) / endReach) * clamp2((length - sample.distance + endReach - finishDistance) / endReach) : 1;
      const radius = Math.max(0, Math.min(
        sample.halfWidth - Math.abs(center),
        sample.halfWidth * breadth * (0.48 + slow * 1.2) * taper * ends
      )) * sample.inkLoad;
      const edge = noise(mark2.seed, sample.distance / Math.max(1.2, mark2.width * 0.055), 950 + fiber);
      const wet = clamp2((0.48 - sample.dryness) / 0.32);
      const upper = sample.halfWidth * (1 - wet) + wetBody[index].left * wet;
      const lower = -sample.halfWidth * (1 - wet) - wetBody[index].right * wet;
      const clippedCenter = clamp2(center, lower, upper);
      const left = Math.max(0, Math.min(upper, center + radius * (0.72 + edge * 0.28)) - clippedCenter);
      const right = Math.max(0, clippedCenter - Math.max(lower, center - radius * (1 - edge * 0.28)));
      return {
        x: sample.centerX + sample.nx * clippedCenter,
        y: sample.centerY + sample.ny * clippedCenter,
        nx: sample.nx,
        ny: sample.ny,
        left,
        right,
        distance: sample.distance
      };
    });
    bands.push(...split(points, mark2.opacity * (branch === 0 ? 0.65 + hash3(mark2.seed, group, 740) * 0.3 : 0.4 + hash3(mark2.seed, fiber, 741) * 0.46)));
  }
  bands.push(...split(wetBody, mark2.opacity * 0.94));
  bands.push(...releaseFilaments(mark2, samples));
  const plan = { length, bands };
  if (immutable) cache.set(mark2, plan);
  return plan;
}
function prefix(points, distance) {
  if (!points.length || points[0].distance >= distance) return [];
  if (points.at(-1).distance <= distance) return points;
  const end = points.findIndex((point2) => point2.distance >= distance);
  const a = points[end - 1], b = points[end], t = (distance - a.distance) / (b.distance - a.distance);
  const nx = a.nx * (1 - t) + b.nx * t, ny = a.ny * (1 - t) + b.ny * t, magnitude = Math.hypot(nx, ny);
  return [...points.slice(0, end), {
    x: a.x * (1 - t) + b.x * t,
    y: a.y * (1 - t) + b.y * t,
    nx: magnitude > 1e-8 ? nx / magnitude : b.nx,
    ny: magnitude > 1e-8 ? ny / magnitude : b.ny,
    left: a.left * (1 - t) + b.left * t,
    right: a.right * (1 - t) + b.right * t,
    distance
  }];
}
function ribbon(ctx, points) {
  if (points.length < 2) return;
  ctx.beginPath();
  points.forEach((point2, index) => {
    const x = point2.x + point2.nx * point2.left, y = point2.y + point2.ny * point2.left;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  const last = points.at(-1);
  const lastCap = (last.left + last.right) * 0.28;
  ctx.quadraticCurveTo(
    last.x + last.ny * lastCap,
    last.y - last.nx * lastCap,
    last.x - last.nx * last.right,
    last.y - last.ny * last.right
  );
  for (let i = points.length - 2; i >= 0; i--) {
    const point2 = points[i];
    ctx.lineTo(point2.x - point2.nx * point2.right, point2.y - point2.ny * point2.right);
  }
  const first = points[0];
  const firstCap = (first.left + first.right) * 0.28;
  ctx.quadraticCurveTo(
    first.x - first.ny * firstCap,
    first.y + first.nx * firstCap,
    first.x + first.nx * first.left,
    first.y + first.ny * first.left
  );
  ctx.closePath();
  ctx.fill();
}
function drawContactStroke(ctx, mark2, progress) {
  const plan = prepare(mark2);
  ctx.save();
  try {
    if (mark2.clip) {
      ctx.beginPath();
      mark2.clip.forEach((point2, index) => index ? ctx.lineTo(point2.x, point2.y) : ctx.moveTo(point2.x, point2.y));
      ctx.closePath();
      ctx.clip();
    }
    ctx.fillStyle = "#171b18";
    ctx.fillStyle = mark2.ink;
    for (const band of plan.bands) {
      ctx.globalAlpha = band.alpha;
      ribbon(ctx, prefix(band.points, plan.length * progress));
    }
  } finally {
    ctx.restore();
  }
}

// src/canvas.ts
var TAU2 = Math.PI * 2;
var EPSILON3 = 1e-8;
function clamp3(value, low = 0, high = 1) {
  return Math.max(low, Math.min(high, value));
}
function mix(a, b, t) {
  return a + (b - a) * t;
}
function hash4(seed, index, channel = 0) {
  let value = ((seed | 0) ^ Math.imul(index | 0, 2654435761) ^ Math.imul(channel | 0, 2246822519)) >>> 0;
  value = Math.imul(value ^ value >>> 16, 2146121005);
  value = Math.imul(value ^ value >>> 15, 2221713035);
  return ((value ^ value >>> 16) >>> 0) / 4294967296;
}
function noise2(seed, position, channel = 0) {
  const left = Math.floor(position);
  const t = position - left;
  return mix(hash4(seed, left, channel), hash4(seed, left + 1, channel), t * t * (3 - 2 * t));
}
function finite5(value, name) {
  if (!Number.isFinite(value)) throw new RangeError(`${name} must be finite`);
}
function range(value, name, low, high) {
  finite5(value, name);
  if (value < low || value > high) throw new RangeError(`${name} must be in ${low}..${high}`);
}
function validatePoints(points, name, minimum) {
  if (!Array.isArray(points) || points.length < minimum) {
    throw new RangeError(`${name} must contain at least ${minimum} points`);
  }
  for (const point2 of points) {
    finite5(point2.x, `${name}.x`);
    finite5(point2.y, `${name}.y`);
    if (point2.pressure !== void 0) range(point2.pressure, `${name}.pressure`, 0, 1);
  }
}
function validate(scene, options) {
  if (scene.version !== 1) throw new RangeError("Unsupported ink scene version");
  finite5(scene.width, "scene.width");
  finite5(scene.height, "scene.height");
  if (scene.width <= 0 || scene.height <= 0) throw new RangeError("Scene dimensions must be positive");
  if (options.time !== void 0) finite5(options.time, "time");
  if (options.paperTexture !== void 0) range(options.paperTexture, "paperTexture", 0, 1);
  if (!Array.isArray(scene.marks)) throw new TypeError("scene.marks must be an array");
  for (const mark2 of scene.marks) {
    range(mark2.opacity, "mark.opacity", 0, 1);
    finite5(mark2.start, "mark.start");
    finite5(mark2.duration, "mark.duration");
    if (mark2.start < 0 || mark2.duration < 0) throw new RangeError("Mark timing must be nonnegative");
    if (mark2.kind === "stroke") {
      validatePoints(mark2.points, "stroke.points", 2);
      finite5(mark2.width, "stroke.width");
      if (mark2.width <= 0) throw new RangeError("stroke.width must be positive");
      range(mark2.dryness, "stroke.dryness", 0, 1);
      finite5(mark2.tipAngle, "stroke.tipAngle");
      if (mark2.nib !== "round" && mark2.nib !== "flat") throw new RangeError("Unknown stroke nib");
      if (mark2.smoothing !== void 0 && mark2.smoothing !== "linear" && mark2.smoothing !== "catmull-rom") {
        throw new RangeError("Unknown stroke smoothing");
      }
      if (mark2.clip !== void 0) validatePoints(mark2.clip, "stroke.clip", 3);
      if (mark2.brush !== void 0) copyStrokeBrush(mark2.brush);
    } else if (mark2.kind === "wash") {
      validatePoints(mark2.outline, "wash.outline", 3);
      range(mark2.roughness, "wash.roughness", 0, 1);
      if (!Number.isInteger(mark2.passes) || mark2.passes < 1 || mark2.passes > 32) {
        throw new RangeError("wash.passes must be an integer in 1..32");
      }
    } else {
      throw new RangeError("Unknown ink mark kind");
    }
  }
}
function polygon(ctx, points) {
  const first = points[0];
  if (!first) return;
  ctx.beginPath();
  ctx.moveTo(first.x, first.y);
  for (let i = 1; i < points.length; i++) {
    const point2 = points[i];
    ctx.lineTo(point2.x, point2.y);
  }
  ctx.closePath();
}
function progressAt(start, duration, time) {
  if (time < start) return 0;
  return duration === 0 ? 1 : clamp3((time - start) / duration);
}
function sampleStroke(mark2) {
  return sampleStrokePath(mark2.points, {
    spacing: clamp3(mark2.width * 0.12, 0.65, 4),
    smoothing: mark2.smoothing ?? "linear"
  });
}
function partialSamples(samples, progress) {
  if (progress >= 1 || samples.length < 2) return samples;
  const distance = samples[samples.length - 1].distance * progress;
  const end = samples.findIndex((sample) => sample.distance >= distance);
  if (end <= 0) return [samples[0]];
  const active = samples.slice(0, end);
  const a = samples[end - 1];
  const b = samples[end];
  const t = (distance - a.distance) / (b.distance - a.distance);
  const nx = mix(a.nx, b.nx, t);
  const ny = mix(a.ny, b.ny, t);
  const normalLength = Math.hypot(nx, ny);
  active.push({
    x: mix(a.x, b.x, t),
    y: mix(a.y, b.y, t),
    pressure: mix(a.pressure, b.pressure, t),
    distance,
    nx: normalLength > EPSILON3 ? nx / normalLength : b.nx,
    ny: normalLength > EPSILON3 ? ny / normalLength : b.ny
  });
  return active;
}
function halfWidth(mark2, sample) {
  let support = 1;
  if (mark2.nib === "flat") {
    const across = sample.nx * Math.cos(mark2.tipAngle) + sample.ny * Math.sin(mark2.tipAngle);
    const along = -sample.nx * Math.sin(mark2.tipAngle) + sample.ny * Math.cos(mark2.tipAngle);
    support = Math.hypot(across, along * 0.16);
  }
  const variation = 1 + (noise2(mark2.seed, sample.distance / Math.max(5, mark2.width), 1) - 0.5) * (0.1 + mark2.dryness * 0.32);
  return mark2.width * sample.pressure * support * variation * 0.5;
}
function ribbon2(ctx, samples, scale = 1) {
  ctx.beginPath();
  for (let i = 0; i < samples.length; i++) {
    const sample = samples[i];
    const width = sample.halfWidth * scale;
    const x = sample.x + sample.nx * width;
    const y = sample.y + sample.ny * width;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  for (let i = samples.length - 1; i >= 0; i--) {
    const sample = samples[i];
    const width = sample.halfWidth * scale;
    ctx.lineTo(sample.x - sample.nx * width, sample.y - sample.ny * width);
  }
  ctx.closePath();
}
function fiberRun(ctx, samples) {
  if (samples.length < 2) return;
  ctx.beginPath();
  for (let i = 0; i < samples.length; i++) {
    const sample = samples[i];
    const x = sample.x + sample.nx * sample.radius;
    const y = sample.y + sample.ny * sample.radius;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  for (let i = samples.length - 1; i >= 0; i--) {
    const sample = samples[i];
    ctx.lineTo(sample.x - sample.nx * sample.radius, sample.y - sample.ny * sample.radius);
  }
  ctx.closePath();
  ctx.fill();
}
function drawDot(ctx, mark2, sample, progress) {
  const radius = mark2.width * sample.pressure * 0.5;
  if (radius <= 0) return;
  ctx.save();
  try {
    ctx.beginPath();
    ctx.ellipse(sample.x, sample.y, radius, radius * (mark2.nib === "flat" ? 0.16 : 1), mark2.tipAngle, 0, TAU2);
    ctx.clip();
    ctx.globalAlpha = mark2.opacity * (1 - mark2.dryness) * progress;
    ctx.fill();
    const flecks = Math.min(180, Math.max(8, Math.ceil(radius * radius * 0.5)));
    ctx.globalAlpha = mark2.opacity * (0.25 + mark2.dryness * 0.5) * progress;
    for (let i = 0; i < flecks; i++) {
      const x = sample.x + (hash4(mark2.seed, i, 71) * 2 - 1) * radius;
      const y = sample.y + (hash4(mark2.seed, i, 72) * 2 - 1) * radius;
      const size = Math.max(0.3, radius * 0.12);
      ctx.fillRect(x, y, size, size * 0.45);
    }
  } finally {
    ctx.restore();
  }
}
function drawStroke(ctx, mark2, progress) {
  const complete = sampleStroke(mark2);
  const samples = partialSamples(complete, progress).map((sample) => ({
    ...sample,
    halfWidth: halfWidth(mark2, sample),
    // Neighboring tufts reuse four broad contact fields rather than recomputing each one.
    contact: Array.from({ length: 4 }, (_, group) => noise2(mark2.seed, sample.distance / Math.max(8, mark2.width * 1.3), group + 231))
  }));
  ctx.save();
  try {
    if (mark2.clip) {
      polygon(ctx, mark2.clip);
      ctx.clip();
    }
    ctx.fillStyle = "#171b18";
    ctx.fillStyle = mark2.ink;
    if (samples.length === 1) {
      drawDot(ctx, mark2, samples[0], progress);
      return;
    }
    const base = mark2.opacity * Math.pow(Math.max(0, 1 - mark2.dryness / 0.78), 1.7);
    if (base > 0) {
      if (mark2.dryness < 0.3 && mark2.width > 3) {
        ctx.globalAlpha = base * 0.09;
        ctx.shadowColor = mark2.ink;
        ctx.shadowBlur = Math.min(3, mark2.width * 0.12) * (1 - mark2.dryness);
        ribbon2(ctx, samples, 1.035);
        ctx.fill();
        ctx.shadowBlur = 0;
      }
      ctx.globalAlpha = base * 0.9;
      ribbon2(ctx, samples);
      ctx.fill();
    }
    const count = Math.round(clamp3(mark2.width * 0.45 + 6, 8, 24));
    const length = complete[complete.length - 1].distance || 1;
    for (let fiber = 0; fiber < count; fiber++) {
      const broad = fiber % 6 === 0;
      const offset = (hash4(mark2.seed, fiber, 81) * 2 - 1) * (broad ? 0.68 : 0.92);
      const breadth = (broad ? 2.4 + hash4(mark2.seed, fiber, 51) * 1.4 : 0.18 + Math.pow(hash4(mark2.seed, fiber, 51), 2) * 1.3) / count;
      const contactLength = Math.max(3, mark2.width * (0.2 + hash4(mark2.seed, fiber, 82) * 0.55));
      const phase = hash4(mark2.seed, fiber, 83) * 19;
      const group = Math.floor((offset + 1) * 2);
      const run = [];
      let previous;
      ctx.globalAlpha = mark2.opacity * (0.22 + hash4(mark2.seed, fiber, 14) * 0.58) * (0.5 + mark2.dryness * 0.5);
      for (const sample of samples) {
        const along = sample.distance / contactLength + phase;
        const shared = sample.contact[group];
        const contact = noise2(mark2.seed, along, fiber + 43) * 0.74 + shared * 0.26;
        const threshold = 0.04 + mark2.dryness * 0.48 + Math.pow(mark2.dryness, 3) * 0.17 - (broad ? 0.06 * (1 - mark2.dryness) : 0) + (1 - sample.pressure) * 0.09 + mark2.dryness * sample.distance / length * 0.06;
        const amount = clamp3((contact - threshold) / 0.22);
        const taper = amount * amount * (3 - 2 * amount);
        const drift = noise2(mark2.seed, along * 0.47, fiber + 17);
        const wander = (drift - 0.5) * 0.19 + (shared - 0.5) * 0.12;
        const band = sample.halfWidth * breadth * (0.35 + drift * 1.2) * taper;
        const center = clamp3(offset + wander, -0.98, 0.98) * sample.halfWidth;
        const radius = Math.max(0, Math.min(band, sample.halfWidth - Math.abs(center)));
        const point2 = {
          x: sample.x + sample.nx * center,
          y: sample.y + sample.ny * center,
          nx: sample.nx,
          ny: sample.ny,
          radius
        };
        if (radius > EPSILON3) {
          if (run.length === 0 && previous) run.push(previous);
          run.push(point2);
        } else if (run.length > 0) {
          run.push(point2);
          fiberRun(ctx, run);
          run.length = 0;
        }
        previous = point2;
      }
      fiberRun(ctx, run);
    }
  } finally {
    ctx.restore();
  }
}
function roughOutline(mark2, pass) {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const point2 of mark2.outline) {
    minX = Math.min(minX, point2.x);
    minY = Math.min(minY, point2.y);
    maxX = Math.max(maxX, point2.x);
    maxY = Math.max(maxY, point2.y);
  }
  const diagonal = Math.hypot(maxX - minX, maxY - minY);
  const amplitude = Math.min(16, diagonal * 0.035) * mark2.roughness;
  const result = [];
  let distance = 0;
  const perimeter = mark2.outline.reduce((sum, point2, index) => {
    const next = mark2.outline[(index + 1) % mark2.outline.length];
    return sum + Math.hypot(next.x - point2.x, next.y - point2.y);
  }, 0);
  const spacing = Math.max(3, perimeter / 4096);
  for (let i = 0; i < mark2.outline.length; i++) {
    const a = mark2.outline[i];
    const b = mark2.outline[(i + 1) % mark2.outline.length];
    const length = Math.hypot(b.x - a.x, b.y - a.y);
    const steps = Math.max(1, Math.ceil(length / spacing));
    const nx = length < EPSILON3 ? 0 : -(b.y - a.y) / length;
    const ny = length < EPSILON3 ? 0 : (b.x - a.x) / length;
    for (let j = 0; j < steps; j++) {
      const t = j / steps;
      const variation = (noise2(mark2.seed, (distance + length * t) / 12, pass + 101) * 2 - 1) * amplitude;
      const displacement = variation * Math.sin(Math.PI * t);
      result.push({ x: mix(a.x, b.x, t) + nx * displacement, y: mix(a.y, b.y, t) + ny * displacement });
    }
    distance += length;
  }
  return result;
}
function drawWash(ctx, mark2, progress) {
  ctx.save();
  try {
    ctx.fillStyle = "#171b18";
    ctx.fillStyle = mark2.ink;
    const passOpacity = 1 - Math.pow(1 - mark2.opacity * progress, 1 / mark2.passes);
    for (let pass = 0; pass < mark2.passes; pass++) {
      ctx.globalAlpha = passOpacity;
      ctx.shadowColor = mark2.ink;
      ctx.shadowBlur = mark2.roughness * 2;
      polygon(ctx, roughOutline(mark2, pass));
      ctx.fill();
    }
  } finally {
    ctx.restore();
  }
}
function drawPaper(ctx, scene, strength) {
  if (strength === 0) return;
  const count = Math.min(12e3, Math.ceil(scene.width * scene.height / 180));
  ctx.fillStyle = "#504638";
  ctx.globalAlpha = strength * 0.17;
  for (let i = 0; i < count; i++) {
    const x = hash4(scene.seed, i, 201) * scene.width;
    const y = hash4(scene.seed, i, 202) * scene.height;
    ctx.fillRect(x, y, 0.35 + hash4(scene.seed, i, 203) * 1.1, 0.2 + hash4(scene.seed, i, 204) * 0.45);
  }
}
function renderCanvas(ctx, scene, options = {}) {
  validate(scene, options);
  const time = options.time ?? Infinity;
  let strokeCount = 0;
  let washCount = 0;
  ctx.save();
  try {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
    ctx.filter = "none";
    if (options.clear !== false) ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    ctx.setTransform(ctx.canvas.width / scene.width, 0, 0, ctx.canvas.height / scene.height, 0, 0);
    if (options.clear !== false) {
      ctx.fillStyle = "#f2efe6";
      ctx.fillStyle = scene.background;
      ctx.fillRect(0, 0, scene.width, scene.height);
      drawPaper(ctx, scene, options.paperTexture ?? 0.06);
    }
    for (const mark2 of scene.marks) {
      const progress = progressAt(mark2.start, mark2.duration, time);
      if (progress <= 0 || mark2.opacity <= 0) continue;
      ctx.globalAlpha = 1;
      if (mark2.kind === "stroke") {
        if (mark2.brush) drawContactStroke(ctx, mark2, progress);
        else drawStroke(ctx, mark2, progress);
        strokeCount++;
      } else {
        drawWash(ctx, mark2, progress);
        washCount++;
      }
    }
  } finally {
    ctx.restore();
  }
  return { visibleMarks: strokeCount + washCount, strokeCount, washCount };
}

// src/brush-gestures.ts
var MAX_CURVE_POINTS = 16384;
var MAX_STATIONS = 2048;
var mix2 = (a, b, t) => a + (b - a) * t;
var clamp4 = (value) => Math.max(0, Math.min(1, value));
var ease3 = (t) => t * t * (3 - 2 * t);
var lerpPoint = (a, b, t) => ({ x: mix2(a.x, b.x, t), y: mix2(a.y, b.y, t), pressure: mix2(a.pressure, b.pressure, t) });
function measure(points) {
  const distances = [0];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    const distance = distances[i - 1] + Math.hypot(b.x - a.x, b.y - a.y);
    if (!Number.isFinite(distance) || distance <= distances[i - 1]) throw new RangeError("Local brush distances exceed finite coordinate precision");
    distances.push(distance);
  }
  return distances;
}
function compact(points) {
  const result = [];
  for (const point2 of points) {
    if (![point2.x, point2.y, point2.pressure].every(Number.isFinite)) throw new RangeError("Local brush coordinates must be finite");
    const previous = result.at(-1);
    if (previous && previous.x === point2.x && previous.y === point2.y) result[result.length - 1] = point2;
    else result.push(point2);
  }
  return result;
}
function at(points, distances, distance) {
  let low = 1, high = points.length - 1;
  while (low < high) {
    const middle = low + high >>> 1;
    if (distances[middle] < distance) low = middle + 1;
    else high = middle;
  }
  const a = points[low - 1], b = points[low];
  const length = distances[low] - distances[low - 1];
  const t = clamp4((distance - distances[low - 1]) / length);
  return {
    ...t === 0 ? a : t === 1 ? b : lerpPoint(a, b, t),
    distance,
    dx: (b.x - a.x) / length,
    dy: (b.y - a.y) / length
  };
}
function tangent2(previous, point2, next) {
  const ax = point2.x - previous.x, ay = point2.y - previous.y, bx = next.x - point2.x, by = next.y - point2.y;
  const before = Math.sqrt(Math.hypot(ax, ay)), after = Math.sqrt(Math.hypot(bx, by));
  return {
    x: ax / before - (ax + bx) / (before + after) + bx / after,
    y: ay / before - (ay + by) / (before + after) + by / after
  };
}
function flattenCurve(source) {
  if (source.length < 3) return [...source];
  const divisions = Math.max(1, Math.min(24, Math.floor((MAX_CURVE_POINTS - 1) / (source.length - 1))));
  const result = [source[0]];
  for (let i = 0; i < source.length - 1; i++) {
    const a = source[i], b = source[i + 1], dx = b.x - a.x, dy = b.y - a.y;
    const knot = Math.sqrt(Math.hypot(dx, dy));
    const left = i > 0 ? tangent2(source[i - 1], a, b) : void 0;
    const right = i + 2 < source.length ? tangent2(a, b, source[i + 2]) : void 0;
    const m0 = left ? { x: left.x * knot, y: left.y * knot } : { x: dx, y: dy };
    const m1 = right ? { x: right.x * knot, y: right.y * knot } : { x: dx, y: dy };
    for (let step = 1; step <= divisions; step++) {
      if (step === divisions) {
        result.push(b);
        continue;
      }
      const t = step / divisions, position = ease3(t), l = t * t * t - 2 * t * t + t, r = t * t * t - t * t;
      result.push({
        x: a.x + dx * position + m0.x * l + m1.x * r,
        y: a.y + dy * position + m0.y * l + m1.y * r,
        pressure: mix2(a.pressure, b.pressure, position)
      });
    }
  }
  return compact(result);
}
function corners(source, options) {
  if (options.turn === "preserve") return options.smoothing === "catmull-rom" ? flattenCurve(source) : [...source];
  const result = [source[0]];
  const divisionsLimit = Math.max(2, Math.min(16, Math.floor((MAX_CURVE_POINTS - 2) / Math.max(1, source.length - 2)) - 1));
  for (let i = 1; i < source.length - 1; i++) {
    const a = source[i - 1], b = source[i], c = source[i + 1];
    const before = Math.hypot(b.x - a.x, b.y - a.y), after = Math.hypot(c.x - b.x, c.y - b.y);
    const dot = (b.x - a.x) / before * ((c.x - b.x) / after) + (b.y - a.y) / before * ((c.y - b.y) / after);
    const reach = Math.min(before * 0.28, after * 0.28, options.width * 1.1) * options.strength;
    if (dot > 0.995 || dot < -0.96 || reach === 0) {
      result.push(b);
      continue;
    }
    const entry2 = lerpPoint(b, a, reach / before), exit = lerpPoint(b, c, reach / after);
    result.push(entry2);
    if (options.turn === "angular") {
      const shoulder = 1 - 0.22 * options.strength;
      result[result.length - 1] = { ...entry2, pressure: entry2.pressure * shoulder };
      result.push(
        { ...b, pressure: b.pressure + b.pressure * (1 - b.pressure) * 0.22 * options.strength },
        { ...exit, pressure: exit.pressure * shoulder }
      );
    } else {
      const requested = Math.max(2, Math.min(divisionsLimit, Math.ceil(reach / Math.max(0.5, options.width * 0.05))));
      const divisions = requested - requested % 2;
      const locations = /* @__PURE__ */ new Set([0.5, ...Array.from({ length: divisions }, (_, step) => (step + 1) / divisions)]);
      for (const t of [...locations].sort((a2, b2) => a2 - b2)) {
        const u = 1 - t;
        result.push({
          x: entry2.x * u * u + 2 * b.x * u * t + exit.x * t * t,
          y: entry2.y * u * u + 2 * b.y * u * t + exit.y * t * t,
          pressure: t <= 0.5 ? mix2(entry2.pressure, b.pressure, ease3(t * 2)) : mix2(b.pressure, exit.pressure, ease3(t * 2 - 1))
        });
      }
    }
  }
  result.push(source.at(-1));
  return compact(result);
}
function noise3(seed, position) {
  const hash8 = (index2) => {
    let value = (seed ^ Math.imul(index2, 2654435761)) >>> 0;
    value = Math.imul(value ^ value >>> 16, 2146121005);
    value = Math.imul(value ^ value >>> 15, 2221713035);
    return ((value ^ value >>> 16) >>> 0) / 4294967296;
  };
  const index = Math.floor(position);
  return mix2(hash8(index), hash8(index + 1), ease3(position - index));
}
function modulate(points, options) {
  if (options.strength === 0 || options.pressureProfile === "preserve" && options.rhythm === "continuous" && options.drag === "forward") return [...points];
  const distances = measure(points), total = distances.at(-1);
  const steps = Math.max(2, Math.min(MAX_STATIONS, Math.ceil(total / Math.max(1, options.width * 0.34))));
  const locations = new Set(distances);
  for (let i = 0; i <= steps; i++) locations.add(total * (i / steps));
  const cycleLength = Math.max(options.width * 1.8, total / 128);
  return [...locations].sort((a, b) => a - b).map((distance) => {
    const station2 = at(points, distances, distance), t = distance / total, edge = t === 0 || t === 1 ? 0 : Math.sin(Math.PI * t);
    const cycle = distance / cycleLength;
    let pressure = station2.pressure, tangentOffset = 0, normalOffset = 0;
    if (options.pressureProfile === "lift-press") pressure *= 1 - options.strength * (1 - (0.2 + 0.8 * Math.pow(Math.sin(Math.PI * t), 0.72)));
    if (options.rhythm === "hesitant") {
      pressure *= 1 - options.strength * 0.46 * noise3(options.seed ^ 1298231915, cycle * 2.4);
      normalOffset += (noise3(options.seed, cycle * 1.7) - 0.5) * options.width * 0.12 * options.strength * edge;
    }
    if (options.drag === "reverse") {
      tangentOffset -= Math.pow(Math.sin(cycle * Math.PI), 2) * options.width * 0.2 * options.strength * edge;
      normalOffset += (noise3(options.seed ^ 5399926, cycle * 3.1) - 0.5) * options.width * 0.2 * options.strength * edge;
      pressure *= 1 - options.strength * (0.1 + 0.25 * noise3(options.seed ^ 11, cycle * 4));
    }
    return {
      x: station2.x + station2.dx * tangentOffset - station2.dy * normalOffset,
      y: station2.y + station2.dy * tangentOffset + station2.dx * normalOffset,
      pressure: clamp4(pressure)
    };
  });
}
function endpointGeometry(points, options) {
  let path2 = [...points];
  if (options.strength === 0) return path2;
  if (options.entry === "exposed" || options.finish === "open" || options.finish === "return") {
    const distances = measure(path2), total = distances.at(-1);
    const entryReach = Math.min(options.width * 1.55, total * 0.24) * options.strength;
    const finishReach = Math.min(options.width * (options.finish === "return" ? 0.82 : 1.7), total * 0.24) * options.strength;
    const locations = new Set(distances);
    for (let i = 0; i <= 16; i++) {
      if (options.entry === "exposed") locations.add(entryReach * i / 16);
      if (options.finish === "open" || options.finish === "return") locations.add(total - finishReach * i / 16);
    }
    path2 = [...locations].sort((a, b) => a - b).map((distance) => {
      const station2 = at(path2, distances, distance);
      const inFactor = options.entry === "exposed" ? mix2(1, ease3(clamp4(distance / entryReach)), options.strength) : 1;
      const remaining = ease3(clamp4((total - distance) / finishReach));
      const outFactor = options.finish === "open" ? mix2(1, remaining, options.strength) : options.finish === "return" ? mix2(1, 0.035 + 0.965 * remaining, options.strength) : 1;
      return { x: station2.x, y: station2.y, pressure: station2.pressure * inFactor * outFactor };
    });
  }
  if (options.entry === "concealed") {
    const a = path2[0], b = path2[1], length = Math.hypot(b.x - a.x, b.y - a.y), total = measure(path2).at(-1);
    const dx = (b.x - a.x) / length, dy = (b.y - a.y) / length;
    const reach = Math.min(options.width * 0.8, total * 0.12) * options.strength;
    const p1 = { x: a.x - dx * reach + -dy * reach * 0.22, y: a.y - dy * reach + dx * reach * 0.22 };
    const p2 = { x: a.x - dx * reach * 0.7, y: a.y - dy * reach * 0.7 };
    const prefix2 = [];
    for (let i = 0; i < 17; i++) {
      const t = i / 16, u = 1 - t;
      prefix2.push({
        x: a.x * (u * u * u + t * t * t) + 3 * p1.x * u * u * t + 3 * p2.x * u * t * t,
        y: a.y * (u * u * u + t * t * t) + 3 * p1.y * u * u * t + 3 * p2.y * u * t * t,
        pressure: a.pressure * mix2(1 - 0.96 * options.strength, 1, ease3(t))
      });
    }
    path2 = [...prefix2, ...path2.slice(1)];
  }
  if (options.finish === "return") {
    const b = path2.at(-1), a = path2.at(-2), length = Math.hypot(b.x - a.x, b.y - a.y), total = measure(path2).at(-1);
    const dx = (b.x - a.x) / length, dy = (b.y - a.y) / length;
    const reach = Math.min(options.width * 0.45, total * 0.08) * options.strength;
    const control = { x: b.x - dx * reach * 0.32 - dy * reach * 0.15, y: b.y - dy * reach * 0.32 + dx * reach * 0.15 };
    const end = { x: b.x - dx * reach, y: b.y - dy * reach };
    for (let i = 1; i <= 16; i++) {
      const t = i / 16, u = 1 - t;
      path2.push({
        x: b.x * u * u + 2 * control.x * u * t + end.x * t * t,
        y: b.y * u * u + 2 * control.y * u * t + end.y * t * t,
        pressure: b.pressure * mix2(1, 1 - ease3(t), options.strength)
      });
    }
  }
  return compact(path2);
}
function compileLocalBrushGeometry(source, options) {
  return endpointGeometry(compact(modulate(corners(source, options), options)), options);
}

// src/brushwork.ts
var MAX_SOURCE_POINTS = 4096;
var MAX_STATIONS2 = 2048;
var mix3 = (a, b, t) => a + (b - a) * t;
function choice(value, name, choices) {
  if (!choices.includes(value)) throw new RangeError(`${name} must be ${choices.join(", ")}`);
  return value;
}
function noise4(seed, position) {
  const hash8 = (index2) => {
    let value = (seed ^ Math.imul(index2, 2654435761)) >>> 0;
    value = Math.imul(value ^ value >>> 16, 2146121005);
    value = Math.imul(value ^ value >>> 15, 2221713035);
    return ((value ^ value >>> 16) >>> 0) / 4294967296;
  };
  const index = Math.floor(position), t = position - index;
  return mix3(hash8(index), hash8(index + 1), t * t * (3 - 2 * t));
}
function unique(points) {
  const result = [];
  for (const point2 of points) {
    const previous = result.at(-1), copy = { x: point2.x, y: point2.y, pressure: point2.pressure ?? 1 };
    if (previous && point2.x === previous.x && point2.y === previous.y) result[result.length - 1] = copy;
    else result.push(copy);
  }
  return result;
}
function measure2(points) {
  const distances = [0];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    const distance = distances[i - 1] + Math.hypot(b.x - a.x, b.y - a.y);
    if (distance <= distances[i - 1]) throw new RangeError("Brushwork segment distances exceed coordinate precision");
    distances.push(distance);
  }
  return distances;
}
function stationAt(points, distances, distance) {
  let low = 1, high = distances.length - 1;
  while (low < high) {
    const middle = low + high >>> 1;
    if (distances[middle] < distance) low = middle + 1;
    else high = middle;
  }
  const segment = low;
  const a = points[segment - 1], b = points[segment];
  const length = distances[segment] - distances[segment - 1];
  const t = Math.max(0, Math.min(1, (distance - distances[segment - 1]) / length));
  return {
    x: t === 0 ? a.x : t === 1 ? b.x : mix3(a.x, b.x, t),
    y: t === 0 ? a.y : t === 1 ? b.y : mix3(a.y, b.y, t),
    pressure: t === 0 ? a.pressure : t === 1 ? b.pressure : mix3(a.pressure, b.pressure, t),
    distance,
    dx: (b.x - a.x) / length,
    dy: (b.y - a.y) / length
  };
}
function createBrushStroke(points, options = {}) {
  const {
    axis = "center",
    entry: entry2 = "preserve",
    finish: finish2 = "preserve",
    pressureProfile = "preserve",
    turn = "preserve",
    rhythm = "continuous",
    drag = "forward",
    strength = 1,
    gestureModel = "legacy",
    ...strokeOptions
  } = options;
  choice(gestureModel, "gestureModel", ["legacy", "local-v1"]);
  choice(axis, "axis", ["center", "side"]);
  choice(entry2, "entry", ["preserve", "exposed", "concealed"]);
  choice(finish2, "finish", ["preserve", "open", "return"]);
  choice(pressureProfile, "pressureProfile", ["preserve", "lift-press"]);
  choice(turn, "turn", ["preserve", "round", "angular"]);
  choice(rhythm, "rhythm", ["continuous", "hesitant"]);
  choice(drag, "drag", ["forward", "reverse"]);
  if (typeof strength !== "number" || !Number.isFinite(strength) || strength < 0 || strength > 1) {
    throw new RangeError("strength must be finite and in 0..1");
  }
  if (!Array.isArray(points) || points.length > MAX_SOURCE_POINTS) throw new RangeError("A brushwork path supports at most 4096 source points");
  const validated2 = createInkScene({ width: 1, height: 1 }).stroke(points, strokeOptions);
  const source = unique(validated2.points), distances = measure2(source), total = distances.at(-1);
  const first = source[0], second = source[1];
  const firstAngle = Math.atan2(second.y - first.y, second.x - first.x);
  const style = {
    ...validated2,
    width: strokeOptions.width ?? (axis === "side" ? 14 : 8),
    nib: strokeOptions.nib ?? (axis === "side" ? "flat" : "round"),
    tipAngle: strokeOptions.tipAngle ?? (axis === "side" ? firstAngle + Math.PI * 0.2 : 0),
    dryness: strokeOptions.dryness ?? (drag === "reverse" ? 0.64 : 0.25),
    ...turn === "round" ? { smoothing: "catmull-rom" } : turn === "angular" ? { smoothing: "linear" } : {}
  };
  const width = style.width;
  if (gestureModel === "local-v1") {
    const path3 = compileLocalBrushGeometry(source, {
      entry: entry2,
      finish: finish2,
      turn,
      pressureProfile,
      rhythm,
      drag,
      strength,
      width,
      seed: validated2.seed,
      smoothing: strokeOptions.smoothing ?? "linear"
    });
    return createInkScene({ width: 1, height: 1 }).stroke(path3, { ...style, smoothing: "linear" });
  }
  const requiresStations = pressureProfile !== "preserve" || rhythm === "hesitant" || drag === "reverse" || turn === "angular";
  let path2;
  if (requiresStations && strength > 0) {
    const steps = Math.max(2, Math.min(MAX_STATIONS2, Math.ceil(total / Math.max(1, width * 0.34))));
    const locations = new Set(distances);
    for (let i = 0; i <= steps; i++) locations.add(total * (i / steps));
    const corners2 = [];
    if (turn === "angular") for (let i = 1; i < source.length - 1; i++) {
      const a = source[i - 1], b = source[i], c = source[i + 1];
      const before = distances[i] - distances[i - 1], after = distances[i + 1] - distances[i];
      const dot = (b.x - a.x) / before * ((c.x - b.x) / after) + (b.y - a.y) / before * ((c.y - b.y) / after);
      if (dot > 0.985) continue;
      const reach = Math.min(before, after, width) * 0.35;
      corners2.push({ distance: distances[i], reach });
      locations.add(distances[i] - reach);
      locations.add(distances[i] + reach);
    }
    const cycleLength = Math.max(width * 1.8, total / 128);
    path2 = [...locations].sort((a, b) => a - b).map((distance) => {
      const sample = stationAt(source, distances, distance), t = distance / total;
      let pressure = sample.pressure;
      if (pressureProfile === "lift-press") pressure *= 1 - strength * (1 - (0.2 + 0.8 * Math.pow(Math.sin(Math.PI * t), 0.72)));
      const edge = t === 0 || t === 1 ? 0 : Math.sin(Math.PI * t);
      const cycle = distance / cycleLength;
      let tangentOffset = 0, normalOffset = 0;
      if (rhythm === "hesitant") {
        const contact = noise4(validated2.seed ^ 1298231915, cycle * 2.4);
        pressure *= 1 - strength * 0.46 * contact;
        normalOffset += (noise4(validated2.seed, cycle * 1.7) - 0.5) * width * 0.12 * strength * edge;
      }
      if (drag === "reverse") {
        tangentOffset -= Math.pow(Math.sin(cycle * Math.PI), 2) * width * 0.2 * strength * edge;
        normalOffset += (noise4(validated2.seed ^ 5399926, cycle * 3.1) - 0.5) * width * 0.2 * strength * edge;
        pressure *= 1 - strength * (0.1 + 0.25 * noise4(validated2.seed ^ 11, cycle * 4));
      }
      for (const corner of corners2) {
        const relative = Math.abs(distance - corner.distance) / corner.reach;
        if (relative <= 1) {
          pressure *= 1 - 0.28 * strength * Math.pow(Math.sin(relative * Math.PI / 2), 2);
          pressure += pressure * (1 - pressure) * 0.24 * strength * (1 - relative);
        }
      }
      return {
        x: sample.x + sample.dx * tangentOffset - sample.dy * normalOffset,
        y: sample.y + sample.dy * tangentOffset + sample.dx * normalOffset,
        pressure: Math.max(0, Math.min(1, pressure))
      };
    });
  } else path2 = source.map((point2) => ({ ...point2 }));
  if (entry2 !== "preserve" && strength > 0) {
    const a = path2[0], sourceLength = distances[1];
    const dx = (second.x - first.x) / sourceLength, dy = (second.y - first.y) / sourceLength;
    const reach = Math.min(width * 0.55, sourceLength * 0.28, total * 0.06) * strength;
    let skip = 1;
    while (skip < path2.length - 1 && Math.hypot(path2[skip].x - a.x, path2[skip].y - a.y) < reach) skip++;
    const b = path2[skip], length = Math.hypot(b.x - a.x, b.y - a.y);
    const lead = { x: a.x + dx * reach, y: a.y + dy * reach, pressure: mix3(a.pressure, b.pressure, Math.min(1, reach / length)) };
    if (entry2 === "exposed") path2 = [{ ...a, pressure: a.pressure * (1 - 0.98 * strength) }, lead, ...path2.slice(skip)];
    else path2 = [
      { ...a, pressure: a.pressure * (1 - 0.88 * strength) },
      { x: a.x - dx * reach, y: a.y - dy * reach, pressure: a.pressure * 0.76 },
      { x: a.x - dx * reach * 0.65 - dy * reach * 0.3, y: a.y - dy * reach * 0.65 + dx * reach * 0.3, pressure: a.pressure },
      lead,
      ...path2.slice(skip)
    ];
  }
  if (finish2 !== "preserve" && strength > 0) {
    const b = path2.at(-1), sourceEnd = source.at(-1), sourceBefore = source.at(-2);
    const sourceLength = Math.hypot(sourceEnd.x - sourceBefore.x, sourceEnd.y - sourceBefore.y);
    const dx = (sourceEnd.x - sourceBefore.x) / sourceLength, dy = (sourceEnd.y - sourceBefore.y) / sourceLength;
    const reach = Math.min(width * 0.62, sourceLength * 0.28, total * 0.06) * strength;
    let keep = path2.length - 2;
    while (keep > 0 && Math.hypot(path2[keep].x - b.x, path2[keep].y - b.y) < reach) keep--;
    const a = path2[keep], length = Math.hypot(b.x - a.x, b.y - a.y);
    if (finish2 === "open") path2 = [
      ...path2.slice(0, keep + 1),
      { x: b.x - dx * reach, y: b.y - dy * reach, pressure: mix3(b.pressure, a.pressure, Math.min(1, reach / length)) },
      { ...b, pressure: b.pressure * (1 - 0.98 * strength) }
    ];
    else path2 = [
      ...path2,
      { x: b.x - dx * reach * 0.3 - dy * reach * 0.18, y: b.y - dy * reach * 0.3 + dx * reach * 0.18, pressure: b.pressure * 0.9 },
      { x: b.x - dx * reach, y: b.y - dy * reach, pressure: b.pressure * (1 - 0.92 * strength) }
    ];
  }
  return createInkScene({ width: 1, height: 1 }).stroke(path2, style);
}

// src/dots.ts
var SHAPES = ["round", "horizontal", "vertical", "oblique", "paired", "triple", "cluster"];
var MAX_MARKS = 1e4;
var GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
function finite6(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function layout(shape2, spread2, count) {
  switch (shape2) {
    case "round":
      return [{ x: 0, y: 0, heading: 0, length: 0.4, width: 1 }];
    case "horizontal":
      return [{ x: 0, y: 0, heading: 0, length: 1.3, width: 0.58 }];
    case "vertical":
      return [{ x: 0, y: 0, heading: Math.PI / 2, length: 1.3, width: 0.58 }];
    case "oblique":
      return [{ x: 0, y: 0, heading: -Math.PI / 3, length: 1.15, width: 0.68 }];
    case "paired":
      return [-1, 1].map((side) => ({
        x: side * spread2 * 0.45,
        y: 0,
        heading: Math.PI / 2 + side * 0.24,
        length: 0.72,
        width: 0.75
      }));
    case "triple":
      return [-1, 0, 1].map((side) => ({
        x: side * spread2 * 0.52,
        y: side === 0 ? -spread2 * 0.3 : 0,
        heading: Math.PI / 2 + side * 0.35,
        length: 0.72,
        width: 0.68
      }));
    case "cluster":
      return Array.from({ length: count }, (_, index) => {
        const radius = index === 0 ? 0 : spread2 * (0.28 + 0.3 * Math.sqrt(index / Math.max(1, count - 1)));
        const theta = (index - 1) * GOLDEN_ANGLE;
        return {
          x: Math.cos(theta) * radius,
          y: Math.sin(theta) * radius,
          heading: Math.PI / 2 + Math.sin(index * 2.4) * 0.5,
          length: 0.46 + Math.cos(index * 1.7) * 0.08,
          width: 0.65 + Math.sin(index * 2.1) * 0.1
        };
      });
  }
}
function createDotMarks(positions, options = {}) {
  const {
    shape: shape2 = "round",
    size = 12,
    angle = 0,
    spread: spread2 = size * 1.3,
    count = 5,
    groupInterval = 0,
    idPrefix = "dot",
    seed = 1,
    ...strokeOptions
  } = options;
  if (!SHAPES.includes(shape2)) throw new RangeError(`shape must be ${SHAPES.join(", ")}`);
  if (finite6(size, "size") <= 0) throw new RangeError("size must be positive");
  finite6(angle, "angle");
  if (finite6(spread2, "spread") < 0) throw new RangeError("spread must be nonnegative");
  if (finite6(groupInterval, "groupInterval") < 0) throw new RangeError("groupInterval must be nonnegative");
  if (!Number.isSafeInteger(count) || count < 1 || count > 32) throw new RangeError("count must be an integer in 1..32");
  if (options.count !== void 0 && shape2 !== "cluster") throw new RangeError("count is only configurable for cluster dots");
  if (typeof idPrefix !== "string" || !idPrefix.trim()) throw new TypeError("idPrefix must be nonempty");
  if (!Array.isArray(positions)) throw new TypeError("positions must be an array");
  const members = layout(shape2, spread2, count);
  if (positions.length * members.length > MAX_MARKS) throw new RangeError("Dot generation is limited to 10000 marks");
  const source = positions.map((point2, index) => {
    if (!point2 || typeof point2 !== "object") throw new TypeError(`positions[${index}] must be a point`);
    const pressure = finite6(point2.pressure ?? 1, "position.pressure");
    if (pressure < 0 || pressure > 1) throw new RangeError("position.pressure must be in 0..1");
    return { x: finite6(point2.x, "position.x"), y: finite6(point2.y, "position.y"), pressure };
  });
  const builder = createInkScene({ width: 1, height: 1, seed });
  const validated2 = createInkScene({ width: 1, height: 1, seed }).stroke([{ x: 0, y: 0 }, { x: 1, y: 0 }], {
    ...strokeOptions,
    seed,
    width: size
  });
  const finalStart = validated2.start + Math.max(0, source.length - 1) * groupInterval;
  if (!Number.isFinite(finalStart + validated2.duration)) throw new RangeError("Dot group timing must remain finite");
  const cos = Math.cos(angle), sin = Math.sin(angle);
  const result = [];
  for (let group = 0; group < source.length; group++) {
    const point2 = source[group];
    for (let index = 0; index < members.length; index++) {
      const member = members[index];
      const markSeed = (validated2.seed ^ Math.imul(group + 1, 2654435761) ^ Math.imul(index + 1, 2246822519)) >>> 0;
      const random = createRandom(markSeed);
      const heading = member.heading + angle + (random() - 0.5) * 0.17;
      const dx = Math.cos(heading), dy = Math.sin(heading);
      const x = point2.x + cos * member.x - sin * member.y, y = point2.y + sin * member.x + cos * member.y;
      const length = size * member.length, bend = (random() - 0.5) * size * 0.045;
      const path2 = [
        { x, y, pressure: point2.pressure * 0.16 },
        { x: x + dx * length * 0.28 - dy * bend, y: y + dy * length * 0.28 + dx * bend, pressure: point2.pressure },
        { x: x + dx * length * 0.7 - dy * bend * 0.35, y: y + dy * length * 0.7 + dx * bend * 0.35, pressure: point2.pressure * 0.78 },
        { x: x + dx * length, y: y + dy * length, pressure: point2.pressure * 0.04 }
      ];
      result.push(builder.stroke(path2, {
        ...validated2,
        id: `${idPrefix}-${group + 1}-${index + 1}`,
        width: size * member.width,
        smoothing: strokeOptions.smoothing ?? "catmull-rom",
        seed: markSeed,
        start: validated2.start + group * groupInterval
      }));
    }
  }
  return Object.freeze(result);
}

// src/technique-learning.ts
function freeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const item of Object.values(value)) freeze(item);
    Object.freeze(value);
  }
  return value;
}
var TECHNIQUE_READING_SOURCES = freeze([
  {
    "id": "shufa-shupu",
    "title": "孙过庭《书谱》公开转录及今译",
    "institution": "书法空间（9610.com）",
    "url": "https://www.9610.com/lilun/sgt.htm",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "实际阅读点画起伏、衄挫、执使转用、淹留劲疾及留遣相参等原文；页面另附今译，本记录不把今译当原文。未做版本校勘。",
    "supports": "一点之内也有运锋变化；转用关系及迟速相济。不能由文字直接复原毛笔物理参数。"
  },
  {
    "id": "shufa-xushupu",
    "title": "姜夔《续书谱》节录，附邓散木图解及译文",
    "institution": "书法空间（9610.com）",
    "url": "https://www.9610.com/lilun/jiangkui.htm",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "实际阅读真书、草书、用笔、用墨、方圆、向背等相关节。原文、后附图解和译文分别辨认；未校勘异本。",
    "supports": "笔锋在画中，藏露相参，转折不同，悬针与垂露，点群的起带应，以及书体与点画位置对动作的限制。"
  },
  {
    "id": "shufa-yiying-study",
    "title": "《怎样临习〈乙瑛碑〉》",
    "institution": "书法空间转载；页末注明摘自《中国书画报》",
    "url": "https://www.9610.com/qinhan/yyb/01.htm",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "实际阅读注意要点、基本点画运笔和点横竖撇的变化。属于现代临习汉碑的教学解释，页面未列作者及刊期，不是汉代作者自述。",
    "supports": "逆入、提按、顿转、折与起收的具体动作次序；同一笔形随结体变化，隶书钩与竖不能套用统一楷书模板。"
  },
  {
    "id": "learn-brush-hanlin",
    "title": "陈绎曾《翰林要诀》公开转录",
    "institution": "书法空间（9610.com）",
    "url": "https://www.9610.com/lilun/chenyiceng.htm",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "实际阅读血法、骨法、肉法、平法、直法、圆法。正文与页末评点区分；页面有缺字与乱码，分数不是实验测量，本轮未校勘。",
    "supports": "提按随疏密、蹲驻与水聚、挫与衄、点画顾盼；策与掠的方向用语和另一八法文献存在差异，保留疑问。"
  },
  {
    "id": "learn-shufa-guxuan-bafa",
    "title": "苏霖编《书法钩玄·永字八法三》",
    "institution": "识典古籍（古籍数字转录平台）",
    "url": "https://www.shidianguji.com/book/NA06439/chapter/1l3x975gcgpbo",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "实际阅读本章八法详说，而非只读搜索摘要。页面标为元苏霖编；少数罕用字与开头句有转录疑点，未核原刻页，故不据疑字推导动作。",
    "supports": "侧之顾右、勒之劲涩、努之微曲、趯之蹲挫借势、策之仰进、掠之左出、啄之短快、磔之右送与驻揭。"
  },
  {
    "id": "learn-shufa-baijiao",
    "title": "白蕉《书法十讲》第五讲：运笔问题",
    "institution": "书法空间转载；页面注明资料来自新浪“纪念白蕉”博客",
    "url": "https://www.9610.com/jindai/baijiao/1005.htm",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "实际阅读运笔、方圆、迟速及永字八法的讨论。属于近现代作者的解释与批评；转录来源有说明，但未核印本。",
    "supports": "八法有教学价值但不能囊括一切点画；其起源传承不能凭传说坐实；方圆和迟速均不能固定成单一公式。"
  },
  {
    "id": "learn-brush-linchi",
    "title": "周星莲《临池管见》公开转录",
    "institution": "书法空间（9610.com）",
    "url": "https://www.9610.com/lilun/zxl.htm",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "实际阅读开篇用锋、中锋、藏露、侧锋画石、团笔扁笔之辨及逆势相关段落。属清代论述，页面有转录瑕疵，未校勘。",
    "supports": "笔管直不保证锋正，藏锋和出锋均可与中锋相容，侧用并非横卧涂抹，偏锋与侧锋不宜仅凭外形武断划界。"
  },
  {
    "id": "learn-brush-yigai",
    "title": "刘熙载《艺概》相关节录",
    "institution": "书法空间（9610.com）",
    "url": "https://www.9610.com/lilun/lxz.htm",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "实际阅读迟速备法、疾涩之辨与如有物拒之的正文；正文、注释和后附评点区分，不将评点里的引语反作此节原文。",
    "supports": "涩不等于迟，疾不等于速；阻力意识与保持控制的关系；强行模拟可见战掣会成弊病。"
  },
  {
    "id": "learn-brush-bimo",
    "title": "书法入门：运笔用墨",
    "institution": "书法空间（9610.com）；页面未署作者及日期",
    "url": "https://www.9610.com/xiezi/bimo.htm",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "实际阅读提按、顺逆、起行收、正侧、藏露、快慢及枯墨段落。作为现代入门教学记录其解释范围，不能当作无争议古代定义。",
    "supports": "拖行与逆势推进、露入与渐提出锋、连续提按，以及干毫摩擦和丝丝露白；枯笔摩擦段仅间接帮助理解擦，不完整定义山水擦法。"
  },
  {
    "id": "dpm-baoshi-cun",
    "title": "抱石皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/239776.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "实际阅读词条正文。条目说明傅抱石综合多种皴法、运用破笔散锋的创作方法；不把列名的其他皴法视为已分别得到定义。；已读正文，融汇多种皴法、破笔散锋、聚散分明、一气呵成。",
    "supports": "散锋在具体山水方法中的聚散组织与整体性；并不支持所有散锋都等于抱石皴。；傅抱石个人综合笔墨语汇，不能压缩成一种通用噪声纹理。"
  },
  {
    "id": "learn-brush-sohu-scrub",
    "title": "中国画笔法（2019-01-31）",
    "institution": "搜狐号“书画收藏鉴赏”（用户发布，不代表搜狐机构意见）",
    "url": "https://www.sohu.com/a/292734430_800368",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "实际阅读全文八项基础笔法，重点为第6项皴擦与第2项侧锋。文中以花鸟画为主要语境，作者身份和教学依据未核，且与古文在偏侧、顺逆、顿挫的说法上不完全一致。",
    "supports": "有限支持干笔侧锋轻擦表现树皮、石块纹理和明暗的现代教学例；该文把侧锋称为偏锋，只证明一种用词习惯，不解决术语边界。"
  },
  {
    "id": "learn-brush-meipian-scrub",
    "title": "中国画三十六技法：第5技中国画皴擦的运用技法",
    "institution": "美篇用户“=w=”（自媒体教学，作者及原始教材未核）",
    "url": "https://www.meipian.cn/3m8fnsin",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "实际阅读技法概况、皴擦关系和假山石三步教学正文。仅作为现代受限示例；文中皴都是短线、擦只能干笔等绝对表述不采为普遍法则，未验证其历史适用范围。",
    "supports": "有限支持在皴迹基础上擦出面感、再轻染的教学顺序，以及皴迹与擦面相融的观察问题；不据此规范所有山水擦法。"
  },
  {
    "id": "learn-brush-jiushi",
    "title": "传蔡邕《九势》公开转录",
    "institution": "书法空间（9610.com）",
    "url": "https://www.9610.com/lilun/caiyong.htm",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "实际阅读九势中的藏头、护尾、藏锋、疾势、涩势。蔡邕署名属于传世归属，本轮未验证成书年代或作者；不将传称当作已确证事实。",
    "supports": "藏头护尾、力收之及相反方向取势等历史表述，可作技法语境对照，不能独自证实唯一动作流程。"
  },
  {
    "id": "learn-ink-five-tones",
    "title": "“墨分五色”",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240484.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "词条全文；浓淡干湿及多种五色／六彩分法。",
    "supports": "浓淡、干湿不是同一维度；五色指墨色丰富变化，并非唯一固定的五级灰度。"
  },
  {
    "id": "learn-ink-dry",
    "title": "渴笔",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240331.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "词条全文；渴笔、干笔、沙笔的称谓及含水量说明。",
    "supports": "含水较少的干笔及山石皴擦用途；不支持把干笔固定解释成浅墨。"
  },
  {
    "id": "learn-ink-wet",
    "title": "湿笔",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240210.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "词条全文。",
    "supports": "湿笔指笔含水较多、滋润，与干笔相对。"
  },
  {
    "id": "learn-ink-flying-white",
    "title": "“飞白”",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240500.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "词条全文，包括丝丝露白、引述书论及今人泛称干枯笔触的区别。",
    "supports": "可见断白的形态及历史书法语境与现代泛用的范围区别；不把词条转述的起源传说另作考证结论。"
  },
  {
    "id": "learn-ink-layered",
    "title": "积墨法",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240580.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "词条全文；由淡而浓逐渐渍染及《林泉高致》引文。",
    "supports": "积墨由层次累积形成浑厚、滋润的目标；引文次数不是通用操作常数。"
  },
  {
    "id": "learn-ink-broken",
    "title": "破墨法",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/242650.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "词条全文；浓破淡、淡破浓与未干时加墨。",
    "supports": "浓淡互破需要先后和湿态条件，兼有分破界限轮廓的作用。"
  },
  {
    "id": "learn-ink-rendering",
    "title": "渲染",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240398.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "词条全文；在本词条中分别解释渲与染。",
    "supports": "皴擦处略敷水墨／色彩和外围大面积湿笔烘托的区分，不将这一词义划分扩大为全部教材的唯一分法。"
  },
  {
    "id": "learn-ink-cheng-sui",
    "title": "程邃山水图轴",
    "institution": "故宫博物院，李湜撰稿",
    "url": "https://www.dpm.org.cn/collection/paint/231388.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "康熙十二年（1673）作品的馆藏说明及用笔用墨分析；本轮读文字，未完成高清局部对照。",
    "supports": "枯笔焦墨、渴笔干墨与刚劲金石意味的具体作品例证，不提供所有焦墨画法的配墨配方。"
  },
  {
    "id": "dpm-shan-ink-survey",
    "title": "单国强《水墨画技法综述》第47页",
    "institution": "《故宫博物院院刊》1998年第4期（总第82期）",
    "url": "https://www.dpm.org.cn/Uploads/pdf/1882/T00047_00.pdf",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "论文第47页总目；本轮另逐页读至第53页，出版信息见第48页页眉。另实际查看第47页扫描，放大水法行并与同页“点、勾、皴、擦”中的“皴”比对，原刊确印“皴水”，不是本轮OCR新增的识别词。",
    "supports": "笔、墨、水的分类及渍水、撞水、皴水、洒水、水破墨、墨破水等名称。首段多为列名，不能由此补造动作定义；扫描核字仍不能排除原刊讹字或作者特殊用语，须继续查同词语境。"
  },
  {
    "id": "learn-ink-survey-48",
    "title": "单国强《水墨画技法综述》第48页",
    "institution": "《故宫博物院院刊》1998年第4期（总第82期）",
    "url": "https://www.dpm.org.cn/Uploads/pdf/1882/T00048_00.pdf",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "全页；早期破墨与水介入墨迹的历史解释。",
    "supports": "新勾墨线／墨点后以水晕开、将干再加浓墨的一种研究性复原；作者引现代研究，不冒称王维自述。"
  },
  {
    "id": "learn-ink-survey-49",
    "title": "单国强《水墨画技法综述》第49页",
    "institution": "《故宫博物院院刊》1998年第4期（总第82期）",
    "url": "https://www.dpm.org.cn/Uploads/pdf/1882/T00049_00.pdf",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "全页；王默泼墨史例及郭熙墨法诸名。",
    "supports": "泼墨后借手、布等晕开再以笔收拾的史例；宿墨在此只是列名，未解释材料或时序。"
  },
  {
    "id": "learn-ink-survey-50",
    "title": "单国强《水墨画技法综述》第50页",
    "institution": "《故宫博物院院刊》1998年第4期（总第82期）",
    "url": "https://www.dpm.org.cn/Uploads/pdf/1882/T00050_00.pdf",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "全页；董源、李成、范宽及米氏云山的浓淡干湿组织。",
    "supports": "淡墨扫染与浓墨点簇、淡墨起后用焦浓墨分远近、湿墨后干焦墨点醒的具体组合。"
  },
  {
    "id": "learn-ink-survey-51",
    "title": "单国强《水墨画技法综述》第51页",
    "institution": "《故宫博物院院刊》1998年第4期（总第82期）",
    "url": "https://www.dpm.org.cn/Uploads/pdf/1882/T00051_00.pdf",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "全页；夏圭浓墨淡墨衔接、郭忠恕用水及陈容泼墨噗水史例。",
    "supports": "水改变墨迹和喷水成雾的史例；不是对所有洒水或墨破水名称的独立完整定义。"
  },
  {
    "id": "learn-ink-survey-52",
    "title": "单国强《水墨画技法综述》第52页",
    "institution": "《故宫博物院院刊》1998年第4期（总第82期）",
    "url": "https://www.dpm.org.cn/Uploads/pdf/1882/T00052_00.pdf",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "全页；陈淳湿笔、徐渭倾墨后勾染及董其昌论墨。",
    "supports": "湿笔渗透与形体组织、泼墨后主动收拾的关系，避免把泼墨等同随机泼洒完成画作。"
  },
  {
    "id": "learn-ink-survey-53",
    "title": "单国强《水墨画技法综述》第53页",
    "institution": "《故宫博物院院刊》1998年第4期（总第82期）",
    "url": "https://www.dpm.org.cn/Uploads/pdf/1882/T00053_00.pdf",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "论文末页正文及注释；龚贤积墨的具体叠加步骤。第54页已是另一篇文章，未混用。",
    "supports": "点、加、皴后歇，待干再加浓淡点，并保留明处的积墨操作；不把层数当成全部方法。"
  },
  {
    "id": "learn-ink-fu-snow",
    "title": "傅抱石雪山行旅图横幅",
    "institution": "故宫博物院，汪亓撰稿",
    "url": "https://www.dpm.org.cn/collection/paint/232782.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "馆藏说明中远方淡墨烘染与雪山留白的分析。",
    "supports": "以远方淡墨烘染阴霾天气，衬出雪山银白的作品例证；文字实读不等于完成图像比较。"
  },
  {
    "id": "learn-ink-zhao-snow",
    "title": "赵左长林积雪图轴",
    "institution": "故宫博物院，杨丹霞撰稿",
    "url": "https://www.dpm.org.cn/collection/paint/231432.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "馆藏说明中淡墨烘染绢地与浓墨山石树木的分析。",
    "supports": "背景烘染与主体着墨分工的作品例证，不用来定义纸背托色。"
  },
  {
    "id": "learn-ink-gongbi-figures",
    "title": "《工笔人物画2》实验教学讲义",
    "institution": "四川师范大学艺术实验教学中心",
    "url": "https://aetc.sicnu.edu.cn/p/0/?StId=st_app_news_i_x493",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2016-01-06教学讲义正文；面部、头发的反复分染与罩染。",
    "supports": "分染塑造局部起伏，罩染统一整体及循序加深；某次练习的遍数不推广成全部技法的规则。"
  },
  {
    "id": "learn-ink-gongbi-birds",
    "title": "《工笔花鸟画1》实验教学讲义",
    "institution": "四川师范大学艺术实验教学中心",
    "url": "https://aetc.sicnu.edu.cn/p/0/?StId=st_app_news_i_x490",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2016-01-06教学讲义正文；实验二临摹的六个步骤，尤其分染、罩色、托色。",
    "supports": "局部分染表现凹凸，着色后大面积罩色协调，纸背托染白粉或石色加强正面厚重；撞水只在前文列名。"
  },
  {
    "id": "learn-ink-caa-figures",
    "title": "美术考级中国画(人物)作品展示",
    "institution": "中国美术学院考级中心",
    "url": "https://mskj.caa.edu.cn/info/1030/22861.htm",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2016-08-23正文的“工笔重彩画法”第（三）、（五）、（六）步；复读本轮已保存的公开页面正文。；2016-08-23教学正文，实读白描、写意、工笔重彩及六步骤；网页有错字，十八描史源与书名采用故宫资料核对。",
    "supports": "颜色笔局部染后清水笔推开的分染、淡色罩色统一，以及纸背托染白粉的现代教学操作。原页有较多错别字，不引其十八描历史书目断言。；白描线的粗细轻重、写意先主线和大墨面后次线、工笔勾线与着色关系；十八描不能穷尽全部描法。"
  },
  {
    "id": "learn-water-gaowei",
    "title": "高嵡",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/241468.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "画家词条全文，重点为将花卉画的撞水、撞粉运用于禽兽的说明。",
    "supports": "撞水实际用于画科转用的例证；没有定义进水时刻、含水量或撞入方式。"
  },
  {
    "id": "learn-water-soak-teaching",
    "title": "水法、铺水、渍水等国画中的7种技法，你还有哪些不知道？",
    "institution": "搜狐号“最美国画”，2020-09-16",
    "url": "https://www.sohu.com/a/418895318_744221",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "正文中水法、铺水、渍水三段；这是自媒体汇编，未给渍水段的原始出处。",
    "supports": "渍水的含水落笔、稍作逗留和留水渍操作线索。只能作待复核教学材料；不采其开头对中国画的笼统断言，不采未核实的古书引文归属。"
  },
  {
    "id": "learn-ink-huang-interview",
    "title": "中国美术学院副研究馆员冯春术：黄宾虹宿墨法启发了中国画现代化探索进程",
    "institution": "新快报收藏周刊，记者梁志钦采访中国美术学院副研究馆员冯春术",
    "url": "https://www.xkb.com.cn/articleDetail/180221",
    "accessed": "2026-11-19",
    "kind": "research",
    "scope": "2023-01-01人物访谈正文；五笔七墨、宿墨对后学的影响及不同画家的表现取向。属于具名研究者访谈，不是宿墨材料实验或古代原文。",
    "supports": "确证黄宾虹宿墨法及其对吴山明、李世南等的影响，并说明既可追求层层深厚，也有湿润透明或重黑的人物画探索。没有定义墨料存放日数或腐败机制。"
  },
  {
    "id": "learn-ink-huang-teaching",
    "title": "黄宾虹作画秘籍：绘画步骤与技法",
    "institution": "网易号“坦腹斋”，2023-12-25",
    "url": "https://www.163.com/dy/article/IMQJ08U00514BNHE.html",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "教学汇编正文的“宿墨提神”和“七是宿墨法”两处，明确给出放置一日或数日、配清水及点醒的操作线索；页面没有提供这些段落的准确原始书目页码。",
    "supports": "宿墨的储存与施用教学线索，以及浓宿、淡宿、留墨痕与水渍的分辨问题；只能作待核实二手材料，不把作者未明的引语和固定配方归给黄宾虹本人。"
  },
  {
    "id": "learn-water-yu-ink-basics",
    "title": "喻建十《山水画基础概论》——山水画的基础墨法（上）丨三品课堂",
    "institution": "搜狐号“艺术圈儿”刊载的三品课堂教学文字，正文讲者署喻建十",
    "url": "https://www.sohu.com/a/317341185_644395",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2019-05-29全文；重点为“水破墨／墨破水”同名操作，以及时间、空间、上下层水分与纸性限制。本轮未核原始讲课视频或出版版本，不把标题中的课程名冒称已核实古书／出版书目。",
    "supports": "明确写出墨破水是先画水再画墨，区别于水破墨的先墨后水；局部交错而非全部覆盖、水分与纸性需分别掌握。该课把破墨用作较宽的教学分类，不据此取代故宫词条的湿态定义，也不采未另核实的纸史及物理类比。"
  },
  {
    "id": "learn-water-collision-guagou",
    "title": "居廉《瓜藕图》（广州艺术博物院藏）",
    "institution": "羊城晚报，陈志云撰；图片由广州艺术博物院提供",
    "url": "https://ep.ycwb.com/epaper/ycwb/html/2021-08/14/content_7_418010.htm",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2021-08-14 A07“镇馆之宝”栏目全文，重点为区分撞水、撞粉的操作段。正文属于报纸作品解说，不能仅因图片由博物院提供就冒称馆方术语词典。",
    "supports": "明确撞水是在色彩半干之际注入少许水，使色墨产生浓淡及阴阳向背变化；撞粉另有薄粉后加水、倾纸的操作，不能混为同一效果。"
  },
  {
    "id": "learn-water-collision-interview",
    "title": "走近岭南画派一代宗师 居廉绘画精品引热议",
    "institution": "金羊网，记者施沛霖、通讯员陈绮雯；受访者含广州美术学院许敦平、黄浩深等",
    "url": "https://culture.ycwb.com/2019-08/12/content_30315281.htm",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2019-08-12正文，重点读第二节“撞水、撞粉”及受访者身份。它是专家访谈报道，并非古代原始文献或同行评审论文。",
    "supports": "撞染的水、墨、色、粉材料、自然流动与形体把握；熟纸、绢、金笺材质差异，以及水分和时间控制的重要性。古代起源断言不是本轮核实重点，不据此作技法创始考证。"
  },
  {
    "id": "dpm-wangshen-yucun",
    "title": "王诜渔村小雪图卷",
    "institution": "故宫博物院，娄玮撰稿",
    "url": "https://www.dpm.org.cn/collection/paint/234606.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "馆藏正文的技法分析；侧锋短笔勾皴后，清水向内化开轮廓。",
    "supports": "先墨后清水的一种具体破墨作品说明，不能直接推出反向墨破水的操作。"
  },
  {
    "id": "dpm-pima",
    "title": "披麻皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240232.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读词条正文；麻线披散交搭的形态、江南土山适用范围与董巨的馆方说明。",
    "supports": "披麻的线性、错落与土山纹理；不据此建立唯一历史起源。"
  },
  {
    "id": "dpm-axe",
    "title": "斧劈皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/243376.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读词条正文，尤其横扫、有力度、坚硬山石与斧劈的形态类比。",
    "supports": "斧劈不是统一的竖直长线；侧面扫劈与石质联系。"
  },
  {
    "id": "learn-cun-zhedai",
    "title": "折带皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240616.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读极短词条，正文只提供山石纹理、折起带子的形态类比和倪瓒说明。",
    "supports": "折带形态，不能独立支持详细腕法。"
  },
  {
    "id": "learn-cun-jiesuo",
    "title": "解索皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240122.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读词条正文，中锋、屈曲密集、绳索类比及山峦石纹用途。",
    "supports": "解索的中锋曲线与密集脉络。"
  },
  {
    "id": "learn-cun-luanchai",
    "title": "高翔山水图扇页",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/collection/paint/234012.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读藏品说明的绘画内容段；淡墨乱柴、纵横交错与水气诗意是该扇页的具体描述。",
    "supports": "乱柴可用淡墨，不能一律规定焦黑；作品语境中的交错组织。"
  },
  {
    "id": "learn-cun-niumao",
    "title": "牛毛皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/244523.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读极短词条，披麻演变、状如牛毛、王蒙善用；未给详细笔序。",
    "supports": "细毛般形态与披麻关联，腕法仍待补证。"
  },
  {
    "id": "learn-cun-juanyun",
    "title": "卷云皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/245324.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读极短词条，确认山石树木纹理技法及李成、郭熙说明；未直接解释与云头皴的关系。",
    "supports": "卷云名称与用途；不能据此认定云头为严格同义或独立两派。"
  },
  {
    "id": "learn-cun-heye",
    "title": "荷叶皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/245071.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读一句正文：皴染线条近似荷叶筋脉。",
    "supports": "筋脉结构类比，非随机放射装饰。"
  },
  {
    "id": "learn-cun-midian",
    "title": "李流芳仿米云山图扇页",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/collection/paint/234060.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读完整藏品说明，重点是远山落茄皴即米点皴、含水横点、点点相叠积染成形与近树卧笔横点。",
    "supports": "米点的含水横点、叠点成体和远近水气层次；仅对该作品直接成立。"
  },
  {
    "id": "learn-cun-mijia",
    "title": "米家山水",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/242648.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读全文的江南烟云、横点排比、破墨积墨焦墨并用及米友仁泼墨拖泥带水皴说明。",
    "supports": "米家墨点组织与拖泥带水的有限文献线索；没有具体拖法和含水参数。"
  },
  {
    "id": "learn-leaf-moss",
    "title": "王原祁山水图扇页",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/collection/paint/234118.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读藏品说明，山石润笔皴擦、墨和绿色点苔、大小间杂、聚散相生及叶点呼应。",
    "supports": "点苔有聚散与画面关系，并非均匀黑点。"
  },
  {
    "id": "learn-leaf-dianye",
    "title": "勾花点叶",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/239344.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读全文，花卉勾轮廓后晕染、叶片直接用色墨点染；该词条为花鸟画技法。",
    "supports": "直接点染与勾勒的差别，不能扩成所有山水叶式的笔序。"
  },
  {
    "id": "learn-leaf-jie-example",
    "title": "钱维乔松堂读书图扇页",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/collection/paint/234015.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读藏品说明中白描勾圈、中锋介字点、仿米卧笔横点的并列描述。",
    "supports": "介字点的中锋例证，勾叶与点叶可并存。"
  },
  {
    "id": "learn-leaf-pepper-example",
    "title": "王鉴九峰读书图轴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/collection/paint/231540.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读正文中山顶浓墨戳点的浑点、破竹点、胡椒点、破墨点，以及解索拖墨而下的作品说明。",
    "supports": "胡椒点的实际作品用例和与其他点法并置；不是孤立几何定义。"
  },
  {
    "id": "learn-cun-general",
    "title": "皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/242610.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读正文，干湿笔墨表现树石山体纹理增强质感；所举种类不当成所有术语的定义。",
    "supports": "皴须联系物象结构和质感，不是无差别填充。"
  },
  {
    "id": "learn-cun-dingtou-disambiguation",
    "title": "“钉头鼠尾”",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240735.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读全文，明确属于人物衣纹十八描，起笔顿挫与收笔尖细。；完整词条正文。",
    "supports": "仅用于排除同名误认，不能证明钉头皴的定义。；衣纹描法，起笔顿挫如钉头，收笔尖细似鼠尾。"
  },
  {
    "id": "learn-cun-guimian-disambiguation",
    "title": "鬼面皴",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/245322.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读极短词条，山石纹理技法与荆浩说明；没有与骷髅皴互释。",
    "supports": "排除凭名字将鬼面皴等同骷髅皴。"
  },
  {
    "id": "learn-leaf-pine-disambiguation",
    "title": "松针（刺绣针法）",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/242987.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "已读全文，内容为刺绣从外向内起针和风车状针法，非绘画。",
    "supports": "仅用于辨别检索同名误命中，不支持绘画松针的操作。"
  },
  {
    "id": "learn-leaf-jiezi33",
    "title": "连载6：上官绘本读《芥子园画谱》树法之点叶33式（巢勋版、附：吴蓬彩色版、附：王概版）",
    "institution": "美篇个人账号“上官绘本”（画谱转录与图版，非馆方）",
    "url": "https://www.meipian.cn/wideksq",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "已读点叶法文字，并实际查看页面第1、3、4、9、11张图。第1张标为巢勋临本树谱五三：小混/胡椒/介、鼠足/梅花/个、松叶等；第3张五五：破笔、攒三聚五等；第4张五六：疏竹、密竹、新篁；第9张 uploader 标康熙版：介/个/胡椒/梅花；第11张 uploader 标王概版：夹叶、攒三聚五等。版次和出版信息仅按上传者标注，未校核原刻本。",
    "supports": "命名叶式的实际结果图与不可死守成法的总述；静态图不提供完整笔顺和手腕动力。"
  },
  {
    "id": "learn-leaf-jiezi-tree",
    "title": "芥子园画谱——树法",
    "institution": "美篇个人账号“信威”（画谱教学转录，非原刻本）",
    "url": "https://www.meipian.cn/1wp1jpzc",
    "accessed": "2026-11-19",
    "kind": "historical-transcription",
    "scope": "已读树法转录文字：先干后点、阴阳顾盼、争让繁简、三株忌根顶俱齐；读点叶法及梅花鼠足、胡椒点树图题。未逐张查看本页图像，也未校勘上传者转录。",
    "supports": "叶法必须依附枝干组织与树群顾盼；图题只能作为名称和语境线索。"
  },
  {
    "id": "learn-cun-wang",
    "title": "著名画家王兴堂谈山水画临摹（四）：皴法是中国山水画特有的艺术形式",
    "institution": "王兴堂讲解，中华网山东频道转载（文末来源“水墨品质”）",
    "url": "https://sd.china.com/qlsh/20000951/20220804/25652397_all.html",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "已读六页合并全文：披麻、米点、荷叶、解索、乱麻、豆瓣、云头、斧劈、折带、乱柴、弹涡、骷髅与总结。郑绩《梦幻居画学简明》等为本文转引，未校原书；文章列举名称不等于逐项定义。",
    "supports": "各皴法的动作与构形差别；豆瓣/雨点的点类联系；云头转背与骷髅细勾的二手说明。"
  },
  {
    "id": "learn-cun-caibihui",
    "title": "国画中的皴法",
    "institution": "彩笔绘，署名 liangge（网络教学汇编）",
    "url": "https://www.caibihui.com/guohua/jiaocheng/3251.html",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "已读全文，重点是雨点/豆瓣、云头、折带、荷叶、解索、米点等分段；网页自称图文来源网络，存在“披/皴”等转录错字，历史归属未经独立核实。",
    "supports": "作为次级教学交叉线索，提供雨点短促长点、中侧锋和折带侧行转折描述；不采纳其全部起源叙述。"
  },
  {
    "id": "learn-cun-dingtou-sohu",
    "title": "山水画钉头皴技法解析",
    "institution": "搜狐号“書畫聯盟”（个人平台教学，未标原教材）",
    "url": "https://www.sohu.com/a/676366951_100169461",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "已读全文：小斧劈中最小皴笔一头平一头尖、夏珪《灞桥风雪图》局部说明、图一勾廓/暗处小斧劈转钉头与图二淡染留白。页面图片地址为编码懒载，本轮未看图；文中“皱/皴”混写。",
    "supports": "提供钉头皴在一个公开教学体系中的实际形态和步骤；不支持“丁头”异体或与豆瓣/雨点同义。"
  },
  {
    "id": "learn-cun-dingtou-163",
    "title": "山石技法钉头皴的画法",
    "institution": "网易号“書畫聯盟”（与已读搜狐号同名，未标原教材）",
    "url": "https://www.163.com/dy/article/I1571NVU0514WC88.html",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "已读正文详情与步骤01—03：铁钉/凿痕类比、先轮廓后小皴点、依体面调疏密浓淡、淡墨罩染后复皴。作者称江贯道源流，但未列文献；本轮不采其源流说，未核其“干至七成”参数。",
    "supports": "补充一个点皴/复皴组织实例；与搜狐同一账号，不计为两个独立传统研究来源。"
  },
  {
    "id": "learn-leaf-pin-classroom",
    "title": "寄情于景-情景交融-中国山水画课件.ppt",
    "institution": "人人文库用户课件（上传者x***，原作者/学校未明确）",
    "url": "https://www.renrendoc.com/paper/223306835.html",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "已读公开文档简介/OCR正文，其中明确列“树及树叶形态：品字点叶、介字点叶、夹叶法”，并再次出现“品字点叶”。课件署上传人x***、2022-09-29、17页；未获原作者/学校，不将平台认证当学术审校。仅页面首5页图可见，未实际查看含该词的叶式图页。",
    "supports": "证明公开教学中确有“品字点叶”命名；不支持固定三笔次序、严格点数或与攒三聚五同义。"
  },
  {
    "id": "learn-line-dpm-eighteen",
    "title": "十八描",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/241509.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整词条正文；列举十八种描法及衣纹、云水的使用范围，没有逐项操作定义。",
    "supports": "核对名单与《绘事指蒙》的书名；不能由列名推导十八套压力公式。"
  },
  {
    "id": "learn-line-dpm-baimiao",
    "title": "白描",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240113.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整词条正文。",
    "supports": "用墨线勾画物象、不施色彩的定义。"
  },
  {
    "id": "learn-line-dpm-mogu",
    "title": "没骨",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/242300.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整词条正文，以直接用彩色描绘为语境。",
    "supports": "不用墨笔为骨、直接以彩色造形；历史传说按馆方转述，不作独立起源考证。"
  },
  {
    "id": "learn-line-dpm-double",
    "title": "双钩法",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/243206.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整绘画词条正文；另读240341双勾词条是复制法帖的语境，不与本条混同。",
    "supports": "以线钩描物象轮廓称勾勒；左右或上下两笔钩描合拢，多用于工笔花鸟。"
  },
  {
    "id": "learn-line-dpm-yousi",
    "title": "游丝描",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/241511.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整词条正文及所引《绘事雕虫》短句。",
    "supports": "中锋笔尖圆匀细描，形似游丝；适合丝绢衣纹的圆润流畅。"
  },
  {
    "id": "learn-line-dpm-tiexian",
    "title": "铁线描",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/241512.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整词条正文。",
    "supports": "中锋圆劲、不显柔弱、外形如铁丝；没有规定一律等宽。"
  },
  {
    "id": "learn-line-dpm-zhanbi",
    "title": "战笔描",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240407.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整词条正文，包含对《宣和画谱》瘦硬战掣的解释。",
    "supports": "衣纹颤动屈曲而瘦硬有力；与李煜金错刀书法的关系按馆方说明记录。"
  },
  {
    "id": "learn-line-dpm-jianbi",
    "title": "减笔",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/240333.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整词条正文。",
    "supports": "与细笔相对的疏体，单纯简括，以貌取神；概括是对象组织，不只是笔数少。"
  },
  {
    "id": "learn-line-dpm-mahezhi",
    "title": "马和之",
    "institution": "故宫博物院",
    "url": "https://www.dpm.org.cn/lemmas/241438.html",
    "accessed": "2026-11-19",
    "kind": "museum",
    "scope": "完整人物词条，重点读兰叶描转为后称蚂蝗描的笔法段。",
    "supports": "马和之人物线条较短促、运笔迅疾，兰叶描有所变化；不能与所有兰叶线无条件互换。"
  },
  {
    "id": "learn-line-shijingshan",
    "title": "文物科普丨古代壁画的绘制——十八描",
    "institution": "标注来源石景山公共文化，搜狐转载页",
    "url": "https://www.sohu.com/a/722875877_121106842",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2023-09-23正文，实读所转《绘事指蒙》名单和短注、法海寺壁画用例；未取得原刻本校勘。",
    "supports": "尖笔、秃笔、肥短撇捺等古诀短注以及多种描法同画并用；不是每项完整定义。"
  },
  {
    "id": "learn-line-modern-eighteen",
    "title": "中国国画“十八描”技法",
    "institution": "临沂艺术网搜狐号，文末注明转载自网络",
    "url": "https://www.sohu.com/a/255548842_99941818",
    "accessed": "2026-11-19",
    "kind": "teaching",
    "scope": "2018-09-22全部十八段教学正文；原作者未署，不把画家归属和作品断代作为已证实史实。",
    "supports": "各描法的现代操作性描述。蚂蝗起源、枣核与橄榄界限等与其他材料有交叉，只作受限学习材料。"
  }
]);
var TECHNIQUE_READINGS = freeze([
  {
    "id": "brush-center",
    "summary": "中锋首先关乎锋尖在点画中的运行关系，不是把线画圆，也不是只把笔杆竖直。《续书谱》提出笔锋在画中；周星莲又指出杆直而毫卧仍可能失锋，且藏锋、出锋都能中锋运行。",
    "actions": "练习时先检查毫尖是否仍能主导行进，再在横、竖、弯线中调整提按和换向，使笔毫受压后能收束回性。入纸与出纸可藏可露；改变方向时维持锋尖的控制，不以固定杆角代替运锋。",
    "appearance": "观察两侧墨痕能否随转向保持协调，线内是否有连续的力向和宽度变化。圆厚是可见结果之一，却不是仅凭轮廓就能判定的充分条件；外形圆的油滑线或失锋团块也可能不符合要求。",
    "application": "适合研究骨线、树干、书法主体点画等需要明确运行关系的部分。与侧用相参时应记录转换位置；对作品的中锋判断要结合局部起收、转向和墨痕，不能由题材或书体直接指定。",
    "pitfall": "把圆头画笔、对称带状轮廓或竖直笔杆标成中锋，会把软件图形特性误作实际用锋。另一个误区是中锋必须藏锋起止；周星莲的论述明确反对这种等同。",
    "translation": "算法假设：把锋尖相对接触区的位置、方向和接触宽度分别保存，在转向处检查锋尖与轨迹的关系。对称轮廓只能作为一组对照，尚不能代表中锋模拟，更不能由参数名自动获得传统技法验收。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-xushupu",
        "locator": "第二个“用笔”节，“常欲笔锋在画中”",
        "support": "definition",
        "note": "支持笔锋与点画的关系；后附译文不另算原始证据。"
      },
      {
        "sourceId": "learn-brush-linchi",
        "locator": "开篇用锋论，“笔管竖，而笔头已卧”及“藏锋、出锋皆谓之中锋”",
        "support": "definition",
        "note": "纠正杆角、藏露与中锋的一一对应。"
      }
    ],
    "name": "中锋",
    "category": "brushwork"
  },
  {
    "id": "brush-side",
    "summary": "侧锋是在侧势中取笔毫之利的用法，不能简单等于扁笔涂色。周星莲以画石说明侧用可使石锋显出，又反对把笔完全横卧；这给出了具体用途，但没有制定适用于所有画家的单一杆角。",
    "actions": "练习时比较毫尖与毫侧的共同接触，逐步改变倾侧、受压和行进方向，并在转向时重新整理锋势。侧拖后仍应能提起、收束或转回正用，不让笔毫始终倒伏而只剩大面积擦涂。",
    "appearance": "重点观察一侧较明确、另一侧较散或宽窄变化的墨迹，以及面状接触如何交代石棱。非对称与宽面是观察线索，不能单凭扁平轮廓诊断侧锋，也不能规定它永远粗糙、永远飞白。",
    "application": "用于研究山石折面、宽线及由线入面的笔触转换。与中锋比较时应保持同一笔路和墨量，仅改变接触关系；作品中的侧锋判断仍需局部分析，不把整幅山水归为一个笔头类型。",
    "pitfall": "给圆笔旋转一个角度而接触模型不变，或把整条笔画填成均匀斜带，都不足以解释侧用。侧锋也不天然属于败笔；但将所有失控偏倒都赞为侧锋，同样抹去了用锋与失锋的区别。",
    "translation": "算法假设：以非对称接触区、笔毫朝向与锋尖偏移共同生成线面变化，并允许同笔在侧用和中用之间转换。保持这些变量可编辑，不以固定椭圆印章或单个倾角数值宣称实现侧锋。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-linchi",
        "locator": "开篇，“至侧锋之法，则以侧势取其利”及画石例",
        "support": "definition",
        "note": "提供侧势用途及横卧模糊的反例，不规定唯一角度。"
      },
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“正侧”段",
        "support": "definition",
        "note": "现代入门文以毫侧接触解释侧锋；与周星莲的限制一并阅读。"
      }
    ],
    "name": "侧锋",
    "category": "brushwork"
  },
  {
    "id": "brush-offset",
    "summary": "偏锋与侧锋的边界在论述和教学中并不统一，不能先按字面假设二者是两种稳定笔刷。所读搜狐教学直接将侧锋称为偏锋；周星莲则批评仅凭团、扁外形区分中、侧、偏。本条记录用词差异，不替各家强行定界。",
    "actions": "学习时将锋尖偏到接触区一侧、笔毫倒伏后失去调锋、以及主动侧用分别记录。在同一路径上比较是否能提起复正、换向和收束；先描述动作与接触事实，再说明所采用文献如何命名。",
    "appearance": "可比较单边偏薄、另一边堆墨、转向时翻折或失去轮廓控制等现象，但这些现象也可能出现在有意侧用中。只看最终墨迹往往不能断定是偏锋弊病，需结合作品语境和运笔过程。",
    "application": "适合用来研究调锋失效与有意侧用的差别，而非直接作为一种传统效果预设。临摹或讨论古代作品时，应注明判断来自哪一教学体系，避免把个人审美评价改写为普遍历史定义。",
    "pitfall": "把偏锋直接当作侧锋别名，或反过来规定一切不对称墨迹都是偏锋败笔，都缺少本轮证据。以圆笔为正确、扁笔为错误的二分，正是所读文献质疑的简化判断。",
    "translation": "算法假设：先暴露锋尖偏移、接触区倾斜与恢复能力三类中性数据，可制作同路径的受控对照。暂不把某个偏移阈值命名为偏锋的统一定义，保留不同术语体系附注和后续作品校验。",
    "confidence": "disputed",
    "evidence": [
      {
        "sourceId": "learn-brush-linchi",
        "locator": "开篇，“以团笔为中锋，以扁笔为侧锋”至“若者中锋，若者偏锋”",
        "support": "mentioned",
        "note": "原文确实讨论并反对表面分类；未给出可统一采用的偏锋动作定义，因此不提升为确定定义。"
      },
      {
        "sourceId": "learn-brush-sohu-scrub",
        "locator": "“2．侧锋”，亦称偏锋",
        "support": "definition",
        "note": "证明这一现代自媒体教学采用合称；不把它升级为所有时代、所有流派的统一定义。"
      }
    ],
    "name": "偏锋",
    "category": "brushwork"
  },
  {
    "id": "brush-follow",
    "summary": "所读现代教程把顺锋行笔解释为拖笔运行，并与锋尖逆势推进对照。这里的顺是毫尖、毫身与运动方向的关系；顺锋起笔与一整笔的顺锋运行应分别讨论，不能只看路径从左到右还是从右到左。",
    "actions": "先确立接触时毫尖拖行的方向，再沿笔路推进并持续整理提按。起笔可以顺势切入，后段仍可顿、转、收；学习时把入纸方式、主体运行方式和收笔方式拆开记录，避免一名统包全笔。",
    "appearance": "教程强调轻快流畅的线性倾向；实际要检查毛束顺行后是否有连续拉出的墨痕与自然提收。轻快不是必须浅淡，流畅也不要求等宽无节奏，墨水、纸面和受压仍会改变边缘及内部肌理。",
    "application": "用于比较顺拖与逆推在相同路径上的差异，以及草木、牵带、长线中较连续的行势。是否采用顺势，应服从对象和前后笔的连接，不能把所有顺滑曲线自动归入某个古代流派。",
    "pitfall": "将点序正向播放叫顺锋、反向播放叫逆锋，只改变了时间或路径朝向，没有改变笔毫受力。另一个误区是顺锋必然轻薄；质量取决于提按、运锋和材料，不能由一个速度开关决定。",
    "translation": "算法假设：在局部笔路坐标中记录毫束拖行方向与接触朝向，使其独立于世界坐标和播放顺序。以相同路径、不同朝向生成对照；这仅检验可见差异，不等同于复原真实毛笔受力。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“顺逆”段，“采用拖笔运行”",
        "support": "definition",
        "note": "支持该现代入门文中的顺锋运行解释；不声称所有历史文献采用同一定义。"
      }
    ],
    "name": "顺锋",
    "category": "brushwork"
  },
  {
    "id": "brush-against",
    "summary": "逆锋至少应区分起笔时反向取势与运行时毫尖逆势推进。所读教程分别讲藏锋逆入和逆锋行笔；后者可产生散毫飞白，却不是一出现飞白就能倒推为逆锋，更不是反转坐标序列。",
    "actions": "起笔练习可先向主行进方向的反侧取势，再转入主体；逆势运行则需持续比较毫尖与推进方向，控制翻折、提按和复正。两种练习分开记录，避免一个逆入动作被误解为全程逆推。",
    "appearance": "观察入纸处是否有被主体包入的反向痕迹，以及推进段的接触阻力、毛束散聚和断白。苍劲或生辣是评价性描述；既不可规定每笔都锯齿化，也不可用密集噪点代替受力与行势。",
    "application": "可研究树石轮廓中的逆势取力、藏锋入笔和需要摩擦感的局部。实际选用应与前笔来势、纸笔状态相配；逆势也可藏于点画内部，不要求观者总能看到明显反向折钩。",
    "pitfall": "把所有起笔都加一个同样大小的小反钩，或把逆锋渲染成沿途抖动，会把取势变成装饰公式。文献中的逆势还有力向含义，不能把它们一律换算成笔路的反向运动。",
    "translation": "算法假设：分别保存起笔局部反向段和主体接触朝向，用短尺度调锋区连接；由接触状态影响断白，不强制每次产生飞白。方向关系应对整体旋转保持一致，不能绑定为屏幕向左。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“顺逆”段的藏锋起笔与逆锋运笔两处",
        "support": "definition",
        "note": "同页将逆入和逆势推进分别叙述，可据此避免混同。"
      },
      {
        "sourceId": "learn-brush-jiushi",
        "locator": "“藏锋”及“藏头护尾”",
        "support": "example",
        "note": "历史取势语境对照；蔡邕署名未经本轮考证。"
      }
    ],
    "name": "逆锋",
    "category": "brushwork"
  },
  {
    "id": "brush-hidden-entry",
    "summary": "藏锋入笔使锋芒含在点画之内，常借逆向取势后转入主行。它是入纸和调锋的组织方式，而非给线头贴圆帽；《续书谱》同时警惕过露与过藏，藏不等于所有棱角都须磨圆。",
    "actions": "以横画练习时先短促逆入，再回折或圆转向右，提按进入主体。应控制反向段与主行的比例，使起势被后续接触包容；横竖和不同书体的转锋做法分别检查，不机械复制同一头部。",
    "appearance": "可比较含蓄的起端、起段与中段的连续关系及是否留下生硬墨疙瘩。起端较圆是常见例子，不是藏锋唯一外形；方折入纸同样可能把锋势收在画内，须结合动作判断。",
    "application": "用于临习有逆入、藏头要求的具体点画，以及山水骨线需要稳实起势的对照。实际作品中应辨书体和字势；不将汉碑教学里的流程直接覆盖到所有行草或写意山水。",
    "pitfall": "把起点扩大成墨团、在每笔开头画一个外露小圈，或用统一圆端封住锋芒，都会掩盖取势和转入关系。藏锋也不是把毫尖缩进笔肚后一直失去锋尖控制。",
    "translation": "算法假设：允许起笔包含可编辑的逆入与调锋小段，并由后续接触自然覆盖部分痕迹；端部形态从动作生成。需要与直接圆帽、方切入等同尺度对照，不能仅凭一条 round 参数确认藏锋。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“基本点画的运笔（一）·横”",
        "support": "example",
        "note": "现代教学明确给出逆左、回右、中线提按的次序。"
      },
      {
        "sourceId": "shufa-xushupu",
        "locator": "第一个“用笔”节，“不欲深藏圭角”",
        "support": "mentioned",
        "note": "限制把藏锋绝对化或钝化所有起端的推论。"
      },
      {
        "sourceId": "learn-brush-linchi",
        "locator": "开篇，藏锋于字画之内的讨论",
        "support": "definition",
        "note": "藏在画内不同于收尖入笔头。"
      }
    ],
    "name": "藏锋",
    "category": "brushwork"
  },
  {
    "id": "brush-exposed-entry",
    "summary": "露锋入笔是锋芒直接入纸、起势可见的组织方式。现代教程以顺势进入说明它，乙瑛碑教学也举尖头撇、横挑可露入等例；不能由这些例子推成露锋只能尖头，或只能用于某一种书体。",
    "actions": "将锋尖或切入边先接触纸面，再按需要增加接触面积并转入主体。练习时分别比较尖入、方切与轻入渐按，检查从入纸到中段的衔接；起端露势之后仍可转为中锋或回锋收束。",
    "appearance": "重点看锋芒与切入方向是否清楚，起端向主体的宽度变化是否连贯。露锋可显尖利或方切的势，不要求尾端也尖；其清劲感应与整笔的压力和结体关系一起观察。",
    "application": "可研究尖头撇、横挑以及写意线条中直接点入的具体做法。与藏入比较时保持主体路径相同，更容易看出起势差异；在成字或成景时再检查露势是否与其他笔呼应。",
    "pitfall": "把每一笔的起端削成同样的三角尖，或用额外黑刺表示锋芒，容易造成造作。露锋并不意味着锋尖失控，也不要求从头到尾使用同一侧向接触，不能与偏锋直接等同。",
    "translation": "算法假设：把入纸角、初始接触宽度与前段压力曲线分开控制，保留尖入和切入两组样片。尖端应服从笔路与接触方向；一个固定的零宽起点只能提供几何近似，尚不能证明露锋质量。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“顺逆”段，起笔的露锋法",
        "support": "definition",
        "note": "现代入门解释区分露入、藏入。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“撇的变化·尖头撇”及“点的变化·横挑点”",
        "support": "example",
        "note": "展示具体笔形可选择露入，不是全书体规则。"
      }
    ],
    "name": "露锋",
    "category": "brushwork"
  },
  {
    "id": "brush-return-finish",
    "summary": "回锋收笔把行势收回点画内，使尾端有回顾，而不是把原路径整段倒走。《续书谱》以垂而复缩解释垂露，并引无往不收；乙瑛碑教学则明确某些隶书竖不可照搬楷书垂露。",
    "actions": "行至预定收束处，结合轻提、转锋或短距离回收整理笔毫，再离纸。尾部回收的方向、幅度和是否留驻应随笔形；练习横尾、圆尾撇、垂露时分别记录，不用一种小圆圈包办所有回锋。",
    "appearance": "观察尾部是否收得坚实而不过肥，回收痕迹与主行是否融为一体，以及有无突然钩回的赘笔。部分回势可以含在墨迹之内，不必留下清晰可见的折返线。",
    "application": "用于具体要求裹尾、回收或笔意未尽的笔画，也可为山水线条的含蓄收束提供对照。先明确书体和部件关系；与悬针、出锋等收束并置，研究同一路径不同笔意的结果。",
    "pitfall": "强制每个端点都回绕成球，既混同回势与轮廓圆帽，也会使不同书体失去差异。无往不收涉及运笔控制，不能误译为每条可见中心线最后必须沿原路反向一段。",
    "translation": "算法假设：在尾部加入可隐藏于接触区的短回收段，并联动提按和毛束方向，允许空收和可见回收分别编辑。几何上同为圆端的样片仍需比较内部墨痕，不能凭端帽形状验收回锋。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-xushupu",
        "locator": "“真书”节，悬针、垂露及“无往不收”",
        "support": "definition",
        "note": "支持复缩与收束；不据此统一所有书体。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“基本点画的运笔（一）·竖”及“圆尾撇”",
        "support": "example",
        "note": "具体指出隶书竖与楷书垂露不同，并说明回尾不宜肿大。"
      }
    ],
    "name": "回锋",
    "category": "brushwork"
  },
  {
    "id": "brush-exposed-finish",
    "summary": "出锋是收笔时锋尖离纸、锋芒显出的过程，可通过渐提并顺势送出完成。它不是给笔画末尾附加一个尖三角；中锋运行也能出锋，是否尖出还要服从具体笔形和下一笔的关系。",
    "actions": "接近尾部时一面行进一面减少接触，让毫束逐步收拢并将力向送到尖端。以尖尾撇练习可比较渐提与较快送出；同时保留回锋、空收等变体，辨认每种尾部与主体的衔接。",
    "appearance": "重点检查粗细过渡是否自然，锋尖方向是否延续笔势，以及尾端是否细而虚弱、断裂或无端生刺。尖尾长度没有统一比例；短促出锋与长弧渐出应分别比较。",
    "application": "适合研究撇、悬针、钩及写意线条中的露尾做法。隶书撇有尖、圆、方、带钩等尾形，不应因名称相近而统一削尖；放回完整字形和景物后再判断所需收势。",
    "pitfall": "把压力终值设为零就声称出锋，忽略了毛束收拢、方向和连贯性。若前段仍很宽、末端突然归零，会像剪切；若延长同一细丝，又可能成为装饰性的鼠尾。",
    "translation": "算法假设：把尾部压力递减、收束长度和出锋方向分别保存，以接触宽度连续变化生成端部。需要与突降、渐收、回收等同路径样片对照；这些控制仅是可视假设，不是物理运锋结论。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“顺逆”段，收笔露锋法",
        "support": "definition",
        "note": "解释逐渐提出纸面的过程。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“撇的变化·尖尾撇”",
        "support": "example",
        "note": "说明尾部边行边提、渐轻且较快出锋。"
      },
      {
        "sourceId": "learn-brush-linchi",
        "locator": "开篇，“藏锋、出锋皆谓之中锋”",
        "support": "mentioned",
        "note": "避免将出锋与中锋对立。"
      }
    ],
    "name": "出锋",
    "category": "brushwork"
  },
  {
    "id": "brush-lift",
    "summary": "提笔是在运行中减轻接触并调整锋势的动作，不仅是整笔结束时抬离纸面。现代教程用提细按粗说明基本现象；《翰林要诀》又将提飞安排在疏密、平险关系中，强调它受结构支配。",
    "actions": "在仍保持行进和锋尖控制时逐步减小接触，必要时再按下或转锋。练习一条线内的提按往复、密集部件处的轻提、尾部渐提三种情形，并分别记录是否接触中断与毫束是否复聚。",
    "appearance": "观察由宽转窄、墨痕由实入虚及两边是否连贯；提起后不一定立即变淡或露白，纸面和含墨量仍有影响。窄线若只是缩小外轮廓，未必呈现出从重到轻的运行关系。",
    "application": "用于控制笔画肥瘦、转折前调锋、密处避免拥塞以及尖尾送出。点画位置不同，提的时机也不同；不可规定每笔中段一律最细、尾部一律匀速变细。",
    "pitfall": "将提等同于减少透明度、取消笔画，或只使用一条对称的头尾渐细曲线，会遗漏保持接触的提行。古文的手指、分力描述不是可直接输入设备的压力标定。",
    "translation": "算法假设：让压力与接触宽度、毛束聚合状态分别联动，保留中段提行和完全离纸的区别。曲线由调用者按结构编辑，不硬编码成全笔通用正弦；宽度缩放只是最初级的几何对照。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“提按”段",
        "support": "definition",
        "note": "提供提细按粗和运行中连续变化的入门解释。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第五·肉法”，“疏处捺满，密处提飞”",
        "support": "example",
        "note": "强调提与空间结构的关系，不按古文分数建立测量模型。"
      }
    ],
    "name": "提笔",
    "category": "brushwork"
  },
  {
    "id": "brush-press",
    "summary": "按笔使接触加深、毫束铺开，常伴随笔画增宽，却不等于一味用力压死笔锋。提按是在一笔内部相互转换的控制；所读汉碑教学将按置于捺脚、波尾等具体位置，而非每笔等量加粗。",
    "actions": "在稳定行势中逐渐增加接触，观察毫束展开与墨量变化，再通过提、转或收完成后段。分别练习中段渐按、尾部按后出锋和转折前局部加压，避免按下后笔毫卧伏而无法复起。",
    "appearance": "观察加宽处是否与前后连成整体，厚处是否仍有力向，墨是否堆成失控团块。黑度受含墨和纸面影响，重按未必更黑；宽厚也不应自动等同于重墨或强力。",
    "application": "用于波捺的展开、点的落势、山石宽笔的负重段及提按对比。变化要服从笔画在字形或体块中的位置；疏处、密处的处理应相参，不能在整幅中均匀制造鼓包。",
    "pitfall": "只把线宽放大而不考虑调锋，会得到气球状轮廓；持续最大压力则失去后续提转空间。把古书所说几分力直接换算成百分比传感器值，也超出了转录文献能支持的范围。",
    "translation": "算法假设：用可编辑的受压段控制接触区展开，同时保留含墨量和透明度的独立变量。比较同样总墨量下不同按提次序的样片，检验宽度组织作用；这不等于对真实按压力进行了标定。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“提按”段",
        "support": "definition",
        "note": "基本现象是连续提按形成粗细变化。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“基本点画的运笔（二）·捺”及“点的变化·捺点”",
        "support": "example",
        "note": "重按与渐提处于具体波捺流程，不能全笔统一套用。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第五·肉法”",
        "support": "example",
        "note": "以疏密和平险说明提按取舍。"
      }
    ],
    "name": "按笔",
    "category": "brushwork"
  },
  {
    "id": "brush-pause",
    "summary": "顿笔可理解为局部按驻、使笔势获得支点的动作，常与随后转行或出锋相接。它包含接触和力向的变化，不能简化成动画暂停；《续书谱》说折欲少驻，也同时要求转不滞。",
    "actions": "在点或折处略增接触、短暂整顿锋势，再向预定方向提转出去。比较轻顿、重顿与只停不按的差异；顿的尺度必须与笔画大小和后续方向相配，不能等到墨失控聚积才继续。",
    "appearance": "关注局部是否有稳住后再行的关系，以及转角、点腹与下一段能否连贯。可见厚度和墨聚并非必然；若只有一个孤立黑疙瘩，便难分辨是有效顿势还是停滞积墨。",
    "application": "用于点的着力、折笔换向、钩前蓄势及波捺中的局部节奏。应和提、挫、转分别比较，理解顿后如何出去；不是所有折点都需要明显扩大，也不是所有慢段都是顿。",
    "pitfall": "在节点插入固定时长等待，若接触和墨量不变，静态墨迹不会因此具有顿势。反过来强制每个节点加一圈墨，会把连续行笔切成串珠，破坏不同笔形的组织。",
    "translation": "算法假设：用局部接触加深、方向整顿及随后释放的短段表达顿势，绝对时间只用于可复现呈现。是否增加墨聚应由含水和接触模型控制，不能将等待时长直接当作传统顿笔完成度。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“基本点画的运笔（一）·点”，顿后向左下出锋",
        "support": "example",
        "note": "顿置于运笔次序中，随后仍有方向性的行出。"
      },
      {
        "sourceId": "shufa-xushupu",
        "locator": "“真书”节，“折欲少驻，驻则有力”",
        "support": "example",
        "note": "短驻有条件，与转不滞并读。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第二·血法”，蹲、驻与水聚",
        "support": "mentioned",
        "note": "提示驻与接触、墨水有关，但顿、蹲、驻不在此强行认作完全同义词。"
      }
    ],
    "name": "顿笔",
    "category": "brushwork"
  },
  {
    "id": "brush-reposition",
    "summary": "挫在所读古文里与衄、换势及前后笔衔接紧密相关，不只是按一下，也不是给轮廓添缺口。《翰林要诀》说可因前笔向各方衄出，《书谱》将衄挫置于一点之内；具体微动作仍需版本和实践比对。",
    "actions": "学习时把主行中的短距离回调、方向整理、提按变化分别记录，比较挫后如何进入下一段。先用局部动作实验说明发生了什么，不预设挫只有向下压，或一定在折角暂停。",
    "appearance": "观察小范围内的力向改变、接触形态重整及出入关系，而非只查是否有明显锯齿。挫势可藏在点内；可见折痕过多也可能只是失去控制，不能越粗糙越算有挫。",
    "application": "可用于钩前整锋、点内转势及前笔末端向新方向衔接的比较。由于不同论述把衄、挫、顿的边界讲法不同，学习记录应保留原语境，避免将这些词合并成单一抖动预设。",
    "pitfall": "把挫解释成固定大小的左右噪声，既没有前后力向，也无法解释一点内部的处理。仅凭一个译名或古书比喻就声称已经复原运腕过程，同样超出所读文字能支持的范围。",
    "translation": "算法假设：用可编辑的局部回调段、接触方向和压力转折共同记录候选动作，并与仅顿、仅转、仅折的对照并列。暂不规定唯一公式；这是针对古文的动作假设，等待具体范字或墨迹验证。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·挫”，“因前笔衄出”",
        "support": "definition",
        "note": "有明确的文内解释，但衄挫微动作与其他用语的边界尚未校勘。"
      },
      {
        "sourceId": "shufa-shupu",
        "locator": "“一画之间，变起伏于锋杪”所在段",
        "support": "mentioned",
        "note": "确认衄挫可发生在一点内部，不独立定义操作流程。"
      }
    ],
    "name": "挫笔",
    "category": "brushwork"
  },
  {
    "id": "brush-turn",
    "summary": "转笔在这里研究连续换向、调锋的关系。《续书谱》将转与折并举，要求转不滞，又说真书可参转、草书可参折；因此转不是全部节点平滑，也不是只有草书才能使用。",
    "actions": "在保持行势时逐步改变方向和接触朝向，配合提按使毛束转得过来。比较宽弯、紧弯、转后再折等情形，检查弯中是否因毫束倒伏而堵塞，或因过快滑过而失去控制。",
    "appearance": "观察弯处是否圆而有力，内外缘是否协调，以及线条能否由前段自然进入后段。平滑几何只是外形条件之一；等宽光滑的管线也可能没有提按与锋势，不能直接当作转笔范例。",
    "application": "用于弧线、钩环、草木和点画连接中的连续换向，并与折法交替构成同一笔。具体圆转范围应服从对象；山石棱线不应因为采用转法就把本来有意义的折角全部消掉。",
    "pitfall": "对全部路径使用同一全局平滑，会丢失刻意折势；把转理解为原地旋转一个笔头而中心线不变，也遗漏了实际运行。转不滞不是要求匀速，重点是换向中的行势不中断。",
    "translation": "算法假设：将连续曲率段与明确折点分别标注，使局部路径、压力与接触朝向协同变化。用同路径的转、折及混合版本比较；曲线插值只处理骨架连续性，不能单独声称完成转笔。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-xushupu",
        "locator": "“真书”节，转折与“转不欲滞”",
        "support": "definition",
        "note": "支持转折区分、真草互参及不滞的限制。"
      },
      {
        "sourceId": "shufa-shupu",
        "locator": "执使转用段，“转谓钩环盘纡之类”",
        "support": "mentioned",
        "note": "提供转的运行语境，不把抽象门类等同一个几何插值器。"
      }
    ],
    "name": "转笔",
    "category": "brushwork"
  },
  {
    "id": "brush-fold",
    "summary": "折笔以较明确的换向和局部调锋构成折势，与连续圆转有别。《续书谱》要求少驻而有力，不主张处处僵停；现代乙瑛碑教学还说明连接处可先提、变锋后下行，折并非单纯尖角。",
    "actions": "主行到转接位置时稍作提驻，整理锋势，再以新方向进入下一段。比较方折、折中含转和转中带折；要看提、驻、换向的次序如何共同作用，而不是只规定拐角必须为九十度。",
    "appearance": "观察转接处是否结构明确、两段能否贯通，外角是否被无端撑大或内角被挖空。棱角可见是线索，却不是唯一判断；圆润墨缘内也可能保留清楚的折势。",
    "application": "用于字形转接、岩石脊线和需要明确方向转换的骨线。与转笔并用时，保留关键棱角并允许其他位置圆转；不同书体、不同山石结构的折点不能共享一个角度和停驻量。",
    "pitfall": "用折线连接几个点而不调锋，会像机械多边形；为消除硬边而全局平滑又会抹去折势。每个角都加同样大墨团同样有问题，折处应有取舍和轻重。",
    "translation": "算法假设：把折点作为保角约束，同时允许前后的短调锋段、宽度变化和局部圆接。比较仅几何转角与加入提驻调锋的结果，检验其作用；直角节点自身不能证明实现了传统折笔。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-xushupu",
        "locator": "“真书”节，“折欲少驻”及真草转折互参",
        "support": "definition",
        "note": "折的节奏与转的节奏有别。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“基本点画的运笔（三）·折”",
        "support": "example",
        "note": "现代教学以提笔变锋再下行说明具体转接。"
      }
    ],
    "name": "折笔",
    "category": "brushwork"
  },
  {
    "id": "brush-fast",
    "summary": "疾笔研究迅捷而仍备法的行势，不能只按每秒像素数理解。孙过庭反对未学淹留便偏追劲疾；刘熙载更明确区分疾与速。快速是可测运行条件，疾势还涉及控制与力向，二者不能直接等号。",
    "actions": "在能完成起行收和调锋后，比较较短时间内送出的点、撇或波磔，保持方向明确。局部可快而整笔不必恒快；进入顿、转、按的位置仍应有取舍，不能靠漏掉过程来显得迅速。",
    "appearance": "观察送势是否爽利、尾部是否有明确去向、重处是否仍能站住。飞白或细长尾只是可能现象；湿笔疾行也可饱满，粗率断裂或无控制尖刺不能因为画得快就算劲疾。",
    "application": "用于啄、掠、钩出、引带或写意线中局部迅出的比较。应与徐行和淹留交替组织，不把整幅画统一加速；先掌握点画关系再研究快慢变化，更符合所读文字的限定。",
    "pitfall": "提高播放倍速只改变观看时长，静态结果未必发生任何用笔变化。另一误区是用更少采样点模拟快笔，所得多边形缺陷来自离散误差，不能当成传统疾势。",
    "translation": "算法假设：把路径上的节奏、接触持续量与提按送出分开建模，以同一绝对时间可复现呈现。快慢对材质的作用必须明确写出并作控制实验；不能用播放速度或采样稀疏度替代疾笔。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-shupu",
        "locator": "“未悟淹留，偏追劲疾”及“留不常迟，遣不恒疾”",
        "support": "definition",
        "note": "说明疾与留相济，不能独尊快。"
      },
      {
        "sourceId": "learn-brush-yigai",
        "locator": "“行笔不论迟速，期于备法”及“疾非速也”",
        "support": "definition",
        "note": "直接限制疾笔等于速度数值的解释。"
      }
    ],
    "name": "疾笔",
    "category": "brushwork"
  },
  {
    "id": "brush-slow",
    "summary": "徐行是有控制的舒缓推进，不是迟钝或停滞。孙过庭区分能速而有意淹留与本来不能迅速；《续书谱》也将乍徐还疾置于承接、牵引的关系中，说明徐应与全笔节奏共同理解。",
    "actions": "保持锋尖可控地推进，观察在较充分接触时间下如何提按、转锋与收束。分别练习竖画中段徐行、弯处短留和徐后迅出，检查是否仍能随意转换，而非毫束黏滞拖不动。",
    "appearance": "比较行进中的稳实、宽度调节和墨色承接，注意厚处是否仍有去向。徐行可能较沉着，却不必一律更黑更宽；纸吸水和墨量会影响滞聚，不能仅按浓淡断定速度。",
    "application": "用于主体竖画、需要细察提按的练习，以及与快出相配的局部段落。面对具体书体或山石线，应依据结构设置徐疾，不把每一处曲线都安排成同样缓慢的机械运动。",
    "pitfall": "降低动画帧率、拉长整笔播放时间或原地等候，都不自动产生徐行。厚重也不等于迟钝；若用均匀沉积墨量掩盖失去调锋的线形，便混淆了材料聚积与运笔控制。",
    "translation": "算法假设：给笔路分段配置有意的节奏和接触变化，保持起行收及转换可编辑。将纯延长播放的版本作为对照，确认静态变化来自接触假设；不以时长数值宣称已表现淹留之致。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-shupu",
        "locator": "“能速不速，所谓淹留；因迟就迟”所在段",
        "support": "definition",
        "note": "区分有意留与能力不足造成的慢。"
      },
      {
        "sourceId": "shufa-xushupu",
        "locator": "“草书”节，“乍徐还疾，忽往复收”",
        "support": "example",
        "note": "徐疾服务前后承接，非全笔恒速规则。"
      },
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“努”及“磔”的笔诀",
        "support": "example",
        "note": "竖笔徐行与下徐行均处于具体动作流程内。"
      }
    ],
    "name": "徐行",
    "category": "brushwork"
  },
  {
    "id": "brush-resistant",
    "summary": "涩行强调行进中有抵抗而能控制的关系，不能只等同慢速、干墨或可见颤抖。刘熙载明确说涩非迟，并以欲行而如有物拒解释；他还警告强效有形战掣容易成病，这是本条最关键的限制。",
    "actions": "保持前进意向，同时体会接触中不滑过的控制，比较提按、转向与纸面阻力的关系。练习时不要刻意逐段停抖；若出现迟滞、跳动或磨纸，应分别记录，不能因表面粗糙就认定得涩。",
    "appearance": "观察线条能否在连续运行中保留凝练的力向，弯处和重处是否稳住而不僵。涩势可以隐而不露，不要求边缘总呈锯齿；干裂、节疤与颗粒都是可能伴随物，并非充分条件。",
    "application": "可用于比较树石骨线或书法点画的浮滑与沉着，也可和疾势同时讨论。它不是仅适合干笔的材质模式；具体效果应通过同路径、不同接触组织的对照和真实墨迹阅读继续研究。",
    "pitfall": "把路径叠上均匀正弦或随机抖动，会把隐含阻力变成外露颤线；降低速度也不能直接叫涩。软件里名为 hesitant 的预设只有在说明具体接触假设后，才可作为有限对照。",
    "translation": "算法假设：研究局部接触负荷与行进之间的微小滞后，而非先强制改变中心线；允许几何平顺但接触组织不同。所有候选扰动必须固定种子，并与无扰动版本比较，尚不将任何抖动公式命名为涩法实现。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-brush-yigai",
        "locator": "“涩非迟也”及“惟笔方欲行，如有物以拒之”两段",
        "support": "definition",
        "note": "直接解释涩与抵抗、速度及有形战掣的区别。"
      }
    ],
    "name": "涩行",
    "category": "brushwork"
  },
  {
    "id": "brush-split",
    "summary": "散锋使笔毫不保持单一聚尖，可形成多束接触，但其价值在聚散组织而不只是毛边。故宫抱石皴条目把破笔散锋置于多种皴法的综合运用中，强调聚散分明、一气呵成；这只支持具体方法的语境。",
    "actions": "比较聚锋、局部裂束和较充分散锋的接触，控制毛束间距、含墨与提按后能否再聚。让散束随主行相互照应，有的连、有的断，避免每一束从头到尾完全平行、等长、等粗。",
    "appearance": "观察成束墨痕的聚合、分开与再汇合，以及断白和实墨能否构成整体。散锋不要求全笔都露纸，也不保证必定干枯；密集均匀刷丝只能说明纹理重复，未必有整体笔势。",
    "application": "用于研究山石皴擦中线、面、丝束的转换，也可作为干湿对比的接触实验。单个散毛笔刷不能直接冠以抱石皴；后者还关乎山体组织、综合用笔和具体画家的创作方法。",
    "pitfall": "按等距细线填满笔画、给每条线相同断口，容易得到梳子或排线贴图。另一个误区是把破笔、散锋、飞白都当完全同义词，忽略工具状态、接触状态与可见墨迹的不同层次。",
    "translation": "算法假设：保存若干可变毛束，各自有局部连续长度、聚散偏移和含墨量，并让它们受共同笔势约束。纹理随弧长及固定种子确定；先比较聚散组织，再讨论任何具名皴法的视觉范围。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "dpm-baoshi-cun",
        "locator": "词条正文，破笔散锋及聚散分明",
        "support": "example",
        "note": "馆方在抱石皴这一具体方法里描述散锋，不据此覆盖一切散锋用法。"
      },
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“顺逆”段逆行散毫及“枯墨·飞白”",
        "support": "example",
        "note": "补充散毫和丝丝露白的现代教学例子，二者不作同义词。"
      }
    ],
    "name": "散锋",
    "category": "brushwork"
  },
  {
    "id": "brush-dot",
    "summary": "点法不是把小圆形盖在纸上。姜夔以字之眉目、向背顾盼说明点，陈绎曾列偃仰飞伏等势，乙瑛碑教学又区分多种点形；单点有起按转收，点群则有起、带、应的组织。",
    "actions": "先练短距离入纸、按驻、转势和收出，比较正、横、竖、撇、捺等点的方向。再将两、三、四点组合，改变相互朝向、轻重与先后关系；点虽短，也应保存实际笔路而非只保存中心坐标。",
    "appearance": "观察点腹与尖尾、圆方、俯仰及局部重心，放大时检查是否有运行痕迹。点群需既有差别又相互呼应；随机改变大小只能避免重复，未必形成有意的向背关系。",
    "application": "基础练习先对应具体书法点形；用于山水苔点、树叶等题材时还需各自的组织证据，不能把书法三点水直接当成点苔定义。两者可以比较动作，却不宜直接合并技法名。",
    "pitfall": "以同一圆点随机散布代替点法，会同时失去单点运势和群点结构。把多样性理解成每点都毫无关联地变化，也不符合起带应的要求；不同不等于任意。",
    "translation": "算法假设：每点保留短笔路、压力和收势，再以显式关系组织点群的朝向与节奏。随机数只在既定组态内提供差异；可编辑组态应优先于一键散点，点叶与苔点另立研究条目。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "shufa-xushupu",
        "locator": "“真书”节的点及第二个“用笔”节的一至四点起带应",
        "support": "definition",
        "note": "同时支持单点顾盼与群点关系。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“点的变化（十六种）”",
        "support": "example",
        "note": "具体教学区分各点动作及多点变化、呼应。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·侧”",
        "support": "example",
        "note": "列不同点势，不能将其简化为一种圆印。"
      }
    ],
    "name": "点法",
    "category": "brushwork"
  },
  {
    "id": "brush-scrub",
    "summary": "已读现代教学把擦与干笔侧锋的轻摩擦联系，并给出在皴迹基础上增加面感的假山石例子。两篇均为自媒体，不能据此规定所有擦都干、都须皴后进行；山水擦法的历史边界仍只得到部分支持。",
    "actions": "依受限示例先勾结构和皴迹，再用较宽、较轻的侧向接触擦过局部，比较皴线与擦面如何相融。另设单向侧拖和局部往复两种实验，记录墨量、接触和先后；不假设每种传统擦法都照这一步骤。",
    "appearance": "重点比较可辨皴迹与较连贯擦面之间的关系，面是否支持纹理凹凸、阴阳明暗，纸面和原有笔迹是否仍能透出。自媒体所说擦时不见笔仅作为该练习的目标，不等于所有擦面都必须消除笔触。",
    "application": "可先在石面、树皮的小块局部研究，检验擦动是否连接已有皴迹并丰富体积。假山石教学的最后轻染提供一组顺序对照，但不把勾皴擦染的单一先后推广为全部画家或书画门类的规范。",
    "pitfall": "大面积低透明度涂灰、用散锋均匀铺丝，均可能有肌理却没有结构。两篇教学将若干用语简单合并，美篇还写擦只能干笔；这些是来源的限制，不能转述成已证古义，也不能据此把皴、擦、染混为一法。",
    "translation": "算法假设：保存皴线层与可编辑的侧拖接触，比较局部摩擦前后线面关系及皴擦先后的差异。用受限教学擦动候选命名，并保持干湿为独立变量；来源与样片均不足以确认传统擦笔实现或艺术验收。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-brush-sohu-scrub",
        "locator": "“6．皴擦”",
        "support": "example",
        "note": "实际解释干笔侧锋轻擦表现树皮石块的纹理与明暗，属花鸟语境的现代受限教学，不确认为所有擦法定义。"
      },
      {
        "sourceId": "learn-brush-meipian-scrub",
        "locator": "“绘画步骤”及“以皴擦运用技法画《假山石》为例”",
        "support": "example",
        "note": "提供皴后擦、线面相融及随后轻染的示例；不采纳擦只能干笔的绝对化主张。"
      },
      {
        "sourceId": "learn-brush-bimo",
        "locator": "“枯墨·枯笔”段，干毫摩擦纸面",
        "support": "example",
        "note": "邻近动作证据，原段主语是枯笔，不冒充山水擦笔完整定义。"
      }
    ],
    "name": "擦笔",
    "category": "brushwork"
  },
  {
    "id": "ink-dark",
    "summary": "浓墨是与淡墨相对的墨色状态，须和干湿、行笔及层次组织共同理解。故宫的“五色”词条明确不同分法并存，不存在由该词条指定的一档绝对黑度。",
    "actions": "从董源相关分析学习淡墨扫染与浓墨点簇的分工；李成相关分析则是淡墨起后再以焦浓墨分出层次。临习先控制浓墨落点和笔势，再比较同一含水条件下的浓淡，避免把整片一律压黑。",
    "appearance": "浓处提供明确的墨色重心，并和淡处、露纸形成节奏；在湿、干不同条件下，边缘与内部接触痕仍可各不相同。",
    "application": "可用于山石结构重点、林木点簇及需要点醒的局部。依据作品安排浓淡关系，不把近景与浓墨绑定成所有画作的固定公式。",
    "pitfall": "纯黑实心块不能自动说明浓墨用得好；降低水量也不能自动等于提高墨浓度。均匀覆盖会丢掉留白、轻重和先后。",
    "translation": "程序转译假设（尚未实现）：将颜料浓度、接触含水和局部覆盖分开，保留浓笔路径与落点的可编辑数据；先做同路径浓淡对照，再验证其在画面中的作用。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-five-tones",
        "locator": "词条“浓淡干湿”“五色说法不一”",
        "support": "definition",
        "note": "支持墨色维度与相对关系，不提供数值色阶。"
      },
      {
        "sourceId": "learn-ink-survey-50",
        "locator": "董源、李成段",
        "support": "example",
        "note": "浓墨点簇及淡起浓收是具体画法组合，不作为全部浓墨画法。"
      }
    ],
    "name": "浓墨",
    "category": "ink"
  },
  {
    "id": "ink-light",
    "summary": "淡墨是墨色较轻的状态，与湿笔不是同义词。淡墨仍可有清楚笔迹，也可参与扫染、起稿或分层组织，须观察它和浓墨、纸白的关系。",
    "actions": "依综述中的例子先以淡墨扫染或立出形势，再在需要处分次补浓。学习时应分别保持水量和墨量的对照，不用一次大面积灰色渐变替代所有淡墨笔迹。",
    "appearance": "较轻墨层可保留底层和纸白的明度，使后续浓笔有衬托；表现并不必然模糊或透明均匀，仍取决于运笔与纸面状态。",
    "application": "本轮例证包括山体淡墨扫染、淡起后加焦浓，以及雪景背景的淡墨烘染。三者用途不同，不能只按“远景”一个标签理解。",
    "pitfall": "把淡墨视为低透明度黑色的唯一同义词，会漏掉含水、笔迹边缘和层间先后的差别；把全画提亮也不是淡墨法。",
    "translation": "程序转译假设（尚未实现）：独立记录低浓度笔墨与大面染层，并允许保留清晰接触边缘；用相同路径、不同浓度及含水的样片检查可辨差异。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-five-tones",
        "locator": "词条全文",
        "support": "definition",
        "note": "浓淡与干湿均参与墨色变化，不能合成一个控制项。"
      },
      {
        "sourceId": "learn-ink-survey-50",
        "locator": "董源“淡墨扫染”、李成淡墨起的分析",
        "support": "example",
        "note": "为具体运用提供操作次序。"
      },
      {
        "sourceId": "learn-ink-fu-snow",
        "locator": "远方淡墨烘染的馆藏分析",
        "support": "example",
        "note": "补充淡墨用于外围气氛的例子。"
      }
    ],
    "name": "淡墨",
    "category": "ink"
  },
  {
    "id": "ink-scorched",
    "summary": "本轮对焦墨已取得明确的作品用例：程邃以枯笔焦墨表现丘峦林壑，并以渴笔干墨行线。它说明焦墨可与枯笔组织结合，但不能据一件作品给全部焦墨画法规定唯一配墨比例。",
    "actions": "按程邃作品说明，先学习渴笔干墨的刚劲行线与局部结构组织，再讨论黑度。综述另有湿墨之后用干焦墨点醒的组合，说明焦墨可以作为后续重点，而非每笔都同样粗重。",
    "appearance": "例证追求枯、劲、苍和金石意味，墨色虽重仍要读得出线与留白的关系。此处是馆方文字描述，尚未进行局部图像比较。",
    "application": "已核实的范围是程邃山水及米氏云山分析中的干焦墨点醒；不能直接把所有大斧劈暗面或黑色前景都认定为焦墨传统的完整复现。",
    "pitfall": "用最黑的色值加随机破洞，只得到粗糙黑块。焦、浓、干之间有联系但不是同一概念；本轮缺少更完整的材料与运笔示范。",
    "translation": "程序转译假设（尚未实现）：先研究高墨量与低水接触下可辨的枯线、断续和聚散，保留笔路与留白；名称维持近似研究，待补材料示范及图像对照再确定参数范围。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-ink-cheng-sui",
        "locator": "“枯笔焦墨”“渴笔干墨”及行线评价",
        "support": "example",
        "note": "官方具体作品例证；不是通用焦墨材料配方。"
      },
      {
        "sourceId": "learn-ink-survey-50",
        "locator": "米氏云山湿墨后干焦墨点醒段",
        "support": "example",
        "note": "支持焦墨在组合中的用途，不独立定义全部焦墨法。"
      }
    ],
    "name": "焦墨",
    "category": "ink"
  },
  {
    "id": "ink-dry",
    "summary": "故宫“渴笔”词条将其亦称干笔、俗称沙笔，关键是笔含水较少。它是接触状态与运笔问题，不能只由墨色深浅判断。",
    "actions": "减低笔中水量后仍按对象皴擦或行线，观察笔毛接触、提按与断续。与相同墨色的湿笔做对照，先看何处接触、何处露纸，而非先铺满随机噪声。",
    "appearance": "可出现枯涩、露纸和不连续的接触痕；词条没有规定每支干笔必有相同条数、方向或比例的断白。",
    "application": "官方明确常用于山石皴擦，程邃作品说明又提供干墨刚劲行线的例子。干笔并不限于给整幅画叠粗糙滤镜。",
    "pitfall": "把“干”处理成整体透明度降低，或把所有线条都挖成等距齿纹；还有把干笔、焦墨和历史飞白完全合并。",
    "translation": "程序转译假设（尚未实现）：沿实际笔路记录水量耗减、接触分束与纸面留白，变化由种子和路径决定；用多方向、急转和不同压力样片检查断续能否随笔势组织。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-dry",
        "locator": "词条全文",
        "support": "definition",
        "note": "直接给出称谓、含水状态及皴擦用途。"
      },
      {
        "sourceId": "learn-ink-cheng-sui",
        "locator": "渴笔干墨行线段",
        "support": "example",
        "note": "补充具体作品的干笔用例。"
      }
    ],
    "name": "干笔",
    "category": "ink"
  },
  {
    "id": "ink-wet",
    "summary": "湿笔按故宫词条是笔含水较多、墨迹滋润，与干笔相对。含水较多并不必然意味着墨色淡，也不等于给所有轮廓添加模糊。",
    "actions": "保留水量较多的笔中状态，依形体运行，比较湿笔单用与其后干焦墨点醒的效果。综述对陈淳的分析提示自然渗透须与形体阴阳结合，不能让扩散取代造形。",
    "appearance": "湿迹可滋润连贯并产生渗透；边界和层次随纸、墨量及前后笔状态改变，不能由一圈固定光晕概括。",
    "application": "可用于湿墨云山、花卉或需要滋润感的形体；本轮例证也显示湿笔可与干笔、焦墨同画并用。",
    "pitfall": "把湿度直接映射成淡灰或固定半径高斯模糊，会抹平形体和笔势；真实水分与墨色浓度仍需分别讨论。",
    "translation": "程序转译假设（尚未实现）：分别保存水量、墨量与落笔次序，在笔迹边界和层间交互中做有限可复现近似；先明确不模拟哪些纸内流动，再评估视觉效果。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-wet",
        "locator": "词条全文",
        "support": "definition",
        "note": "湿笔含水较多的直接定义。"
      },
      {
        "sourceId": "learn-ink-survey-52",
        "locator": "陈淳湿笔与自然渗透段",
        "support": "example",
        "note": "支持渗透与形体组织的联系。"
      },
      {
        "sourceId": "learn-ink-survey-50",
        "locator": "米氏云山段",
        "support": "example",
        "note": "湿墨后干焦墨点醒为一类具体组合。"
      }
    ],
    "name": "湿笔",
    "category": "ink"
  },
  {
    "id": "ink-flying-white",
    "summary": "故宫将飞白解释为笔墨线条夹杂丝丝露白，并区分历史书法语境和今人泛称干枯笔触的用法。本库先研究可见的顺势断白，不据此宣称再现某一历史飞白书体。",
    "actions": "学习时跟踪白丝在一笔起、行、收中的聚散与走向，注意露白仍受笔势统领。需把干枯接触造成的断白和历史书论所说的“势飞举”分开记录，不能只数白洞。",
    "appearance": "笔画内部有丝发般露纸，黑白仍属于同一运行轨迹；密度可变化，不能是全笔宽等距排列的白梳齿。",
    "application": "可作为书画干枯线条的可观察目标。是否适用于某件古代作品，仍须读其书体、用笔与局部图像，词条定义本身不能完成作品归类。",
    "pitfall": "矩形网格、均匀点洞或固定平行线只能产生贴图感；另一个误区是见到任何纸白就称历史飞白，忽略轮廓留白和材料缺损。",
    "translation": "程序转译假设（尚未实现）：用沿笔路的束状接触及间歇变化产生白丝，保持连续的走向与可编辑的断白数据；以转弯、宽窄变化和多种纸面样片检查是否仍随笔势。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-flying-white",
        "locator": "“丝丝露白”“若丝发”“今人…泛称”",
        "support": "definition",
        "note": "支持形态与语境区别；本轮不另考证词条所引飞白起源。"
      }
    ],
    "name": "飞白",
    "category": "ink"
  },
  {
    "id": "ink-layered",
    "summary": "积墨通过逐层组织淡浓墨迹增加厚度，故宫词条概括为由淡而浓逐渐渍染。龚贤例子进一步说明层间有停歇、待干以及留明处的选择，不是同一区域机械重复涂黑。",
    "actions": "参照综述的龚贤分析：先点、再加、再皴，之后停歇，待干再以浓点与淡点增补；叠加时分出明处和暗处。此顺序是一个具体例子，不能把某句引文中的次数推广成唯一配方。",
    "appearance": "墨层累积而有浑厚、苍秀与滋润的目标，明处仍能呼吸；若只剩均匀黑板，层数虽多也失去层次组织。",
    "application": "本轮可靠材料主要在山水画，尤其龚贤相关方法。可借其学习山体、树丛的墨层结构，但不把同名用于任意透明叠加而声称学会传统积墨。",
    "pitfall": "只增加重复次数或使用正片叠底，会让明部和先前笔迹一起消失；把全部层在同一湿态下混成一滩，也不能体现龚贤例中的待干步骤。",
    "translation": "程序转译假设（尚未实现）：将每层路径、浓度、覆盖范围、先后和预设干湿状态保留为数据，允许局部增补与留明；渲染层可缓存已完成层，但层数不直接作为艺术效果分数。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-layered",
        "locator": "词条全文及《林泉高致》引文",
        "support": "definition",
        "note": "给出由淡而浓的积墨目标，不固定次数。"
      },
      {
        "sourceId": "learn-ink-survey-53",
        "locator": "龚贤积墨、待干再加浓淡点段",
        "support": "example",
        "note": "提供停歇、局部叠加和保留明处的操作细节。"
      }
    ],
    "name": "积墨",
    "category": "ink"
  },
  {
    "id": "ink-broken",
    "summary": "破墨重在浓淡互破、相互掩映及轮廓分破。故宫明确浓破淡是在淡墨未干时添浓墨，也有淡破浓，因而必须记录先后与湿态。",
    "actions": "先选浓破淡或淡破浓的具体实验，再在前墨未干的阶段加入另一墨态，观察交界变化。本轮综述另述新勾墨线后以水晕开、将干再添浓墨的一种历史解释，不能把这些次序折叠成同一个叠色操作。",
    "appearance": "墨色浓淡有交融与掩映，物象界限可被分破而仍存结构。结果不只是两块半透明色的硬性交叠，也不是无控制地把轮廓全部化掉。",
    "application": "可用于山水形体内外的墨色与界限处理；具体应用需说明是哪种先后组合，不能只标一个“破墨”开关。",
    "pitfall": "等前层完全干后做普通透明叠加，却声称已模拟未干互破；或把水破墨、墨破水与浓淡互破混为同一无时序效果。",
    "translation": "程序转译假设（尚未实现）：将两次落墨、作用区域和中间湿态显式入数据，用有限边界交换近似交融；若仅有静态合成，必须标明尚未模拟材料互破。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-broken",
        "locator": "“淡墨未干之时，加添浓墨”及淡破浓段",
        "support": "definition",
        "note": "明确时序、湿态与浓淡关系。"
      },
      {
        "sourceId": "learn-ink-survey-48",
        "locator": "水晕新墨、将干再加浓墨的解释",
        "support": "example",
        "note": "为作者所引现代研究的历史复原，不当作古代画家的直接操作自述。"
      }
    ],
    "name": "破墨",
    "category": "ink"
  },
  {
    "id": "ink-splashed",
    "summary": "泼墨具有超出逐线勾描的大面布墨与随形收拾过程。综述中的王默、徐渭史例既有泼、倾、晕的介入，也有之后用笔勾染成形，不能把它理解为无条件随机洒点。",
    "actions": "王默史例是泼墨后借手、布等晕开，再以笔随形势收拾；徐渭史例则强调倾墨之后仍作勾染。学习应把初始墨势和后续选择分开分析，并记录纸绢材料的限制。",
    "appearance": "大面墨势与后续形体相互借用，偶发边界被收束为可读形象；史例描述不见墨污，并非把污浊作为目标。",
    "application": "本轮证据支持山水及写意墨形的组织思路。不能从泼墨一词推出所有画科均需同样工具、同样泼洒高度或同样材料。",
    "pitfall": "预制墨团反复盖章、均匀撒噪点，或仅生成一滩黑后宣称完成泼墨；忽略“顺势再收拾”会丢失形体控制。",
    "translation": "程序转译假设（尚未实现）：把大面初始墨势、局部晕开及后续可编辑勾染作为独立阶段，由固定种子约束偶发形状；不得把一个噪声阈值贴图当作全部泼墨过程。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-survey-49",
        "locator": "王默泼墨、晕开、收拾段",
        "support": "example",
        "note": "具体历史材料的现代综述，说明过程而非规定唯一泼墨法。"
      },
      {
        "sourceId": "learn-ink-survey-52",
        "locator": "徐渭倾墨及勾染段",
        "support": "example",
        "note": "补充主动控制形体的另一例证。"
      }
    ],
    "name": "泼墨",
    "category": "ink"
  },
  {
    "id": "ink-aged",
    "summary": "宿墨已从正式综述的列名推进到研究访谈与具体教学线索。新快报具名采访确证其在黄宾虹及后学中的重要性；另有二手教学把它说明为放置一日或数日的墨汁配清水施用，但其材料段原始出处尚待核实，不能将日数视作统一标准。",
    "actions": "所读教学页先说储存磨好的墨汁或墨滓，再在使用时配水点醒，另分浓宿、淡宿；其中涉及脱胶和水墨分离的描述仍需可靠材料示范确认。下一步应固定墨料、纸、水量并比较新墨与留置墨，核实准备过程及用法，不能把网络段落未注明来源的引语当作黄宾虹原话。",
    "appearance": "教学页提出较明确的观察线索：浓宿的重黑、淡宿的墨痕与旁侧水渍。不过具名研究访谈同时区分黄宾虹的层层深厚、吴山明的湿润透明和李世南的较重较黑，说明宿墨不是一种固定黑度或粗糙颗粒外观。",
    "application": "新快报访谈明确讨论由黄宾虹山水启发人物画的不同探索，提供了跨题材的研究范围。现有材料足以提出储墨、配水和点醒的学习问题，尚不足以确定单一脱胶配方或全部材料机制。",
    "pitfall": "把古纸泛黄、霉斑和污损当成宿墨，或把一层黑色颗粒贴图当作完整材料过程；还须防止只学“黑密”表面而忽略不同画家的笔墨与结构。关于“宿而不腐”的确切出处和材料含义，本轮尚未确证。",
    "translation": "程序转译假设（尚未实现）：可为后续研究分开记录墨料准备类别、含水、沉积及可辨笔痕，先做同墨同纸的留置前后比较；暂不以存放天数直接驱动做旧滤镜，也不把未验证的胶性变化当作已模拟物理过程。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-ink-survey-49",
        "locator": "郭熙墨法并列淡、浓、焦、宿诸名的段落",
        "support": "mentioned",
        "note": "正式研究只列名，不从这里提取不存在的准备步骤。"
      },
      {
        "sourceId": "learn-ink-huang-interview",
        "locator": "“最大的启发是他从中华文化内部寻找突破”一节，吴山明、李世南比较",
        "support": "example",
        "note": "具名研究者证实宿墨传播与不同视觉取向；没有储墨配方。"
      },
      {
        "sourceId": "learn-ink-huang-teaching",
        "locator": "“宿墨提神”及“七是宿墨法”两段",
        "support": "definition",
        "note": "明确提供留置墨汁、配清水与点醒线索，但为未标原始书目页码的二手汇编，故仍为partial。"
      }
    ],
    "name": "宿墨",
    "category": "ink"
  },
  {
    "id": "ink-rendering",
    "summary": "故宫“渲染”词条在其语境中把“渲”解释为皴擦处略敷水墨或色彩，把“染”解释为外围大面积湿笔着色、着墨以烘托形象。应先记录具体作用位置，再用这个总称。",
    "actions": "先确认已有的皴擦与形象结构，再选择局部略敷或外围大面湿染，控制它和原有笔迹的关系。此处用作一套可核实的教学区分，不宣称全部时代与教材都按此二分。",
    "appearance": "局部染可联系皴擦，外围染可衬出主体；共同目标是突出形象，而非用均匀渐变消除所有笔触差异。",
    "application": "可用于山水已有结构后的辅助处理，也能涉及色彩；工笔人物讲义另有先分染局部、后罩染整体的细分，名称之间应保留语境。",
    "pitfall": "把任何平涂、渐变、模糊都叫渲染，会失去作用位置和操作次序；将词条的具体分法冒充通用唯一术语标准也不妥。",
    "translation": "程序转译假设（尚未实现）：把局部结构染区与外围烘托染区分成调用者可编辑的区域和顺序，保留原笔迹可见性；不要把一个画布全局模糊直接标为传统渲染。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-rendering",
        "locator": "词条中“渲”“染”的分别解释",
        "support": "definition",
        "note": "直接定义，但明确限于本词条措辞。"
      },
      {
        "sourceId": "learn-ink-gongbi-figures",
        "locator": "面部局部渲染后薄肉色整体罩染的教学步骤",
        "support": "example",
        "note": "显示现代教学还按局部与整体继续细分，不混同各项。"
      }
    ],
    "name": "渲染",
    "category": "ink"
  },
  {
    "id": "ink-background-wash",
    "summary": "烘染可通过主体周围或背景的墨色衬出形象和气氛。傅抱石雪景的馆藏说明用远方淡墨烘染阴霾，反衬雪山银白；赵左雪景又提供淡墨烘染绢地的例子。",
    "actions": "先留出需要明亮或空白的主体，再在外围背景安排淡墨关系。学习时比较染在主体上与染在其周围的差别，并控制过渡，不把所有背景一律铺成相同灰度。",
    "appearance": "背景轻染与主体纸白或白色相衬，空间气氛由相邻色阶建立；留白是构图关系，未必等于后期橡皮擦出的孔洞。",
    "application": "本轮具体例证在雪景与阴霾空间。可据此研究云气或空白的衬托，但各对象的材料与处理仍须另做对照。",
    "pitfall": "用不透明白色云带覆盖全部山脚，却把它和背景淡墨烘染视为同一个方法；另一误区是把烘染、罩染和纸背托色只因都改变色调而合并。",
    "translation": "程序转译假设（尚未实现）：由调用者给出主体保留区域与背景染区，独立控制背景覆盖和墨量；以相同构图比较外围染与主体染，检查留白是否仍有空间意义。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-fu-snow",
        "locator": "远方淡墨烘染阴霾、衬雪山的段落",
        "support": "example",
        "note": "直接给出烘染的画面作用，尚未做图像局部比较。"
      },
      {
        "sourceId": "learn-ink-zhao-snow",
        "locator": "淡墨烘染绢地与浓墨主体段",
        "support": "example",
        "note": "补充背景与主体分工。"
      },
      {
        "sourceId": "learn-ink-rendering",
        "locator": "外围大面积湿笔烘托形象",
        "support": "definition",
        "note": "为外围烘托提供术语关联，不把所有背景染归为同一配方。"
      }
    ],
    "name": "烘染",
    "category": "ink"
  },
  {
    "id": "ink-glazing",
    "summary": "现代工笔教学中的罩染／罩色是在已有渲染、着色基础上施较统一的色层，以协调或统一色调。它与主要表现局部凹凸的分染有不同分工。",
    "actions": "川师讲义先局部分染并逐渐加深，再以整体色层统一；CAA讲义的面部例子在渲染着色后用淡赭石平涂。操作须保留结构和线条，不把某个练习的颜色、遍数推广成必用配方。",
    "appearance": "既有局部深浅仍可辨，上层色调产生整体联系；若罩层不透明地抹平底层，就失去此处讲义所示的调和作用。",
    "application": "本轮证据在工笔人物的面部、头发及工笔花鸟罩色。将其用于纯水墨场景时只能先借鉴层次组织，不能无证据等同所有水墨铺染。",
    "pitfall": "把分染、罩染都实现成同一个区域渐变；或者重复加层直至底下墨线消失，再靠加黑补救，失去原有结构。",
    "translation": "程序转译假设（尚未实现）：保留局部结构层与其后的统一色层，记录罩染区域、色相、薄厚和顺序；通过能否读到既有笔迹与结构的对照决定近似范围。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-gongbi-figures",
        "locator": "头发分染后花青调墨罩染、面部薄色整体罩染步骤",
        "support": "example",
        "note": "有反复分染与整体罩染的具体操作。"
      },
      {
        "sourceId": "learn-ink-gongbi-birds",
        "locator": "实验二步骤（五）“罩色”",
        "support": "definition",
        "note": "解释着色后统一协调的大面积色层。"
      },
      {
        "sourceId": "learn-ink-caa-figures",
        "locator": "工笔重彩画法（五）“罩色”",
        "support": "example",
        "note": "淡赭平涂为人物教学例子，并非所有罩染都必须用赭石。"
      }
    ],
    "name": "罩染",
    "category": "ink"
  },
  {
    "id": "ink-divided-wash",
    "summary": "分染按本轮工笔教学资料用于局部的凹凸起伏与结构变化。CAA给出两笔法操作：颜色笔局部染，随后清水笔推开；它不是把整幅画平均分块填色。",
    "actions": "先辨面部、衣褶或花鸟的局部结构，在暗处局部施色，再用清水笔推开形成过渡；随对象反复调整深浅。川师头发教学强调循序加深，不能一笔把黑度填足。",
    "appearance": "局部有浓向淡、实向虚的控制，底层勾线与体面关系仍可读；过渡方向从结构而来，不能由画布中心或固定光源统一生成。",
    "application": "本轮可靠范围为工笔人物面部、衣褶、头发以及花鸟凹凸。是否适用于特定山水墨迹，仍需独立作品例证。",
    "pitfall": "把分染做成每个封闭轮廓的均匀渐变，或一次重色盖住墨线；忽略水笔推开的过程也会把局部层次化成硬色带。",
    "translation": "程序转译假设（尚未实现）：以可编辑局部区域、深浅起点、推染方向和边界控制描述两笔关系；先做固定结构的分染／平涂对照，再研究多层与线条共存。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-ink-caa-figures",
        "locator": "工笔重彩画法（三）“分染底色”",
        "support": "definition",
        "note": "明确颜色笔与清水笔的先后操作。"
      },
      {
        "sourceId": "learn-ink-gongbi-figures",
        "locator": "面部起伏、头发循序分染步骤",
        "support": "example",
        "note": "补充局部结构与反复加深的限制。"
      },
      {
        "sourceId": "learn-ink-gongbi-birds",
        "locator": "实验二步骤（三）“分染及底色”",
        "support": "example",
        "note": "补充花鸟用途，不从其简略用语反推全部分染分类。"
      }
    ],
    "name": "分染",
    "category": "ink"
  },
  {
    "id": "ink-supporting-wash",
    "summary": "目录中的泛称“托”仍需界定。本轮取得了明确的现代工笔“托色”用例：从纸背托染白粉或石色以加强正面效果；但单国强总目中的“托”不能无证据直接认作这一种背面操作。",
    "actions": "在有证据的托色分支，川师花鸟讲义要求从纸背托染白粉或其他石色；CAA人物例在面部三白相关处理中提及纸背白粉。应先核对纸绢正反面、颜料和对象，再讨论此目录名的其他含义。",
    "appearance": "已核实分支意在加强正面色彩的厚重或人物面部粉白，并非背景变暗造成的轮廓衬托。对单字“托”的全部外观范围，目前没有充分材料。",
    "application": "可确认用于工笔花鸟及工笔重彩人物的纸背托色教学。不能把装裱托纸、外围烘托、背染、衬染等近邻词不加说明地当作全部同义。",
    "pitfall": "只根据“烘托”字面就把托色做成背景光晕，或把论文并列的“托”直接定为背面白粉；另须避免把文物装裱操作混入笔墨技法。",
    "translation": "程序转译假设（尚未实现）：若研究已核实的纸背托色，可显式分正反面与底材透显关系；泛称“托”暂不设统一效果函数，待补原始语境后再确定术语范围。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "dpm-shan-ink-survey",
        "locator": "首段“渲、染、烘、托”的总目",
        "support": "mentioned",
        "note": "只证明泛称存在，未解释其范围。"
      },
      {
        "sourceId": "learn-ink-gongbi-birds",
        "locator": "实验二步骤（六）“托色”",
        "support": "definition",
        "note": "直接说明纸背白粉／石色操作，但术语是托色。"
      },
      {
        "sourceId": "learn-ink-caa-figures",
        "locator": "工笔重彩画法（六）“提色”中的纸背托染白粉",
        "support": "example",
        "note": "支持人物工笔中的一个托染用例，未证明泛称“托”只有此义。"
      }
    ],
    "name": "托",
    "category": "ink"
  },
  {
    "id": "water-soak",
    "summary": "渍水之名已在正式综述核实。当前找到的具体教学描述来自自媒体汇编：笔端含水，落纸后全笔头略逗留，留下水渍与渗透痕；因缺原始出处，本轮只作待复核的操作线索。",
    "actions": "依该弱来源可提出含水落笔、短暂停留、观察水渍的临习问题，但尚不能把停留时长、纸性和含墨量写成规范。下一步须找可辨材料的示范或可靠教学版本，并与铺水、普通湿笔区分。",
    "appearance": "教学文字称有水渍、漶漫与渗透感，未给高清样例或明确边缘指标。因此不能预设为固定环形水印，也不能仅靠模糊效果判断。",
    "application": "可作为水法中停留与渗透的研究题目；当前未核实特定古代作品的同名运用，也不将近邻“铺水”全局统一画面的用途套给它。",
    "pitfall": "把渍水误作随意洒点或全画铺水；把现代汇编中未注明来源的古书归属当一手原文；由名称猜一个水渍贴图。",
    "translation": "程序转译假设仅供后续实验（尚未实现）：若示范核实，可记录含水接触区域与停留阶段，观察水渍边缘及底墨变化；在证据不足时不把径向水圈直接命名为渍水。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "dpm-shan-ink-survey",
        "locator": "首段水法列名",
        "support": "mentioned",
        "note": "可靠来源确认名称，不定义动作。"
      },
      {
        "sourceId": "learn-water-soak-teaching",
        "locator": "“渍水”段及相邻“铺水”段",
        "support": "definition",
        "note": "有具体动作，但为无原始出处的自媒体汇编；不足以单独确证传统规范。"
      }
    ],
    "name": "渍水",
    "category": "water"
  },
  {
    "id": "water-collision",
    "summary": "撞水已有明确操作说明：羊城晚报对居廉《瓜藕图》的解说写在色彩半干时注入少许水，让色墨发生浓淡与向背变化。故宫画家词条另有花卉技法转用于禽兽的用例；两者合读，不能只把它当作水滴噪点。",
    "actions": "先施色、墨，掌握尚有水分的阶段，再局部注入少许水，让原有颜色产生变化。所读《瓜藕图》解说写“半干”，不是完全干透后漂白；访谈又强调不同材料要控制水分与时间，故不能固定成唯一等待秒数或滴水量。",
    "appearance": "色墨的过渡渗化、浓淡与明暗可服务花果叶片的阴阳向背，形成鲜润透明的目标。具体边界与纹理随材料变化，并非只要有一圈浅晕就能判为撞水。",
    "application": "已核实花卉瓜果的具体作品说明，以及花卉技法转用于飞禽走兽的馆方用例。广美相关专家访谈强调用笔和对形的把握，材料包含熟纸、绢、金笺，效果和控制条件不能照搬。",
    "pitfall": "把撞水、撞粉、喷水、泼水统称一个喷溅效果，或把完全干层上的白色擦除冒称湿态撞染。粉与水有不同材料作用，形体组织也不能被“自然流动”四字替代。",
    "translation": "程序转译假设（尚未实现）：显式保存初始色墨区域、半干状态及后续局部加水事件，近似色墨重分布和边界变化；将材料与时间条件作为独立输入，且与白粉叠入的分支分开验证。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "dpm-shan-ink-survey",
        "locator": "首段水法列名",
        "support": "mentioned",
        "note": "确认名称，不定义操作。"
      },
      {
        "sourceId": "learn-water-gaowei",
        "locator": "花卉画撞水、撞粉用于飞禽走兽段",
        "support": "example",
        "note": "可靠画科用例，仍无动作时序。"
      },
      {
        "sourceId": "learn-water-collision-guagou",
        "locator": "“撞水法即在色彩半干之际，注入少许的水”及相邻撞粉段",
        "support": "definition",
        "note": "新补的明确同名操作，半干与少量水为此处解说范围；不把报纸解说包装成馆方定义。"
      },
      {
        "sourceId": "learn-water-collision-interview",
        "locator": "第二节黄浩深与许敦平的发言、末段材料差异",
        "support": "example",
        "note": "补充形体组织及材质、水分、时间限制，不用于未经核实的起源考证。"
      },
      {
        "sourceId": "learn-ink-gongbi-birds",
        "locator": "实验一鉴赏要略列举分染、罩染、撞水等段",
        "support": "mentioned",
        "note": "教学资料亦列名，但没有示范，故不提高定义置信度。"
      }
    ],
    "name": "撞水",
    "category": "water"
  },
  {
    "id": "water-texture",
    "summary": "已实际放大核对单国强第47页扫描，原刊水法行确实印作“皴水”，并与同页“皴、擦”的字形相同，不能归咎于本轮OCR误识。但逐页读完第47—53页仍无独立动作定义，扫描核字也不能排除原刊排印问题或作者的特殊用语。",
    "actions": "暂无可有依据复述的操作顺序。后续需找独立同词出处或作者相关语境，先判断原刊是否有勘误及该词的稳定用法，再问含水、笔序和作用对象；现阶段不从两个字推定“用清水皴山”。",
    "appearance": "可靠实读来源未给出专属形态。不能将皴线、水纹、清水涂痕或颗粒水渍任取一种当成视觉标准。",
    "application": "当前仅确认其在水法研究中的分类位置；未建立题材、材料或作品的确切适用范围，因此不进入同名演示或成品能力声明。",
    "pitfall": "名称带“皴”就借用某个山石纹理生成器，或把河水的皴线叫皴水。两者都跳过了术语证据，制造虚假的学习完成。",
    "translation": "程序转译假设暂缓（尚未实现）：在获得动作说明、出处语境及可比较样例前，不规定算法或视觉目标；本条保留的是确切的研究缺口和后续核实问题。",
    "confidence": "unresolved",
    "evidence": [
      {
        "sourceId": "dpm-shan-ink-survey",
        "locator": "第47页扫描左栏首段“渍水、撞水、皴水、洒水”一行，与上方“皴、擦”字形核对",
        "support": "mentioned",
        "note": "实际扫描核字确认原刊印作皴水，非本轮OCR新增；全文仍只列名，不据此补造定义或断言原刊绝无讹字。"
      }
    ],
    "name": "皴水",
    "category": "water"
  },
  {
    "id": "water-sprinkle",
    "summary": "洒水名称在正式水法总目中可核实。综述另述陈容噗水成雾，为喷水参与画面的邻近史例；但该史例不足以证明所有洒水具有相同工具、时序或形态。",
    "actions": "先把洒水这一目录名与陈容史例分开记录。可从后者学习水的分散施用能服务雾气组织，但本轮尚无充足材料规定洒水是在干墨、湿墨、空白纸还是色层上操作。",
    "appearance": "可靠史例描述的是成雾的整体效果，不等于画面布满可见白点或圆形水斑。颗粒尺度、散布与墨层状态尚待示范核实。",
    "application": "陈容例证联系龙画云雾；目录名本身没有限定题材。不能据此断言山水、花鸟、人物中的洒水都按同一个喷点函数工作。",
    "pitfall": "让随机白点平均散满画布，或把水滴一概实现成橡皮擦；也不能把史例中的“噗水”无说明换成现代喷壶而声称材料完全相同。",
    "translation": "程序转译假设仅供待核实实验（尚未实现）：可以区分水事件与白颜料事件，记录分布、作用层及湿态；得到可靠示范前，不把粒子喷点直接命名为完整洒水法。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "dpm-shan-ink-survey",
        "locator": "首段水法总目“洒水”",
        "support": "mentioned",
        "note": "确认目录名，不定义其全部范围。"
      },
      {
        "sourceId": "learn-ink-survey-51",
        "locator": "陈容泼墨成云、噗水成雾段",
        "support": "example",
        "note": "水分散施用的邻近历史例证，未证明等同全部洒水定义。"
      }
    ],
    "name": "洒水",
    "category": "water"
  },
  {
    "id": "water-break-ink",
    "summary": "本轮可明确学习先墨后水的一类操作：故宫王诜作品分析中，先侧锋短笔勾皴，再用清水向内化开轮廓，称为一种破墨。此具体实例为水作用于既有墨迹提供依据。",
    "actions": "先有墨线、墨点或勾皴，再在指定方向加入清水，使已有界限化开。综述还述水晕新墨、将干再添浓墨的解释；这些是具体次序，水量、等待程度与纸性仍需逐项核对。",
    "appearance": "既有墨迹局部向内或向作用区化开，轮廓仍与原结构有关。目标不是用白色清空墨迹，也不是所有黑线周围出现相同宽度的灰晕。",
    "application": "已核实王诜雪景勾皴后的清水处理，以及早期破墨的研究性复原。使用该名称时应说明采用哪一操作分支。",
    "pitfall": "把先墨后水与先水后墨混同，或把清水实现成纯白盖层／擦除；这样无法保留墨被带动、化开的先后关系。",
    "translation": "程序转译假设（尚未实现）：记录先墨数据与其后的局部加水区域、方向、预设湿态，近似原墨边界的重分布；若只用遮罩擦除，须标明尚未模拟此处的化墨作用。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "dpm-wangshen-yucun",
        "locator": "侧锋短笔勾皴后清水向内化开轮廓的技法分析",
        "support": "example",
        "note": "官方具体先墨后水例子；本轮未完成图像局部比较。"
      },
      {
        "sourceId": "learn-ink-survey-48",
        "locator": "新墨后水破、将干再加浓墨的解释",
        "support": "example",
        "note": "是论文所引现代研究的解释，不冒称王维自述。"
      },
      {
        "sourceId": "dpm-shan-ink-survey",
        "locator": "水法总目“水破墨”",
        "support": "mentioned",
        "note": "确认术语在总目中单列。"
      }
    ],
    "name": "水破墨",
    "category": "water"
  },
  {
    "id": "ink-break-water",
    "summary": "墨破水已找到明确同名的现代教学说明：三品课堂喻建十讲课文字写“先画水再画墨”。这使它不再只有总目列名；不过目前取得的是平台刊载的讲课文字，原始课堂载体与可辨图像示范尚待核实。",
    "actions": "按这节课所述，先在画面上施水，再施墨，比较水分、先后时间与纸性对渗化的影响。课程同时强调局部交错与预留空间；它没有把纯清水、带墨水、局部水域和整纸预湿逐一细分，因此不能自行给本词固定全纸浸湿或等待时长。",
    "appearance": "先水后墨会在所作用水域内形成与纸性、湿态有关的墨迹变化；课程要求保留前后笔关系和空间，未提供可据以判断所有墨破水的统一边缘或黑度。仍需同材料、同路径的先后顺序样例比较。",
    "application": "本轮明确用例来自山水画基础墨法课堂，适合作为水墨先后关系的学习入口。仅记录其纸面先水后墨语境，不把笔中先蘸水后蘸墨、所有湿底技法或其他画科自动纳入同一范围。",
    "pitfall": "把先后顺序改为同帧合成、把水域当白色擦除，或只用水破墨的定义字面倒置；另须分清本课堂较宽的“破墨”分类与故宫词条未干互破语境，不能混成唯一术语标准。",
    "translation": "程序转译假设（尚未实现）：保留先施水区域及其预设湿态，再记录可编辑墨迹与时间次序，针对纸面先水后墨做有限近似；先验证水域与墨迹的关系，再考虑扩散，不能把现有湿画布效果直接升级为传统技法已复现。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "dpm-shan-ink-survey",
        "locator": "首段水法总目中的“墨破水”",
        "support": "mentioned",
        "note": "正式论文确认名称；此文全文仍未给独立步骤。"
      },
      {
        "sourceId": "learn-water-yu-ink-basics",
        "locator": "“水破墨，先画墨”之后的“墨破水，则是先画水再画墨”段及随后时间／空间／水分／纸性小节",
        "support": "definition",
        "note": "明确同名操作，非从反义字面猜测；仍为讲课文字的二手平台载体，未核原始课堂及具体墨破水图像示范，故partial。"
      }
    ],
    "name": "墨破水",
    "category": "water"
  },
  {
    "id": "cun-pima",
    "summary": "披麻以错落交搭的线组织山体，不是等距竖线贴图；长短两路的动作和组织可不同。",
    "actions": "从山体转折、坡势安排线组。王兴堂转引大披麻可连廓兼皴、起重行轻收复重；小披麻可先廓后皴、由淡至浓。两者不可强制用同一笔长模板。",
    "appearance": "松软但有筋力的条状墨线相接相让，长短、干湿、浓淡变化构成山体起伏；线间留气，不把整个面涂满。",
    "application": "江南土山、疏松岩石与较圆缓坡势；应先确定坡的方向与前后层次，再决定线条在哪里交搭。",
    "pitfall": "“披散”不等于无结构散丝；故宫对董巨长短习惯与后世教学分法不能硬拼成一个唯一谱系。",
    "translation": "待验证的程序化假设：让调用者给坡面方向场和几条骨线，以可编辑的长短线组表示皴；将落笔/行笔/收笔压力分别建模，局部交搭，保留空隙。此项不是已实现或已验收的披麻算法。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "dpm-pima",
        "locator": "正文“如麻披散而错落交搭”“江南土山”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“披麻皴”段及转引郑绩文字",
        "support": "definition",
        "note": "二手引文，未校原书。"
      }
    ],
    "name": "披麻皴",
    "category": "texture"
  },
  {
    "id": "cun-axe",
    "summary": "斧劈依靠侧笔的按、踢、挑、拖产生石面切削感；大斧劈和小斧劈不是仅缩放同一图章。",
    "actions": "故宫说明有力度横扫；王兴堂转引大斧劈侧按踢挑、头重尾轻、长笔踢拖，小斧劈用笔嘴勾跳、可先廓后皴。根据石面方向选择侧锋和收尾。",
    "appearance": "宽狭不齐、头尾有明显势差的劈面，与轮廓搭接出坚硬棱角；不是每一笔都笔直向下，也不必一律焦墨。",
    "application": "有棱角、坚硬石质及面之间的转折。皴与石面形体一起成立，而非在光滑山丘上覆盖黑色条纹。",
    "pitfall": "不能把实验中的纵向剑峰长擦等同整个斧劈传统；也不能把斧劈写成全由干笔构成。",
    "translation": "待验证的程序化假设：提供宽侧面扫劈、窄锋勾跳两组独立手势，携带倾角、压扁、拖尾和石面方向；用组合石面测试结构，不能以一张纹理替代。未作艺术验收。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "dpm-axe",
        "locator": "正文“横扫”“有力度”“坚硬的山石”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“斧劈皴”段的大小、按踢挑拖说明",
        "support": "definition",
        "note": ""
      }
    ],
    "name": "斧劈皴",
    "category": "texture"
  },
  {
    "id": "cun-zhedai",
    "summary": "折带重在方平结形和侧笔转折，像折转的带子，不是把任意曲线降低平滑度。",
    "actions": "按教学转引用侧笔，层层连叠、左闪右按，轻重起伏；网络教学另述侧行后横刮、转折向下，方向应随石层布置而变。",
    "appearance": "转折处能看出折带的面与棱，山顶可方平转折，横向层叠和向下连接相间；保留渴笔的虚处。",
    "application": "层理、台阶和方平转折的山石，尤其可用来研究边缘与面的连接，不能只用来随机制造锯齿。",
    "pitfall": "不把“折”理解为每隔固定像素转九十度；不把馆方一句类比当作完整腕法教程。",
    "translation": "待验证的程序化假设：在调用者给出的层理骨线上分段标记侧行、折转、下接与轻重，保持各段可编辑；折点控制结构，毛边另由材质处理。尚未实现为传统技法。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-cun-zhedai",
        "locator": "正文“状似折起的带子”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“折带皴”段“用笔要侧，结形要方”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-caibihui",
        "locator": "“折带皴”段",
        "support": "definition",
        "note": "次级网络教学，方向描述仅作动作线索。"
      }
    ],
    "name": "折带皴",
    "category": "texture"
  },
  {
    "id": "cun-jiesuo",
    "summary": "解索是中锋屈曲而密集的线组织，像解开的绳索，仍须表现山石脉络。",
    "actions": "用中锋连续屈曲而非长段率直；组织密线时留出山体主脉与明面，教学特别区分其曲曲挛挛与披麻的悠扬流畅。",
    "appearance": "紧密弯曲、相穿插而有笔力的线群，形似解索但不成柔软无骨的乱麻团。",
    "application": "裂纹繁密、山势复杂的石质山峦；作品中可以与披麻组合，同一面上的屈曲须受山势约束。",
    "pitfall": "不能用整页随机卷曲线代替；“密”不等于每处同密，曲线也不能因样条过度平滑而失去笔力。",
    "translation": "待验证的程序化假设：沿结构骨线生成受约束的屈曲线束，控制转弯尺度、局部密度和遮让；保留每一条普通路径以供调整，而非合成一块绕线贴图。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-cun-jiesuo",
        "locator": "正文“中锋”“屈曲密集”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“解索皴”段与王蒙作品说明",
        "support": "definition",
        "note": ""
      }
    ],
    "name": "解索皴",
    "category": "texture"
  },
  {
    "id": "cun-luanchai",
    "summary": "乱柴以劲健、直中有折的线像柴枝交叠；名称的“乱”受阴阳结构统摄。",
    "actions": "教学转引直笔参折笔、笔笔用力；阴处密而粗、阳处疏而细。高翔扇页则用淡墨乱柴与树线交错，说明墨色可以按画面变化。",
    "appearance": "有枯枝折断般骨感的粗细、疏密和方向交错；松活而非无规律划痕，也不要求全部黑硬。",
    "application": "裂纹较多的石体及需要交错线势的山川；用粗密区建立暗部，再与疏细明部相接。",
    "pitfall": "不能因名叫“乱柴”就把分布交给均匀随机数；也不能将其与细软盘绕的乱麻混作同一材质。",
    "translation": "待验证的程序化假设：在石面上建立两三束主要直折方向，按面向调节粗细密度和虚实；用淡墨/浓墨两种构图验证，保留被叠压前后的路径顺序。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-cun-wang",
        "locator": "“乱柴皴”段的阴阳粗细、直折比较",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-luanchai",
        "locator": "藏品正文“淡墨乱柴皴”“纵横交错”",
        "support": "example",
        "note": ""
      }
    ],
    "name": "乱柴皴",
    "category": "texture"
  },
  {
    "id": "cun-niumao",
    "summary": "目前可核实牛毛皴由披麻演变、形似牛毛；精确运腕与组织法尚缺本轮可靠正文支持。",
    "actions": "现有馆方短词条没有逐步动作，只可确认其线性来源。学习下一步应对王蒙相关作品局部与长短披麻同尺度比较，而非凭“牛毛”补写腕法。",
    "appearance": "可暂把细毛般线纹作为观察目标；密度、曲率、交叉比例仍需图像比较确认，不能把极细平行线直接认作牛毛。",
    "application": "用于理解王蒙相关山石纹理的细密组织；具体适用石质和画面位置应由作品证据决定。",
    "pitfall": "避免把“牛毛”理解为各处均匀细丝，或因为目录相邻就与解索互换；本轮没有证明两者有一条固定曲率阈值。",
    "translation": "待验证的程序化假设：仅开细线束实验，并显式标注牛毛形态研究；在同一骨面比较线径、屈曲和疏密，取得局部对照前不提供传统技法保真承诺。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-niumao",
        "locator": "全文“由披麻皴演变”“状如牛毛”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-jiesuo",
        "locator": "解索词条中的牛毛基础线索",
        "support": "mentioned",
        "note": "关联线索不能补足牛毛的独立笔序。"
      }
    ],
    "name": "牛毛皴",
    "category": "texture"
  },
  {
    "id": "cun-juanyun",
    "summary": "卷云皴的山石树木纹理用法已由馆方词条确认；它与云头皴的名称边界，本轮资料仍不足以严格划分。",
    "actions": "馆方词条没有描述笔序。可把云头教学中的圆运转背作为邻近研究线索，但不能未经论证直接复制为卷云的唯一操作。",
    "appearance": "名称提示卷曲云状，但目前只把它列为待观察形态；需看李成、郭熙相关局部，确认曲线如何构成面与前后。",
    "application": "用于卷曲线与山石结构的对照研究，不先指定一套独立于云头的“卷云图章”。",
    "pitfall": "不能仅凭两名相近就断言完全同义，也不能为了凑齐种类虚构两个互斥算法。",
    "translation": "待验证的程序化假设：在元数据中保留卷云名称与云头关系待考；共用可编辑曲线基础，待作品局部与文献建立区别后再决定是否分开预设。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-juanyun",
        "locator": "全文",
        "support": "definition",
        "note": "只支持名称、用途与馆方画家线索。"
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“云头皴”段及开头皴法名单",
        "support": "mentioned",
        "note": "正文解释云头，名单列卷云，未明确互释。"
      }
    ],
    "name": "卷云皴",
    "category": "texture"
  },
  {
    "id": "cun-yuntou",
    "summary": "所读王兴堂教学的云头皴强调圆运团旋中的转背，使云头状山石有背面；这是一份二手引文解释，其与卷云的名称边界仍未核清。",
    "actions": "王兴堂转引宜干笔、运腕圆、力贯笔尖；由后搭前、从左搭右组织转背。网络教学概括屈曲迂回、向中心环抱，但不可省略正背面。",
    "appearance": "细而有筋、屈曲环抱的线把石块团起；线团有开面和转背、搭接与空隙，避免扁平香蜡饼般圆片。",
    "application": "有团块翻卷、烟云隐现意味的山石结构；可借正面与转背对照研究石面空间。",
    "pitfall": "不能把圆形噪声或同心旋涡当云头皴；与卷云是否同义在本轮仍未核实。",
    "translation": "待验证的程序化假设：先给石块正背面和搭接次序，再布置不闭死的环抱线；比较有/无转背的同一结构，检查体块是否成立。该试验不是对卷云命名的裁定。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-wang",
        "locator": "“云头皴”段，转引郑绩“团旋中又须背面分明”",
        "support": "definition",
        "note": "本项有具体教学解释，但来源/转录层次有限，未校原典或建立专名边界。"
      },
      {
        "sourceId": "learn-cun-caibihui",
        "locator": "“云头皴”段",
        "support": "definition",
        "note": "辅助方向线索，未采用其全部起源叙述。 本项有具体教学解释，但来源/转录层次有限，未校原典或建立专名边界。"
      }
    ],
    "name": "云头皴",
    "category": "texture"
  },
  {
    "id": "cun-yudian",
    "summary": "所读网络教学把雨点解释为短促长点或垂直短线的点类皴法；其与豆瓣的关系有联系线索，尚不足以建立跨教材统一边界。",
    "actions": "中锋为主、可间侧锋，短促点凿；依轮廓阴阳分布聚点，不作全体等距雨滴。王兴堂在豆瓣段描述笔尖着纸后随用笔肚的点法。",
    "appearance": "点形有长度和起收方向，短点成群而形成石面厚重感；它不同于米点例证中含水横卧、相叠迷蒙的墨块。",
    "application": "石面阴阳、坚实山体的短点组织；需用结构性聚散研究点与面的关系。",
    "pitfall": "不要把“雨”画成画面上垂直落雨，也不要为了目录分项武断规定雨点和豆瓣有固定长宽比分界。",
    "translation": "待验证的程序化假设：以短线点凿为一个可调手势族，提供方向、按落与群组密度；和含水米点并排比较，保留雨点/豆瓣名称关系说明。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-caibihui",
        "locator": "“雨点皴”段“长点形的短促笔触”“中锋稍间侧锋”",
        "support": "definition",
        "note": "本项有具体教学解释，但来源/转录层次有限，未校原典或建立专名边界。"
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“豆瓣皴”段的雨点与短线点凿说明",
        "support": "definition",
        "note": "本项有具体教学解释，但来源/转录层次有限，未校原典或建立专名边界。"
      }
    ],
    "name": "雨点皴",
    "category": "texture"
  },
  {
    "id": "cun-douban",
    "summary": "所读王兴堂教学把豆瓣与芝麻、雨点归于点子类，解释聚点点出石面阴阳；该教学分法不能直接当作各教材一致的同义词规范。",
    "actions": "先起轮廓，自阴面细点阴阳向背；笔尖着纸随用笔肚，灵活变动；再可用淡墨或青绿渲染。教学明确忌呆点排列。",
    "appearance": "小点与短线厚薄参差、聚散有序，点的堆积带出阴面和硬实质感；点间保持气口。",
    "application": "适合检验点类皴法如何从轮廓和阴面生长，而非先随机撒点再寻找山形。",
    "pitfall": "不要把每一笔做成相同豆瓣剪影；目前证据不支持雨点、芝麻、豆瓣各有一套互不相容的固定参数。",
    "translation": "待验证的程序化假设：在调用者的明暗分区上，按轮廓距离和面向组织短点；把起点压力与按肚时间作为手势参数，不以规则格点布置。仍须局部图像对照。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-wang",
        "locator": "“豆瓣皴”完整段落",
        "support": "definition",
        "note": "本项有具体教学解释，但来源/转录层次有限，未校原典或建立专名边界。"
      },
      {
        "sourceId": "learn-cun-caibihui",
        "locator": "“雨点皴”段的又名说法",
        "support": "definition",
        "note": "转录称“豆瓣披”，视为次级关联线索。 本项有具体教学解释，但来源/转录层次有限，未校原典或建立专名边界。"
      }
    ],
    "name": "豆瓣皴",
    "category": "texture"
  },
  {
    "id": "cun-midian",
    "summary": "米点皴在李流芳实例中是饱含水分的墨笔横点、点点相叠积染成形，兼顾明露与迷蒙。",
    "actions": "以卧笔横点组织点群，按山峦层次叠加；深浅浓淡与点的进退厚薄一起运作。不可仅靠模糊滤镜掩盖运笔。",
    "appearance": "横向墨点既各自独立又联结成面，有水气与前后层次；边缘虚实不一，点群之间可有云气留白。",
    "application": "江南烟雨云山与远近树山的水气表现；同样的横点可用于近树，但点大小和组织由具体物象决定。",
    "pitfall": "不是任意散布椭圆，也不等于一律淡墨；馆方米家词条明确提到破墨、积墨、焦墨结合。",
    "translation": "待验证的程序化假设：用含水横点手势沿山脉分层积点，分别保存点次序、墨负载和层级；对照清晰单点、叠点和层间留白，检验点能否积染成形。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-cun-midian",
        "locator": "藏品正文“饱含水分的墨笔横点，点点相叠，积染成形”",
        "support": "example",
        "note": ""
      },
      {
        "sourceId": "learn-cun-mijia",
        "locator": "正文“横点排比”“破墨、积墨、焦墨”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“米点皴”段“不可太模糊或太明露”",
        "support": "definition",
        "note": ""
      }
    ],
    "name": "米点皴",
    "category": "texture"
  },
  {
    "id": "cun-heye",
    "summary": "荷叶皴以峰顶到山脚的分歧筋脉建立结构，像覆荷叶脉下垂，不是从任意中心放射的花纹。",
    "actions": "王兴堂转引从山顶尖处起筋，自上而下、由重而轻、笔笔分歧；山脚开处用轻淡接气。",
    "appearance": "长而有韧性的线逐步分出支脉，向下散放；顶端集中、足部舒展虚淡，线与山形互为表里。",
    "application": "尖顶向下开张、具有较深裂纹走势的山体；应由山顶、坡面转折与山脚组织连续脉络。",
    "pitfall": "避免把所有支线均匀等角放射，或末端一起截断；“四面散放”仍受形体和前后遮让制约。",
    "translation": "待验证的程序化假设：输入峰顶和主沟骨线，以有层次的分支路径表达筋脉，父子线共享方向但分别调压、收淡；山脚留气，保留路径可编辑性。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-cun-heye",
        "locator": "正文“线条近似荷叶的筋脉”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "“荷叶皴”段的上重下轻与分歧说明",
        "support": "definition",
        "note": ""
      }
    ],
    "name": "荷叶皴",
    "category": "texture"
  },
  {
    "id": "cun-kulou",
    "summary": "骷髅皴在本轮王兴堂转引中指精细谨严的勾勒组织，像头颅尸骨；不是给山体加骷髅图案。",
    "actions": "二手引文强调纯用勾勒、细中有力、密处有疏，常用白描、小幅；完整骨孔构形与笔序仍需原书及实例复核。",
    "appearance": "细线勾成有骨架意味的石形，精细但有疏处；本轮未取得足够图版，不将具体孔洞数量或固定轮廓写成规则。",
    "application": "可先在小型白描石组中研究，与细树夹叶等精细环境关系另待作品核查。",
    "pitfall": "不能凭鬼面词条认定二者同义；不能根据现代解剖骷髅外形直接建立山石图章。",
    "translation": "待验证的程序化假设：只作细勾骨架与疏密空隙研究，待原书图版和具名作品确认后再建立预设；暂不自动生成所谓骷髅皴。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-wang",
        "locator": "“骷髅皴”段",
        "support": "definition",
        "note": "转引有省略号，非完整原书，动作与图形范围需复核。"
      },
      {
        "sourceId": "learn-cun-guimian-disambiguation",
        "locator": "全文",
        "support": "mentioned",
        "note": "负向辨别：未说明与骷髅互释。"
      }
    ],
    "name": "骷髅皴",
    "category": "texture"
  },
  {
    "id": "cun-tuoni",
    "summary": "拖泥带水皴已在故宫米家山水词条中确认为米友仁的泼墨相关用法；详细操作尚未核实。",
    "actions": "现读正文只说“泼墨的拖泥带水皴”，没有交代先水后墨、干湿次序、拖笔方式与纸面状态。本轮不凭名称补写成规范步骤。",
    "appearance": "可研究水墨交融、烟云氤氲与边界虚实，但这些是米家总体语境，不能当作该皴法独有的外观定义。",
    "application": "用于后续对米友仁烟云山水中泼墨与点皴关系的专门比较；必须找到术语对应的作品说明或教学再细分。",
    "pitfall": "不要把拖动湿笔、拖泥色块或“湿边”材质的任一种直接宣称为拖泥带水皴。",
    "translation": "待验证的程序化假设：保留一个明确标注未定名的水墨拖曳实验，记录次序、含水和笔向；在证据补足前不将它作为传统技法预设实现。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-mijia",
        "locator": "末段“泼墨的拖泥带水皴”",
        "support": "mentioned",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "开头皴法名单",
        "support": "mentioned",
        "note": "仅列名，不支持动作。"
      }
    ],
    "name": "拖泥带水皴",
    "category": "texture"
  },
  {
    "id": "cun-dingtou",
    "summary": "已查到公开教学中独立的钉头皴：小斧劈中的短小钉状笔，一头平/方而尾尖；“丁头”写法和豆瓣、雨点的关系仍未核清。",
    "actions": "所读搜狐教学先以深墨中锋、间有偏转勾廓，在最暗处用小斧劈皴擦，再过渡到头方尾尖的钉头，轻重大小与位置参差，最亮处留白；随后按皴向淡染。网易同账号另示先廓后小皴点、按体面调疏密浓淡与罩染复皴。",
    "appearance": "短小钉状痕迹形成一头较平重、另一头尖细的势差；在该教学中暗部密实、向亮部渐淡疏，保留大片雪白。不将这一个雪景例扩为全部钉头皴只能画雪。",
    "application": "可先研究坚硬斑驳石面与小斧劈至短皴的过渡。网易还提远景点苔/树木，但缺具名作品与文献，本轮仅记为该教学的用途说法，不泛化为传统定义。",
    "pitfall": "独立山水正文补上了前轮缺口，但不能因此把人物“钉头鼠尾描”直接等同钉头皴；也未证明钉头就是豆瓣、雨点，或“丁头”为规范异写。搜索引擎自动纠错不构成别名证据。两篇平台文章出自同一账号，不能算独立互证。",
    "translation": "待验证的程序化假设：在小斧劈石面上配置短侧按/提收的钉状笔，保持方重头、渐尖尾与局部疏密可编辑；与豆瓣、雨点分组并排对照，暂不合并专名或制定几何分界。仅作已读教学的近似研究。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-cun-dingtou-sohu",
        "locator": "开头定义、“钉头皴技法说明”图一/图二正文",
        "support": "definition",
        "note": "实际读到独立山水教学；未校其原教材或历史源流，未看懒载图。"
      },
      {
        "sourceId": "learn-cun-dingtou-163",
        "locator": "“详情介绍”及步骤01—03",
        "support": "example",
        "note": "同一账号的另一个教学，不作独立权威互证；不采江贯道源流与七成干的定量化。"
      },
      {
        "sourceId": "learn-cun-dingtou-disambiguation",
        "locator": "全文“人物衣纹十八描”",
        "support": "mentioned",
        "note": "继续用于区分衣纹描与山水皴，非钉头皴的独立定义。"
      }
    ],
    "name": "钉头皴",
    "category": "texture"
  },
  {
    "id": "cun-baoshi",
    "summary": "抱石皴是傅抱石融汇多种皴法与破笔散锋的个人综合语汇，聚散和一气呵成是重要关系。",
    "actions": "依馆方描述以破笔散锋运作，融披麻、解索、乱柴等而非固定重复一种单笔。具体握笔、毛锋分叉和湿墨积层应继续对照作品。",
    "appearance": "散锋形成的复杂线面既有聚又有散，不能只表现为均匀多丝刷痕；完整画面中应看到笔势连贯。",
    "application": "傅抱石相关山水风格研究；范围应限定为程序近似，不能推广成一切散锋山石画法的通名。",
    "pitfall": "不能将“抱石”当作怀抱石块的几何构图，也不能用一次散点噪声覆盖就宣称得到抱石皴。",
    "translation": "待验证的程序化假设：先把散锋作为普通笔毛接触数据，再在受山势约束的线面组中组合与积墨，比较聚区/散区和连续笔势；不把个人风格简化为一个噪声开关。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "dpm-baoshi-cun",
        "locator": "全文“破笔散锋”“聚散分明”“一气呵成”",
        "support": "definition",
        "note": ""
      }
    ],
    "name": "抱石皴",
    "category": "texture"
  },
  {
    "id": "vegetation-moss",
    "summary": "点苔是画面结构中的点群呼应，可以墨、色并用，大小与聚散共同作用。",
    "actions": "王原祁例中山石先润笔皴擦，再以墨与绿色点苔；按山石与树叶的关系组织大小间杂和聚散，不作固定均匀覆盖。",
    "appearance": "细碎点与山石笔纹、树叶点互相联结，在局部成群、在另处留空，整体杂而不乱。",
    "application": "石脊、坡面等可承载苔点的结构处与全画节奏；具体落点应结合物象和构图，不预先全域撒点。",
    "pitfall": "不能只理解为加黑或遮丑噪声，也不能把点苔与所有点叶、胡椒点直接混名。",
    "translation": "待验证的程序化假设：输入结构锚点与呼应对象，按组给苔点大小、墨色和疏密；让种子改变姿态和间距而保持主要聚散位置可编辑。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-leaf-moss",
        "locator": "藏品正文“以墨和绿色点苔”“大小间杂，聚散相生”",
        "support": "example",
        "note": ""
      }
    ],
    "name": "点苔",
    "category": "vegetation"
  },
  {
    "id": "vegetation-dot-leaves",
    "summary": "点叶以色墨直接点染叶片，叶群仍依附枝干、顾盼和繁简而生，不是一层任意颗粒。",
    "actions": "勾花点叶词条明确叶片直接点染；树法转录强调先干后点、阴阳向背、左右顾盼。先安排枝架与叶簇，再选相应点式和墨色。",
    "appearance": "叶片实点可清楚成形，也可叠成簇；与勾叶的空心轮廓方式不同，点式之间仍有许多自然相似与变化。",
    "application": "花鸟叶片和山水树冠的直接用墨；馆方勾花点叶定义的范围是花鸟，山水组织另由树法证据补充。",
    "pitfall": "不把点叶当“任意点函数”，也不严格规定某树只能某点；画谱总述明确提醒不可死守成法。",
    "translation": "待验证的程序化假设：把枝干锚点、叶簇布局与叶片手势分开，允许调用者选择点式和画自己的路径；单叶可编辑、叶群有疏密而不固定树种模板。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-leaf-dianye",
        "locator": "正文叶片“用色墨直接点染”",
        "support": "definition",
        "note": ""
      },
      {
        "sourceId": "learn-leaf-jiezi-tree",
        "locator": "“杂树法”及“点叶法”转录",
        "support": "definition",
        "note": "平台转录未校原刻。"
      }
    ],
    "name": "点叶",
    "category": "vegetation"
  },
  {
    "id": "vegetation-outline-leaves",
    "summary": "夹叶在所看画谱转载图中以细线双勾叶形组织叶群，与实墨点叶有明显空实差别。",
    "actions": "第11张“夹叶点”图可见密集的细长空心叶形沿小枝成簇；静态图不提供每叶起收顺序，暂只把双边勾形与分组作为观察结论。",
    "appearance": "细边围出叶中留白，层叠中仍可见疏空；与旁边实点叶簇比较，表现更清透、线性更强。",
    "application": "用于需要线性叶群和透气树冠的研究；可与实点叶互补，具体树种和设色流程不能只从此图断定。",
    "pitfall": "不把所有椭圆环都叫夹叶，也不把夹叶解释成两种墨色夹一层；闭合程度和枝叶相接仍需实物图版补查。",
    "translation": "待验证的程序化假设：以一叶两边的可编辑细线表示，沿枝的朝向与叶簇安排分组；先测试叶间空隙和轮廓交叠，不把中间白区直接做成填白图章。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第11张，上传者标“王概版”，左页下中“夹叶点”图",
        "support": "example",
        "note": "实际看图结果；未校原刻版次与笔序。"
      },
      {
        "sourceId": "learn-leaf-jie-example",
        "locator": "藏品正文“白描勾圈”",
        "support": "example",
        "note": "支持勾叶与点叶并用的语境，并非夹叶专名定义。"
      }
    ],
    "name": "夹叶",
    "category": "vegetation"
  },
  {
    "id": "leaf-jie",
    "summary": "介字点在馆藏实例中用中锋；所看画谱图呈分叉尖叶小组，需与个字点区分而不凭名称猜笔数。",
    "actions": "中锋落叶有作品文字依据。第1、9张图中，介字点的分叉叶组较密、分向清楚；静态图不能证明一套固定笔顺或每组必须几笔。",
    "appearance": "尖长、分叉的小组顺枝连接，图中与个字点相邻但组织不完全一样；整体有俯仰与左右顾盼。",
    "application": "用于由小分叉组织而成的叶群，可与勾圈、米家横点混合表现不同树态。",
    "pitfall": "不要把电脑字体“介”机械复制到树上；也不要把介与个当成同一个三笔预设仅换标签。",
    "translation": "待验证的程序化假设：先分别标注所读介/个图中叶组的方向关系，做可编辑分叉锚点实验；笔数和笔序保持显式可选，待更清楚教学核实。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-jie-example",
        "locator": "正文“中锋介字点”",
        "support": "example",
        "note": ""
      },
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第1张五三右上与第9张右上“介字点”",
        "support": "example",
        "note": "图形观察，不等于固定笔序证明。"
      }
    ],
    "name": "介字点",
    "category": "vegetation"
  },
  {
    "id": "leaf-ge",
    "summary": "个字点在所看画谱中是分向尖叶组成的小组；其近似字形的分叉关系比照写字更重要。",
    "actions": "第1、9张的个字点可见上/中向与两侧下垂的叶笔组合，分组较清楚；未读到规定三笔顺序的正文，不将观察转成唯一教学笔序。",
    "appearance": "叶组疏密和俯仰不同，簇与簇有空隙；与上方介字点并列呈现，说明两名不应无条件合并。",
    "application": "树叶与竹状尖叶点组的比较研究；是否对应某一树种不宜由字名硬定。",
    "pitfall": "不是按“个”字轮廓画三条同宽直线；也不能以同一组图认定所有版本都具有相同笔数。",
    "translation": "待验证的程序化假设：给叶簇一个主向与两侧方向，分别配置尖叶手势和重叠，保留组姿态；与介字点实验采用不同标注而非伪造历史参数界限。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第1张五三中右、第9张右下“个字点”图",
        "support": "example",
        "note": "观察到结果形态，未获得完整操作文字。"
      }
    ],
    "name": "个字点",
    "category": "vegetation"
  },
  {
    "id": "leaf-pin",
    "summary": "公开教学课件正文确有“品字点叶”命名，本轮已从完全未核实推进到名称与教学语境有据；具体图式和动作尚未核清。",
    "actions": "2022年上传课件将品字点叶与介字点叶、夹叶法并列，但公开OCR没有解释其笔序、单点手势和组内计数；本轮未看到对应叶式图页，不能据“品”字猜成严格三笔。",
    "appearance": "现阶段只确认它被当作树叶形态/点叶方法来讲授。三点、三叶、三团的哪一级构成“品”字尚缺实际图示，不将任何三角布局冒认为已确认外观。",
    "application": "可保留为点叶教学的一个命名项目。原课件作者、教材版次和图式来源未明，尚不能指定树种、时代或宣称与《芥子园》某点式严格同义。",
    "pitfall": "不能再说公开教学没有该名称；同样不能从名称存在推论操作已掌握。攒三、攒三聚五与品字点叶没有在本轮正文中被互释，不能直接合并。",
    "translation": "待验证的程序化假设：保留品字点叶为需补图式的学习项；三组错位排布仍应以普通布局名提供。待核实原图中的单点、叶组和枝架关系后，再决定是否建立独立预设。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-pin-classroom",
        "locator": "文档简介/OCR第1—2段“树及树叶形态：品字点叶介字点叶夹叶法”",
        "support": "mentioned",
        "note": "实际正文可证名称与教学范围；对应图页未查看，不支持几何或笔序。"
      },
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "点叶法名单与已看第1/3/4/9/11张图",
        "support": "mentioned",
        "note": "范围限制：本页未见“品字点”专名。不能用这套图替代另一课件的同名图式。"
      }
    ],
    "name": "品字点",
    "category": "vegetation"
  },
  {
    "id": "leaf-three-five",
    "summary": "攒三聚五是成簇聚散的组织线索；已看图中叶组相攒而有疏密，并非每处严格三片加五片。",
    "actions": "图中尖叶小组向不同方向簇聚；王兴堂总结引用“点叶要攒三聚五”，与皴笔隔三差五相对，强调松活疏密、避免重复死墨。",
    "appearance": "几组叶团聚而相让，内部有密、边缘有疏，组与组之间保留空隙和顾盼。",
    "application": "树冠叶簇的布局与节奏；可以用于约束点叶群而非直接决定单叶形状。",
    "pitfall": "不要把三、五做成不可变数额后排成整齐网格；“聚”与“散”的构图意义不能让位于计数。",
    "translation": "待验证的程序化假设：用可编辑叶簇锚点控制组群关系，组内数量可变并保留空白通道；种子只改变局部姿态，不能抹掉主要顾盼关系。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第3张五五中右“攒三聚五点”；第11张右页中部同名图",
        "support": "example",
        "note": ""
      },
      {
        "sourceId": "learn-cun-wang",
        "locator": "结尾“点叶要攒三聚五，画皴要隔三差五”",
        "support": "definition",
        "note": ""
      }
    ],
    "name": "攒三聚五",
    "category": "vegetation"
  },
  {
    "id": "leaf-pepper",
    "summary": "胡椒点在画谱图中是细小密点群，在王鉴作品说明中是浓墨戳点的一种；它与米点横点积染的形态及任务不同。",
    "actions": "画谱第1、9张图可见细小点成簇；王鉴例描述浓墨戳点。先安排叶/山顶组群，再用小点的疏密、大小形成层次，未获严格固定点序。",
    "appearance": "颗粒细小、不规则而密集成云簇，点间有细白；不同于小混点较宽的横向团点，也不同于米点的含水卧横叠点。",
    "application": "树冠与山顶点法的实例研究；用途由实际作品决定，不把它限定为单一树种。",
    "pitfall": "不是给墨面添加均匀噪声，也不是把米点缩小；图版黑白印刷不足以判断纸面水分的全部变化。",
    "translation": "待验证的程序化假设：在叶簇轮廓内以受锚点约束的小点群建立疏密，保留个别点；与米点保持不同的手势和叠染测试，不只改变点尺寸。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第1张五三上中、第9张中部“胡椒点”图",
        "support": "example",
        "note": ""
      },
      {
        "sourceId": "learn-leaf-pepper-example",
        "locator": "藏品正文山顶浓墨戳点的列举",
        "support": "example",
        "note": "文字支持用例，不提供全部腕法。"
      }
    ],
    "name": "胡椒点",
    "category": "vegetation"
  },
  {
    "id": "leaf-broken",
    "summary": "破笔点的画谱图表现为较粗、边缘分裂而互有叠接的点组；破笔散锋的材料状态与破墨不是同一概念。",
    "actions": "第3张左上图可见厚点、条点并组，边缘不整；静态版图不能独立证明怎样开锋或含水。抱石词条仅给破笔散锋的相关用法线索。",
    "appearance": "墨痕有不规则、分叉或破口，成组后有粗朴的密面与短小边缘点；不能据此要求每点必须有平均白丝。",
    "application": "粗朴叶群与破锋接触研究；适用位置和笔墨浓淡需继续看具名作品。",
    "pitfall": "不要把破笔点当作往规则点上随机挖洞，也不要与湿墨相破的破墨自动同义。",
    "translation": "待验证的程序化假设：把分锋后的接触束作为一次落点的多个相关痕迹，组合点群并保留手势朝向；与人为随机缺口进行并排比较，确认是否像一次真实接触。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第3张五五左上“破笔点”图",
        "support": "example",
        "note": ""
      },
      {
        "sourceId": "dpm-baoshi-cun",
        "locator": "正文“破笔散锋”",
        "support": "mentioned",
        "note": "仅关联材料/运锋语境，不是破笔点专名定义。"
      }
    ],
    "name": "破笔点",
    "category": "vegetation"
  },
  {
    "id": "vegetation-pine-needles",
    "summary": "绘画松叶在画谱图中由扇状、放射状细针组附着枝端；不能使用同名刺绣“松针”词条作为笔法依据。",
    "actions": "第1张五三左下松叶点与第3张刺松点可见针束从小组中心分出、组间搭接。静态图不证明统一从内向外或从外向内的笔序。",
    "appearance": "细长针线成束，方向有扇开和偏向，暗处针束相叠；不宜把每束做成等角、等长的完整太阳。",
    "application": "松树叶部及前景树冠组织研究；松干、枝势和针束应分别建模，再让针束服从枝端与树冠空隙。",
    "pitfall": "故宫“松针”词条实为绣法，不能把其起针中心规则移到绘画；也不要把刺松和所有松叶当成完全同一图式。",
    "translation": "待验证的程序化假设：用枝端锚点和针束主向控制放射角区间、长短和叠压；各针作为普通路径保留，先测试不规则扇束与树干的接势，再谈材质。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第1张五三左下“松叶点”；第3张五五中部“刺松点”",
        "support": "example",
        "note": ""
      },
      {
        "sourceId": "learn-leaf-pine-disambiguation",
        "locator": "全文刺绣语境",
        "support": "mentioned",
        "note": "只排除错误命中，不支持绘画针序。"
      }
    ],
    "name": "松针",
    "category": "vegetation"
  },
  {
    "id": "vegetation-bamboo-leaves",
    "summary": "竹叶图式用尖长叶笔和枝架组成疏密、俯仰不同的叶群；本轮看到了疏竹、密竹、新篁的结果差别。",
    "actions": "第4张五六可见疏竹留出明显枝架、新篁叶组较嫩细、密竹叶组互叠。未读到完整画竹用笔教程，暂不指定唯一撇叶顺序或墨阶。",
    "appearance": "叶笔有尖端和方向，组内交错、组间留空；疏密关系来自枝叶组织，不是改变全局随机数量。",
    "application": "竹枝、幼竹及密叶比较研究；适用动态如风、雨须另找对应图式证据。",
    "pitfall": "不能把竹叶写成相同菱形贴片；不能只凭“个”字类比认定竹叶与个字点完全相同。",
    "translation": "待验证的程序化假设：调用者提供竹枝骨架与叶簇朝向，逐叶配置尖入/按腹/尖出手势；分别建立疏竹与密竹布局研究，保持两者的枝架差异。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第4张五六“疏竹”“密竹”“新篁”图",
        "support": "example",
        "note": "看图所得形态与组织范围，无完整落笔教程。"
      }
    ],
    "name": "竹叶",
    "category": "vegetation"
  },
  {
    "id": "leaf-plum",
    "summary": "梅花点在所看树谱中是花簇式的叶点名称，不能直接等同于画梅花花瓣。",
    "actions": "第1、9张中叶点围聚成小花簇并沿枝连接；图中可辨多瓣/多点围攒，但印刷与重叠不支持每簇必须严格五笔的断言。树法另有梅花鼠足点题名。",
    "appearance": "小簇似花而实际组织树叶，簇与簇之间有疏密和错落；不是把红梅图章贴到树冠。",
    "application": "花簇式叶群与其他叶点组合比较；是否专用于某树种或画家的全部用法，本轮未建立。",
    "pitfall": "不要将梅花点与梅花实物写生、品字点、鼠足点混成一种固定布局；图题相邻也不等于同义。",
    "translation": "待验证的程序化假设：以小叶簇围攒作为布局研究，单叶手势保持可换，组内数目和重叠显式可编辑；未完成原典校勘前不固化“五瓣点法”。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-leaf-jiezi33",
        "locator": "第1张五三中央、第9张中下“梅花点”图",
        "support": "example",
        "note": ""
      },
      {
        "sourceId": "learn-leaf-jiezi-tree",
        "locator": "“梅花鼠足点”“梅道人喜画之”图题",
        "support": "mentioned",
        "note": "转录图题只支持语境，不能独立证明笔序。"
      }
    ],
    "name": "梅花点",
    "category": "vegetation"
  },
  {
    "id": "line-outline",
    "summary": "勾勒以笔线取物象外廓，线的位置首先承担造形。",
    "actions": "沿对象的转面与遮挡关系组织轮廓，安排起收、断续与主次；是否合拢取决于物象。",
    "appearance": "边界有轻重、藏露，转折能解释形体，内部不因平均描边而变成线框。",
    "application": "人物、花鸟及山石的结构轮廓；工笔着色前后也可有不同勾线次序。",
    "pitfall": "给任意封闭多边形描一圈等粗线，不等于已经完成勾勒造形。",
    "translation": "调用者提供结构路径及遮挡顺序，分别编辑压力和断笔；不自动闭合所有路径。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-double",
        "locator": "词条首句及左右、上下两笔说明",
        "support": "definition",
        "note": "此处绘画语境将勾勒与双钩联系；开放轮廓是本项目的可编辑表示，不冒充词条逐字定义。"
      },
      {
        "sourceId": "learn-ink-caa-figures",
        "locator": "工笔重彩画法（二）钩线",
        "support": "definition",
        "note": "教学还区分淡墨起线后重勾及勾填的流程。"
      }
    ],
    "name": "勾勒",
    "category": "linework"
  },
  {
    "id": "line-baimiao",
    "summary": "以墨线造形、不施彩色，线条本身承担形体、质感与神态。",
    "actions": "先决定主线、次线和空处；随衣褶转向变化轻重粗细，起收须与相邻线呼应。",
    "appearance": "无须设色也能分清对象和前后，线有疏密与厚薄，不能只剩空洞描边。",
    "application": "人物衣纹、花鸟和画稿；不同题材的线组织仍需分别研究。",
    "pitfall": "关闭颜色后剩下的任意轮廓图，并不会自动具备白描的表现力。",
    "translation": "先用同一组对象轮廓对照线型与疏密，验收造形可读性；不是新增一个黑白滤镜。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-baimiao",
        "locator": "完整词条定义",
        "support": "definition",
        "note": "词条给出墨线与不施色彩的边界。"
      },
      {
        "sourceId": "learn-ink-caa-figures",
        "locator": "人物画技巧——白描法画法",
        "support": "definition",
        "note": "现代教学补充粗细轻重与衣褶动感，不据其错字考证古籍。"
      }
    ],
    "name": "白描",
    "category": "linework"
  },
  {
    "id": "line-mogu",
    "summary": "本轮正式词条中的没骨，是不用墨线为骨、直接以彩色造形。",
    "actions": "以色笔的落形、叠接和空白确定物象，色面的边缘与内部变化一起承担结构。",
    "appearance": "轮廓由色与空白的相邻关系出现，没有独立墨线支架，仍须有形体与层次。",
    "application": "词条覆盖设色没骨；水墨没骨、不同画科的历史范围还需单独作品证据。",
    "pitfall": "不是删除描边后的任意色块，也不能把词条的彩色语境擅自扩展成所有墨洗。",
    "translation": "由调用者设计色面与笔路的重叠关系，检验边缘是否造形；区域填色仅是工具。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-mogu",
        "locator": "首两句技法定义",
        "support": "definition",
        "note": "这里限定直接彩色；起源叙述含相传，不改写为确定考证。"
      }
    ],
    "name": "没骨",
    "category": "linework"
  },
  {
    "id": "line-double-outline",
    "summary": "绘画双钩以两侧或上下轮廓钩合物象，与复制法帖的双钩廓填分属不同用途。",
    "actions": "先看对象两侧边缘的对应，再用两笔或分段钩合；叶脉和枝条连接另行安排。",
    "appearance": "两侧线之间的空白构成对象宽窄，合拢处自然，有正侧与遮挡。",
    "application": "多见工笔花鸟的花叶等轮廓；不能把两条平行笔路当成全部方法。",
    "pitfall": "不要把书法复制术的摹轮廓填墨流程误套到画花叶上。",
    "translation": "为一个对象保存可分别编辑的左右轮廓与共同锚点，保留交叠遮挡关系。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-double",
        "locator": "全词条，左右或上下两笔钩描合拢",
        "support": "definition",
        "note": "绘画定义；另读故宫240341条只谈法帖复制，因此不混同。"
      }
    ],
    "name": "双勾",
    "category": "linework"
  },
  {
    "id": "line-gaogu-yousi",
    "summary": "中锋笔尖圆匀细描，线似游丝，细而有力、圆润流畅。",
    "actions": "保持较小接触量，连续转向并控制起收，避免急转处突然膨大或断裂。",
    "appearance": "细密绵长、圆匀相续，曲线有牵引感；细不应弱，匀不等于机械无变化。",
    "application": "丝绢衣纹及人物装束；故宫列文人学士、贵族和仕女衣纹为常见对象。",
    "pitfall": "不要用一条无提按的矢量细线或等距平行丝束代替游丝线。",
    "translation": "比较细线在长弯、急转和不同长度上的连续性，压力小幅变化随结构而定。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-yousi",
        "locator": "全词条及《绘事雕虫》引句",
        "support": "definition",
        "note": "中锋笔尖圆匀细描及适用衣纹有明确说明。"
      }
    ],
    "name": "高古游丝描",
    "category": "linework"
  },
  {
    "id": "line-qinxian",
    "summary": "现代教学把琴弦描解释为中锋稳定、较粗劲而富韧性、偏平直挺拔的线。",
    "actions": "控制线的张力与连续性，衣褶垂落时保持主方向，转折仍依对象变化。",
    "appearance": "相对游丝更挺拔有分量，可弯而不松垮；不是必须画成绝对直线。",
    "application": "现代教材常以丝裙衣褶、垂直飘带解释；具体作品的命名仍需局部对照。",
    "pitfall": "古诀仅有画家比拟，不能据名称推断琴弦描只有一根等宽直线。",
    "translation": "固定宽度预算比较线的拉力、长短与转向，先研究主方向，再配压力。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【琴弦描】段",
        "support": "definition",
        "note": "操作说明来自未署原作者的现代转载，不能当古代唯一标准。"
      },
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀琴弦描条“如周举”",
        "support": "mentioned",
        "note": "古诀短注并未提供完整动作。"
      }
    ],
    "name": "琴弦描",
    "category": "linework"
  },
  {
    "id": "line-tiexian",
    "summary": "用中锋圆劲之笔，线圆而有骨力，形似可屈曲的铁丝。",
    "actions": "保持承墨稳定，起收与转弯有控制，长线不可在局部软塌或突然瘪细。",
    "appearance": "圆劲挺健，曲折仍有力度；圆润与刚劲同时存在。",
    "application": "人物衣纹等线描；硬质衣料的用法见现代教学，不概括全部历史对象。",
    "pitfall": "像金属线缆的多重亮暗沟槽只是材质错误，不能因像铁丝就称铁线描。",
    "translation": "先检验单条线在弯折处的稳定宽度和边缘，避免内部平行纤维把线拆成电缆。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-tiexian",
        "locator": "完整词条",
        "support": "definition",
        "note": "明确中锋圆劲；不规定数值上处处等宽。"
      }
    ],
    "name": "铁线描",
    "category": "linework"
  },
  {
    "id": "line-xingyun-liushui",
    "summary": "现代释义强调线如云舒卷、似水转折不滞，长线有起倒与流动节奏。",
    "actions": "沿衣褶走势连贯转折，舒展与收束交替，起收及粗细变化服从整组线的势。",
    "appearance": "线组流畅而有方向，疏密与长短形成动势，不是重复波浪。",
    "application": "飘带、旗帜及柔软衣衫是现代教学的常见例子。",
    "pitfall": "把任意路径整体平滑或加正弦波，不能自动得到行云流水的线意。",
    "translation": "先设计对象的流向与成组长线，再考察曲率和压力在转折处是否连贯。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【行云流水描】段",
        "support": "definition",
        "note": "正文有操作性说明与二手古文引句，未与原书影印校勘。"
      },
      {
        "sourceId": "learn-line-dpm-eighteen",
        "locator": "名单第四项",
        "support": "mentioned",
        "note": "官方词条确认名称，不单独定义。"
      }
    ],
    "name": "行云流水描",
    "category": "linework"
  },
  {
    "id": "line-mahuang",
    "summary": "故宫对马和之的说明强调较短促、运笔迅疾的线，兰叶描在此发生变化，后称蚂蝗描。",
    "actions": "用短促而有起伏的提按形成伸曲，控制重按处不过肥，线与线保持疏密呼应。",
    "appearance": "粗细有变化，伸曲灵动而不臃肿，不宜画成两端钝圆的小虫印章。",
    "application": "人物衣纹；需联系马和之具体作品，而非给所有兰叶形线统一改名。",
    "pitfall": "现代转载把蚂蝗与兰叶完全等同、或直接归吴道子创制，不能据此消去差异。",
    "translation": "比较短线的按提位置、长短及群组方向，保留与长兰叶线的独立样片。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-mahezhi",
        "locator": "人物笔法段：线条短促、运笔迅疾、兰叶描的变化",
        "support": "definition",
        "note": "支持该画家语境的笔法解释，不推广为所有时代统一规范。"
      },
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【蚂蝗描】段",
        "support": "definition",
        "note": "其归属与别名概括较宽，作为需辨别的现代说法。"
      }
    ],
    "name": "马蝗描",
    "category": "linework"
  },
  {
    "id": "line-dingtou-shuwei",
    "summary": "起笔顿挫如钉头，收笔尖细似鼠尾，重起与渐提形成强烈方向。",
    "actions": "在起端集中按顿，再顺衣纹行笔逐渐提收；头与尾须通过中段连成一势。",
    "appearance": "头重尾轻、起处有骨、末端收尖，不能只是大圆头接细线。",
    "application": "人物衣纹褶皱；重起位置应解释衣褶受力和转向。",
    "pitfall": "不能每条线都盖同样大小的黑点，或把这一压力曲线当成所有书画起收。",
    "translation": "给起端保留局部压力与方向变化，中段可编辑；做正向、斜向和折返对照。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-cun-dingtou-disambiguation",
        "locator": "完整词条，起笔顿挫、收笔尖细",
        "support": "definition",
        "note": "起收形态有直接释义。"
      }
    ],
    "name": "钉头鼠尾描",
    "category": "linework"
  },
  {
    "id": "line-hun",
    "summary": "本轮现代教学把混描解释为淡墨勾皴衣纹后，以较浓墨醒线并使线面交融。",
    "actions": "先用淡墨定结构，再有选择地以浓墨醒出重点；线与染面连接而非整幅等量复描。",
    "appearance": "淡线、浓线与局部墨面互相衔接，仍能读出衣纹和层次。",
    "application": "人物衣纹及线面结合；现代教程中的用法不能直接当成古诀完整原义。",
    "pitfall": "混描不应解释成随机混合多种笔刷；古诀“人多描”等短注不能单独证明叠墨流程。",
    "translation": "保存两阶段笔路与遮罩，比较哪些线醒出、哪些留淡；区分时间先后与单笔材质。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【混描】整段",
        "support": "definition",
        "note": "提供具体操作，但属来源未明的现代教学。"
      },
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀混描短注",
        "support": "mentioned",
        "note": "文本不足以校定同名操作范围。"
      }
    ],
    "name": "混描",
    "category": "linework"
  },
  {
    "id": "line-juetou",
    "summary": "古诀以秃笔说明橛头丁；现代教学强调短粗、苍老有骨力的线。",
    "actions": "以较钝接触落笔，控制中段强转折和略收尾，短线仍需完整走势。",
    "appearance": "端头偏钝、线短而壮，中部转折明确；不是钉头鼠尾的简单截短。",
    "application": "人物较硬衣褶的现代练习；名称有橛头丁、橛头钉、撅头丁等写法。",
    "pitfall": "不能只用方头盖章；旧目录“撅头钉”应同时保留其他写法供检索。",
    "translation": "分别控制起端接触、中段转角和末端收缩，比较它与尖尾线的结构差异。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀橛头丁条“秃笔也”",
        "support": "definition",
        "note": "短注支持秃笔，完整形态仍依现代解释。"
      },
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【橛头钉描】段",
        "support": "definition",
        "note": "短粗、老硬和中段转折来自现代教学；画家归属不作定论。"
      }
    ],
    "name": "撅头钉描",
    "category": "linework"
  },
  {
    "id": "line-caoyi",
    "summary": "常见现代释义重在稠密紧贴身体的衣纹，即“曹衣出水”；这同时涉及线组与服饰结构。",
    "actions": "沿躯体弧面密布细劲衣褶，控制线间距与遮挡，使衣褶贴体而非平均铺线。",
    "appearance": "衣纹重叠、紧窄、似湿衣贴身；不是一条单笔便能独立识别的线型。",
    "application": "佛像等人物服饰的特定造形语境；不能把它当作真的画水或晕湿效果。",
    "pitfall": "所读古诀转录写曹魏不兴，现代教学多指北齐曹仲达；人名归属和图式传承不能据转载统一。",
    "translation": "研究依人体曲面的线场、密度和重叠关系；不设计名为曹衣的通用压力预设。",
    "confidence": "disputed",
    "evidence": [
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀曹衣描条",
        "support": "mentioned",
        "note": "转录归属与后代常见解释不一致，尚无原刻本校勘。"
      },
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【曹衣描】段",
        "support": "definition",
        "note": "可学习贴体稠叠的现代解释，不能据此裁定历史归属。"
      }
    ],
    "name": "曹衣描",
    "category": "linework"
  },
  {
    "id": "line-zhelu",
    "summary": "尖细长笔中有强转折，像芦叶折转；方折需与连贯走势并存。",
    "actions": "细入细收，中段随衣褶加按换向，控制折处力度；可有淡墨辅助而不淹没线骨。",
    "appearance": "线有棱角、削劲而不僵断，尖细两端与中段转折相连。",
    "application": "人物衣纹的硬折和受力处；具体线组依人体姿态组织。",
    "pitfall": "保留所有多边形尖角或将每条线折成同一角度，都不等于折芦描。",
    "translation": "把转角位置、角部压力及两端收束分开编辑，避免全局平滑抹掉折处。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀折芦条“尖笔细长”及撇捺短注",
        "support": "definition",
        "note": "转录有字形讹误，取其尖细长笔信息。"
      },
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【折芦描】段",
        "support": "definition",
        "note": "细端、增强压力与转折关系来自现代释义。"
      }
    ],
    "name": "折芦描",
    "category": "linework"
  },
  {
    "id": "line-ganlan",
    "summary": "现代释义强调起讫极轻、两端细而中段沉着，线形如橄榄。",
    "actions": "轻入，逐渐按重再提收；中段可转向或颤动，不只是一块对称的椭圆。",
    "appearance": "两端收束、中间有分量，宽窄变化沿实际行笔发生。",
    "application": "人物衣纹中需要有分量的短长线；不能按一个形状套遍所有衣褶。",
    "pitfall": "与枣核描在所读材料中形近且有互释，尚不能建立固定长宽比的历史分界。",
    "translation": "研究压力峰的位置、偏斜和转折；把与枣核的差别保留为待对照问题。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【橄榄描】与【枣核描】两段对读",
        "support": "definition",
        "note": "有可观察起收描述，但差异只属现代概括，未得统一古法界限。"
      },
      {
        "sourceId": "learn-line-dpm-eighteen",
        "locator": "名单第11项",
        "support": "mentioned",
        "note": "确认名称在十八描名单。"
      }
    ],
    "name": "橄榄描",
    "category": "linework"
  },
  {
    "id": "line-zaohe",
    "summary": "古诀称尖大笔；现代教学以较短促、中段圆浑鼓起的线解释枣核描。",
    "actions": "尖笔入纸后按开，中间转折顿挫须圆浑，再收回细端；按提随线势而变化。",
    "appearance": "中段鼓起但不肿胀，短线可有转折，不应只有相同的枣核轮廓。",
    "application": "现代教学常以麻布衣纹作例；材质判断仍要看成组线和作品。",
    "pitfall": "和橄榄描有大量交叉，不把两端尖、中间宽这一共同特征冒充已分清两法。",
    "translation": "保留笔头大小与局部转折参数，做同题对照；暂不硬编码所谓唯一枣核曲线。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀枣核条“尖大笔也”",
        "support": "definition",
        "note": "短注只有笔具特征。"
      },
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【枣核描】段",
        "support": "definition",
        "note": "鼓起、短促及衣料说明来自现代教学。"
      }
    ],
    "name": "枣核描",
    "category": "linework"
  },
  {
    "id": "line-liuye",
    "summary": "现代教学以轻盈、尖收且有提按的柳叶形线解释此描法，长短与兰叶等存在相邻用法。",
    "actions": "轻入，局部按开后提出，线的方向随衣褶；避免重砸成钉头。",
    "appearance": "细端与中段宽面衔接，线显灵动；群内长短粗细须有变化。",
    "application": "柔软轻薄衣褶的现代练习；不是植物叶片生成器。",
    "pitfall": "柳叶、兰叶、竹叶名称交叉，不能用单一长宽阈值宣布历史分类完成。",
    "translation": "在同一衣褶结构上比较压力峰与长短，观察组合是否轻而有骨。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【柳叶描】段",
        "support": "definition",
        "note": "现代教学解释，范围不能外推为全部时代。"
      },
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀柳叶描条",
        "support": "mentioned",
        "note": "画家比拟不是完整操作定义。"
      }
    ],
    "name": "柳叶描",
    "category": "linework"
  },
  {
    "id": "line-zhuye",
    "summary": "古诀短注为肥短撇捺；现代教学强调横卧用笔及粗细起伏。",
    "actions": "以短促撇捺形成按开再提出的线，方向、轻重和长短服从衣纹组织。",
    "appearance": "短而有宽面、端部收束，数笔之间有向背，不是重复叶形贴片。",
    "application": "人物衣纹线组；虽然借墨竹笔意，衣纹竹叶描与画竹叶仍是两个层级。",
    "pitfall": "不能把植物竹叶图章搬到人物轮廓上，就声称完成竹叶描。",
    "translation": "复用按提的单笔工具，但由人物结构决定锚点、向量和疏密，另作衣纹验收。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀竹叶描“笔肥短”撇捺说明",
        "support": "definition",
        "note": "古诀短注可核对基本笔性。"
      },
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【竹叶描】段",
        "support": "definition",
        "note": "近邻叶形描法的差异含轻重刚柔长短，不是硬性几何模板。"
      }
    ],
    "name": "竹叶描",
    "category": "linework"
  },
  {
    "id": "line-zhanbi-shuiwen",
    "summary": "战笔描呈颤动屈曲而瘦硬有力；十八描名单又称战笔水纹描。",
    "actions": "在主要衣纹方向上加入受控颤掣，颤动与转折、提按相连，不破坏主线。",
    "appearance": "线有细小屈曲与内在张力，远看仍是一条有势的衣纹；不是无向白噪声。",
    "application": "人物衣纹；与颤笔、金错刀的联系有馆方文字说明，但不可将各名称全部无条件合并。",
    "pitfall": "每个采样点独立抖动会变成锯齿，整条周期波浪也缺少战掣的局部变化。",
    "translation": "先保留主路径，再用局部相关、有限幅度的偏移研究颤掣；同时检验缩略图的造形。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-zhanbi",
        "locator": "全词条，瘦硬战掣及颤动屈曲解释",
        "support": "definition",
        "note": "直接释义战笔描；完整名称由十八描名单核对。"
      },
      {
        "sourceId": "learn-line-dpm-eighteen",
        "locator": "名单第15项",
        "support": "mentioned",
        "note": "确认战笔水纹描这一名称。"
      }
    ],
    "name": "战笔水纹描",
    "category": "linework"
  },
  {
    "id": "line-jianbi",
    "summary": "减笔是相对细笔的疏体，力求单纯简括、以貌取神，首要任务是概括对象。",
    "actions": "先取主线和大墨势，省略次要细节；用少量关键笔交代姿态、结构和神情。",
    "appearance": "笔少而对象仍成立，线面可结合，虚处与实处各有作用。",
    "application": "写意人物；不同对象的关键线不同，不能固定删掉某几类轮廓。",
    "pitfall": "减少随机笔数、加快播放、降低采样分辨率都不是减笔造形。",
    "translation": "由创作者选择结构要点与省略关系，再用普通笔触实现；验收对象可读性而不是笔数。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-line-dpm-jianbi",
        "locator": "完整词条",
        "support": "definition",
        "note": "正式释义是疏体与概括。"
      },
      {
        "sourceId": "learn-ink-caa-figures",
        "locator": "人物画技巧——写意画法",
        "support": "definition",
        "note": "现代教学明确主线、大墨面与次线的顺序，避免琐碎笔墨。"
      }
    ],
    "name": "减笔描",
    "category": "linework"
  },
  {
    "id": "line-kuchai",
    "summary": "古诀以粗大减笔解释柴笔；现代教学强调较多渴笔、偏侧接触和分叉断白。",
    "actions": "在主要衣褶走势上用较干、较宽接触形成顿挫，干处保留骨力而不任意散乱。",
    "appearance": "苍涩、分叉露纸，线组简括有结构；枯处与实墨相互衬托。",
    "application": "写意人物衣纹的苍老质感，所读材料将其视为与减笔接近的笔性。",
    "pitfall": "把整幅图统一加干燥噪声，或把断裂做成均匀栅格，不会得到枯柴描。",
    "translation": "沿笔势控制束的聚散及断白，在同一衣褶上比较干湿组织；不新增泛用纹理标签。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-shijingshan",
        "locator": "古诀柴笔描“粗大减笔也”",
        "support": "definition",
        "note": "确认与减笔的关系，非完整材料操作。"
      },
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【枯柴描】段",
        "support": "definition",
        "note": "渴笔、偏锋与飞白来自现代教学。"
      }
    ],
    "name": "枯柴描",
    "category": "linework"
  },
  {
    "id": "line-qiuyin",
    "summary": "现代释义强调融入篆书的圆匀遒劲，线外柔而内有骨力，并非模拟蚯蚓身体。",
    "actions": "以圆笔连贯行走，转折保持内在承接；适度控制宽度，避免松软臃肿。",
    "appearance": "圆浑、有韧性，曲而不瘫；线能交代人物结构而不沦为装饰蛇纹。",
    "application": "人物衣纹的圆劲线组；并非所有圆转线都可直接同名。",
    "pitfall": "同名比喻不等于画虫形，连续管状阴影和任意扭曲尤其容易误导。",
    "translation": "比较圆转处的压力延续、弧长节奏和线组连接，先消除线缆式材质再谈风格。",
    "confidence": "partial",
    "evidence": [
      {
        "sourceId": "learn-line-modern-eighteen",
        "locator": "【蚯蚓描】段",
        "support": "definition",
        "note": "现代教学的篆书笔法解释；没有据此认定古法唯一形状。"
      },
      {
        "sourceId": "learn-line-dpm-eighteen",
        "locator": "名单第18项",
        "support": "mentioned",
        "note": "正式词条只确认分类名称。"
      }
    ],
    "name": "蚯蚓描",
    "category": "linework"
  },
  {
    "id": "eight-ce",
    "summary": "永字八法的侧是点法名称，不能当作侧锋的同义词。《书法钩玄》解释其顾右、取侧险之势，以揭腕、轻蹲、顿挫和借势出之构成一个短小过程；这是八法语境中的点，不是所有点的唯一写法。",
    "actions": "依所读章先取顾右的入势，轻蹲锋后借短促顿挫整势，再轻揭出。将入纸方向、点腹受压和出口分别检查；与后续勒的关联也要记录，不把侧点孤立成没有下一笔的小黑块。",
    "appearance": "观察点的重心、顾向与露藏，短小形体是否有起落和内在方向。侧险不是越歪越好，棱利也不是外加尖刺；若平卧成一团或过急失中，均与章中限制不符。",
    "application": "先作为永字或相应楷书点画的学习入口，再比较其他书体的点。陈绎曾列点的偃仰向背，说明点的变化远超一种侧点；八法侧不能覆盖山水点苔、点叶等所有点法。",
    "pitfall": "把名称侧直接映射为软件的 side-brush，或只用一枚倾斜椭圆表达点，会跳过起按出及顾盼。八法并非所有书法点画的完备分类，起源也不可凭传承故事确证。",
    "translation": "算法假设：建立短笔路的顾右入势、腹部接触和借势出口三段，并在组字中保留下一横的承接目标。这里只探索本章侧点的程序近似，不能用一个侧锋参数替代，也不宣称八法普遍适用于所有书体。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "本章“侧不得平其笔”及“不言点而言侧”问答",
        "support": "definition",
        "note": "明确侧点顾右、揭腕蹲锋及借势出，区别于泛称侧锋。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·侧”",
        "support": "example",
        "note": "列多种点势，限制单一形状模板。"
      },
      {
        "sourceId": "learn-shufa-baijiao",
        "locator": "第五讲中永字八法的历史与分类讨论",
        "support": "mentioned",
        "note": "八法仅作教学线索；传承及完备性不可直接确认。"
      }
    ],
    "name": "侧（点）",
    "category": "calligraphy"
  },
  {
    "id": "eight-le",
    "summary": "勒以横画为学习对象，但重点是如勒缰般控制行势，而不是画一条水平直杠。《书法钩玄》强调不得卧、笔心压与劲涩；《翰林要诀》还说横画有仰偃变化，尾部处理随燥湿有别。",
    "actions": "先整理入势，以笔心控制前进，在横向运行中保持提按与轻微仰偃，再根据含墨状态收尾。起、行、收均需有来去；练习不同长度和位置的横，不把同一压力曲线等比例拉长。",
    "appearance": "观察横画是否稳而能动，宽度和弧势能否呼应左右，尾端是否有控制。不得卧不等于把横画整体倾成陡坡；劲涩也不是一串颤线，等宽、等长、并列算子状应作为反例比较。",
    "application": "适合研究楷书横画的受力与收束，再对照隶书横和波画的差异。同字有多横时需顾其位置、长短和向背，不能把一种勒的模板复制成全部横画，更不能包办所有书体。",
    "pitfall": "把勒直接等同水平长方形、固定两端粗中间细，或将古书的勒马比喻当作可测拉力公式。另一误区是湿干两种尾部一律处理，忽略所读文献明示的材料条件。",
    "translation": "算法假设：横画保存局部仰偃、压力和收束条件，以同长度的直杠、提按横、含仰偃横作对照。含墨状态可作为独立输入，但材料规则尚待实验，不能由勒字比喻推算真实受力数值。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“勒不得卧”及“不言画而言勒”问答",
        "support": "definition",
        "note": "将勒与受控横行、劲涩联系。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第六·平法·勒”及横画忌如算子",
        "support": "definition",
        "note": "提供仰偃和干湿尾部差异，不把描述换成统一压力公式。"
      }
    ],
    "name": "勒（横）",
    "category": "calligraphy"
  },
  {
    "id": "eight-nu",
    "summary": "努在八法中指向有力的竖势。《书法钩玄》反对僵直，要求微曲、徐行、近左引势，同时反对滞散；其不宜直是运势要求，不能解释成所有竖都应明显弯曲。目录另收弩字作异写，不据此另造一笔。",
    "actions": "整锋起下，在竖行中维持受控接触和轻微势向，再依据是否接趯安排末段。比较直中寓曲、僵直与夸张弯曲，检查前后力向；长短肥瘦需随字中位置，不按固定比例绘制每根竖。",
    "appearance": "观察竖段是否能撑住结构，微曲是否自然，重处是否仍能向下推进。轮廓笔直也可能含有内在势向，明显弧线也可能软弱；不能只以曲率阈值判断努是否有力。",
    "application": "用于永字竖钩主体及相关竖势的学习，对照垂露、悬针等不同收束。竖笔的具体末段须放回字形判断；不是每一根竖都应接钩，也不是每一种书体都按楷书竖处理。",
    "pitfall": "把不宜直变成强制香蕉形，或用竖直粗柱代替运势，均误读文本。将徐行理解成无控制的停拖，容易在中段堆墨；把努、垂露、悬针统成同一端帽，也抹去了收束差异。",
    "translation": "算法假设：独立编辑竖行中的微曲势向、压力分布与末段连接，保留直、微曲、过曲三组对照。是否接钩由部件结构指定；几何曲率和线宽只能描述外形，不能自动标定笔力。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“努不宜直”及“竖笔徐行，近左引势”",
        "support": "definition",
        "note": "微曲和不滞需并读，不支持任意大弯。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第七·直法·努”及末句肥瘠长短随偏旁",
        "support": "example",
        "note": "支持竖势服从字形位置，非统一比例。"
      }
    ],
    "name": "努（竖）",
    "category": "calligraphy"
  },
  {
    "id": "eight-ti",
    "summary": "趯在八法中研究从前段收势中蹲挫、借势而出的钩。《书法钩玄》明确趯自努出，并非孤立附在竖尾的尖三角；钩出的方向和幅度需与前段及字内关系相接，不是所有书体钩的通式。",
    "actions": "竖行到收处先整锋、略蹲挫取得支点，再转笔提锋借势出去，保留短而明确的出口。比较蓄势后出、直接拐出和尾部另贴钩三个版本，检查钩与竖段是否为同一运行过程。",
    "appearance": "关注钩根与竖段是否贯通，钩尖是否有明确去向，蓄势处是否臃肿。短钩可锐利而不薄弱，出口长短不能统一；尖形外观本身不说明是否有蹲挫和借势。",
    "application": "先用于永字竖钩与相关楷书钩势的比较，再研究别的书体。乙瑛碑教学特别说明隶钩转而无挑、形势较平长，提示不能用永字钩模板覆盖所有历史钩画。",
    "pitfall": "在竖的末端画一条细V形或附加三角，容易成为拼接符号；钩根堆大黑团也未必有蓄势。更不能因名称同为钩，就忽略篆隶楷行以及字形位置的差异。",
    "translation": "算法假设：将竖尾、局部蹲挫和提锋出口存成连续笔路，允许连接区压力与接触方向共同变化。作为八法语境下的钩势候选，不设全书体通用钩；几何连续也仍需墨迹与范字验证。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“趯须蹲锋得势而出”及“趯自努出”",
        "support": "definition",
        "note": "强调与前画的关系、潜锋轻挫及转笔出锋。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·趯下”",
        "support": "example",
        "note": "以末蹲提趯、抱身贵短描述相近过程。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“基本点画的运笔（二）·钩”",
        "support": "example",
        "note": "隶钩的转而无挑是避免跨书体套模的重要反例。"
      }
    ],
    "name": "趯（钩）",
    "category": "calligraphy"
  },
  {
    "id": "eight-ce-lift",
    "summary": "目录以策对应提画作学习入口。《书法钩玄》说仰锋、轻抬而进并向右取势；但《翰林要诀》策条出现圆锋左出的文字。两页转录未校勘，不应默改其中一处，也不能宣称所有策都只有一个方向。",
    "actions": "先在钩玄语境中练筑锋后仰策、轻抬推进，观察接触由实转轻和右向出口。同时将翰林要诀的左出表述保留为待考，不直接据此另造相反模板；需核版本及其所指笔形后再决定能否合并。",
    "appearance": "候选观察是短横斜向上引出的势、起处的支点及尾部渐轻，而不是简单画一条向右上的尖线。若文本指代的笔形不同，外观也可能不同；本轮尚未以原刻图或对应字形解决该差异。",
    "application": "可在永字提画或相关上提笔势中进行局部比较，必须注明采用的文本。策与提笔的动作层级不同：前者是具名点画门类，后者能发生于多种笔画，不能将两条目录直接合并。",
    "pitfall": "把提等同压力一直下降，或把名称策固定为屏幕右上方向，都忽略起势和坐标语境。更严重的是挑选方便的文献、静默纠正另一处左右字，使尚有争议的转录看起来完全一致。",
    "translation": "算法假设：暂以钩玄右向仰进作为注明出处的一组候选，开放入势、斜度与接触递减。左出文字只保存为研究注释，未核版本前不作标准化规则；任何样片都不代表争议已解决。",
    "confidence": "disputed",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“策须斫笔”至“仰笔趯锋，轻抬而进”",
        "support": "definition",
        "note": "本章有揭腕趯势于右的表述，支持注明出处的右向仰策候选。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·策”，“圆锋左出”",
        "support": "definition",
        "note": "与常用右向提画解释有方向差异；未校勘，不判断究竟是异义、异本还是转录问题。"
      },
      {
        "sourceId": "learn-shufa-baijiao",
        "locator": "永字八法的分类与局限讨论",
        "support": "mentioned",
        "note": "支持不把八法名称绝对化为覆盖全书法的固定模板。"
      }
    ],
    "name": "策（提）",
    "category": "calligraphy"
  },
  {
    "id": "eight-lue",
    "summary": "目录用掠对应长撇作学习入口。《书法钩玄》明说锋左出、微曲且疾徐有准；但《翰林要诀》掠条为右出微仰。二者所指是否一致、是否存在转录问题，本轮未核清，不能把长撇长度当成唯一确定定义。",
    "actions": "依钩玄先整势轻驻，再微曲向左送出并逐渐收锋，保持迅出与控制相兼。将其与短促啄势比较，研究两者不同的蓄送过程；翰林要诀的右出原句另记待考，不自行翻转以制造一致。",
    "appearance": "观察较长弧势的中段支撑、由实到轻的出口及尖锋是否仍有去向。掠不是把短撇等比例拉长；过弯易软，单纯直削又可能缺少势向，具体变化必须与字内空间比较。",
    "application": "先用于永字长撇及注明文献的左出候选，再比较其他书体和结构位置。不能把所有左下长线都认作同一种掠；外形相近的山水线也不因此自动取得书法掠的传统名称。",
    "pitfall": "用长短阈值把撇分成掠和啄，忽略起势、曲势和送出节奏；或为了统一资料而改掉左右异文。现代网页也会混淆方向，必须回到实际正文和可核作品，不能累计搜索命中数充当定论。",
    "translation": "算法假设：以钩玄左出候选保存轻驻、微曲、提锋出口三段，同时开放长短和弧度。将不同文献的解释保持可追溯，未核异文前不合并成唯一掠算法；几何增长不等于掌握掠势。",
    "confidence": "disputed",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“掠者，拂掠须迅”及“不言分发而言掠”的问答",
        "support": "definition",
        "note": "正文重复左出，并强调微曲、疾徐有准。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·掠”，“右出微仰”",
        "support": "definition",
        "note": "保留方向差异，尚不能判断古义差别或转录错误。"
      }
    ],
    "name": "掠（长撇）",
    "category": "calligraphy"
  },
  {
    "id": "eight-zhuo",
    "summary": "啄以禽鸟啄物比喻短促而轻劲的落出，目录暂以短撇作为入口。《书法钩玄》强调及纸、下罨、借势与不可缓滞；《翰林要诀》写点首撇尾左出。它不只是缩短的掠，更不是一个静态尖三角。",
    "actions": "在很短距离内完成落纸、局部蹲锋和借势送出，比较入点重心与短尾的关系。迅捷不能省去控制；先掌握清楚的落出次序，再改变角度和长度，不用延长暂停来制造沉重。",
    "appearance": "关注头部着力、尾部轻劲及短小形体内明确的方向变化。短不等于粗圆，快不等于破碎；若只是从宽截面突然切成尖端，没有连贯出势，应与有效候选区分。",
    "application": "用于永字短撇和相关短促左出笔势的对照。放入不同字形时比较顾向与空间，不将所有短线、飞鸟或树叶都命名为啄；相似动作跨题材使用时仍要另行验证范围。",
    "pitfall": "将长撇等比缩小只会改变尺寸，未必改变节奏和接触次序。以极低采样制造棱角、或在尾端硬切透明，同样是几何伪差，不能当作迅疾有力的证据。",
    "translation": "算法假设：用短笔路上的集中落势与迅速提收形成候选，与同尺度匀速撇、缩小长撇比较。保留独立压力曲线和出口方向；视觉差异可用于学习，不足以确认已复原古代用腕。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“啄者，如禽之啄物也”及“啄以轻劲为胜”",
        "support": "definition",
        "note": "支持短促轻劲和不能缓滞的动作语境。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·啄”",
        "support": "definition",
        "note": "有点首、撇尾、左出的简要描述。"
      }
    ],
    "name": "啄（短撇）",
    "category": "calligraphy"
  },
  {
    "id": "eight-zhe",
    "summary": "磔在所读八法章中与右送之波相关，经过入势、徐行、驻转、轻揭而展开收出。《翰林要诀》另说偏蹲偏驻、疾过缓出。两者共同指向有阶段变化的捺势，不能简化为一个斜向右下的粗三角。",
    "actions": "先紧筑而微仰，随主势下行并逐步组织接触，力势充足后驻转、轻揭送出；具体快慢与藏露须按采用的段落说明。练习渐按至脚、再渐提的关系，不把所有阶段压成单一线宽坡度。",
    "appearance": "观察捺身、捺脚与出口是否连贯，展开处是否有支撑且不肥滞。锋芒可露可藏，尾端不要求统一燕尾；厚实与舒展应同时检查，尖端长度也须与字内其他笔画相衡。",
    "application": "用于八法捺势与具体楷书点画的比较，再与乙瑛碑波捺对照。隶书雁尾的展开位置和形态随结体改变，不能凭右向展开的相似外形就把所有波捺视为同一个模板。",
    "pitfall": "从起点到终点一味加宽、末尾突然归零，会像楔形贴片；给每一捺同样夸张的脚，也忽略结构。个别现代网页把方向写成左下与本段右送冲突，本记录不沿用该疑似笔误。",
    "translation": "算法假设：分别保存入势、主体展开、局部驻转与尾部提收，并让藏露可选；以同墨量的楔形填充作为反例。保留楷书候选和隶书波捺的不同数据，等待具体范字比较，不宣称统一复原磔法。",
    "confidence": "supported",
    "evidence": [
      {
        "sourceId": "learn-shufa-guxuan-bafa",
        "locator": "“磔者”段、“右送之波皆名磔”及末尾笔诀",
        "support": "definition",
        "note": "支持右送、徐行、驻揭及藏露可选；罕用转录字不据此细推。"
      },
      {
        "sourceId": "learn-brush-hanlin",
        "locator": "“第八·圆法·磔”",
        "support": "definition",
        "note": "提供偏蹲偏驻、疾过缓出的另一段节奏表述，不合并为固定数值。"
      },
      {
        "sourceId": "shufa-yiying-study",
        "locator": "“注意要点·波捺”及“基本点画的运笔（二）·捺”",
        "support": "example",
        "note": "现代汉碑教学说明波捺随结体变化，防止跨书体统一套用。"
      }
    ],
    "name": "磔（捺）",
    "category": "calligraphy"
  }
]);
function getTechniqueReading(id) {
  return TECHNIQUE_READINGS.find((note) => note.id === id);
}

// src/technique-catalog.ts
var TECHNIQUE_CATEGORIES = Object.freeze([
  Object.freeze({ id: "brushwork", name: "基础用笔", description: "笔锋、起收、提按、转折与运行节奏。" }),
  Object.freeze({ id: "ink", name: "墨态与墨法", description: "墨色、干湿与叠加组织；墨态不等于墨法。" }),
  Object.freeze({ id: "water", name: "水法", description: "水与墨的先后、相破和区域组织；当前尚无流体模拟。" }),
  Object.freeze({ id: "texture", name: "山石皴法", description: "顺山石结构组织线、面、点；同名可有流派差异。" }),
  Object.freeze({ id: "vegetation", name: "树叶与苔点", description: "点叶、夹叶、针叶与苔点的组织方法。" }),
  Object.freeze({ id: "linework", name: "勾描与衣纹", description: "造形方法和线描类型；十八描是一组历史分类。" }),
  Object.freeze({ id: "calligraphy", name: "书法笔画", description: "先以永字八法整理单笔动作，不把书体当作笔法。" })
]);
var INITIAL_TECHNIQUE_SOURCES = Object.freeze([
  Object.freeze({
    id: "dpm-wangshen-yucun",
    title: "王诜渔村小雪图卷",
    institution: "故宫博物院",
    url: "https://www.dpm.org.cn/collection/paint/234606.html",
    accessed: "2026-11-19",
    supports: "馆藏说明实际描述侧锋短笔勾皴、清水向内化开轮廓的破墨，以及中锋浓墨寒林；仅支持该作品的观察范围。"
  }),
  Object.freeze({
    id: "dpm-baoshi-cun",
    title: "抱石皴",
    institution: "故宫博物院",
    url: "https://www.dpm.org.cn/lemmas/239776.html",
    accessed: "2026-11-19",
    supports: "说明破笔散锋、聚散分明；提及披麻、解索、乱柴、牛毛、卷云、折带的融合，不分别定义这些皴法。"
  }),
  Object.freeze({
    id: "dpm-pima",
    title: "披麻皴",
    institution: "故宫博物院",
    url: "https://www.dpm.org.cn/lemmas/240232.html",
    accessed: "2026-11-19",
    supports: "实际读取词条正文；亦称麻皮皴，如麻披散、错落交搭，并比较董源偏短与巨然偏长。未以此替代作品笔触的局部对照。"
  }),
  Object.freeze({
    id: "dpm-axe",
    title: "斧劈皴",
    institution: "故宫博物院",
    url: "https://www.dpm.org.cn/lemmas/243376.html",
    accessed: "2026-11-19",
    supports: "实际读取词条正文；以毛笔横扫及坚硬石质说明这种皴法，不能把全部斧劈皴解释成纵向长黑条。"
  }),
  Object.freeze({
    id: "dpm-shan-ink-survey",
    title: "单国强《水墨画技法综述》首段单页",
    institution: "故宫博物院",
    url: "https://www.dpm.org.cn/Uploads/pdf/1882/T00047_00.pdf",
    accessed: "2026-11-19",
    supports: "可访问文件为印刷页47单页，仅实读首段；列用笔、用墨与用水方法，无逐项定义，不能据此声称已读完整文章。"
  }),
  Object.freeze({
    id: "shufa-shupu",
    title: "孙过庭《书谱》公开转录及今译",
    institution: "书法空间公开文献页",
    url: "https://www.9610.com/lilun/sgt.htm",
    accessed: "2026-11-19",
    supports: "原文“执使转用”及点画内的起伏衄挫；网页同时有今译，属于公开转录，未作版本校勘。"
  }),
  Object.freeze({
    id: "shufa-xushupu",
    title: "姜夔《续书谱》节录、图解及译文",
    institution: "书法空间公开文献页",
    url: "https://www.9610.com/lilun/jiangkui.htm",
    accessed: "2026-11-19",
    supports: "原文谈笔锋在画中、转折停驻、迟速露藏及悬针垂露；网页为节录并含后人图解译注，未作版本校勘。"
  }),
  Object.freeze({
    id: "shufa-yiying-study",
    title: "怎样临习《乙瑛碑》",
    institution: "书法空间转载，页末注明摘自《中国书画报》",
    url: "https://www.9610.com/qinhan/yyb/01.htm",
    accessed: "2026-11-19",
    supports: "现代临习教学文章，实读起收、提按和转折操作；可辅助解释逆入、回锋、转折及波捺，不是汉代作者自述。"
  })
]);
var TECHNIQUE_SOURCES = Object.freeze([
  ...new Map([...INITIAL_TECHNIQUE_SOURCES, ...TECHNIQUE_READING_SOURCES].map((source) => [source.id, source])).values()
]);
var surveyMentions = /* @__PURE__ */ new Set([
  "brush-follow",
  "brush-against",
  "brush-fast",
  "brush-slow",
  "brush-dot",
  "brush-scrub",
  "ink-light",
  "ink-scorched",
  "ink-dry",
  "ink-wet",
  "ink-layered",
  "ink-splashed",
  "ink-rendering",
  "ink-background-wash",
  "ink-supporting-wash",
  "water-soak",
  "water-collision",
  "water-texture",
  "water-sprinkle",
  "water-break-ink",
  "ink-break-water"
]);
var firstBatchIds = /* @__PURE__ */ new Set(["brush-center", "brush-side", "brush-lift", "brush-press", "ink-dry", "ink-wet", "ink-flying-white"]);
var firstBatchReview = Object.freeze({
  status: "user-approved",
  batch: "foundation-batch1",
  date: "2026-11-19",
  scope: "首批回修前归档的弧线样片效果获用户认可；不扩为本轮回修材质、所有笔路、真实毛笔动作或传统技法全义的验收。",
  record: "docs/foundation-review.md"
});
function entry(id, name, category, level, target, options = {}) {
  const reading = getTechniqueReading(id);
  if (!reading) throw new Error(`Missing technique reading: ${id}`);
  const previousSources = options.sourceIds ?? (surveyMentions.has(id) ? ["dpm-shan-ink-survey"] : []);
  const sourceIds = [.../* @__PURE__ */ new Set([...previousSources, ...reading.evidence.map((item) => item.sourceId)])];
  const previousStatus = options.evidence ?? (surveyMentions.has(id) ? "mentioned" : "pending");
  const hasDefinition = reading.confidence === "supported" && reading.evidence.some((item) => item.support === "definition");
  const evidenceStatus = hasDefinition ? "definition" : previousStatus === "pending" && sourceIds.length ? "mentioned" : previousStatus;
  return Object.freeze({
    id,
    name,
    aliases: Object.freeze([...options.aliases ?? []]),
    variants: Object.freeze([...options.variants ?? []]),
    category,
    level,
    target,
    evidence: Object.freeze({ status: evidenceStatus, sourceIds: Object.freeze(sourceIds) }),
    implementation: options.entryPoint ? "prototype" : "pending",
    entryPoint: options.entryPoint ?? null,
    validation: "unreviewed",
    notes: options.notes ?? "已补逐项阅读笔记；程序实现和作品局部的艺术效果对照分别记录。",
    ...firstBatchIds.has(id) ? { sampleReview: firstBatchReview } : {},
    reading
  });
}
var wang = ["dpm-wangshen-yucun"];
var baoshi = ["dpm-baoshi-cun"];
var TECHNIQUE_CATALOG = Object.freeze([
  // Basic actions. A round/flat software nib does not prove centered/side brush handling.
  entry("brush-center", "中锋", "brushwork", "brush-action", "观察线条两侧承墨、宽度与转向的稳定关系。", { sourceIds: [...wang, "shufa-xushupu"], evidence: "definition", entryPoint: "createBrushDocument", notes: "《续书谱》支持锋尖与点画的运行关系。新接触模型以side:0保持锋尖居中，可与提按等组合；它是待实际毛笔对照的程序近似，不由圆厚外形直接验收。" }),
  entry("brush-side", "侧锋", "brushwork", "brush-action", "观察偏侧接触形成的宽面、边缘虚实与方向变化。", { sourceIds: wang, evidence: "mentioned", entryPoint: "createBrushDocument", notes: "新接触模型以side、angle和压力控制接触区相对锋尖的偏置与展开，支持同笔转换；传统侧锋范围仍须参考对照，不以固定扁笔代替。" }),
  entry("brush-offset", "偏锋", "brushwork", "brush-action", "区分笔锋偏置、边缘薄厚与中侧锋转换的控制。", { notes: "与侧锋的评价和用法在不同论述中有差别，暂不作为侧锋同义词。" }),
  entry("brush-follow", "顺锋", "brushwork", "brush-action", "比较顺势运行时笔路与丝束拖曳方向的关系。"),
  entry("brush-against", "逆锋", "brushwork", "brush-action", "研究逆势入纸或逆行时的摩擦、翻转与顿挫。", { notes: "简单倒置点序只改变行进方向，不能代表真实逆锋接触。" }),
  entry("brush-hidden-entry", "藏锋", "brushwork", "brush-action", "比较逆入后转向的起笔与直接尖入的边缘形态。", { sourceIds: ["shufa-xushupu", "shufa-yiying-study"], evidence: "definition", entryPoint: "createBrushDocument", notes: "local-v1模型的entry:concealed增加有界逆入再转向的路径，可与偏侧接触组合；现代临习解释用于辅助，不概括所有藏锋。" }),
  entry("brush-exposed-entry", "露锋", "brushwork", "brush-action", "观察锋尖入纸与压力渐增形成的起端。", { sourceIds: ["shufa-xushupu"], evidence: "mentioned", entryPoint: "createBrushDocument", notes: "local-v1模型的entry:exposed在起段逐步展开接触，满强度从零压力进入；属于待对照的几何近似，不涵盖所有切入方式。" }),
  entry("brush-return-finish", "回锋", "brushwork", "brush-action", "比较收笔回转路径和停笔外形，不只封圆端点。", { sourceIds: ["shufa-yiying-study"], evidence: "definition", entryPoint: "createBrushDocument", notes: "local-v1模型的finish:return先渐提再短弧内收，满强度回行后离纸；不模拟笔毫翻转和真实停驻。" }),
  entry("brush-exposed-finish", "出锋", "brushwork", "brush-action", "观察提笔离纸时压力、宽度和路径延续的关系。", { entryPoint: "createBrushDocument", notes: "local-v1模型的finish:open在尾段渐提，满强度到零压力；原始笔路和控制分别保存，不以附加三角尖表示出锋全义。" }),
  entry("brush-lift", "提笔", "brushwork", "brush-action", "沿调用者路径减小接触宽度并保持笔路连续。", { sourceIds: ["shufa-yiying-study"], evidence: "definition", entryPoint: "createBrushDocument", notes: "局部controls.pressure乘以路径已有压力，控制接触展开并保留零压力离纸段；可保存设定后继续修改，尚待艺术评判。" }),
  entry("brush-press", "按笔", "brushwork", "brush-action", "增加接触宽度，检查增重位置与前后衔接。", { sourceIds: ["shufa-yiying-study"], evidence: "definition", entryPoint: "createBrushDocument", notes: "局部controls.pressure在弧长关键位置组织受压段，与用锋和枯润分别控制；压力未做真实设备或笔杆受力标定。" }),
  entry("brush-pause", "顿笔", "brushwork", "brush-action", "比较短促加压处的墨量与局部形变。"),
  entry("brush-reposition", "挫笔", "brushwork", "brush-action", "观察短距离移锋、重新定向与压力再分配。", { sourceIds: ["shufa-shupu"], evidence: "mentioned", notes: "《书谱》谈点画内的衄挫；本条观察目标是现代程序拆解，不冒充原文逐字定义。" }),
  entry("brush-turn", "转笔", "brushwork", "brush-action", "检查转向处切线与接触宽度是否保持连续。", { sourceIds: ["shufa-xushupu", "shufa-yiying-study"], evidence: "definition", entryPoint: "createBrushDocument", notes: "《续书谱》有“转不欲滞”的要求；local-v1模型的turn:round仅局部圆转、保留长直段，可组合接触换向，尚不模拟毫锋转动。" }),
  entry("brush-fold", "折笔", "brushwork", "brush-action", "保留转折处的停顿、换向与棱角，避免全路径平滑。", { sourceIds: ["shufa-xushupu", "shufa-yiying-study"], evidence: "definition", entryPoint: "createBrushDocument", notes: "《续书谱》有“折欲少驻”的要求；local-v1模型的turn:angular保留节点并组织肩部提按、转点承压，不模拟时间停驻。" }),
  entry("brush-fast", "疾笔", "brushwork", "brush-action", "研究快速行笔的提按压缩与断续，不以播放速度冒充材质。"),
  entry("brush-slow", "徐行", "brushwork", "brush-action", "研究慢行时的持续接触与用力节奏，不以播放速度冒充材质。"),
  entry("brush-resistant", "涩行", "brushwork", "brush-action", "观察前进中的小幅阻滞、节奏和干擦接触。", { entryPoint: "createBrushStroke", notes: "rhythm:hesitant仅以空间偏置和压力模拟局部接触节奏，既非真实速度也非物理阻力；专门术语证据待核实。" }),
  entry("brush-split", "散锋", "brushwork", "brush-action", "区分聚锋与散锋的接触束、聚散和局部断白。", { sourceIds: baoshi, evidence: "definition", notes: "故宫抱石皴条目说明破笔散锋；“破笔”在不同场合也可能指笔具或用笔状态，不统一作别名。" }),
  entry("brush-dot", "点法", "brushwork", "brush-action", "研究单点的方向、提按和起收，再观察成组疏密。", { entryPoint: "createDotDocument", notes: "保存调用者落点、逐点短笔路与提按、方向和局部控制；稳定标识允许独立移动、重排和改向。群点疏密由调用者组织，不表示点叶、点苔或米点等传统组织已经实现。旧createDotMarks继续保留。" }),
  entry("brush-scrub", "擦笔", "brushwork", "brush-action", "研究局部侧拖接触与已有皴线、墨面的连接。"),
  // Ink states and ink processes must remain distinct.
  entry("ink-dark", "浓墨", "ink", "ink-state", "固定笔势，比较较深墨色的层次和重叠关系。", { sourceIds: wang, evidence: "mentioned", entryPoint: "createInkProcessDocument", notes: "墨层concentration控制程序光学密度，与water、接触dryness及opacity分别保存；由同笔组观察浓淡及叠处，不是实测墨粒浓度或完整材料模拟。" }),
  entry("ink-light", "淡墨", "ink", "ink-state", "固定形状，观察浅墨的边缘、层次与留白。", { entryPoint: "createInkProcessDocument", notes: "墨层可在保留相同接触与水量时降低concentration，观察淡墨外观；它是光学密度近似，不能独自概括传统淡墨的纸性与含水。旧透明度接口继续保留。" }),
  entry("ink-scorched", "焦墨", "ink", "ink-state", "比较高黑度与枯接触共存时的断白和积叠。", { notes: "可调黑色和枯润不等于已有焦墨法；本条仍待独立组织与参考对照。" }),
  entry("ink-dry", "干笔", "ink", "ink-state", "检查接触中断是否顺笔势变化，是否出现均匀排线。", { aliases: ["枯笔"], entryPoint: "createBrushDocument", notes: "局部dryness与inkLoad控制顺笔路沉积和接触覆盖，可在同笔改变；它们是视觉控制量，尚非真实含水、储墨或纸面摩擦模型。" }),
  entry("ink-wet", "湿笔", "ink", "ink-state", "比较连续承墨与边缘虚实；含水量和含墨量分别观察。", { entryPoint: "createBrushDocument", notes: "局部dryness可形成较连续的沉积，与墨色和透明度分别设置；当前接触模型不模拟毛细扩散、纸性或干燥，艺术效果待评。" }),
  entry("ink-flying-white", "飞白", "ink", "ink-state", "比较顺笔势断续的露纸丝束，排除等距梳齿纹。", { entryPoint: "createBrushDocument", notes: "新接触渲染沿笔路生成断续墨束和露纸；这里只研究可见断白，历史飞白的范围及艺术效果仍须独立对照。" }),
  entry("ink-layered", "积墨", "ink", "ink-process", "分层安排先后、干湿和浓淡，观察叠墨后的结构是否保留。", { entryPoint: "createInkProcessDocument", notes: "保存调用者逐层笔路、浓度、绝对时刻与声明干燥窗口，可修改覆盖与留明；第四批比较单薄层、全覆与分区递加。程序叠层和wash.passes都不独立证明传统积墨成立，艺术范围待评。" }),
  entry("ink-broken", "破墨", "ink", "ink-process", "研究水墨、浓淡相破的方向、时序和边缘关系。", { sourceIds: wang, evidence: "definition", entryPoint: "createInkProcessDocument", notes: "wet-mix在新旧墨层仍有声明湿态的局部进行有限栅格颜料交换，区分待干叠加、浓破淡及反向淡破浓；不是全画模糊、连续干燥或真实纸内流体。王诜清水化轮廓用例的范围另记，不代表全部破墨。" }),
  entry("ink-splashed", "泼墨", "ink", "ink-process", "研究大面积墨势、流动与后续收拾，区分随机泼点。"),
  entry("ink-aged", "宿墨", "ink", "ink-process", "研究沉积、颗粒与边缘变化的视觉范围和材料前提。", { notes: "宿墨涉及材料状态；不能由一个噪声纹理开关冒充。" }),
  entry("ink-rendering", "渲染", "ink", "ink-process", "研究水墨在区域内的层次过渡和造形作用。", { entryPoint: "wash", notes: "已有区域墨洗原型，尚无真实湿边交融。" }),
  entry("ink-background-wash", "烘染", "ink", "ink-process", "从形外或周围衬托对象，观察空白与明暗的关系。"),
  entry("ink-glazing", "罩染", "ink", "ink-process", "比较已有笔墨上薄层统一色调时的保形程度。"),
  entry("ink-divided-wash", "分染", "ink", "ink-process", "沿形体分区组织由深至浅的层次，避免均匀铺色。"),
  entry("ink-supporting-wash", "托", "ink", "ink-process", "先核对托法的对象、区域和前后关系，再设计局部对照。", { notes: "故宫综述仅列“托”；不同语境下的含义还需文献与作品交叉确认。" }),
  entry("water-soak", "渍水", "water", "water-process", "核对局部积水与墨层先后、边界沉积的关系。"),
  entry("water-collision", "撞水", "water", "water-process", "研究水与已有墨层相遇时的区域边缘；先区分干湿前提。"),
  entry("water-texture", "皴水", "water", "water-process", "先核对名词用例，再研究水的笔触与山石纹理的关系。"),
  entry("water-sprinkle", "洒水", "water", "water-process", "研究水滴大小、疏密及施于墨前墨后的差异。", { entryPoint: "createWaterProcessDocument", notes: "第五批仅作分散加水／洒水研究：保存调用者显式清水滴与墨事件，比较先后和局部湿交换；水不着色或擦除。洒水阅读仍部分支持，陈容噗水是邻近用例，不能由水滴程序宣称传统洒水已复现。" }),
  entry("water-break-ink", "水破墨", "water", "water-process", "研究清水进入墨层时的先后顺序和边缘变化。", { entryPoint: "createWaterProcessDocument", notes: "显式区分先墨与后到清水，在声明湿窗的局部交区重分布原墨，清水浓度固定为零，不用白色覆盖或擦除；王诜清水向内化轮廓只为具体用例，当前有限交换不等于真实纸内渗化，艺术效果留待统一评价。" }),
  entry("ink-break-water", "墨破水", "water", "water-process", "研究墨进入预湿区域时的先后顺序和边缘变化。", { entryPoint: "createWaterProcessDocument", notes: "保存纸面预施清水与后到墨笔，区分有效湿窗与已过窗口；清水自身不可见。仅采用已读课堂文字的先水后墨语境，阅读仍部分支持，不并入笔中蘸水次序或全部湿底技法，艺术效果留待统一评价。" }),
  // Family entries keep long/short and large/small variants together.
  entry("cun-pima", "披麻皴", "texture", "texture-family", "用疏密、交搭和曲直变化的线组织山石走势。", { aliases: ["麻皮皴"], variants: ["长披麻皴", "短披麻皴"], sourceIds: ["dpm-pima", ...baoshi], evidence: "definition", entryPoint: "createCunDocument", notes: "第六批以作者显式笔路组织错落长线与长短交搭，pima提供顺线提按和接触默认；不自动填山体或决定坡势。故宫董源偏短、巨然偏长的比较不等于统一配方；旧cun原型保留，艺术范围待统一评价。" }),
  entry("cun-axe", "斧劈皴", "texture", "texture-family", "比较侧向切面、横扫落势和干湿接触对坚硬石质的作用。", { variants: ["大斧劈皴", "小斧劈皴"], sourceIds: ["dpm-axe"], evidence: "definition", entryPoint: "createCunDocument", notes: "axe在作者横扫斜拖笔路上提供侧锋重起与收窄接触，石面、大小与交叠仍由作者组织；不同大小不是已确证的全部传统变体。故宫横扫用例不概括成纵向长黑条，旧cun不因此自动升级，艺术待评。" }),
  entry("cun-zhedai", "折带皴", "texture", "texture-family", "研究横向行笔与折转的带状结构，保留折处骨力。", { sourceIds: baoshi, evidence: "mentioned", entryPoint: "createCunDocument", notes: "zhedai在作者横行折转骨线上保留方折并组织局部侧用和提按，分层位置与虚实仍需显式设计。不是任意曲线降平滑度或固定等距直角纹；传统用法及艺术效果留到统一评价。" }),
  entry("cun-jiesuo", "解索皴", "texture", "texture-family", "研究曲线交搭和松紧变化，避免平行长帘。", { sourceIds: baoshi, evidence: "mentioned", entryPoint: "createCunDocument", notes: "第七批jiesuo在作者屈曲笔路上提供中用接触与提按，屈曲尺度、穿插、疏密和山石主脉由作者组织；不从直线自动生成卷曲或用随机绕线填面。程序原型及艺术范围待统一评价。" }),
  entry("cun-luanchai", "乱柴皴", "texture", "texture-family", "研究交错短长线的结构组织，区别无向随机散线。", { sourceIds: baoshi, evidence: "mentioned", entryPoint: "createCunDocument", notes: "第七批luanchai在作者直折笔路上保折并组织轻重接触；阴处粗密、阳处疏细及多方向交错仍为显式作者设定。淡墨用例不强制全黑硬，不把均匀随机散线当成乱柴，艺术待评。" }),
  entry("cun-niumao", "牛毛皴", "texture", "texture-family", "研究细密曲线的疏密、层次和山体走向。", { sourceIds: baoshi, evidence: "mentioned", entryPoint: "createCunDocument", notes: "第七批niumao仅作牛毛细线形态研究：在作者细短曲路上提供细线接触默认，疏密、留脉、曲率和交叉比例是待核的程序假设。阅读仍部分支持，缺少可靠独立笔序与高清局部比较；不宣称传统牛毛皴成立，也不因有入口就默认推荐给创作Agent。" }),
  entry("cun-juanyun", "卷云皴", "texture", "texture-family", "研究卷曲笔组与山石轮廓的连接方式。", { sourceIds: baoshi, evidence: "mentioned", entryPoint: "createCunDocument", notes: "第八批以cloud-study共享入口研究作者环抱线的接触、开面与搭接。与云头的资料逐名保留，名称边界待核，不因共用程序判定同义，也不虚构互斥预设。阅读仍部分支持，无可靠完整笔序或艺术认可，不自动推荐。" }),
  entry("cun-yuntou", "云头皴", "texture", "texture-family", "核对块面转折与云团状线组的关系。", { entryPoint: "createCunDocument", notes: "第八批以cloud-study共享研究作者开口环抱线和前后笔序；中侧接触不等于已复现团旋转背。与卷云名称边界及独立资料保留，阅读仍部分支持，不直接合并为同义词或默认推荐，艺术待统一评价。" }),
  entry("cun-yudian", "雨点皴", "texture", "texture-family", "比较密集点状皴擦的落向、疏密与体块关系。", { entryPoint: "createCunDocument", notes: "第八批point-study共享入口沿作者短笔路执行轻触、按腹与收提，方向、阴阳聚散和留气由作者组织。雨点与豆瓣资料各自保留，阅读仍部分支持，不以固定长宽比划历史界线，不画落雨或自动撒点；原型不默认推荐，艺术待评。" }),
  entry("cun-douban", "豆瓣皴", "texture", "texture-family", "研究较宽点笔的提按、排列和山石结构。", { entryPoint: "createCunDocument", notes: "第八批与雨点共用point-study作短点接触研究，保留两名各自资料及名称关系待核。按腹、粗细和布局由显式源控制，不用统一豆形图章或两套互斥阈值；阅读仍部分支持，无传统艺术认可，不自动推荐。" }),
  entry("cun-midian", "米点皴", "texture", "texture-family", "研究横点的大小、浓淡和层叠怎样联系云山。", { aliases: ["米家点"] }),
  entry("cun-heye", "荷叶皴", "texture", "texture-family", "研究主脉与分披走势，避免均匀放射线。", { entryPoint: "createCunDocument", notes: "第八批heye在作者分歧筋脉上提供上重下轻与开收；峰顶、主次、交接、遮让和山脚留气由普通独立源路径及笔序组织。不自动创建父子拓扑、伞骨图章或固定山形；资料支持不代替程序艺术验收。" }),
  entry("cun-kulou", "骷髅皴", "texture", "texture-family", "核对凹凸、孔洞式山石结构与笔组的关系。"),
  entry("cun-tuoni", "拖泥带水皴", "texture", "texture-family", "研究带水拖笔与墨色连接的结构作用。"),
  entry("cun-dingtou", "钉头皴", "texture", "texture-family", "核对起笔重、收笔轻的点划及其组织关系。"),
  entry("cun-baoshi", "抱石皴", "texture", "texture-family", "研究破笔散锋的聚散层次，以及线面点的综合组织。", { sourceIds: baoshi, evidence: "definition", notes: "有明确画家与形成背景的综合用笔；不能直接给噪声笔刷冠同名并宣称掌握。" }),
  entry("vegetation-moss", "点苔", "vegetation", "vegetation-method", "比较点的聚散、大小与岩面或树根结构的关系。", { entryPoint: "createVegetationDocument", notes: "第九批moss在作者结构线和显式苔点路径上研究接触与聚散，稳定单元和逐笔身份保留局部修改责任。落点、大小、呼应和石面关系由作者组织，不随机填满或以加黑遮丑代替造形；程序原型显式选用，艺术待统一评价。" }),
  entry("vegetation-dot-leaves", "点叶", "vegetation", "vegetation-method", "以点笔组织叶丛的正侧、重叠和外轮廓。", { entryPoint: "createVegetationDocument", notes: "第九批dot-leaf在作者单叶笔路上提供点叶接触默认，枝架、叶簇、顾盼、疏密和叠压均以显式单元与笔序保存。不自动补叶、指定树种或把全部点叶归并为一个图式；资料阅读与程序艺术范围分别记录，原型不默认推荐。" }),
  entry("vegetation-outline-leaves", "夹叶", "vegetation", "vegetation-method", "研究叶片双边勾出后与叶脉、枝条的衔接。", { entryPoint: "createVegetationDocument", notes: "第九批outline-leaf研究作者提供的一叶两边细线及沿枝组织，叶片单元保留可编辑的逐边路径；内部留白来自未着墨区域，不使用填白图章。阅读仍部分支持，未确证完整笔序、树种或设色流程，不自动生成椭圆叶群或获得艺术认可。" }),
  entry("leaf-jie", "介字点", "vegetation", "vegetation-method", "研究介字式分叉点组的方向和聚散。", { entryPoint: "createVegetationDocument", notes: "第十批共享dot-leaf执行作者分叉叶组，逐笔显式使用中用接触控制；中锋有馆藏文字用例，图式阅读仍部分支持。作者示例四笔是本次标注假设，不是传统固定笔数或唯一笔序；不把字体介字复制到树冠，艺术待统一评价。" }),
  entry("leaf-ge", "个字点", "vegetation", "vegetation-method", "研究个字式点组与枝条方向的连接。", { entryPoint: "createVegetationDocument", notes: "第十批共享dot-leaf研究作者主向与两侧下垂的分向叶组，保留普通单元与逐笔身份；与介字使用分别组织的源而不只更换标签。三笔只是本次作者选择，静态图不证明固定数额、完整起收笔序或树种；阅读仍部分支持，程序原型显式选用。" }),
  entry("leaf-pin", "品字点", "vegetation", "vegetation-method", "研究三点组合的大小、间隙与疏密。"),
  entry("leaf-three-five", "攒三聚五", "vegetation", "vegetation-method", "研究三五点簇的聚散而非重复同一印章。", { entryPoint: "createVegetationDocument", notes: "第十批沿用dot-leaf接触和作者显式叶簇，比较重复单元与数量、方向、浓淡、空白通道的组织；这是成簇聚散关系，不新增一套同名笔头或规定每处三片加五片。整组变换保留逐笔源与身份，阅读依据不代替完整树冠或艺术验收。" }),
  entry("leaf-pepper", "胡椒点", "vegetation", "vegetation-method", "核对细密点法的对象、浓淡与疏密变化。", { entryPoint: "createVegetationDocument", notes: "第十一批沿用普通点笔接触，以作者显式保存的细短点路研究细粒密簇、簇边及细白；馆藏说明另有山顶浓墨戳点用例。不把米点缩小或均匀噪声作为胡椒点的完整定义，点数、附枝与留空仍由作者组织，艺术待统一评价。" }),
  entry("leaf-broken", "破笔点", "vegetation", "vegetation-method", "比较破笔接触在点叶、点苔中的不规则分束。", { entryPoint: "createVegetationDocument", notes: "第十一批研究破笔点的散锋形态，createSplitVegetationUnit把作者给定笔路和相关束偏移展开为普通可编辑多笔源，保留束宽、压力与单元身份；它是几何多笔源近似，非真实一次接触物理。图式阅读仍部分支持，抱石皴词条只提供相关破笔散锋语境，不定义破笔点，也不等同破墨。" }),
  entry("vegetation-pine-needles", "松针", "vegetation", "vegetation-method", "研究针束扇开、交叉、叠合以及与枝干的连接。", { entryPoint: "createVegetationDocument", notes: "第十一批沿用普通细线接触研究作者逐针保存的偏向扇束、长短、交叠与枝端空隙；针束为可编辑单元，不新增等角太阳式树冠预设。阅读仍部分支持，静态松叶点、刺松点图式不证明唯一落针顺序；刺绣松针词条明确排除，松干、竹叶与完整树法不连带认定实现。" }),
  entry("vegetation-bamboo-leaves", "竹叶", "vegetation", "vegetation-method", "研究叶笔的起按提收与成组穿插。", { entryPoint: "createVegetationDocument", notes: "第十二批沿用dot-leaf接触，作者分别组织竹枝架、尖长叶笔、疏密俯仰与穿插；逐叶保存压力、笔宽、时间和组身份，普通单元可整体变换。图式阅读仍部分支持，不把个字点改名或同形叶片贴到任意枝上，也不规定唯一撇叶顺序、风雨动态或全部画竹法。" }),
  entry("leaf-plum", "梅花点", "vegetation", "vegetation-method", "先核对名称所指点叶图式，再研究多点簇的组织。", { entryPoint: "createVegetationDocument", notes: "第十二批以作者显式短叶围攒研究树谱中的花簇式点叶，沿用dot-leaf基础，成员数量、方向、大小、叠接和簇间留空可编辑。阅读仍部分支持，不画梅花实物、不固定五瓣或五笔，不与品字点、梅花鼠足点裁为同义；程序原型不代替传统笔序与艺术评价。" }),
  entry("line-outline", "勾勒", "linework", "depiction-method", "以开放或闭合线勾出形体边界，并检查提按与转折。", { entryPoint: "stroke", notes: "stroke接受调用者路径；形成什么对象和画法由创作者决定。" }),
  entry("line-baimiao", "白描", "linework", "depiction-method", "研究不依赖设色的线条造形、主次与质感。", { notes: "白描是一种造形方法，不能简化为关闭颜色。" }),
  entry("line-mogu", "没骨", "linework", "depiction-method", "研究以墨色或色面直接造形、弱化独立轮廓线的关系。", { notes: "“没骨”不等于任意无描边填色。" }),
  entry("line-double-outline", "双勾", "linework", "depiction-method", "比较对象两侧轮廓、内部空白和转折的对应。", { entryPoint: "createVegetationDocument", notes: "第十二批选取叶形对象，以outline-leaf细线接触执行作者分别提供的两侧轮廓、合拢、宽窄与分段遮挡，复用普通单元和整组编辑，不新增历史固定轮廓算法。绘画双钩词条有明确支持，与复制法帖的双钩廓填分别研究；内部留白来自未着墨，后缘省略由作者源段明确表达，不以白色填充擦除既有墨迹，也不自动认定完整花鸟造形。" }),
  entry("line-gaogu-yousi", "高古游丝描", "linework", "line-style", "研究细长连续线在转折、速度和压力上的稳定变化。"),
  entry("line-qinxian", "琴弦描", "linework", "line-style", "研究长线的张力与宽度控制，并核对历史分类。"),
  entry("line-tiexian", "铁线描", "linework", "line-style", "研究有力均整线条在转折和收笔中的控制。"),
  entry("line-xingyun-liushui", "行云流水描", "linework", "line-style", "研究连贯长线的曲折节奏与衣纹走向。"),
  entry("line-mahuang", "马蝗描", "linework", "line-style", "研究线条粗细、圆转和弹性变化。", { aliases: ["蚂蝗描"] }),
  entry("line-dingtou-shuwei", "钉头鼠尾描", "linework", "line-style", "研究重起轻收与线条衔接，不把它当成全部衣纹的统一压力模板。"),
  entry("line-hun", "混描", "linework", "line-style", "核对淡墨衣纹与浓墨醒线、线面交融的操作及历史范围。"),
  entry("line-juetou", "撅头钉描", "linework", "line-style", "核对硬折、端头与短线组织的具体形态。", { aliases: ["撅头丁描", "橛头丁描", "橛头钉描"] }),
  entry("line-caoyi", "曹衣描", "linework", "line-style", "研究衣纹密集、贴体的线组与结构关系。"),
  entry("line-zhelu", "折芦描", "linework", "line-style", "研究线的硬折、长短衔接和人物结构。"),
  entry("line-ganlan", "橄榄描", "linework", "line-style", "核对两端与中段宽度变化在真实衣纹中的组织。"),
  entry("line-zaohe", "枣核描", "linework", "line-style", "研究两端轻、中段重的压力节奏及转折形态。"),
  entry("line-liuye", "柳叶描", "linework", "line-style", "研究细长叶形线的按提和与相邻衣纹的衔接。"),
  entry("line-zhuye", "竹叶描", "linework", "line-style", "核对短长尖收线条在衣纹中的结构用法。", { notes: "衣纹竹叶描与画竹叶不是同一层级，分别研究。" }),
  entry("line-zhanbi-shuiwen", "战笔水纹描", "linework", "line-style", "研究受控小幅颤动与水纹式转折，避免独立随机抖线。"),
  entry("line-jianbi", "减笔描", "linework", "line-style", "研究省略与概括哪些线仍能保持对象结构。"),
  entry("line-kuchai", "枯柴描", "linework", "line-style", "核对干涩、折断线质与用线组织的关系。"),
  entry("line-qiuyin", "蚯蚓描", "linework", "line-style", "研究圆转曲线的厚度、连贯性及人物结构。"),
  // Eight methods name stroke forms; ce is not a synonym for side-tip brushwork.
  entry("eight-ce", "侧（点）", "calligraphy", "calligraphic-stroke", "研究点笔的入纸、按顿和回收。", { aliases: ["永字八法·侧"], notes: "八法“侧”是点法名称，不与“侧锋”混为同义词。" }),
  entry("eight-le", "勒（横）", "calligraphy", "calligraphic-stroke", "研究横画的入笔、行笔和收束。", { aliases: ["永字八法·勒"] }),
  entry("eight-nu", "努（竖）", "calligraphy", "calligraphic-stroke", "研究竖画的中段控制、力向与收笔。", { aliases: ["永字八法·努", "弩（竖）"] }),
  entry("eight-ti", "趯（钩）", "calligraphy", "calligraphic-stroke", "研究转折蓄势与钩出的短促提锋。", { aliases: ["永字八法·趯"] }),
  entry("eight-ce-lift", "策（提）", "calligraphy", "calligraphic-stroke", "研究轻入渐出或按后提行的具体笔势。", { aliases: ["永字八法·策"] }),
  entry("eight-lue", "掠（长撇）", "calligraphy", "calligraphic-stroke", "研究长撇的起行、弧势与提锋。", { aliases: ["永字八法·掠"] }),
  entry("eight-zhuo", "啄（短撇）", "calligraphy", "calligraphic-stroke", "研究短撇的迅捷落出及其压力压缩。", { aliases: ["永字八法·啄"] }),
  entry("eight-zhe", "磔（捺）", "calligraphy", "calligraphic-stroke", "研究捺画的渐按、转向与铺毫出锋。", { aliases: ["永字八法·磔"] })
]);
function getTechnique(id) {
  return TECHNIQUE_CATALOG.find((value) => value.id === id);
}
function findTechniques(options = {}) {
  if (options.query !== void 0 && typeof options.query !== "string") throw new TypeError("query must be a string");
  const query = (options.query ?? "").trim().toLocaleLowerCase("zh-CN");
  return Object.freeze(TECHNIQUE_CATALOG.filter((value) => (!options.category || value.category === options.category) && (!options.implementation || value.implementation === options.implementation) && (!options.evidence || value.evidence.status === options.evidence) && (!query || [
    value.id,
    value.name,
    ...value.aliases,
    ...value.variants,
    value.target,
    value.notes,
    value.reading.summary,
    value.reading.actions,
    value.reading.appearance,
    value.reading.application,
    value.reading.pitfall,
    value.reading.translation
  ].some((text7) => text7.toLocaleLowerCase("zh-CN").includes(query)))));
}

// src/brush-document.ts
var GENERATOR = "ink-brush-contact-v1";
var LOCAL_GENERATOR = "ink-brush-local-gestures-v1";
var generatorFor = (options) => options?.gestureModel === "local-v1" ? LOCAL_GENERATOR : GENERATOR;
var supportedOptions = /* @__PURE__ */ new Set([
  "id",
  "width",
  "ink",
  "opacity",
  "dryness",
  "smoothing",
  "seed",
  "clip",
  "start",
  "duration",
  "axis",
  "entry",
  "finish",
  "pressureProfile",
  "turn",
  "rhythm",
  "drag",
  "strength",
  "controls",
  "gestureModel"
]);
function assertOptions(options) {
  if (!options || typeof options !== "object" || Array.isArray(options)) throw new TypeError("Brush program options must be an object");
  for (const key of Object.keys(options)) {
    if (!supportedOptions.has(key)) throw new RangeError(`Unsupported brush program option: ${key}`);
  }
  for (const key of ["nib", "tipAngle", "brush"]) {
    if (key in options) throw new RangeError(`Brush programs use contact controls instead of ${key}`);
  }
}
function contactControls(controls, axis, dryness) {
  const defaults3 = { pressure: 1, side: axis === "side" ? 1 : 0, angle: Math.PI / 2, dryness, inkLoad: 1 };
  const input = copyStrokeBrush({ version: 1, controls }).controls;
  const first = input[0], last = input.at(-1);
  const middle = input.filter((control) => control.at > 0 && control.at < 1);
  return copyStrokeBrush({ version: 1, controls: [
    { at: 0, ...defaults3, ...first?.at === 0 ? first : {} },
    ...middle,
    { at: 1, ...defaults3, ...last?.at === 1 ? last : {} }
  ] });
}
function prepare2(points, options) {
  assertOptions(options);
  const { controls = [], ...gestures } = options;
  const ownedControls = copyStrokeBrush({ version: 1, controls }).controls;
  const authored = createInkScene({ width: 1, height: 1 }).stroke(points, {
    ...gestures,
    width: options.width ?? 24,
    dryness: options.dryness ?? 0.25,
    nib: "round",
    tipAngle: 0
  });
  const canonical = Object.freeze({
    id: authored.id,
    width: authored.width,
    ink: authored.ink,
    opacity: authored.opacity,
    dryness: authored.dryness,
    smoothing: options.smoothing ?? "linear",
    seed: authored.seed,
    start: authored.start,
    duration: authored.duration,
    ...authored.clip === void 0 ? {} : { clip: authored.clip },
    axis: options.axis ?? "center",
    entry: options.entry ?? "preserve",
    finish: options.finish ?? "preserve",
    pressureProfile: options.pressureProfile ?? "preserve",
    turn: options.turn ?? "preserve",
    rhythm: options.rhythm ?? "continuous",
    drag: options.drag ?? "forward",
    strength: options.strength ?? 1,
    ...options.gestureModel === void 0 ? {} : { gestureModel: options.gestureModel },
    controls: ownedControls
  });
  const { controls: _controls, ...canonicalGestures } = canonical;
  const geometry = createBrushStroke(authored.points, { ...canonicalGestures, nib: "round", tipAngle: 0 });
  const result = createInkScene({ width: 1, height: 1 }).stroke(geometry.points, {
    ...geometry,
    brush: contactControls(ownedControls, canonical.axis, authored.dryness)
  });
  return { points: authored.points, options: canonical, result };
}
function assertRecipe(recipe) {
  if (!recipe || typeof recipe !== "object" || recipe.version !== 1) throw new RangeError("Unsupported brush recipe version");
  if (recipe.generator !== GENERATOR && recipe.generator !== LOCAL_GENERATOR) throw new RangeError("Unsupported brush generator version");
  if (recipe.generator !== generatorFor(recipe.options)) throw new RangeError("Brush generator version does not match gesture model");
}
function createBrushDocument(points, options = {}) {
  const prepared = prepare2(points, options);
  const recipe = Object.freeze({
    version: 1,
    generator: generatorFor(prepared.options),
    points: prepared.points,
    options: prepared.options
  });
  return Object.freeze({ version: 1, recipe, result: prepared.result });
}
function compileBrushRecipe(recipe) {
  assertRecipe(recipe);
  return prepare2(recipe.points, recipe.options).result;
}
function reviseBrushDocument(document, patch) {
  if (!document || typeof document !== "object" || document.version !== 1) throw new RangeError("Unsupported brush document version");
  assertRecipe(document.recipe);
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) throw new TypeError("Brush document patch must be an object");
  for (const key of Object.keys(patch)) {
    if (key !== "points" && key !== "options") throw new RangeError(`Unsupported brush document patch: ${key}`);
  }
  if (patch.options !== void 0) assertOptions(patch.options);
  return createBrushDocument(patch.points === void 0 ? document.recipe.points : patch.points, { ...document.recipe.options, ...patch.options });
}

// src/dot-document.ts
var GENERATOR2 = "ink-dot-contact-v1";
var MAX_DOTS = 2048;
var supportedOptions2 = /* @__PURE__ */ new Set([
  "shape",
  "size",
  "angle",
  "idPrefix",
  "interval",
  "ink",
  "opacity",
  "dryness",
  "seed",
  "clip",
  "start",
  "duration",
  "axis",
  "entry",
  "finish",
  "pressureProfile",
  "turn",
  "rhythm",
  "drag",
  "strength",
  "controls"
]);
var supportedPlacement = /* @__PURE__ */ new Set(["x", "y", "pressure", "id", "shape", "size", "angle", "controls", "start"]);
var supportedControl = /* @__PURE__ */ new Set(["at", "pressure", "side", "angle", "dryness", "inkLoad"]);
function objectFields(value, fields7, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  for (const key of Object.keys(value)) {
    if (!fields7.has(key)) throw new RangeError(`Unsupported ${name} field: ${key}`);
  }
}
function finite7(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be a finite number`);
  return value;
}
function positive3(value, name) {
  finite7(value, name);
  if (value <= 0) throw new RangeError(`${name} must be greater than zero`);
  return value;
}
function nonnegative(value, name) {
  finite7(value, name);
  if (value < 0) throw new RangeError(`${name} must be nonnegative`);
  return value;
}
function text2(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${name} must be a nonempty string`);
  return value;
}
function shape(value) {
  if (!["round", "horizontal", "vertical", "oblique"].includes(value)) {
    throw new RangeError("Dot shape must be round, horizontal, vertical or oblique");
  }
  return value;
}
function copyControls(controls) {
  if (!Array.isArray(controls)) throw new TypeError("Dot controls must be an array");
  for (const control of controls) objectFields(control, supportedControl, "dot control");
  return copyStrokeBrush({ version: 1, controls }).controls;
}
function assertOptions2(options) {
  objectFields(options, supportedOptions2, "dot program option");
  if (options.shape !== void 0) shape(options.shape);
  if (options.size !== void 0) positive3(options.size, "size");
  if (options.angle !== void 0) finite7(options.angle, "angle");
  if (options.interval !== void 0) nonnegative(options.interval, "interval");
  if (options.idPrefix !== void 0) text2(options.idPrefix, "idPrefix");
  if (options.controls !== void 0) copyControls(options.controls);
}
function canonicalOptions(options) {
  assertOptions2(options);
  const { shape: pointShape = "round", size = 24, angle = 0, idPrefix = "dot", interval = 0, ...brush } = options;
  const probe = createBrushDocument([{ x: 0, y: 0, pressure: 0 }, { x: 10, y: 0, pressure: 1 }, { x: 20, y: 0, pressure: 0 }], {
    ...brush,
    id: "dot-validation",
    width: size,
    seed: options.seed ?? 1,
    gestureModel: "local-v1",
    smoothing: "catmull-rom"
  });
  const { id: _id, width: _width, gestureModel: _model, smoothing: _smoothing, ...shared } = probe.recipe.options;
  return Object.freeze({
    ...shared,
    shape: pointShape,
    size,
    angle,
    idPrefix,
    interval,
    seed: probe.result.seed,
    start: probe.result.start
  });
}
function copyPlacements(positions) {
  if (!Array.isArray(positions) || positions.length > MAX_DOTS) throw new RangeError("A dot document supports an array of at most 2048 positions");
  return Object.freeze(positions.map((position, index) => {
    objectFields(position, supportedPlacement, "dot placement");
    const name = `positions[${index}]`;
    const copy = {
      x: finite7(position.x, `${name}.x`),
      y: finite7(position.y, `${name}.y`),
      ...position.pressure === void 0 ? {} : { pressure: finite7(position.pressure, `${name}.pressure`) },
      ...position.id === void 0 ? {} : { id: text2(position.id, `${name}.id`) },
      ...position.shape === void 0 ? {} : { shape: shape(position.shape) },
      ...position.size === void 0 ? {} : { size: positive3(position.size, `${name}.size`) },
      ...position.angle === void 0 ? {} : { angle: finite7(position.angle, `${name}.angle`) },
      ...position.start === void 0 ? {} : { start: nonnegative(position.start, `${name}.start`) },
      ...position.controls === void 0 ? {} : { controls: copyControls(position.controls) }
    };
    if (copy.pressure !== void 0 && (copy.pressure < 0 || copy.pressure > 1)) throw new RangeError(`${name}.pressure must be in 0..1`);
    return Object.freeze(copy);
  }));
}
function hashId(id) {
  let hash8 = 2166136261;
  for (let i = 0; i < id.length; i++) hash8 = Math.imul(hash8 ^ id.charCodeAt(i), 16777619);
  return hash8 >>> 0;
}
var proportions = {
  // A broad, short contact needs a shallow tip bend: tighter turns make its
  // offset edge fold back and leave artificial concentric fiber bands.
  round: { length: 0.44, width: 1, heading: 0, bend: 4e-3 },
  horizontal: { length: 1.38, width: 0.6, heading: 0, bend: 0.055 },
  vertical: { length: 1.38, width: 0.6, heading: Math.PI / 2, bend: 0.055 },
  oblique: { length: 1.18, width: 0.68, heading: -Math.PI / 3, bend: 0.075 }
};
function createContact(position, options, index) {
  const pointShape = position.shape ?? options.shape, size = position.size ?? options.size;
  const geometry = proportions[pointShape], angle = geometry.heading + options.angle + (position.angle ?? 0);
  finite7(angle, "Combined dot angle");
  const cos = Math.cos(angle), sin = Math.sin(angle), pressure = position.pressure ?? 1;
  const path2 = [
    { along: 0, across: 0, pressure: 0 },
    { along: 0.28, across: geometry.bend, pressure: 1 },
    { along: 0.7, across: geometry.bend * 0.45, pressure: 0.78 },
    { along: 1, across: 0, pressure: 0 }
  ].map((point2) => {
    const x = point2.along * geometry.length * size, y = point2.across * size;
    return { x: position.x + x * cos - y * sin, y: position.y + x * sin + y * cos, pressure: point2.pressure * pressure };
  });
  const { shape: _shape, size: _size, angle: _angle, idPrefix, interval, ...shared } = options;
  const id = position.id ?? `${idPrefix}-${index + 1}`;
  const start = position.start ?? options.start + index * interval;
  return createBrushDocument(path2, {
    ...shared,
    id,
    width: size * geometry.width,
    seed: (options.seed ^ hashId(id)) >>> 0,
    gestureModel: "local-v1",
    smoothing: "catmull-rom",
    start,
    ...position.controls === void 0 ? {} : { controls: position.controls }
  });
}
function createDotDocument(positions, options = {}) {
  const canonical = canonicalOptions(options), owned = copyPlacements(positions);
  const ids = /* @__PURE__ */ new Set();
  for (const [index, position] of owned.entries()) {
    const id = position.id ?? `${canonical.idPrefix}-${index + 1}`;
    if (ids.has(id)) throw new RangeError(`Duplicate dot id: ${id}`);
    ids.add(id);
  }
  const documents = Object.freeze(owned.map((position, index) => createContact(position, canonical, index)));
  const recipe = Object.freeze({ version: 1, generator: GENERATOR2, positions: owned, options: canonical });
  return Object.freeze({ version: 1, recipe, documents, result: Object.freeze(documents.map((document) => document.result)) });
}
function assertRecipe2(recipe) {
  if (!recipe || typeof recipe !== "object" || recipe.version !== 1) throw new RangeError("Unsupported dot recipe version");
  objectFields(recipe, /* @__PURE__ */ new Set(["version", "generator", "positions", "options"]), "dot recipe");
  if (recipe.generator !== GENERATOR2) throw new RangeError("Unsupported dot generator version");
  assertOptions2(recipe.options);
}
function compileDotRecipe(recipe) {
  assertRecipe2(recipe);
  return createDotDocument(recipe.positions, recipe.options).result;
}
function reviseDotDocument(document, patch) {
  if (!document || typeof document !== "object" || document.version !== 1) throw new RangeError("Unsupported dot document version");
  objectFields(document, /* @__PURE__ */ new Set(["version", "recipe", "result", "documents"]), "dot document");
  assertRecipe2(document.recipe);
  objectFields(patch, /* @__PURE__ */ new Set(["positions", "options"]), "dot document patch");
  if (patch.options !== void 0) assertOptions2(patch.options);
  return createDotDocument(
    patch.positions === void 0 ? document.recipe.positions : patch.positions,
    { ...document.recipe.options, ...patch.options }
  );
}

// src/ink-process.ts
var GENERATOR3 = "ink-layer-process-v1";
var MAX_LAYERS = 32;
var MAX_STROKES = 512;
var MAX_SOURCE_POINTS2 = 4096;
var MAX_CELLS = 262144;
var layerFields = /* @__PURE__ */ new Set(["id", "strokes", "concentration", "water", "at", "dryAfter", "mode", "edgeSoftness", "edgeDeposit", "edgeWidth"]);
var strokeFields = /* @__PURE__ */ new Set(["points", "options"]);
var strokeOptionFields = /* @__PURE__ */ new Set([
  "id",
  "width",
  "opacity",
  "dryness",
  "smoothing",
  "seed",
  "clip",
  "axis",
  "entry",
  "finish",
  "pressureProfile",
  "turn",
  "rhythm",
  "drag",
  "strength",
  "controls",
  "gestureModel"
]);
var processOptionFields = /* @__PURE__ */ new Set(["width", "height", "seed", "background", "ink"]);
var pointFields = /* @__PURE__ */ new Set(["x", "y", "pressure"]);
var vectorFields = /* @__PURE__ */ new Set(["x", "y"]);
var controlFields = /* @__PURE__ */ new Set(["at", "pressure", "side", "angle", "dryness", "inkLoad"]);
var resultLayerFields = /* @__PURE__ */ new Set(["id", "marks", "concentration", "water", "at", "dryAfter", "mode", "edgeSoftness", "edgeDeposit", "edgeWidth"]);
var markFields = /* @__PURE__ */ new Set([
  "kind",
  "id",
  "points",
  "width",
  "ink",
  "opacity",
  "dryness",
  "nib",
  "smoothing",
  "tipAngle",
  "seed",
  "clip",
  "start",
  "duration",
  "brush"
]);
var clamp5 = (value) => Math.max(0, Math.min(1, value));
function fields(value, allowed, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  for (const key of Object.keys(value)) if (!allowed.has(key)) throw new RangeError(`Unsupported ${name} field: ${key}`);
}
function finite8(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function positive4(value, name) {
  if (finite8(value, name) <= 0) throw new RangeError(`${name} must be positive`);
  return value;
}
function nonnegative2(value, name) {
  if (finite8(value, name) < 0) throw new RangeError(`${name} must be nonnegative`);
  return value;
}
function unit2(value, name) {
  finite8(value, name);
  if (value < 0 || value > 1) throw new RangeError(`${name} must be in 0..1`);
  return value;
}
function identifier(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${name} must be a nonempty string`);
  return value;
}
function seedValue4(value) {
  finite8(value, "seed");
  if (!Number.isSafeInteger(value)) throw new RangeError("seed must be a safe integer");
  return value >>> 0;
}
function colour(value, name) {
  if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) throw new RangeError(`${name} must be a #RRGGBB colour`);
  return value.toLowerCase();
}
function mode(value) {
  if (value !== "glaze" && value !== "wet-mix") throw new RangeError("Unknown ink layer mode");
  return value;
}
function hashText2(value) {
  let result = 2166136261;
  for (let i = 0; i < value.length; i++) result = Math.imul(result ^ value.charCodeAt(i), 16777619);
  return result >>> 0;
}
function hash5(seed, a, b) {
  let value = ((seed | 0) ^ Math.imul(a | 0, 2654435761) ^ Math.imul(b | 0, 2246822519)) >>> 0;
  value = Math.imul(value ^ value >>> 16, 2146121005);
  value = Math.imul(value ^ value >>> 15, 2221713035);
  return ((value ^ value >>> 16) >>> 0) / 4294967296;
}
function noise5(seed, along, across) {
  const x = Math.floor(along), y = Math.floor(across), a = along - x, b = across - y;
  const u = a * a * (3 - 2 * a), v = b * b * (3 - 2 * b);
  return (hash5(seed, x, y) * (1 - u) + hash5(seed, x + 1, y) * u) * (1 - v) + (hash5(seed, x, y + 1) * (1 - u) + hash5(seed, x + 1, y + 1) * u) * v;
}
function canonicalOptions2(options) {
  fields(options, processOptionFields, "ink process option");
  return Object.freeze({
    width: positive4(options.width, "width"),
    height: positive4(options.height, "height"),
    seed: seedValue4(options.seed ?? 1),
    background: colour(options.background ?? "#f5f2e9", "background"),
    ink: colour(options.ink ?? "#20251f", "ink")
  });
}
function validateStrokeSource(source) {
  fields(source, strokeFields, "ink stroke source");
  if (!Array.isArray(source.points) || source.points.length > MAX_SOURCE_POINTS2) throw new RangeError("Ink stroke sources support at most 4096 points");
  for (const point2 of source.points) fields(point2, pointFields, "ink stroke point");
  if (source.options !== void 0) {
    fields(source.options, strokeOptionFields, "ink stroke option");
    if (source.options.controls !== void 0) {
      if (!Array.isArray(source.options.controls)) throw new TypeError("controls must be an array");
      for (const control of source.options.controls) fields(control, controlFields, "ink stroke control");
    }
    if (source.options.clip !== void 0) {
      if (!Array.isArray(source.options.clip)) throw new TypeError("clip must be an array");
      for (const point2 of source.options.clip) fields(point2, vectorFields, "ink stroke clip point");
    }
  }
}
function layerState(source) {
  return {
    id: identifier(source.id, "layer.id"),
    concentration: unit2(source.concentration ?? 0.5, "layer.concentration"),
    water: unit2(source.water ?? 0.5, "layer.water"),
    at: nonnegative2(source.at ?? 0, "layer.at"),
    dryAfter: nonnegative2(source.dryAfter ?? 2, "layer.dryAfter"),
    mode: mode(source.mode ?? "glaze"),
    ...source.edgeSoftness === void 0 ? {} : { edgeSoftness: unit2(source.edgeSoftness, "layer.edgeSoftness") },
    ...source.edgeDeposit === void 0 ? {} : { edgeDeposit: unit2(source.edgeDeposit, "layer.edgeDeposit") },
    ...source.edgeWidth === void 0 ? {} : { edgeWidth: unit2(source.edgeWidth, "layer.edgeWidth") }
  };
}
function copySourceOptions(source, canonical) {
  if (source.options === void 0) return void 0;
  const owned = {};
  for (const key of Object.keys(source.options)) {
    const value = source.options[key];
    if (value !== void 0) owned[key] = key === "id" ? value : canonical[key];
  }
  return Object.freeze(owned);
}
function createInkProcessDocument(layers, options) {
  const canonical = canonicalOptions2(options);
  if (!Array.isArray(layers) || layers.length > MAX_LAYERS) throw new RangeError("Ink processes support at most 32 layers");
  const ids = /* @__PURE__ */ new Set(), markIds = /* @__PURE__ */ new Set();
  let count = 0, previousTime = -1;
  const authored = [], compiled = [];
  for (const source of layers) {
    fields(source, layerFields, "ink layer source");
    const state = layerState(source);
    if (ids.has(state.id)) throw new RangeError(`Duplicate ink layer id: ${state.id}`);
    ids.add(state.id);
    if (state.at < previousTime) throw new RangeError("Ink layer times must be nondecreasing");
    previousTime = state.at;
    if (!Array.isArray(source.strokes) || (count += source.strokes.length) > MAX_STROKES) throw new RangeError("Ink processes support at most 512 strokes");
    const strokes = [], marks = [];
    for (const [index, stroke] of source.strokes.entries()) {
      validateStrokeSource(stroke);
      const localId = stroke.options?.id ?? `stroke-${index + 1}`, id = `${state.id}/${identifier(localId, "stroke.id")}`;
      if (markIds.has(id)) throw new RangeError(`Duplicate ink stroke id: ${id}`);
      markIds.add(id);
      const document = createBrushDocument(stroke.points, {
        ...stroke.options,
        id,
        seed: stroke.options?.seed ?? (canonical.seed ^ hashText2(id)) >>> 0,
        ink: canonical.ink,
        start: 0,
        duration: 0
      });
      const ownedOptions = copySourceOptions(stroke, document.recipe.options);
      strokes.push(Object.freeze({ points: document.recipe.points, ...ownedOptions === void 0 ? {} : { options: ownedOptions } }));
      marks.push(document.result);
    }
    const sparse = { id: state.id, strokes: Object.freeze(strokes) };
    for (const key of ["concentration", "water", "at", "dryAfter", "mode", "edgeSoftness", "edgeDeposit", "edgeWidth"]) if (source[key] !== void 0) sparse[key] = state[key];
    authored.push(Object.freeze(sparse));
    compiled.push(Object.freeze({ ...state, marks: Object.freeze(marks) }));
  }
  const result = Object.freeze({ version: 1, ...canonical, layers: Object.freeze(compiled) });
  const recipe = Object.freeze({ version: 1, generator: GENERATOR3, layers: Object.freeze(authored), options: canonical });
  return Object.freeze({ version: 1, recipe, result });
}
function assertRecipe3(recipe) {
  if (!recipe || recipe.version !== 1) throw new RangeError("Unsupported ink process recipe version");
  fields(recipe, /* @__PURE__ */ new Set(["version", "generator", "layers", "options"]), "ink process recipe");
  if (recipe.generator !== GENERATOR3) throw new RangeError("Unsupported ink process generator");
}
function compileInkProcessRecipe(recipe) {
  assertRecipe3(recipe);
  return createInkProcessDocument(recipe.layers, recipe.options).result;
}
function reviseInkProcessDocument(document, patch) {
  if (!document || document.version !== 1) throw new RangeError("Unsupported ink process document version");
  fields(document, /* @__PURE__ */ new Set(["version", "recipe", "result"]), "ink process document");
  assertRecipe3(document.recipe);
  fields(patch, /* @__PURE__ */ new Set(["layers", "options"]), "ink process patch");
  if (patch.options !== void 0) fields(patch.options, processOptionFields, "ink process option");
  return createInkProcessDocument(
    patch.layers === void 0 ? document.recipe.layers : patch.layers,
    { ...document.recipe.options, ...patch.options }
  );
}
var maskCache = /* @__PURE__ */ new WeakMap();
var validated = /* @__PURE__ */ new WeakSet();
function deeplyFrozen(value, seen = /* @__PURE__ */ new WeakSet()) {
  if (!value || typeof value !== "object") return true;
  if (seen.has(value)) return true;
  seen.add(value);
  if (!Object.isFrozen(value)) return false;
  return Object.values(value).every((child) => deeplyFrozen(child, seen));
}
function validateProcess(process) {
  const immutable = deeplyFrozen(process);
  if (immutable && validated.has(process)) return true;
  if (!process || process.version !== 1) throw new RangeError("Unsupported ink process version");
  fields(process, /* @__PURE__ */ new Set(["version", "width", "height", "seed", "background", "ink", "layers"]), "ink process");
  canonicalOptions2({ width: process.width, height: process.height, seed: process.seed, background: process.background, ink: process.ink });
  seedValue4(process.seed);
  colour(process.background, "background");
  colour(process.ink, "ink");
  if (!Array.isArray(process.layers) || process.layers.length > MAX_LAYERS) throw new RangeError("Ink processes support at most 32 layers");
  const ids = /* @__PURE__ */ new Set(), markIds = /* @__PURE__ */ new Set();
  const scene = createInkScene({ width: 1, height: 1 });
  let previous = -1, count = 0;
  for (const layer of process.layers) {
    fields(layer, resultLayerFields, "compiled ink layer");
    const state = {
      id: identifier(layer.id, "layer.id"),
      concentration: unit2(layer.concentration, "layer.concentration"),
      water: unit2(layer.water, "layer.water"),
      at: nonnegative2(layer.at, "layer.at"),
      dryAfter: nonnegative2(layer.dryAfter, "layer.dryAfter"),
      mode: mode(layer.mode)
    };
    if (layer.edgeSoftness !== void 0) unit2(layer.edgeSoftness, "layer.edgeSoftness");
    if (layer.edgeDeposit !== void 0) unit2(layer.edgeDeposit, "layer.edgeDeposit");
    if (layer.edgeWidth !== void 0) unit2(layer.edgeWidth, "layer.edgeWidth");
    if (ids.has(state.id)) throw new RangeError(`Duplicate ink layer id: ${state.id}`);
    ids.add(state.id);
    if (state.at < previous) throw new RangeError("Ink layer times must be nondecreasing");
    previous = state.at;
    if (!Array.isArray(layer.marks) || (count += layer.marks.length) > MAX_STROKES) throw new RangeError("Ink processes support at most 512 strokes");
    for (const mark2 of layer.marks) {
      fields(mark2, markFields, "compiled ink stroke");
      if (mark2.kind !== "stroke" || mark2.start !== 0 || mark2.duration !== 0 || mark2.ink !== process.ink) throw new RangeError("Compiled ink strokes must use process pigment and layer timing");
      identifier(mark2.id, "stroke.id");
      positive4(mark2.width, "stroke.width");
      unit2(mark2.opacity, "stroke.opacity");
      unit2(mark2.dryness, "stroke.dryness");
      seedValue4(mark2.seed);
      finite8(mark2.tipAngle, "stroke.tipAngle");
      if (mark2.nib !== "round" && mark2.nib !== "flat") throw new RangeError("Unknown compiled ink nib");
      if (markIds.has(mark2.id)) throw new RangeError(`Duplicate ink stroke id: ${mark2.id}`);
      markIds.add(mark2.id);
      if (mark2.brush !== void 0) {
        fields(mark2.brush, /* @__PURE__ */ new Set(["version", "controls"]), "compiled ink brush");
        if (Array.isArray(mark2.brush.controls)) for (const control of mark2.brush.controls) fields(control, controlFields, "compiled ink control");
      }
      scene.stroke(mark2.points, mark2);
    }
  }
  if (immutable) validated.add(process);
  return immutable;
}
function inside(x, y, polygon3) {
  let contained = false;
  for (let i = 0, j = polygon3.length - 1; i < polygon3.length; j = i++) {
    const a = polygon3[i], b = polygon3[j];
    if (a.y > y !== b.y > y && x < (b.x - a.x) * (y - a.y) / (b.y - a.y) + a.x) contained = !contained;
  }
  return contained;
}
function rasterStroke(mark2, columns, rows, cellSize, paperSeed, edgeSoftness = 0, interior) {
  const result = new Float32Array(columns * rows);
  const nearest = new Float64Array(result.length);
  nearest.fill(Infinity);
  const marginAt = new Float64Array(result.length);
  marginAt.fill(-Infinity);
  const alongAt = new Float64Array(result.length), acrossAt = new Float64Array(result.length);
  const drynessAt = new Float32Array(result.length), loadAt = new Float32Array(result.length);
  const widthAt = new Float64Array(result.length), sideAt = new Float32Array(result.length);
  const samples = sampleBrushContact(mark2);
  const segment = (a, b) => {
    const dx = b.centerX - a.centerX, dy = b.centerY - a.centerY, lengthSquared = dx * dx + dy * dy;
    const reach = Math.max(a.halfWidth, b.halfWidth) + cellSize;
    if (!Number.isFinite(lengthSquared) || !Number.isFinite(reach)) throw new RangeError("Ink contact exceeds finite raster coordinates");
    if (a.halfWidth === 0 && b.halfWidth === 0) return;
    const x0 = Math.max(0, Math.floor((Math.min(a.centerX, b.centerX) - reach) / cellSize));
    const x1 = Math.min(columns - 1, Math.floor((Math.max(a.centerX, b.centerX) + reach) / cellSize));
    const y0 = Math.max(0, Math.floor((Math.min(a.centerY, b.centerY) - reach) / cellSize));
    const y1 = Math.min(rows - 1, Math.floor((Math.max(a.centerY, b.centerY) + reach) / cellSize));
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
      const px = (x + 0.5) * cellSize, py = (y + 0.5) * cellSize;
      if (mark2.clip && !inside(px, py, mark2.clip)) continue;
      const t = lengthSquared > 1e-16 ? clamp5(((px - a.centerX) * dx + (py - a.centerY) * dy) / lengthSquared) : 0;
      const halfWidth2 = a.halfWidth + (b.halfWidth - a.halfWidth) * t;
      if (halfWidth2 <= 0) continue;
      const cx = a.centerX + dx * t, cy = a.centerY + dy * t, distance = Math.hypot(px - cx, py - cy);
      const edge = clamp5((halfWidth2 - distance) / cellSize + 0.5);
      if (edge === 0) continue;
      const index = y * columns + x;
      if (edge > result[index]) result[index] = edge;
      marginAt[index] = Math.max(marginAt[index], halfWidth2 - distance);
      if (distance < nearest[index]) {
        nearest[index] = distance;
        alongAt[index] = a.distance + (b.distance - a.distance) * t;
        const nx = a.nx + (b.nx - a.nx) * t, ny = a.ny + (b.ny - a.ny) * t;
        acrossAt[index] = ((px - cx) * nx + (py - cy) * ny) / Math.max(1, mark2.width);
        drynessAt[index] = a.dryness + (b.dryness - a.dryness) * t;
        loadAt[index] = a.inkLoad + (b.inkLoad - a.inkLoad) * t;
        widthAt[index] = halfWidth2;
        sideAt[index] = a.side + (b.side - a.side) * t;
      }
    }
  };
  for (let i = 1; i < samples.length; i++) segment(samples[i - 1], samples[i]);
  for (let i = 0; i < result.length; i++) {
    if (result[i] === 0) continue;
    const along = alongAt[i], across = acrossAt[i], dryness = drynessAt[i], load = loadAt[i];
    const warp = noise5(mark2.seed ^ 2123724318, along / Math.max(5, mark2.width * 0.36), across * 4 + 13) - 0.5;
    const fibers = noise5(mark2.seed, along / Math.max(3, mark2.width * 0.39), across * 27 + warp * 2.8 + 19);
    const shared = noise5(mark2.seed ^ 1374496523, along / Math.max(6, mark2.width), across * 5 + warp + 7);
    const threshold = 0.08 + dryness * 0.6 + (1 - load) * 0.38;
    const broken = clamp5((fibers * 0.73 + shared * 0.27 - threshold) / 0.21);
    const solid = clamp5(1 - dryness / 0.64);
    const px = (i % columns + 0.5) * cellSize, py = (Math.floor(i / columns) + 0.5) * cellSize;
    const paper = noise5(paperSeed ^ 928523121, px * 0.29 + py * 0.09, py * 0.34);
    const roughSide = clamp5(0.5 + across * sideAt[i] * 2.5);
    const erosion = Math.min(widthAt[i] * 0.23, 0.35 + mark2.width * 0.028) * (0.1 + paper * 0.42 + shared * 0.48) * (0.7 + roughSide * Math.abs(sideAt[i]) * 1.8);
    const edge = Math.min(result[i], clamp5((marginAt[i] - erosion) / cellSize + 0.5));
    const body = 0.72 + 0.28 * noise5(paperSeed ^ 328562723, px * 0.17, py * 0.23);
    result[i] = edge * load * mark2.opacity * body * (solid + (1 - solid) * broken);
    if (interior && result[i] > 0) interior[i] = Math.max(interior[i], clamp5((marginAt[i] - erosion) / Math.max(cellSize, widthAt[i])));
    if (edgeSoftness > 0) {
      const reach = Math.max(cellSize, widthAt[i]) * edgeSoftness * (0.84 + paper * 0.32);
      const fade = clamp5((marginAt[i] - erosion) / reach);
      result[i] = result[i] * fade * fade * (3 - 2 * fade);
    }
  }
  return result;
}
function prepareMasks(process, columns, rows, cellSize, immutable) {
  const cached = immutable ? maskCache.get(process)?.get(cellSize) : void 0;
  if (cached) return cached;
  const layers = process.layers.map((layer) => {
    const coverage = new Float32Array(columns * rows);
    const deposit = layer.edgeDeposit ?? 0, bandWidth = layer.edgeWidth ?? 0.22;
    const interior = deposit > 0 && bandWidth > 0 ? new Float32Array(coverage.length) : void 0;
    for (const mark2 of layer.marks) {
      const contact = rasterStroke(mark2, columns, rows, cellSize, process.seed, layer.edgeSoftness ?? 0, interior);
      for (let i = 0; i < contact.length; i++) coverage[i] = coverage[i] + contact[i];
    }
    if (interior) {
      const seed = process.seed ^ hashText2(layer.id);
      const weights = new Float32Array(coverage.length);
      let mass = 0, weightedMass = 0;
      for (let i = 0; i < coverage.length; i++) {
        const contact = coverage[i];
        if (contact <= 0) continue;
        const x = (i % columns + 0.5) * cellSize, y = (Math.floor(i / columns) + 0.5) * cellSize;
        const field = getFractalNoise2D(x / 19 + 7.173, y / 23 + 11.317, seed, 3);
        const depth = clamp5(interior[i] / (0.88 + field * 0.27));
        const gate = clamp5(0.48 + field * 1.1);
        weights[i] = 1 + deposit * 4 * accumulateEdge(depth, bandWidth) * gate;
        mass += contact;
        weightedMass += contact * weights[i];
      }
      const normalization = weightedMass > 0 ? mass / weightedMass : 1;
      for (let i = 0; i < coverage.length; i++) if (coverage[i] > 0) coverage[i] = coverage[i] * weights[i] * normalization;
    }
    return coverage;
  });
  const needsWetMaps = process.layers.some((layer, index) => layer.mode === "wet-mix" && layer.water > 0 && process.layers.slice(0, index).some((earlier) => earlier.water > 0 && earlier.dryAfter > layer.at - earlier.at));
  const paper = needsWetMaps ? paperField(process.seed, columns, rows, cellSize) : { uptake: new Float32Array(0), horizontal: new Float32Array(0), vertical: new Float32Array(0) };
  const radius = Math.max(1, Math.min(8, Math.ceil(7 / cellSize)));
  const wetLayers = needsWetMaps ? layers.map((layer) => spread(layer, columns, rows, radius, paper)) : [];
  const masks = { columns, rows, layers, wetLayers, paper };
  if (immutable) {
    let scales = maskCache.get(process);
    if (!scales) {
      scales = /* @__PURE__ */ new Map();
      maskCache.set(process, scales);
    }
    if (scales.size >= 2) scales.delete(scales.keys().next().value);
    scales.set(cellSize, masks);
  }
  return masks;
}
function paperField(seed, columns, rows, cellSize) {
  const uptake = new Float32Array(columns * rows), horizontal = new Float32Array(uptake.length), vertical = new Float32Array(uptake.length);
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const px = (x + 0.5) * cellSize, py = (y + 0.5) * cellSize, i = y * columns + x;
    const coarse = noise5(seed ^ 1831565813, px / 11, py / 11);
    const grain = noise5(seed ^ 73244475, px / 3.2 + py / 17, py / 4.7);
    uptake[i] = clamp5((coarse * 0.65 + grain * 0.35 - 0.18) / 0.65);
    const angle = (noise5(seed ^ 295559667, px / 43, py / 43) - 0.5) * Math.PI;
    horizontal[i] = 0.22 + 0.78 * Math.cos(angle) ** 2;
    vertical[i] = 0.22 + 0.78 * Math.sin(angle) ** 2;
  }
  return { uptake, horizontal, vertical };
}
function spread(mask, columns, rows, radius, paper) {
  const result = new Float32Array(mask.length);
  const stencil = [];
  for (let dy = -radius; dy <= radius; dy++) for (let dx = -radius; dx <= radius; dx++) {
    const distance = Math.hypot(dx, dy);
    if (distance < radius + 0.25) stencil.push({ dx, dy, weight: clamp5((radius + 0.25 - distance) / Math.max(1, radius * 0.65)) });
  }
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const i = y * columns + x;
    let maximum = clamp5(mask[i]);
    for (const { dx, dy, weight } of stencil) {
      const sx = x + dx, sy = y + dy;
      if (sx >= 0 && sx < columns && sy >= 0 && sy < rows) maximum = Math.max(maximum, mask[sy * columns + sx] * weight);
    }
    result[i] = Math.max(clamp5(mask[i]), clamp5(maximum) * (0.22 + paper.uptake[i] * 0.78));
  }
  return result;
}
function exchange(density, mobility, columns, rows, steps, paper) {
  const change = new Float64Array(density.length);
  const capacity = new Float32Array(density.length);
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const i = y * columns + x, wet = mobility[i];
    let neighbour = wet;
    if (x > 0) neighbour = Math.max(neighbour, mobility[i - 1]);
    if (x + 1 < columns) neighbour = Math.max(neighbour, mobility[i + 1]);
    if (y > 0) neighbour = Math.max(neighbour, mobility[i - columns]);
    if (y + 1 < rows) neighbour = Math.max(neighbour, mobility[i + columns]);
    const front = neighbour > 0 ? clamp5((neighbour - wet) / neighbour) : 0;
    capacity[i] = 0.75 + paper.uptake[i] * 0.75 + front * (0.35 + paper.uptake[i] * 0.55);
  }
  for (let step = 0; step < steps; step++) {
    change.fill(0);
    const pair = (a, b, alignment) => {
      const conductance = Math.min(mobility[a], mobility[b]) * 0.13 * (alignment[a] + alignment[b]) * 0.5;
      if (conductance <= 0) return;
      const flux = (density[a] / capacity[a] - density[b] / capacity[b]) * conductance;
      change[a] = change[a] - flux;
      change[b] = change[b] + flux;
    };
    for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
      const index = y * columns + x;
      if (x + 1 < columns) pair(index, index + 1, paper.horizontal);
      if (y + 1 < rows) pair(index, index + columns, paper.vertical);
    }
    for (let i = 0; i < density.length; i++) density[i] = density[i] + change[i];
  }
}
function depositAtFront(density, mobility, front, columns, rows, cellSize, paper, seed, marks) {
  const reach = Math.max(1, Math.min(4, Math.round(3 / cellSize)));
  const horizontal = new Float32Array(density.length), vertical = new Float32Array(density.length);
  const release = new Float32Array(density.length);
  const directions = marks.flatMap((mark2) => mark2.points.slice(1).flatMap((b, index) => {
    const a = mark2.points[index], dx = b.x - a.x, dy = b.y - a.y, lengthSquared = dx * dx + dy * dy;
    if (lengthSquared <= cellSize * cellSize) return [];
    const length = Math.sqrt(lengthSquared);
    return [{ x: a.x, y: a.y, dx, dy, lengthSquared, ux: dx / length, uy: dy / length, reachSquared: (mark2.width * 0.9 + 8) ** 2 }];
  }));
  const at2 = (x, y) => front[Math.max(0, Math.min(rows - 1, y)) * columns + Math.max(0, Math.min(columns - 1, x))];
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const i = y * columns + x, center = front[i];
    if (mobility[i] <= 0 || center <= 0) continue;
    const left = at2(x - reach, y), right = at2(x + reach, y), up = at2(x, y - reach), down = at2(x, y + reach);
    const high = Math.max(center, left, right, up, down), low = Math.min(center, left, right, up, down);
    const margin = clamp5(((high - low) / Math.max(0.01, high) - 0.22) / 0.48);
    if (margin === 0) continue;
    const px = (x + 0.5) * cellSize, py = (y + 0.5) * cellSize;
    const gate = clamp5((noise5(seed ^ 2052007857, px / 17 + py / 61, py / 13) * 0.72 + noise5(seed ^ 1407593427, px / 6, py / 8) * 0.28 - 0.47) / 0.25);
    if (gate === 0) continue;
    const dx = (left - right) * paper.horizontal[i], dy = (up - down) * paper.vertical[i];
    const magnitude = Math.abs(dx) + Math.abs(dy);
    if (magnitude <= 1e-8) continue;
    horizontal[i] = dx / magnitude;
    vertical[i] = dy / magnitude;
    let directionX = 0, directionY = 0, nearest = Infinity;
    for (const direction of directions) {
      const t = clamp5(((px - direction.x) * direction.dx + (py - direction.y) * direction.dy) / direction.lengthSquared);
      const distanceSquared = (px - direction.x - direction.dx * t) ** 2 + (py - direction.y - direction.dy * t) ** 2;
      if (distanceSquared < nearest && distanceSquared < direction.reachSquared) {
        nearest = distanceSquared;
        directionX = direction.ux;
        directionY = direction.uy;
      }
    }
    const facing = 0.28 + 0.72 * clamp5(0.5 + (dx * directionX + dy * directionY) / magnitude);
    const lodged = clamp5((0.58 - center / Math.max(0.01, high)) / 0.3) * gate;
    release[i] = 0.22 * margin * gate * facing * (1 - lodged) * Math.sqrt(clamp5(mobility[i]));
  }
  const change = new Float64Array(density.length);
  const steps = Math.max(3, Math.min(10, Math.round(9 / cellSize)));
  for (let step = 0; step < steps; step++) {
    change.fill(0);
    for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
      const i = y * columns + x, amount = density[i] * release[i];
      if (amount <= 0) continue;
      const dx = horizontal[i], dy = vertical[i];
      const move = (target, weight) => {
        if (target < 0 || target >= density.length || mobility[target] <= 0 || front[target] <= 0) return;
        const pigment = amount * weight;
        change[i] = change[i] - pigment;
        change[target] = change[target] + pigment;
      };
      if (dx > 0 && x + 1 < columns) move(i + 1, dx);
      else if (dx < 0 && x > 0) move(i - 1, -dx);
      if (dy > 0 && y + 1 < rows) move(i + columns, dy);
      else if (dy < 0 && y > 0) move(i - columns, -dy);
    }
    for (let i = 0; i < density.length; i++) density[i] = density[i] + change[i];
  }
}
function rasterizeInkProcess(process, options = {}) {
  fields(options, /* @__PURE__ */ new Set(["time", "cellSize"]), "ink raster option");
  if (options.time !== void 0) finite8(options.time, "time");
  const cellSize = positive4(options.cellSize ?? 2, "cellSize"), immutable = validateProcess(process);
  const columns = Math.max(1, Math.ceil(process.width / cellSize)), rows = Math.max(1, Math.ceil(process.height / cellSize));
  if (!Number.isSafeInteger(columns * rows) || columns * rows > MAX_CELLS) throw new RangeError("Ink raster supports at most 262144 cells; increase cellSize");
  const masks = prepareMasks(process, columns, rows, cellSize, immutable), density = new Float32Array(columns * rows);
  const activeLayers = process.layers.filter((layer) => options.time === void 0 || layer.at <= options.time).length;
  const contributions = [];
  let interactions = 0;
  for (let index = 0; index < activeLayers; index++) {
    const layer = process.layers[index], contact = masks.layers[index];
    const pigment = new Float32Array(density.length);
    for (let i = 0; i < pigment.length; i++) pigment[i] = contact[i] * layer.concentration * 2.6;
    contributions.push(pigment);
    if (layer.mode !== "wet-mix" || layer.water === 0) continue;
    const previousWater = new Float32Array(density.length);
    const movable = [index];
    for (let previous = 0; previous < index; previous++) {
      const earlier = process.layers[previous];
      if (earlier.water === 0 || earlier.dryAfter === 0 || layer.at - earlier.at >= earlier.dryAfter) continue;
      const remaining = earlier.water * (1 - (layer.at - earlier.at) / earlier.dryAfter);
      const wet = masks.wetLayers[previous];
      for (let i = 0; i < wet.length; i++) previousWater[i] = Math.max(previousWater[i], wet[i] * remaining);
      movable.push(previous);
    }
    if (movable.length === 1) continue;
    const incoming = masks.wetLayers[index], mobility = new Float32Array(density.length), front = new Float32Array(density.length);
    let affected = false;
    for (let i = 0; i < mobility.length; i++) {
      const x = i % columns, y = Math.floor(i / columns);
      const irregular = 0.24 + 0.76 * noise5(process.seed ^ hashText2(layer.id), x * cellSize / 7, y * cellSize / 7);
      const amount = previousWater[i] * incoming[i] * layer.water * irregular;
      mobility[i] = amount;
      front[i] = Math.min(previousWater[i], incoming[i] * layer.water);
      if (amount > 0) affected = true;
    }
    if (affected) {
      const steps = Math.max(8, Math.min(64, Math.round(64 / cellSize)));
      for (const contributor of movable) {
        if (process.layers[contributor].concentration > 0) {
          exchange(contributions[contributor], mobility, columns, rows, steps, masks.paper);
          depositAtFront(contributions[contributor], mobility, front, columns, rows, cellSize, masks.paper, process.seed ^ hashText2(layer.id), layer.marks);
        }
      }
      interactions++;
    }
  }
  for (const contribution of contributions) for (let i = 0; i < density.length; i++) density[i] = density[i] + contribution[i];
  return { width: process.width, height: process.height, columns, rows, cellSize, density, activeLayers, interactions };
}

// src/ink-canvas.ts
var images = /* @__PURE__ */ new WeakMap();
var MAX_IMAGES = 8;
function isImmutable(process) {
  return Object.isFrozen(process) && Array.isArray(process.layers) && Object.isFrozen(process.layers) && process.layers.every((layer) => Object.isFrozen(layer) && Object.isFrozen(layer.marks) && layer.marks.every((mark2) => Object.isFrozen(mark2) && Object.isFrozen(mark2.points) && mark2.points.every(Object.isFrozen) && (mark2.clip === void 0 || Object.isFrozen(mark2.clip) && mark2.clip.every(Object.isFrozen)) && (mark2.brush === void 0 || Object.isFrozen(mark2.brush) && Object.isFrozen(mark2.brush.controls) && mark2.brush.controls.every(Object.isFrozen))));
}
function rgb(color) {
  return [1, 3, 5].map((offset) => Number.parseInt(color.slice(offset, offset + 2), 16));
}
function prepare3(process, frame, ctx) {
  let canvas;
  if (typeof OffscreenCanvas !== "undefined") canvas = new OffscreenCanvas(frame.columns, frame.rows);
  else if (ctx.canvas.ownerDocument) {
    canvas = ctx.canvas.ownerDocument.createElement("canvas");
    canvas.width = frame.columns;
    canvas.height = frame.rows;
  } else throw new Error("Ink process rendering requires a browser Canvas implementation");
  const target = canvas.getContext("2d");
  if (!target) throw new Error("Unable to create ink process Canvas 2D context");
  const image = target.createImageData(frame.columns, frame.rows);
  const paper = rgb(process.background), ink = rgb(process.ink);
  for (let index = 0; index < frame.density.length; index++) {
    const transmission = Math.exp(-frame.density[index]);
    for (let channel = 0; channel < 3; channel++) {
      image.data[index * 4 + channel] = Math.round(ink[channel] + (paper[channel] - ink[channel]) * transmission);
    }
    image.data[index * 4 + 3] = 255;
  }
  target.putImageData(image, 0, 0);
  return { canvas, stats: Object.freeze({
    activeLayers: frame.activeLayers,
    interactions: frame.interactions,
    columns: frame.columns,
    rows: frame.rows,
    cellSize: frame.cellSize
  }) };
}
function renderInkProcess(ctx, process, options = {}) {
  if (!options || typeof options !== "object" || Array.isArray(options)) throw new TypeError("Ink render options must be an object");
  for (const key2 of Object.keys(options)) if (key2 !== "time" && key2 !== "cellSize") throw new RangeError(`Unsupported ink render option: ${key2}`);
  if (options.time !== void 0 && !Number.isFinite(options.time)) throw new RangeError("time must be finite");
  if (options.cellSize !== void 0 && (!Number.isFinite(options.cellSize) || options.cellSize <= 0)) throw new RangeError("cellSize must be positive and finite");
  const immutable = !!process && isImmutable(process);
  const phase = immutable ? process.layers.filter((layer) => layer.at <= (options.time ?? Infinity)).length : 0;
  const key = `${options.cellSize ?? 2}:${phase}`;
  const cache2 = immutable ? images.get(process) : void 0;
  let prepared = cache2?.get(key);
  if (!prepared) {
    prepared = prepare3(process, rasterizeInkProcess(process, options), ctx);
    if (immutable) {
      const frames = cache2 ?? /* @__PURE__ */ new Map();
      frames.set(key, prepared);
      if (frames.size > MAX_IMAGES) frames.delete(frames.keys().next().value);
      images.set(process, frames);
    }
  }
  ctx.save();
  try {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    ctx.shadowBlur = 0;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
    ctx.filter = "none";
    ctx.imageSmoothingEnabled = true;
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    ctx.drawImage(prepared.canvas, 0, 0, ctx.canvas.width, ctx.canvas.height);
  } finally {
    ctx.restore();
  }
  return prepared.stats;
}

// src/water-process.ts
var GENERATOR4 = "water-event-process-v1";
var commonFields = ["kind", "id", "water", "at", "dryAfter", "mode", "strokes", "edgeSoftness", "edgeDeposit", "edgeWidth"];
var inkFields = /* @__PURE__ */ new Set([...commonFields, "concentration"]);
var waterFields = /* @__PURE__ */ new Set([...commonFields, "droplets"]);
var optionFields = /* @__PURE__ */ new Set(["width", "height", "seed", "background", "ink"]);
var dropletFields = /* @__PURE__ */ new Set(["id", "x", "y", "radius"]);
function fields2(value, allowed, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  for (const key of Object.keys(value)) if (!allowed.has(key)) throw new RangeError(`Unsupported ${name} field: ${key}`);
}
function finite9(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function droplet(source, ids) {
  fields2(source, dropletFields, "water droplet");
  if (typeof source.id !== "string" || !source.id.trim()) throw new TypeError("water droplet.id must be a nonempty string");
  if (ids.has(source.id)) throw new RangeError(`Duplicate water droplet id: ${source.id}`);
  ids.add(source.id);
  const x = finite9(source.x, "water droplet.x"), y = finite9(source.y, "water droplet.y");
  const radius = finite9(source.radius, "water droplet.radius");
  if (radius <= 0 || !Number.isFinite(radius * 2)) throw new RangeError("water droplet.radius must have a positive finite diameter");
  return Object.freeze({ id: source.id, x, y, radius });
}
function dropContact(source) {
  const extent = Math.max(1e-6, source.radius * 1e-4);
  const left = finite9(source.x - extent, "water droplet left extent");
  const right = finite9(source.x + extent, "water droplet right extent");
  return {
    points: [{ x: left, y: source.y, pressure: 1 }, { x: right, y: source.y, pressure: 1 }],
    options: {
      id: `drop:${source.id}`,
      width: source.radius * 2,
      opacity: 1,
      dryness: 0,
      axis: "center",
      smoothing: "linear"
    }
  };
}
function createWaterProcessDocument(events, options) {
  if (!Array.isArray(events) || events.length > 32) throw new RangeError("Water processes support at most 32 events");
  const layers = [], dropsByEvent = [];
  let contacts = 0;
  for (const source of events) {
    if (!source || source.kind !== "ink" && source.kind !== "water") throw new RangeError("Water event kind must be ink or water");
    fields2(source, source.kind === "ink" ? inkFields : waterFields, "water event");
    for (const key of ["concentration", "water", "at", "dryAfter", "mode", "edgeSoftness", "edgeDeposit", "edgeWidth"]) {
      if (key in source && source[key] === null) throw new TypeError(`water event.${key} cannot be null`);
    }
    if (source.kind === "ink" && !Array.isArray(source.strokes)) throw new TypeError("Ink events require a strokes array");
    if (source.strokes !== void 0 && !Array.isArray(source.strokes)) throw new TypeError("Water event strokes must be an array");
    const rawDrops = source.kind === "water" ? source.droplets : void 0;
    if (rawDrops !== void 0 && !Array.isArray(rawDrops)) throw new TypeError("Water event droplets must be an array");
    contacts += (source.strokes?.length ?? 0) + (rawDrops?.length ?? 0);
    if (contacts > 512) throw new RangeError("Water processes support at most 512 stroke and droplet contacts");
    if (source.kind === "water" && (source.strokes?.length ?? 0) + (rawDrops?.length ?? 0) === 0) {
      throw new RangeError("Clear water events require at least one stroke or droplet contact");
    }
    const ids = /* @__PURE__ */ new Set();
    const drops = rawDrops === void 0 ? void 0 : Object.freeze(rawDrops.map((value) => droplet(value, ids)));
    dropsByEvent.push(drops);
    layers.push({
      id: source.id,
      strokes: [...source.strokes ?? [], ...drops?.map(dropContact) ?? []],
      concentration: source.kind === "water" ? 0 : source.concentration ?? 0.5,
      water: source.water ?? (source.kind === "water" ? 1 : 0.5),
      at: source.at ?? 0,
      dryAfter: source.dryAfter ?? 2,
      mode: source.mode ?? "wet-mix",
      ...source.edgeSoftness === void 0 ? {} : { edgeSoftness: source.edgeSoftness },
      ...source.edgeDeposit === void 0 ? {} : { edgeDeposit: source.edgeDeposit },
      ...source.edgeWidth === void 0 ? {} : { edgeWidth: source.edgeWidth }
    });
  }
  const compiled = createInkProcessDocument(layers, options);
  const authored = events.map((source, index) => {
    const owned = compiled.recipe.layers[index];
    const sparse = { kind: source.kind, id: owned.id };
    if (source.strokes !== void 0) sparse.strokes = Object.freeze(owned.strokes.slice(0, source.strokes.length));
    if (source.kind === "water" && source.droplets !== void 0) sparse.droplets = dropsByEvent[index];
    for (const key of ["concentration", "water", "at", "dryAfter", "mode", "edgeSoftness", "edgeDeposit", "edgeWidth"]) {
      if (key in source && source[key] !== void 0) sparse[key] = owned[key];
    }
    return Object.freeze(sparse);
  });
  const recipe = Object.freeze({
    version: 1,
    generator: GENERATOR4,
    events: Object.freeze(authored),
    options: compiled.recipe.options
  });
  return Object.freeze({ version: 1, recipe, result: compiled.result });
}
function assertRecipe4(recipe) {
  if (!recipe || recipe.version !== 1) throw new RangeError("Unsupported water process recipe version");
  fields2(recipe, /* @__PURE__ */ new Set(["version", "generator", "events", "options"]), "water process recipe");
  if (recipe.generator !== GENERATOR4) throw new RangeError("Unsupported water process generator");
}
function compileWaterProcessRecipe(recipe) {
  assertRecipe4(recipe);
  return createWaterProcessDocument(recipe.events, recipe.options).result;
}
function reviseWaterProcessDocument(document, patch) {
  if (!document || document.version !== 1) throw new RangeError("Unsupported water process document version");
  fields2(document, /* @__PURE__ */ new Set(["version", "recipe", "result"]), "water process document");
  assertRecipe4(document.recipe);
  fields2(patch, /* @__PURE__ */ new Set(["events", "options"]), "water process patch");
  if (patch.options !== void 0) fields2(patch.options, optionFields, "water process option");
  return createWaterProcessDocument(
    patch.events === void 0 ? document.recipe.events : patch.events,
    { ...document.recipe.options, ...patch.options }
  );
}

// src/cun-document.ts
var GENERATOR5 = "ink-cun-guides-v1";
var programFields = /* @__PURE__ */ new Set(["technique", "width", "height", "seed", "background", "ink", "strokeWidth", "dryness", "opacity", "strength"]);
var strokeFields2 = /* @__PURE__ */ new Set(["id", "points", "options"]);
var strokeOptionFields2 = /* @__PURE__ */ new Set([
  "width",
  "opacity",
  "dryness",
  "smoothing",
  "clip",
  "start",
  "duration",
  "axis",
  "entry",
  "finish",
  "pressureProfile",
  "turn",
  "rhythm",
  "drag",
  "strength",
  "controls"
]);
var pointFields2 = /* @__PURE__ */ new Set(["x", "y", "pressure"]);
var clipFields = /* @__PURE__ */ new Set(["x", "y"]);
var controlFields2 = /* @__PURE__ */ new Set(["at", "pressure", "side", "angle", "dryness", "inkLoad"]);
var defaults = {
  pima: { width: 6, dryness: 0.48, opacity: 0.74 },
  axe: { width: 26, dryness: 0.55, opacity: 0.84 },
  zhedai: { width: 13, dryness: 0.6, opacity: 0.75 },
  jiesuo: { width: 5, dryness: 0.52, opacity: 0.76 },
  luanchai: { width: 7, dryness: 0.66, opacity: 0.76 },
  niumao: { width: 3, dryness: 0.45, opacity: 0.66 },
  "cloud-study": { width: 6, dryness: 0.55, opacity: 0.74 },
  "point-study": { width: 10, dryness: 0.48, opacity: 0.8 },
  heye: { width: 7, dryness: 0.5, opacity: 0.76 }
};
function fields3(value, allowed, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new RangeError(`Unsupported ${name} field: ${key}`);
    if (value[key] === null) throw new TypeError(`${name}.${key} cannot be null`);
  }
}
function finite10(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function positive5(value, name) {
  if (finite10(value, name) <= 0) throw new RangeError(`${name} must be positive`);
  return value;
}
function unit3(value, name) {
  if (finite10(value, name) < 0 || value > 1) throw new RangeError(`${name} must be in 0..1`);
  return value;
}
function text3(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${name} must be a nonempty string`);
  return value;
}
function colour2(value, name) {
  if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) throw new RangeError(`${name} must be a #RRGGBB colour`);
  return value.toLowerCase();
}
function hashText3(value) {
  let hash8 = 2166136261;
  for (let i = 0; i < value.length; i++) hash8 = Math.imul(hash8 ^ value.charCodeAt(i), 16777619);
  return hash8 >>> 0;
}
function validateProgram(options) {
  fields3(options, programFields, "cun option");
  if (!["pima", "axe", "zhedai", "jiesuo", "luanchai", "niumao", "cloud-study", "point-study", "heye"].includes(options.technique)) {
    throw new RangeError("Unsupported cun technique");
  }
  positive5(options.width, "width");
  positive5(options.height, "height");
  if (options.seed !== void 0 && !Number.isSafeInteger(finite10(options.seed, "seed"))) throw new RangeError("seed must be a safe integer");
  for (const key of ["background", "ink"]) if (options[key] !== void 0) colour2(options[key], key);
  if (options.strokeWidth !== void 0) positive5(options.strokeWidth, "strokeWidth");
  for (const key of ["dryness", "opacity", "strength"]) if (options[key] !== void 0) unit3(options[key], key);
}
function validateSource(source) {
  fields3(source, strokeFields2, "cun stroke");
  text3(source.id, "cun stroke.id");
  if (!Array.isArray(source.points) || source.points.length < 2 || source.points.length > 4096) {
    throw new RangeError("A cun source path supports 2..4096 points");
  }
  for (const point2 of source.points) {
    fields3(point2, pointFields2, "cun point");
    finite10(point2.x, "point.x");
    finite10(point2.y, "point.y");
    if (point2.pressure !== void 0) unit3(point2.pressure, "point.pressure");
  }
  if (source.options === void 0) return;
  fields3(source.options, strokeOptionFields2, "cun stroke option");
  if (source.options.clip !== void 0) {
    if (!Array.isArray(source.options.clip) || source.options.clip.length > 4096) throw new RangeError("cun stroke clip supports at most 4096 points");
    for (const point2 of source.options.clip) fields3(point2, clipFields, "cun clip point");
  }
  if (source.options.controls !== void 0) {
    if (!Array.isArray(source.options.controls)) throw new TypeError("cun controls must be an array");
    for (const control of source.options.controls) fields3(control, controlFields2, "cun control");
  }
}
function foldedControls(points) {
  const distances = [0];
  for (let i = 1; i < points.length; i++) distances.push(distances[i - 1] + Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y));
  const total = distances.at(-1);
  const controls = /* @__PURE__ */ new Map([
    [0, { at: 0, pressure: 0.72, side: 0.7, angle: Math.PI / 2 }],
    [1, { at: 1, pressure: 0.26, side: 0.55, angle: Math.PI / 2 }]
  ]);
  let counted = 0;
  for (let i = 1; i < points.length - 1 && counted < 42; i++) {
    const a = points[i - 1], b = points[i], c = points[i + 1];
    const before = distances[i] - distances[i - 1], after = distances[i + 1] - distances[i];
    if (!(before > 0 && after > 0 && total > 0 && Number.isFinite(total))) continue;
    const dot = ((b.x - a.x) * (c.x - b.x) + (b.y - a.y) * (c.y - b.y)) / before / after;
    if (dot > 0.94 || dot < -0.96) continue;
    const at2 = distances[i] / total, reach = Math.min(before / total * 0.22, after / total * 0.22, 0.035);
    controls.set(at2 - reach, { at: at2 - reach, pressure: 0.54, side: 0.42, angle: Math.PI * 0.38 });
    controls.set(at2, { at: at2, pressure: 0.94, side: 0.88, angle: Math.PI * 0.6 });
    controls.set(at2 + reach, { at: at2 + reach, pressure: 0.72, side: 0.7, angle: Math.PI / 2 });
    counted++;
  }
  return [...controls.values()].sort((a, b) => a.at - b.at);
}
function contactPreset(technique, points) {
  if (technique === "pima") return {
    axis: "center",
    smoothing: "catmull-rom",
    turn: "preserve",
    finish: "open",
    controls: [
      { at: 0, pressure: 0.45, side: 0.1, angle: 1.2 },
      { at: 0.15, pressure: 0.96, side: 0.18, angle: 1.35 },
      { at: 0.56, pressure: 0.73, side: 0.26, angle: 1.55 },
      { at: 1, pressure: 0.2, side: 0.1, angle: 1.4 }
    ]
  };
  if (technique === "axe") return {
    axis: "side",
    smoothing: "linear",
    turn: "angular",
    finish: "open",
    controls: [
      { at: 0, pressure: 1, side: 0.95, angle: 1.3 },
      { at: 0.18, pressure: 1, side: 1, angle: 1.4 },
      { at: 0.62, pressure: 0.65, side: 0.84, angle: 1.8 },
      { at: 1, pressure: 0.03, side: 0.5, angle: 1.55 }
    ]
  };
  if (technique === "jiesuo") return {
    // Curvature, crossings and density are in the caller's authored guides.
    axis: "center",
    smoothing: "catmull-rom",
    turn: "preserve",
    finish: "open",
    controls: [
      { at: 0, pressure: 0.62, side: 0.06, angle: 1.4 },
      { at: 0.2, pressure: 0.92, side: 0.14, angle: 1.55 },
      { at: 0.48, pressure: 0.55, side: 0.1, angle: 1.4 },
      { at: 0.76, pressure: 0.84, side: 0.18, angle: 1.6 },
      { at: 1, pressure: 0.24, side: 0.06, angle: 1.45 }
    ]
  };
  if (technique === "luanchai") return {
    // Angular geometry keeps the author's straight/folded directions readable.
    axis: "center",
    smoothing: "linear",
    turn: "angular",
    finish: "open",
    controls: [
      { at: 0, pressure: 0.78, side: 0.12, angle: 1.45 },
      { at: 0.18, pressure: 1, side: 0.3, angle: 1.25 },
      { at: 0.46, pressure: 0.62, side: 0.08, angle: 1.5 },
      { at: 0.72, pressure: 0.94, side: 0.32, angle: 1.75 },
      { at: 1, pressure: 0.17, side: 0.04, angle: 1.5 }
    ]
  };
  if (technique === "niumao") return {
    // A fine-line contact study, without an inferred historical wrist sequence.
    axis: "center",
    smoothing: "catmull-rom",
    turn: "preserve",
    finish: "open",
    controls: [
      { at: 0, pressure: 0.32, side: 0.03, angle: 1.5 },
      { at: 0.26, pressure: 0.74, side: 0.08, angle: 1.4 },
      { at: 0.64, pressure: 0.56, side: 0.12, angle: 1.6 },
      { at: 1, pressure: 0.18, side: 0.03, angle: 1.45 }
    ]
  };
  if (technique === "cloud-study") return {
    // Shared morphology study: the caller supplies open curves, faces and overlap order.
    axis: "center",
    smoothing: "catmull-rom",
    turn: "preserve",
    finish: "open",
    controls: [
      { at: 0, pressure: 0.48, side: 0.12, angle: 1.45 },
      { at: 0.24, pressure: 0.86, side: 0.26, angle: 1.3 },
      { at: 0.52, pressure: 0.68, side: 0.44, angle: 1.75 },
      { at: 0.78, pressure: 0.83, side: 0.2, angle: 1.5 },
      { at: 1, pressure: 0.16, side: 0.08, angle: 1.4 }
    ]
  };
  if (technique === "point-study") return {
    // Contact phases on an authored short path, not a scatterer or a fixed bean stamp.
    axis: "center",
    smoothing: "catmull-rom",
    turn: "preserve",
    finish: "open",
    controls: [
      { at: 0, pressure: 0.18, side: 0.04, angle: 1.5 },
      { at: 0.28, pressure: 0.72, side: 0.18, angle: 1.4 },
      { at: 0.54, pressure: 1, side: 0.34, angle: 1.6 },
      { at: 0.78, pressure: 0.7, side: 0.18, angle: 1.55 },
      { at: 1, pressure: 0.1, side: 0.04, angle: 1.5 }
    ]
  };
  if (technique === "heye") return {
    // Branches are independent authored paths; this preset creates no parent/child topology.
    axis: "center",
    smoothing: "catmull-rom",
    turn: "preserve",
    finish: "open",
    controls: [
      { at: 0, pressure: 1, side: 0.18, angle: 1.35 },
      { at: 0.22, pressure: 0.9, side: 0.22, angle: 1.45 },
      { at: 0.55, pressure: 0.65, side: 0.16, angle: 1.5 },
      { at: 0.82, pressure: 0.36, side: 0.08, angle: 1.6 },
      { at: 1, pressure: 0.08, side: 0.03, angle: 1.45 }
    ]
  };
  return { axis: "side", smoothing: "linear", turn: "angular", finish: "preserve", controls: foldedControls(points) };
}
function createCunDocument(strokes, options) {
  validateProgram(options);
  if (!Array.isArray(strokes) || strokes.length > 512) throw new RangeError("Cun documents support at most 512 source strokes");
  const builder = createInkScene({
    width: options.width,
    height: options.height,
    seed: options.seed ?? 1,
    background: colour2(options.background ?? "#f5f2e9", "background")
  });
  const scene = builder.snapshot(), ids = /* @__PURE__ */ new Set();
  const authored = [];
  for (const source of strokes) {
    validateSource(source);
    if (ids.has(source.id)) throw new RangeError(`Duplicate cun stroke id: ${source.id}`);
    ids.add(source.id);
    const preset = defaults[options.technique];
    const overrides = Object.fromEntries(Object.entries(source.options ?? {}).filter(([, value]) => value !== void 0));
    const brush = createBrushDocument(source.points, {
      ...contactPreset(options.technique, source.points),
      width: options.strokeWidth ?? preset.width,
      dryness: options.dryness ?? preset.dryness,
      opacity: options.opacity ?? preset.opacity,
      strength: options.strength ?? 1,
      ...overrides,
      id: source.id,
      seed: (scene.seed ^ hashText3(source.id)) >>> 0,
      ink: colour2(options.ink ?? "#20251f", "ink"),
      gestureModel: "local-v1"
    });
    const sparse = {};
    for (const key of Object.keys(source.options ?? {})) {
      if (source.options[key] !== void 0) sparse[key] = brush.recipe.options[key];
    }
    authored.push(Object.freeze({
      id: source.id,
      points: brush.recipe.points,
      ...source.options === void 0 ? {} : { options: Object.freeze(sparse) }
    }));
    builder.stroke(brush.result.points, brush.result);
  }
  const sparseOptions = Object.fromEntries(Object.entries(options).filter(([, value]) => value !== void 0).map(([key, value]) => [key, key === "ink" || key === "background" ? colour2(value, key) : value]));
  const recipe = Object.freeze({
    version: 1,
    generator: GENERATOR5,
    strokes: Object.freeze(authored),
    options: Object.freeze(sparseOptions)
  });
  return Object.freeze({ version: 1, recipe, result: builder.snapshot() });
}
function assertRecipe5(recipe) {
  if (!recipe || recipe.version !== 1) throw new RangeError("Unsupported cun recipe version");
  fields3(recipe, /* @__PURE__ */ new Set(["version", "generator", "strokes", "options"]), "cun recipe");
  if (recipe.generator !== GENERATOR5) throw new RangeError("Unsupported cun generator");
}
function compileCunRecipe(recipe) {
  assertRecipe5(recipe);
  return createCunDocument(recipe.strokes, recipe.options).result;
}
function reviseCunDocument(document, patch) {
  if (!document || document.version !== 1) throw new RangeError("Unsupported cun document version");
  fields3(document, /* @__PURE__ */ new Set(["version", "recipe", "result"]), "cun document");
  assertRecipe5(document.recipe);
  fields3(patch, /* @__PURE__ */ new Set(["strokes", "options"]), "cun patch");
  if (patch.options !== void 0) fields3(patch.options, programFields, "cun option");
  return createCunDocument(
    patch.strokes === void 0 ? document.recipe.strokes : patch.strokes,
    { ...document.recipe.options, ...patch.options }
  );
}

// src/vegetation-document.ts
var GENERATOR6 = "ink-vegetation-guides-v1";
var programFields2 = /* @__PURE__ */ new Set(["technique", "width", "height", "seed", "background", "ink", "strokeWidth", "dryness", "opacity", "strength"]);
var unitFields = /* @__PURE__ */ new Set(["id", "role", "strokes"]);
var strokeFields3 = /* @__PURE__ */ new Set(["id", "points", "options"]);
var strokeOptionFields3 = /* @__PURE__ */ new Set([
  "width",
  "opacity",
  "dryness",
  "smoothing",
  "clip",
  "start",
  "duration",
  "axis",
  "entry",
  "finish",
  "pressureProfile",
  "turn",
  "rhythm",
  "drag",
  "strength",
  "controls"
]);
var pointFields3 = /* @__PURE__ */ new Set(["x", "y", "pressure"]);
var clipFields2 = /* @__PURE__ */ new Set(["x", "y"]);
var controlFields3 = /* @__PURE__ */ new Set(["at", "pressure", "side", "angle", "dryness", "inkLoad"]);
var defaults2 = {
  moss: { width: 9, dryness: 0.5, opacity: 0.78 },
  "dot-leaf": { width: 15, dryness: 0.35, opacity: 0.78 },
  "outline-leaf": { width: 2.2, dryness: 0.32, opacity: 0.74 }
};
function fields4(value, allowed, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new RangeError(`Unsupported ${name} field: ${key}`);
    if (value[key] === null) throw new TypeError(`${name}.${key} cannot be null`);
  }
}
function finite11(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function positive6(value, name) {
  if (finite11(value, name) <= 0) throw new RangeError(`${name} must be positive`);
  return value;
}
function unit4(value, name) {
  if (finite11(value, name) < 0 || value > 1) throw new RangeError(`${name} must be in 0..1`);
  return value;
}
function text4(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${name} must be a nonempty string`);
}
function colour3(value, name) {
  if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) throw new RangeError(`${name} must be a #RRGGBB colour`);
  return value.toLowerCase();
}
function hashText4(value) {
  let hash8 = 2166136261;
  for (let i = 0; i < value.length; i++) hash8 = Math.imul(hash8 ^ value.charCodeAt(i), 16777619);
  return hash8 >>> 0;
}
function validateProgram2(options) {
  fields4(options, programFields2, "vegetation option");
  if (!["moss", "dot-leaf", "outline-leaf"].includes(options.technique)) throw new RangeError("Unsupported vegetation technique");
  positive6(options.width, "width");
  positive6(options.height, "height");
  if (options.seed !== void 0 && !Number.isSafeInteger(finite11(options.seed, "seed"))) throw new RangeError("seed must be a safe integer");
  for (const key of ["background", "ink"]) if (options[key] !== void 0) colour3(options[key], key);
  if (options.strokeWidth !== void 0) positive6(options.strokeWidth, "strokeWidth");
  for (const key of ["dryness", "opacity", "strength"]) if (options[key] !== void 0) unit4(options[key], key);
}
function validateStroke(source) {
  fields4(source, strokeFields3, "vegetation stroke");
  text4(source.id, "stroke.id");
  if (!Array.isArray(source.points) || source.points.length < 2 || source.points.length > 4096) throw new RangeError("A vegetation path supports 2..4096 points");
  for (const point2 of source.points) {
    fields4(point2, pointFields3, "vegetation point");
    finite11(point2.x, "point.x");
    finite11(point2.y, "point.y");
    if (point2.pressure !== void 0) unit4(point2.pressure, "point.pressure");
  }
  if (source.options === void 0) return;
  fields4(source.options, strokeOptionFields3, "vegetation stroke option");
  if (source.options.clip !== void 0) {
    if (!Array.isArray(source.options.clip) || source.options.clip.length > 4096) throw new RangeError("vegetation stroke clip supports at most 4096 points");
    for (const point2 of source.options.clip) fields4(point2, clipFields2, "vegetation clip point");
  }
  if (source.options.controls !== void 0) {
    if (!Array.isArray(source.options.controls)) throw new TypeError("vegetation controls must be an array");
    for (const control of source.options.controls) fields4(control, controlFields3, "vegetation control");
  }
}
function createSplitVegetationUnit(id, source, bundles) {
  text4(id, "vegetation unit id");
  validateStroke(source);
  if (!Array.isArray(bundles) || bundles.length < 2 || bundles.length > 32) throw new RangeError("Split contact supports 2..32 bundles");
  const ids = /* @__PURE__ */ new Set();
  for (const bundle of bundles) {
    fields4(bundle, /* @__PURE__ */ new Set(["id", "offset", "width", "pressureScale"]), "vegetation contact bundle");
    text4(bundle.id, "bundle.id");
    if (ids.has(bundle.id)) throw new RangeError(`Duplicate contact bundle id: ${bundle.id}`);
    ids.add(bundle.id);
    finite11(bundle.offset, "bundle.offset");
    positive6(bundle.width, "bundle.width");
    if (bundle.pressureScale !== void 0) unit4(bundle.pressureScale, "bundle.pressureScale");
  }
  const original = createBrushDocument(source.points, source.options ?? {});
  const points = original.recipe.points;
  const sparse = Object.fromEntries(Object.keys(source.options ?? {}).filter((key) => source.options[key] !== void 0).map((key) => [key, original.recipe.options[key]]));
  const before = [], after = [];
  let previous = -1, next = -1;
  for (let i = 0; i < points.length; i++) {
    if (i > 0 && (points[i].x !== points[i - 1].x || points[i].y !== points[i - 1].y)) previous = i - 1;
    before[i] = previous;
  }
  for (let i = points.length - 1; i >= 0; i--) {
    if (i < points.length - 1 && (points[i].x !== points[i + 1].x || points[i].y !== points[i + 1].y)) next = i + 1;
    after[i] = next;
  }
  const direction = (a, b) => {
    const x = finite11(b.x - a.x, "contact tangent.x"), y = finite11(b.y - a.y, "contact tangent.y");
    const length = Math.hypot(x, y);
    if (!Number.isFinite(length) || length === 0) throw new RangeError("Contact tangent must have finite, nonzero length");
    return { x: x / length, y: y / length };
  };
  const normals = points.map((point2, i) => {
    const incoming = before[i] < 0 ? void 0 : direction(points[before[i]], point2);
    const outgoing = after[i] < 0 ? void 0 : direction(point2, points[after[i]]);
    const fallback = outgoing ?? incoming;
    const x = (incoming?.x ?? 0) + (outgoing?.x ?? 0), y = (incoming?.y ?? 0) + (outgoing?.y ?? 0);
    const length = Math.hypot(x, y);
    const tangent3 = length > 1e-12 ? { x: x / length, y: y / length } : fallback;
    return { x: -tangent3.y, y: tangent3.x };
  });
  const strokes = bundles.map((bundle) => {
    const pressureScale = bundle.pressureScale ?? 1;
    const shifted = points.map((point2, i) => ({
      ...point2,
      ...bundle.offset === 0 ? {} : {
        x: finite11(point2.x + normals[i].x * bundle.offset, "contact point.x"),
        y: finite11(point2.y + normals[i].y * bundle.offset, "contact point.y")
      },
      ...pressureScale === 1 ? {} : { pressure: (point2.pressure ?? 1) * pressureScale }
    }));
    const member = createBrushDocument(shifted, { ...sparse, width: bundle.width });
    return Object.freeze({
      id: `${source.id.length}:${source.id}/${bundle.id.length}:${bundle.id}`,
      points: member.recipe.points,
      options: Object.freeze({ ...sparse, width: bundle.width })
    });
  });
  return Object.freeze({ id, strokes: Object.freeze(strokes) });
}
function contactPreset2(technique, structure) {
  if (structure) return {
    axis: "center",
    smoothing: "catmull-rom",
    entry: "preserve",
    finish: "preserve",
    turn: "preserve",
    controls: [{ at: 0, pressure: 1, side: 0, angle: 1.5 }, { at: 1, pressure: 1, side: 0, angle: 1.5 }]
  };
  if (technique === "moss") return {
    axis: "center",
    smoothing: "catmull-rom",
    entry: "preserve",
    finish: "open",
    turn: "preserve",
    controls: [
      { at: 0, pressure: 0.28, side: 0.08, angle: 1.5 },
      { at: 0.3, pressure: 0.9, side: 0.26, angle: 1.3 },
      { at: 0.62, pressure: 0.76, side: 0.34, angle: 1.65 },
      { at: 1, pressure: 0.12, side: 0.08, angle: 1.5 }
    ]
  };
  if (technique === "dot-leaf") return {
    axis: "side",
    smoothing: "catmull-rom",
    entry: "preserve",
    finish: "open",
    turn: "preserve",
    controls: [
      { at: 0, pressure: 0.16, side: 0.18, angle: 1.4 },
      { at: 0.3, pressure: 1, side: 0.76, angle: 1.35 },
      { at: 0.62, pressure: 0.68, side: 0.55, angle: 1.6 },
      { at: 1, pressure: 0.03, side: 0.08, angle: 1.5 }
    ]
  };
  return {
    // Both boundaries are supplied explicitly; neither filling nor white erasure occurs.
    axis: "center",
    smoothing: "catmull-rom",
    entry: "preserve",
    finish: "preserve",
    turn: "preserve",
    controls: [
      { at: 0, pressure: 0.65, side: 0.02, angle: 1.5 },
      { at: 0.38, pressure: 0.95, side: 0.06, angle: 1.4 },
      { at: 1, pressure: 0.35, side: 0.02, angle: 1.5 }
    ]
  };
}
function createVegetationDocument(units, options) {
  validateProgram2(options);
  if (!Array.isArray(units) || units.length > 512) throw new RangeError("Vegetation documents support at most 512 units");
  const unitIds = /* @__PURE__ */ new Set();
  let strokeCount = 0;
  for (const source of units) {
    fields4(source, unitFields, "vegetation unit");
    text4(source.id, "unit.id");
    if (unitIds.has(source.id)) throw new RangeError(`Duplicate vegetation unit id: ${source.id}`);
    unitIds.add(source.id);
    if (source.role !== void 0 && !["structure", "mark"].includes(source.role)) throw new RangeError("Unsupported vegetation unit role");
    if (!Array.isArray(source.strokes) || source.strokes.length < 1 || source.strokes.length > 512) throw new RangeError("A vegetation unit supports 1..512 strokes");
    strokeCount += source.strokes.length;
    if (strokeCount > 512) throw new RangeError("Vegetation documents support at most 512 source strokes");
    const ids = /* @__PURE__ */ new Set();
    for (const stroke of source.strokes) {
      validateStroke(stroke);
      if (ids.has(stroke.id)) throw new RangeError(`Duplicate stroke id in unit ${source.id}: ${stroke.id}`);
      ids.add(stroke.id);
    }
  }
  const builder = createInkScene({
    width: options.width,
    height: options.height,
    seed: options.seed ?? 1,
    background: colour3(options.background ?? "#f5f2e9", "background")
  });
  const scene = builder.snapshot(), authored = [];
  for (const source of units) {
    const structure = source.role === "structure", preset = structure ? { width: 2.4, dryness: 0.5, opacity: 0.65 } : defaults2[options.technique];
    const strokes = [];
    for (const stroke of source.strokes) {
      const id = `${source.id.length}:${source.id}/${stroke.id.length}:${stroke.id}`;
      const overrides = Object.fromEntries(Object.entries(stroke.options ?? {}).filter(([, value]) => value !== void 0));
      const brush = createBrushDocument(stroke.points, {
        ...contactPreset2(options.technique, structure),
        width: options.strokeWidth ?? preset.width,
        dryness: options.dryness ?? preset.dryness,
        opacity: options.opacity ?? preset.opacity,
        strength: options.strength ?? 1,
        ...overrides,
        id,
        seed: (scene.seed ^ hashText4(id)) >>> 0,
        ink: colour3(options.ink ?? "#20251f", "ink"),
        gestureModel: "local-v1"
      });
      const sparse = {};
      for (const key of Object.keys(stroke.options ?? {})) {
        if (stroke.options[key] !== void 0) sparse[key] = brush.recipe.options[key];
      }
      strokes.push(Object.freeze({
        id: stroke.id,
        points: brush.recipe.points,
        ...stroke.options === void 0 ? {} : { options: Object.freeze(sparse) }
      }));
      builder.stroke(brush.result.points, brush.result);
    }
    authored.push(Object.freeze({ id: source.id, ...source.role === void 0 ? {} : { role: source.role }, strokes: Object.freeze(strokes) }));
  }
  const sparseOptions = Object.fromEntries(Object.entries(options).filter(([, value]) => value !== void 0).map(([key, value]) => [key, key === "ink" || key === "background" ? colour3(value, key) : value]));
  const recipe = Object.freeze({
    version: 1,
    generator: GENERATOR6,
    units: Object.freeze(authored),
    options: Object.freeze(sparseOptions)
  });
  return Object.freeze({ version: 1, recipe, result: builder.snapshot() });
}
function assertRecipe6(recipe) {
  if (!recipe || recipe.version !== 1) throw new RangeError("Unsupported vegetation recipe version");
  fields4(recipe, /* @__PURE__ */ new Set(["version", "generator", "units", "options"]), "vegetation recipe");
  if (recipe.generator !== GENERATOR6) throw new RangeError("Unsupported vegetation generator");
}
function compileVegetationRecipe(recipe) {
  assertRecipe6(recipe);
  return createVegetationDocument(recipe.units, recipe.options).result;
}
function reviseVegetationDocument(document, patch) {
  if (!document || document.version !== 1) throw new RangeError("Unsupported vegetation document version");
  fields4(document, /* @__PURE__ */ new Set(["version", "recipe", "result"]), "vegetation document");
  assertRecipe6(document.recipe);
  fields4(patch, /* @__PURE__ */ new Set(["units", "options"]), "vegetation patch");
  if (patch.options !== void 0) fields4(patch.options, programFields2, "vegetation option");
  return createVegetationDocument(
    patch.units === void 0 ? document.recipe.units : patch.units,
    { ...document.recipe.options, ...patch.options }
  );
}
function transformVegetationUnit(document, unitId, transform) {
  if (!document || document.version !== 1) throw new RangeError("Unsupported vegetation document version");
  fields4(document, /* @__PURE__ */ new Set(["version", "recipe", "result"]), "vegetation document");
  assertRecipe6(document.recipe);
  validateProgram2(document.recipe.options);
  text4(unitId, "vegetation unit id");
  fields4(transform, /* @__PURE__ */ new Set(["translation", "rotation", "pivot", "widthScale"]), "vegetation transform");
  const rotation = transform.rotation === void 0 ? 0 : finite11(transform.rotation, "rotation");
  const widthScale = transform.widthScale === void 0 ? 1 : positive6(transform.widthScale, "widthScale");
  const vector = (value, name) => {
    fields4(value, clipFields2, name);
    return { x: finite11(value.x, `${name}.x`), y: finite11(value.y, `${name}.y`) };
  };
  const translation = transform.translation === void 0 ? { x: 0, y: 0 } : vector(transform.translation, "translation");
  const pivot = transform.pivot === void 0 ? { x: 0, y: 0 } : vector(transform.pivot, "pivot");
  if (rotation !== 0 && transform.pivot === void 0) throw new RangeError("A nonzero rotation requires an explicit pivot");
  const units = document.recipe.units;
  if (!Array.isArray(units) || units.length > 512) throw new RangeError("Vegetation documents support at most 512 units");
  const selected = units.find((source) => source?.id === unitId);
  if (!selected) throw new RangeError(`Unknown vegetation unit id: ${unitId}`);
  fields4(selected, unitFields, "vegetation unit");
  if (!Array.isArray(selected.strokes) || selected.strokes.length < 1 || selected.strokes.length > 512) throw new RangeError("A vegetation unit supports 1..512 strokes");
  const geometryChanged = rotation !== 0 || translation.x !== 0 || translation.y !== 0;
  if (!geometryChanged && widthScale === 1) return reviseVegetationDocument(document, {});
  const cosine = Math.cos(rotation), sine = Math.sin(rotation);
  const move = (point2) => {
    const x = finite11(point2.x, "transform point.x"), y = finite11(point2.y, "transform point.y");
    const dx = x - pivot.x, dy = y - pivot.y;
    return {
      ...point2,
      x: finite11((rotation === 0 ? x : pivot.x + dx * cosine - dy * sine) + translation.x, "transformed point.x"),
      y: finite11((rotation === 0 ? y : pivot.y + dx * sine + dy * cosine) + translation.y, "transformed point.y")
    };
  };
  const strokes = selected.strokes.map((stroke) => {
    validateStroke(stroke);
    const scale = widthScale === 1 ? {} : { width: positive6(positive6(stroke.options?.width ?? document.recipe.options.strokeWidth ?? (selected.role === "structure" ? 2.4 : defaults2[document.recipe.options.technique].width), "effective stroke width") * widthScale, "transformed stroke width") };
    const clip = geometryChanged && stroke.options?.clip !== void 0 ? { clip: stroke.options.clip.map(move) } : {};
    const options = stroke.options === void 0 && widthScale === 1 ? void 0 : { ...stroke.options, ...scale, ...clip };
    return {
      ...stroke,
      ...geometryChanged ? { points: stroke.points.map(move) } : {},
      ...options === void 0 ? {} : { options }
    };
  });
  return reviseVegetationDocument(document, { units: units.map((source) => source === selected ? { ...source, strokes } : source) });
}

// src/stroke-group-document.ts
var programFields3 = /* @__PURE__ */ new Set(["width", "height", "seed", "background", "ink", "strokeWidth", "dryness", "opacity"]);
var strokeFields4 = /* @__PURE__ */ new Set(["id", "points", "options"]);
var brushFields = /* @__PURE__ */ new Set([
  "width",
  "ink",
  "opacity",
  "dryness",
  "smoothing",
  "clip",
  "start",
  "duration",
  "axis",
  "entry",
  "finish",
  "pressureProfile",
  "turn",
  "rhythm",
  "drag",
  "strength",
  "controls",
  "gestureModel"
]);
function fields5(value, allowed, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  for (const [key, item] of Object.entries(value)) {
    if (!allowed.has(key)) throw new RangeError(`Unsupported ${name} field: ${key}`);
    if (item === null) throw new TypeError(`${name}.${key} cannot be null`);
  }
}
function text5(value, name) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${name} must be a nonempty string`);
}
function colour4(value) {
  if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) throw new RangeError("Group colours must be #RRGGBB");
  return value.toLowerCase();
}
function hash6(value) {
  let result = 2166136261;
  for (let i = 0; i < value.length; i++) result = Math.imul(result ^ value.charCodeAt(i), 16777619);
  return result >>> 0;
}
function validateParents(groups) {
  const lookup = new Map(groups.map((group) => [group.id, group.parentId]));
  const finished = /* @__PURE__ */ new Set();
  for (const group of groups) {
    const visiting = /* @__PURE__ */ new Set();
    let id = group.id;
    while (id !== void 0 && !finished.has(id)) {
      if (!lookup.has(id)) throw new RangeError(`Unknown parent group: ${id}`);
      if (visiting.has(id)) throw new RangeError("Group parents must be acyclic");
      visiting.add(id);
      id = lookup.get(id);
    }
    for (const item of visiting) finished.add(item);
  }
}
function createStrokeGroupDocument(groups, options) {
  fields5(options, programFields3, "stroke group option");
  const builder = createInkScene({
    width: options.width,
    height: options.height,
    seed: options.seed ?? 1,
    background: colour4(options.background ?? "#f5f2e9")
  });
  const seed = builder.snapshot().seed;
  createBrushDocument([{ x: 0, y: 0 }, { x: 1, y: 0 }], {
    width: options.strokeWidth ?? 3,
    ink: colour4(options.ink ?? "#252820"),
    dryness: options.dryness ?? 0.35,
    opacity: options.opacity ?? 0.8
  });
  if (!Array.isArray(groups) || groups.length > 512) throw new RangeError("Stroke group documents support at most 512 groups");
  let count = 0;
  const ids = /* @__PURE__ */ new Set();
  for (const group of groups) {
    fields5(group, /* @__PURE__ */ new Set(["id", "role", "parentId", "strokes"]), "stroke group");
    text5(group.id, "group.id");
    if (ids.has(group.id)) throw new RangeError(`Duplicate stroke group id: ${group.id}`);
    ids.add(group.id);
    if (group.parentId !== void 0) text5(group.parentId, "group.parentId");
    if (group.role !== void 0 && !["structure", "mark"].includes(group.role)) throw new RangeError("Unsupported group role");
    if (!Array.isArray(group.strokes) || group.strokes.length < 1) throw new RangeError("A stroke group requires strokes");
    count += group.strokes.length;
    if (count > 512) throw new RangeError("Stroke group documents support at most 512 strokes");
  }
  validateParents(groups);
  const authored = groups.map((group) => {
    const strokeIds = /* @__PURE__ */ new Set();
    const strokes = group.strokes.map((stroke) => {
      fields5(stroke, strokeFields4, "group stroke");
      text5(stroke.id, "stroke.id");
      if (strokeIds.has(stroke.id)) throw new RangeError(`Duplicate stroke id in group ${group.id}: ${stroke.id}`);
      strokeIds.add(stroke.id);
      if (!Array.isArray(stroke.points) || stroke.points.length < 2 || stroke.points.length > 4096) throw new RangeError("Group paths support 2..4096 points");
      for (const point2 of stroke.points) fields5(point2, /* @__PURE__ */ new Set(["x", "y", "pressure"]), "group point");
      if (stroke.options !== void 0) fields5(stroke.options, brushFields, "group stroke option");
      if (stroke.options?.clip !== void 0) {
        if (!Array.isArray(stroke.options.clip) || stroke.options.clip.length > 4096) throw new RangeError("Group clips support at most 4096 points");
        for (const point2 of stroke.options.clip) fields5(point2, /* @__PURE__ */ new Set(["x", "y"]), "group clip point");
      }
      if (stroke.options?.controls !== void 0) {
        if (!Array.isArray(stroke.options.controls)) throw new TypeError("Group controls must be an array");
        for (const control of stroke.options.controls) fields5(
          control,
          /* @__PURE__ */ new Set(["at", "pressure", "side", "angle", "dryness", "inkLoad"]),
          "group control"
        );
      }
      const overrides = Object.fromEntries(Object.entries(stroke.options ?? {}).filter(([, value]) => value !== void 0));
      const id = `${group.id.length}:${group.id}/${stroke.id.length}:${stroke.id}`;
      const brush = createBrushDocument(stroke.points, {
        width: options.strokeWidth ?? 3,
        dryness: options.dryness ?? 0.35,
        opacity: options.opacity ?? 0.8,
        ink: colour4(options.ink ?? "#252820"),
        gestureModel: "local-v1",
        ...overrides,
        id,
        seed: (seed ^ hash6(id)) >>> 0
      });
      const sparse = Object.fromEntries(Object.keys(overrides).map((key) => [
        key,
        brush.recipe.options[key]
      ]));
      if (sparse.ink !== void 0) sparse.ink = colour4(sparse.ink);
      builder.stroke(brush.result.points, brush.result);
      return Object.freeze({
        id: stroke.id,
        points: brush.recipe.points,
        ...stroke.options === void 0 ? {} : { options: Object.freeze(sparse) }
      });
    });
    return Object.freeze({
      id: group.id,
      ...group.role === void 0 ? {} : { role: group.role },
      ...group.parentId === void 0 ? {} : { parentId: group.parentId },
      strokes: Object.freeze(strokes)
    });
  });
  const savedOptions = Object.fromEntries(Object.entries(options).filter(([, value]) => value !== void 0).map(([key, value]) => [key, key === "ink" || key === "background" ? colour4(value) : value]));
  const recipe = Object.freeze({
    version: 1,
    generator: "ink-stroke-groups-v1",
    groups: Object.freeze(authored),
    options: Object.freeze(savedOptions)
  });
  return Object.freeze({ version: 1, recipe, result: builder.snapshot() });
}
function assertRecipe7(recipe) {
  fields5(recipe, /* @__PURE__ */ new Set(["version", "generator", "groups", "options"]), "stroke group recipe");
  if (recipe.version !== 1 || recipe.generator !== "ink-stroke-groups-v1") throw new RangeError("Unsupported stroke group recipe version or generator");
}
function compileStrokeGroupRecipe(recipe) {
  assertRecipe7(recipe);
  return createStrokeGroupDocument(recipe.groups, recipe.options).result;
}
function reviseStrokeGroupDocument(document, patch) {
  fields5(document, /* @__PURE__ */ new Set(["version", "recipe", "result"]), "stroke group document");
  if (document.version !== 1) throw new RangeError("Unsupported stroke group document version");
  assertRecipe7(document.recipe);
  fields5(patch, /* @__PURE__ */ new Set(["groups", "options"]), "stroke group patch");
  if (patch.options !== void 0) fields5(patch.options, programFields3, "stroke group option");
  return createStrokeGroupDocument(patch.groups ?? document.recipe.groups, { ...document.recipe.options, ...patch.options });
}

// src/subject-macros.ts
var commonFields2 = ["id", "seed", "ink"];
var treeFields = [...commonFields2, "start", "angle", "size", "width", "depth", "style", "branches"];
var rockFields = [...commonFields2, "position", "size", "outline", "region", "ridges", "textureCount", "moss", "mossCount", "mossAnchors", "flow"];
var birdParts = ["tail", "back", "wing", "head", "beak", "feet", "eye"];
var birdFields = [...commonFields2, "position", "size", "angle", "type", "pose", "facing", "paper", "attachment", "parts"];
function fields6(value, allowed, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${name} must be an object`);
  for (const key of Object.keys(value)) if (!allowed.includes(key) || value[key] === null) throw new RangeError(`Unsupported ${name} field: ${key}`);
}
function finite12(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`${name} must be finite`);
  return value;
}
function positive7(value, name) {
  if (finite12(value, name) <= 0) throw new RangeError(`${name} must be positive`);
  return value;
}
function integer2(value, name, min, max) {
  if (!Number.isSafeInteger(finite12(value, name)) || value < min || value > max) throw new RangeError(`${name} must be an integer in ${min}..${max}`);
  return value;
}
function unit5(value, name) {
  if (finite12(value, name) < 0 || value > 1) throw new RangeError(`${name} must be in 0..1`);
  return value;
}
function text6(value) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError("IDs must be nonempty strings");
  return value;
}
function colour5(value) {
  if (typeof value !== "string" || !/^#[0-9a-f]{6}$/i.test(value)) throw new RangeError("Macro colours must be #RRGGBB");
  return value.toLowerCase();
}
function point(value) {
  fields6(value, ["x", "y"], "point");
  return { x: finite12(value.x, "x"), y: finite12(value.y, "y") };
}
function path(values) {
  if (!Array.isArray(values) || values.length < 2 || values.length > 4096) throw new RangeError("Macro paths need 2..4096 points");
  const owned = values.map((p) => {
    fields6(p, ["x", "y", "pressure"], "path point");
    return { x: finite12(p.x, "x"), y: finite12(p.y, "y"), ...p.pressure === void 0 ? {} : { pressure: unit5(p.pressure, "pressure") } };
  });
  if (!owned.some((p) => p.x !== owned[0].x || p.y !== owned[0].y)) throw new RangeError("Macro paths need nonzero length");
  return owned;
}
function polygon2(values) {
  if (!Array.isArray(values) || values.length < 3 || values.length > 4096) throw new RangeError("Macro regions need 3..4096 points");
  const owned = values.map(point), origin = owned[0];
  const area = owned.reduce((sum, a, i) => {
    const b = owned[(i + 1) % owned.length];
    return sum + (a.x - origin.x) * (b.y - origin.y) - (b.x - origin.x) * (a.y - origin.y);
  }, 0);
  if (!Number.isFinite(area) || Math.abs(area) < 1e-8) throw new RangeError("Macro regions need nonzero area");
  return owned;
}
function hash7(value) {
  let result = 2166136261;
  for (const ch of value) result = Math.imul(result ^ ch.charCodeAt(0), 16777619);
  return result >>> 0;
}
function settings(options, kind) {
  return {
    id: text6(options.id ?? kind),
    seed: integer2(options.seed ?? 1, "seed", -Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER) >>> 0,
    ink: colour5(options.ink ?? "#252b24")
  };
}
function mark(id, points, width, options = {}) {
  return { id, points, options: { width, opacity: 0.78, dryness: 0.5, smoothing: "catmull-rom", gestureModel: "local-v1", duration: 0, ...options } };
}
function finish(kind, groups, attachments, seed) {
  if (groups.reduce((sum, g) => sum + g.strokes.length, 0) > 512) throw new RangeError("A subject macro cannot exceed 512 strokes");
  const document = createStrokeGroupDocument(groups, { width: 1, height: 1, seed });
  const anchors = attachments.map((a) => {
    const p = point({ x: a.point.x, y: a.point.y }), t = point(a.tangent), length = Math.hypot(t.x, t.y);
    if (length <= 0) throw new RangeError("Attachment direction must be nonzero");
    return Object.freeze({ ...a, point: Object.freeze(p), tangent: Object.freeze({ x: t.x / length, y: t.y / length }) });
  });
  return Object.freeze({ version: 1, kind, groups: document.recipe.groups, attachments: Object.freeze(anchors) });
}
function station(points, at2) {
  const lengths = points.slice(1).map((b, i) => Math.hypot(b.x - points[i].x, b.y - points[i].y));
  const total = lengths.reduce((a, b) => a + b, 0), distance = total * at2;
  let before = 0;
  for (let i = 0; i < lengths.length; i++) {
    const length = lengths[i];
    if (length > 0 && (before + length >= distance || i === lengths.length - 1)) {
      const a = points[i], b = points[i + 1], t = Math.max(0, Math.min(1, (distance - before) / length));
      return { point: { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, pressure: (a.pressure ?? 1) * (1 - t) + (b.pressure ?? 1) * t }, tangent: { x: (b.x - a.x) / length, y: (b.y - a.y) / length } };
    }
    before += length;
  }
  throw new RangeError("A branch needs finite nonzero length");
}
function branchStation(points, at2) {
  const result = station(points, at2);
  if (!points.some((p) => p.x === result.point.x && p.y === result.point.y)) {
    let best = 0, error = Infinity;
    for (let i = 0; i < points.length - 1; i++) {
      const a = points[i], b = points[i + 1], length = Math.hypot(b.x - a.x, b.y - a.y);
      const distance = Math.abs(Math.hypot(result.point.x - a.x, result.point.y - a.y) + Math.hypot(b.x - result.point.x, b.y - result.point.y) - length);
      if (distance < error) {
        best = i;
        error = distance;
      }
    }
    points.splice(best + 1, 0, result.point);
  }
  return result;
}
function createTreeMacro(options = {}) {
  fields6(options, treeFields, "tree option");
  const s = settings(options, "tree"), size = positive7(options.size ?? 160, "size"), width = positive7(options.width ?? size * 0.085, "width");
  const depth = integer2(options.depth ?? 3, "depth", 0, 6), start = point(options.start ?? { x: 0, y: 0 }), angle = finite12(options.angle ?? -Math.PI / 2, "angle");
  const style = options.style ?? "dry";
  if (!["dry", "wet"].includes(style)) throw new RangeError("Unknown tree style");
  const branches = [];
  if (options.branches !== void 0) {
    if (!Array.isArray(options.branches) || options.branches.length < 1 || options.branches.length > 127) throw new RangeError("Trees support 1..127 branches");
    const lookup = /* @__PURE__ */ new Map();
    for (const branch of options.branches) {
      fields6(branch, ["id", "points", "width", "parentId", "attachAt"], "branch");
      text6(branch.id);
      if (lookup.has(branch.id)) throw new RangeError("Duplicate branch ID");
      lookup.set(branch.id, branch);
    }
    const resolved = /* @__PURE__ */ new Map(), visiting = /* @__PURE__ */ new Set();
    const resolve = (id) => {
      if (resolved.has(id)) return resolved.get(id);
      if (visiting.has(id)) throw new RangeError("Branch parents must be acyclic");
      const source = lookup.get(id);
      if (!source) throw new RangeError("Unknown parent branch");
      visiting.add(id);
      let points = path(source.points), parent;
      if (source.parentId !== void 0) {
        parent = resolve(text6(source.parentId));
        const at2 = unit5(source.attachAt ?? 0.78, "attachAt"), anchor = branchStation(parent.points, at2).point;
        const dx = anchor.x - points[0].x, dy = anchor.y - points[0].y;
        points = points.map((p) => ({ ...p, x: p.x + dx, y: p.y + dy }));
      } else if (source.attachAt !== void 0) throw new RangeError("attachAt requires parentId");
      const branchWidth = positive7(source.width ?? (parent ? parent.width * 0.56 : width), "branch width");
      if (parent && branchWidth >= parent.width) throw new RangeError("Child branch width must be smaller than its parent");
      points = points.map((p, i) => ({ ...p, pressure: i === points.length - 1 ? 0 : p.pressure ?? 0.88 - i / points.length * 0.18 }));
      const branch = { id, points, width: branchWidth, ...source.parentId === void 0 ? {} : { parentId: source.parentId, attachAt: source.attachAt ?? 0.78 } };
      resolved.set(id, branch);
      visiting.delete(id);
      return branch;
    };
    for (const source of options.branches) resolve(source.id);
    branches.push(...resolved.values());
  } else {
    const grow = (id, p, a, length, w, level, parent, at2) => {
      const random = createRandom(s.seed ^ hash7(id)), bend = (random() - 0.5) * 0.24;
      const points = [{ ...p, pressure: 0.9 }, ...[0.32, 0.7, 1].map((t, i) => ({ x: p.x + Math.cos(a + bend * t) * length * t, y: p.y + Math.sin(a + bend * t) * length * t, pressure: [0.96, 0.63, 0][i] }))];
      const branch = { id, points, width: w, ...parent ? { parentId: parent.id, attachAt: at2 } : {} };
      branches.push(branch);
      if (level < depth) for (const [index, side] of [-1, 1].entries()) {
        const at3 = 0.64 + random() * 0.18, anchor = branchStation(points, at3).point;
        grow(`${id}.${index}`, anchor, a + side * (Math.PI / 12 + random() * Math.PI / 12), length * (0.57 + random() * 0.14), w * (0.48 + random() * 0.13), level + 1, branch, at3);
      }
    };
    grow("root", start, angle, size, width, 0);
  }
  const groups = [], attachments = [];
  for (const branch of branches) {
    const id = `${s.id.length}:${s.id}/branch/${branch.id}`, random = createRandom(s.seed ^ hash7(id));
    const strokes = [mark("body", branch.points, branch.width, { ink: s.ink, opacity: style === "dry" ? 0.42 : 0.55, dryness: style === "dry" ? 0.5 : 0.22 })];
    const grain = jitterPath(branch.points.map((p, i) => ({ ...p, pressure: i === branch.points.length - 1 ? 0 : (p.pressure ?? 1) * (0.28 + random() * 0.37) })), branch.width * 0.05, { seed: s.seed ^ hash7(`${id}/grain`), wavelength: Math.max(1, branch.width) });
    strokes.push(mark("grain", grain, branch.width * 0.38, { ink: s.ink, opacity: 0.7, dryness: 0.82, axis: "side" }));
    if (branch.parentId && random() > 0.42) {
      const p = branch.points[0], t = station(branch.points, 0.1).tangent, reach = branch.width * 0.8;
      strokes.push(mark("joint", [{ x: p.x - t.x * reach * 0.2, y: p.y - t.y * reach * 0.2, pressure: 0.25 }, { ...p, pressure: 0.82 }, { x: p.x + t.x * reach, y: p.y + t.y * reach, pressure: 0 }], branch.width * 1.25, { ink: s.ink, opacity: 0.49, dryness: 0.15 }));
    }
    groups.push({ id, role: "structure", ...branch.parentId ? { parentId: `${s.id.length}:${s.id}/branch/${branch.parentId}` } : {}, strokes });
    attachments.push(
      { id: `${branch.id}:root`, groupId: id, point: branch.points[0], tangent: station(branch.points, 0).tangent },
      { id: `${branch.id}:tip`, groupId: id, point: branch.points.at(-1), tangent: station(branch.points, 1).tangent }
    );
  }
  return finish("tree", groups, attachments, s.seed);
}
function createRockMacro(options = {}) {
  fields6(options, rockFields, "rock option");
  const s = settings(options, "rock"), size = positive7(options.size ?? 100, "size"), position = point(options.position ?? { x: 0, y: 0 });
  const outline = polygon2(options.outline ?? [[0, 0.85], [0.08, 0.35], [0.34, 0.02], [0.67, 0.12], [0.88, 0.43], [1, 0.87], [0.71, 1], [0.2, 0.97]].map(([x, y]) => ({ x: position.x + x * size, y: position.y + y * size })));
  const region = polygon2(options.region ?? outline), xs = outline.map((p) => p.x), ys = outline.map((p) => p.y), left = Math.min(...xs), top = Math.min(...ys), w = Math.max(...xs) - left, h = Math.max(...ys) - top;
  const scale = Math.max(w, h), count = integer2(options.textureCount ?? 14, "textureCount", 0, 96), mossCount = integer2(options.mossCount ?? 8, "mossCount", 0, 32);
  if (options.moss !== void 0 && typeof options.moss !== "boolean") throw new TypeError("moss must be boolean");
  if (options.flow !== void 0 && typeof options.flow !== "function") throw new TypeError("flow must be a function");
  const ridges = options.ridges ?? [{ id: "fold", points: [{ x: left + w * 0.35, y: top + h * 0.07, pressure: 0.6 }, { x: left + w * 0.43, y: top + h * 0.43, pressure: 0.9 }, { x: left + w * 0.28, y: top + h * 0.8, pressure: 0.05 }] }];
  if (!Array.isArray(ridges) || ridges.length > 64) throw new RangeError("Rocks support at most 64 ridges");
  const boneStrokes = [mark("outline", [...outline.map((p, i) => ({ ...p, pressure: 0.32 + i % 3 * 0.23 })), { ...outline[0], pressure: 0 }], scale * 0.018, { ink: s.ink, dryness: 0.69, smoothing: "linear" })];
  const ridgeIds = /* @__PURE__ */ new Set();
  for (const ridge of ridges) {
    fields6(ridge, ["id", "points"], "ridge");
    text6(ridge.id);
    if (ridgeIds.has(ridge.id)) throw new RangeError("Duplicate ridge ID");
    ridgeIds.add(ridge.id);
    boneStrokes.push(mark(`ridge/${ridge.id}`, path(ridge.points), scale * 0.016, { ink: s.ink, dryness: 0.61, smoothing: "linear" }));
  }
  const groups = [{ id: `${s.id.length}:${s.id}/bones`, role: "structure", strokes: boneStrokes }];
  groups.push({ id: `${s.id.length}:${s.id}/pale`, role: "mark", parentId: `${s.id.length}:${s.id}/bones`, strokes: [mark("shadow", [{ x: left + w * 0.57, y: top + h * 0.17, pressure: 0.25 }, { x: left + w * 0.68, y: top + h * 0.52, pressure: 0.9 }, { x: left + w * 0.62, y: top + h * 0.91, pressure: 0 }], scale * 0.32, { ink: s.ink, opacity: 0.12, dryness: 0.14, axis: "side", clip: region })] });
  const random = createRandom(s.seed ^ hash7(`${s.id.length}:${s.id}/texture`)), texture = [];
  for (let attempt = 0; texture.length < count && attempt < count * 60; attempt++) {
    const p = { x: left + random() * w, y: top + random() * h };
    if (!pointInPolygon(p, region)) continue;
    const direction = options.flow ? point(options.flow({ ...p })) : { x: 0.24 + (p.x - left) / Math.max(w, 1) * 0.24, y: 1 }, length = Math.hypot(direction.x, direction.y);
    if (length <= 0) throw new RangeError("Rock flow must be nonzero");
    const tx = direction.x / length, ty = direction.y / length, reach = scale * (0.09 + random() * 0.18), side = (random() - 0.5) * scale * 0.07;
    texture.push(mark(`facet/${texture.length}`, [{ ...p, pressure: 0.65 }, { x: p.x + tx * reach * 0.55 - ty * side, y: p.y + ty * reach * 0.55 + tx * side, pressure: 0.8 }, { x: p.x + tx * reach, y: p.y + ty * reach, pressure: 0 }], scale * (0.025 + random() * 0.035), { ink: s.ink, opacity: 0.3 + random() * 0.36, dryness: 0.61 + random() * 0.25, axis: "side", smoothing: "linear", clip: region }));
  }
  if (texture.length) groups.push({ id: `${s.id.length}:${s.id}/texture`, role: "mark", parentId: `${s.id.length}:${s.id}/bones`, strokes: texture });
  const anchors = options.mossAnchors?.map(point) ?? outline.filter((p) => p.y <= top + h * 0.35);
  if (options.mossAnchors !== void 0 && (!Array.isArray(options.mossAnchors) || anchors.length > 64)) throw new RangeError("Rocks support at most 64 moss anchors");
  if ((options.moss ?? true) && mossCount > 0 && anchors.length) {
    const mossRandom = createRandom(s.seed ^ hash7(`${s.id.length}:${s.id}/moss`)), moss = [];
    for (let i = 0; i < mossCount; i++) {
      const anchor = anchors[Math.floor(i / 3) % anchors.length], dx = (mossRandom() - 0.5) * scale * 0.11, dy = mossRandom() * scale * 0.045;
      const x = anchor.x + dx, y = anchor.y + dy, reach = scale * (0.02 + mossRandom() * 0.025);
      moss.push(mark(`dab/${i}`, [{ x, y, pressure: 0.24 }, { x: x + reach * 0.3, y: y + reach * 0.2, pressure: 0.94 }, { x: x + reach, y: y + reach * 0.35, pressure: 0 }], scale * (i % 3 === 0 ? 0.05 : 0.03), { ink: s.ink, opacity: i % 3 === 0 ? 0.95 : 0.7, dryness: 0.52, axis: "side" }));
    }
    groups.push({ id: `${s.id.length}:${s.id}/moss`, role: "mark", parentId: `${s.id.length}:${s.id}/bones`, strokes: moss });
  }
  return finish("rock", groups, anchors.map((p, i) => ({ id: `moss-anchor/${i}`, groupId: `${s.id.length}:${s.id}/bones`, point: p, tangent: { x: 1, y: 0 } })), s.seed);
}
function createBirdMacro(options = {}) {
  fields6(options, birdFields, "bird option");
  const s = settings(options, "bird"), size = positive7(options.size ?? 64, "size"), paper = colour5(options.paper ?? "#f5f2e9");
  if ((options.type ?? "crow") !== "crow") throw new RangeError("Only the crow study is available");
  const pose = options.pose ?? "sitting", facing = options.facing ?? "left";
  if (!["sitting", "looking-up", "looking-down"].includes(pose) || !["left", "right"].includes(facing)) throw new RangeError("Unknown bird pose or facing");
  let origin = point(options.position ?? { x: 0, y: 0 }), angle = finite12(options.angle ?? 0, "angle");
  if (options.attachment) {
    fields6(options.attachment, ["point", "tangent"], "attachment");
    origin = point(options.attachment.point);
    if (options.attachment.tangent) {
      const t = point(options.attachment.tangent);
      if (Math.hypot(t.x, t.y) <= 0) throw new RangeError("Perch direction must be nonzero");
      angle += Math.atan2(t.y, t.x);
    }
  }
  if (options.parts) fields6(options.parts, birdParts, "bird part");
  const flip = facing === "left" ? 1 : -1, c = Math.cos(angle), sn = Math.sin(angle);
  const transform = ([x, y, p = 1]) => ({ x: origin.x + (x * flip * c - y * sn) * size, y: origin.y + (x * flip * sn + y * c) * size, pressure: p });
  const draw = (id, values, width, extra = {}) => mark(id, values.map(transform), width * size, { ink: s.ink, ...extra });
  const h = pose === "looking-up" ? [-0.22, -0.64] : pose === "looking-down" ? [-0.39, -0.39] : [-0.31, -0.51], hx = h[0], hy = h[1];
  const beak = pose === "looking-up" ? [-0.12, -0.2] : pose === "looking-down" ? [-0.22, 0.16] : [-0.25, 0.045];
  const parts = { tail: [], back: [], wing: [], head: [], beak: [], feet: [], eye: [] };
  parts.tail.push(draw("main", [[0.21, -0.19, 0.48], [0.48, -0.01, 0.9], [0.9, 0.12, 0]], 0.095, { dryness: 0.76, opacity: 0.9 }), draw("split", [[0.23, -0.16, 0.3], [0.58, 0.12, 0.7], [0.84, 0.17, 0]], 0.045, { dryness: 0.84, opacity: 0.64 }));
  parts.back.push(draw("back-wash", [[hx * 0.6, hy * 0.8, 0.3], [-0.02, -0.4, 0.92], [0.22, -0.26, 0.7], [0.28, -0.13, 0]], 0.35, { opacity: 0.3, dryness: 0.16 }), draw("nape", [[hx, hy + 0.07, 0.3], [-0.13, -0.31, 0.8], [0.04, -0.31, 0]], 0.19, { opacity: 0.54, dryness: 0.25 }));
  const random = createRandom(s.seed ^ hash7(`${s.id.length}:${s.id}/wing`));
  for (let i = 0; i < 5; i++) {
    const x = -0.12 + i * 0.072, y = -0.29 + i * 0.035;
    parts.wing.push(draw(`feather/${i}`, [[x, y, 0.35], [x + 0.14, y + 0.07, 0.75 + random() * 0.2], [x + 0.26 + random() * 0.07, y + 0.15, 0]], 0.085 + random() * 0.035, { dryness: 0.58 + random() * 0.25, opacity: 0.57 + random() * 0.24 }));
  }
  parts.head.push(draw("head", [[hx + 0.09, hy - 0.04, 0.3], [hx, hy, 0.96], [hx - 0.065, hy + 0.075, 0.55]], 0.25, { dryness: 0.2, opacity: 0.91 }), draw("throat", [[hx - 0.02, hy + 0.06, 0.65], [hx + 0.06, hy + 0.16, 0.83], [hx + 0.15, hy + 0.2, 0]], 0.13, { dryness: 0.4, opacity: 0.65 }));
  parts.beak.push(draw("upper", [[hx - 0.06, hy + 0.035, 0.7], [hx + beak[0], hy + beak[1], 0]], 0.035, { dryness: 0.76, opacity: 1, smoothing: "linear" }), draw("lower", [[hx - 0.055, hy + 0.07, 0.65], [hx + beak[0] * 0.84, hy + beak[1] + 0.02, 0]], 0.019, { dryness: 0.66, opacity: 0.9, smoothing: "linear" }));
  parts.feet.push(draw("leg", [[-0.03, -0.09, 0.25], [0.01, -0.015, 0.85], [0, 0, 0.5]], 0.025, { dryness: 0.7, opacity: 0.9 }), draw("toe-left", [[0, 0, 0.75], [-0.09, 0.01, 0]], 0.024, { dryness: 0.66, opacity: 1 }), draw("toe-right", [[0, 0, 0.75], [0.12, 0.015, 0]], 0.022, { dryness: 0.71, opacity: 0.94 }));
  parts.eye.push(draw("socket", [[hx - 0.035, hy - 0.01, 0.2], [hx - 0.07, hy + 0.025, 0.7], [hx - 0.035, hy + 0.04, 0.35]], 0.065, { ink: paper, opacity: 0.95, dryness: 0.07 }), draw("pupil", [[hx - 0.055, hy + 8e-3, 0.95], [hx - 0.052, hy + 0.018, 0.85]], 0.031, { ink: s.ink, dryness: 0.59, opacity: 1 }));
  const groups = [];
  for (const part of birdParts) {
    const selected = options.parts?.[part] ?? parts[part];
    if (!Array.isArray(selected) || selected.length > 64) throw new RangeError("Each bird part supports at most 64 strokes");
    if (selected.length) groups.push({ id: `${s.id.length}:${s.id}/${part}`, role: part === "feet" ? "structure" : "mark", strokes: selected.map((stroke) => {
      fields6(stroke, ["id", "points", "options"], "bird stroke");
      return { id: stroke.id, points: path(stroke.points), options: { ink: s.ink, ...stroke.options } };
    }) });
  }
  const feet = groups.find((g) => g.id === `${s.id.length}:${s.id}/feet`) ?? groups[0];
  return finish("bird", groups, feet ? [{ id: "perch", groupId: feet.id, point: origin, tangent: { x: c, y: sn } }] : [], s.seed);
}

// src/technique-studies.ts
var TECHNIQUE_STUDY_BATCHES = Object.freeze([
  { "number": 1, "title": "基础运笔", "techniqueIds": ["brush-lift", "brush-press", "brush-center", "brush-side", "ink-wet", "ink-dry", "ink-flying-white"], "example": "examples/revision-review.html?batch=1" },
  { "number": 2, "title": "起收转折", "techniqueIds": ["brush-exposed-entry", "brush-hidden-entry", "brush-side", "brush-exposed-finish", "brush-return-finish", "brush-turn", "brush-fold", "brush-center"], "example": "examples/revision-review.html?batch=2" },
  { "number": 3, "title": "点迹组织", "techniqueIds": ["brush-dot", "brush-lift", "brush-press", "brush-side"], "example": "examples/revision-review.html?batch=3" },
  { "number": 4, "title": "浓淡积破", "techniqueIds": ["ink-light", "ink-dark", "ink-layered", "ink-broken", "ink-wet"], "example": "examples/revision-review.html?batch=4" },
  { "number": 5, "title": "水墨先后", "techniqueIds": ["water-break-ink", "ink-break-water", "water-sprinkle"], "example": "examples/revision-review.html?batch=5" },
  { "number": 6, "title": "披麻斧劈折带", "techniqueIds": ["cun-pima", "cun-axe", "cun-zhedai"], "example": "examples/revision-review.html?batch=6" },
  { "number": 7, "title": "解索乱柴牛毛", "techniqueIds": ["cun-jiesuo", "cun-luanchai", "cun-niumao"], "example": "examples/revision-review.html?batch=7" },
  { "number": 8, "title": "环抱点皴筋脉", "techniqueIds": ["cun-juanyun", "cun-yuntou", "cun-yudian", "cun-douban", "cun-heye"], "example": "examples/revision-review.html?batch=8" },
  { "number": 9, "title": "点苔点叶夹叶", "techniqueIds": ["vegetation-moss", "vegetation-dot-leaves", "vegetation-outline-leaves"], "example": "examples/revision-review.html?batch=9" },
  { "number": 10, "title": "介字个字聚簇", "techniqueIds": ["leaf-jie", "leaf-ge", "leaf-three-five"], "example": "examples/revision-review.html?batch=10" },
  { "number": 11, "title": "胡椒破笔松针", "techniqueIds": ["leaf-pepper", "leaf-broken", "vegetation-pine-needles"], "example": "examples/revision-review.html?batch=11" },
  { "number": 12, "title": "竹叶梅花双勾", "techniqueIds": ["vegetation-bamboo-leaves", "leaf-plum", "line-double-outline"], "example": "examples/revision-review.html?batch=12" },
  { "number": 13, "title": "锋势与毛束", "techniqueIds": ["brush-offset", "brush-follow", "brush-against", "brush-split"], "example": "examples/remaining-review.html?batch=13" },
  { "number": 14, "title": "局部驻转与阻力", "techniqueIds": ["brush-pause", "brush-reposition", "brush-resistant"], "example": "examples/remaining-review.html?batch=14" },
  { "number": 15, "title": "运行节奏与摩擦", "techniqueIds": ["brush-fast", "brush-slow", "brush-scrub"], "example": "examples/remaining-review.html?batch=15" },
  { "number": 16, "title": "墨料与大面布墨", "techniqueIds": ["ink-scorched", "ink-aged", "ink-splashed"], "example": "examples/remaining-review.html?batch=16" },
  { "number": 17, "title": "区域造形", "techniqueIds": ["ink-rendering", "ink-background-wash", "line-mogu"], "example": "examples/remaining-review.html?batch=17" },
  { "number": 18, "title": "染层与透显", "techniqueIds": ["ink-divided-wash", "ink-glazing", "ink-supporting-wash"], "example": "examples/remaining-review.html?batch=18" },
  { "number": 19, "title": "水法条件与缺证", "techniqueIds": ["water-soak", "water-collision", "water-texture"], "example": "examples/remaining-review.html?batch=19" },
  { "number": 20, "title": "湿点与综合皴组", "techniqueIds": ["cun-midian", "cun-tuoni", "cun-baoshi"], "example": "examples/remaining-review.html?batch=20" },
  { "number": 21, "title": "骨架、短皴与待核叶组", "techniqueIds": ["cun-kulou", "cun-dingtou", "leaf-pin"], "example": "examples/remaining-review.html?batch=21" },
  { "number": 22, "title": "线墨造形与取舍", "techniqueIds": ["line-hun", "line-baimiao", "line-jianbi"], "example": "examples/remaining-review.html?batch=22" },
  { "number": 23, "title": "细劲长线", "techniqueIds": ["line-gaogu-yousi", "line-qinxian", "line-tiexian"], "example": "examples/remaining-review.html?batch=23" },
  { "number": 24, "title": "流向与贴体线组", "techniqueIds": ["line-xingyun-liushui", "line-qiuyin", "line-caoyi"], "example": "examples/remaining-review.html?batch=24" },
  { "number": 25, "title": "短线起收", "techniqueIds": ["line-mahuang", "line-dingtou-shuwei", "line-juetou"], "example": "examples/remaining-review.html?batch=25" },
  { "number": 26, "title": "衣纹宽窄与叶形比喻", "techniqueIds": ["line-ganlan", "line-zaohe", "line-liuye", "line-zhuye"], "example": "examples/remaining-review.html?batch=26" },
  { "number": 27, "title": "衣纹转折与枯接触", "techniqueIds": ["line-zhelu", "line-zhanbi-shuiwen", "line-kuchai"], "example": "examples/remaining-review.html?batch=27" },
  { "number": 28, "title": "八法的点横竖", "techniqueIds": ["eight-ce", "eight-le", "eight-nu"], "example": "examples/remaining-review.html?batch=28" },
  { "number": 29, "title": "八法的钩提撇", "techniqueIds": ["eight-ti", "eight-ce-lift", "eight-lue"], "example": "examples/remaining-review.html?batch=29" },
  { "number": 30, "title": "短撇、捺与结构勾勒", "techniqueIds": ["eight-zhuo", "eight-zhe", "line-outline"], "example": "examples/remaining-review.html?batch=30" }
].map((batch) => Object.freeze({ ...batch, techniqueIds: Object.freeze(batch.techniqueIds) })));
function getTechniqueStudies(id) {
  return Object.freeze(TECHNIQUE_STUDY_BATCHES.filter((batch) => batch.techniqueIds.includes(id)));
}
export {
  TECHNIQUE_CATALOG,
  TECHNIQUE_CATEGORIES,
  TECHNIQUE_READINGS,
  TECHNIQUE_READING_SOURCES,
  TECHNIQUE_SOURCES,
  TECHNIQUE_STUDY_BATCHES,
  accumulateEdge,
  compileBrushRecipe,
  compileCunRecipe,
  compileDotRecipe,
  compileInkProcessRecipe,
  compileStrokeGroupRecipe,
  compileVegetationRecipe,
  compileWaterProcessRecipe,
  createBirdMacro,
  createBrushDocument,
  createBrushStroke,
  createCunDocument,
  createDotDocument,
  createDotMarks,
  createInkProcessDocument,
  createInkScene,
  createNoise1D,
  createRandom,
  createRockMacro,
  createSplitVegetationUnit,
  createStrokeGroupDocument,
  createTreeMacro,
  createVegetationDocument,
  createWaterProcessDocument,
  findTechniques,
  getFractalNoise2D,
  getNoise1D,
  getNoise2D,
  getTechnique,
  getTechniqueReading,
  getTechniqueStudies,
  jitterPath,
  pointInPolygon,
  poissonDiskSampling,
  rasterizeInkProcess,
  renderCanvas,
  renderInkProcess,
  resamplePath,
  reviseBrushDocument,
  reviseCunDocument,
  reviseDotDocument,
  reviseInkProcessDocument,
  reviseStrokeGroupDocument,
  reviseVegetationDocument,
  reviseWaterProcessDocument,
  sampleBrushContact,
  sampleStrokePath,
  splitBristles,
  transformVegetationUnit,
  varyPressure
};
