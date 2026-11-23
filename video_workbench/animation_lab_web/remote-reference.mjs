// Shared request contract for references read from the original video site.
export const REMOTE_VIDEO_SOURCE = 'video-url';

export function sourcePageUrl(value) {
  if (typeof value !== 'string' || value.length > 2000) return null;
  try {
    const url = new URL(value.trim());
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

const present = value => value !== undefined && value !== null && value !== '';
function number(value, label) {
  if (!present(value) || !['number', 'string'].includes(typeof value) || typeof value === 'string' && !value.trim() || !Number.isFinite(Number(value))) throw Error(label + '应为有效数字');
  return Number(value);
}

export function remoteReferenceBody(fields, requestId, {picture = false} = {}) {
  const source_url = sourcePageUrl(fields.source_url);
  if (!source_url) throw Error('请填写完整的 HTTPS 视频链接，不包含账号密码');
  const body = {request_id: requestId, source_type: REMOTE_VIDEO_SOURCE, source_url};
  if (!picture) return body;
  body.timestamp_seconds = number(fields.timestamp_seconds, '截图时间');
  if (body.timestamp_seconds < 0) throw Error('截图时间不能小于 0 秒');
  const keys = ['x', 'y', 'width', 'height'];
  const crop = fields.crop ?? Object.fromEntries(keys.map(key => [key, fields['crop_' + key]]));
  if (keys.some(key => present(crop?.[key]))) {
    if (typeof crop !== 'object' || crop === null || Array.isArray(crop) || Object.keys(crop).some(key => !keys.includes(key))) throw Error('裁剪范围应包含 x、y、宽度和高度');
    body.crop = Object.fromEntries(keys.map(key => [key, number(crop[key], '裁剪范围')]));
    const {x, y, width, height} = body.crop;
    if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1 || y + height > 1) throw Error('裁剪范围使用 0–1 的比例，且不能超出画面');
  }
  return body;
}

export function validRemoteReferenceBody(body, options = {}) {
  if (!body || body.source_type !== REMOTE_VIDEO_SOURCE || typeof body.request_id !== 'string' || !body.request_id) return false;
  const allowed = ['request_id', 'source_type', 'source_url', ...(options.picture ? ['timestamp_seconds', 'crop'] : [])];
  if (Object.keys(body).some(key => !allowed.includes(key))) return false;
  try {
    const expected = remoteReferenceBody(body, body.request_id, options);
    return Object.keys(expected).length === Object.keys(body).length && Object.keys(expected).every(key => key === 'crop'
      ? Object.keys(expected.crop).length === Object.keys(body.crop ?? {}).length && Object.keys(expected.crop).every(part => expected.crop[part] === body.crop[part])
      : expected[key] === body[key]);
  }
  catch { return false; }
}

export const isRemoteReference = reference => reference?.source_type === REMOTE_VIDEO_SOURCE;

export function referenceDescription(reference) {
  const parts = [];
  const observed = reference?.observation_status === 'sampled_frames_only';
  const width = observed ? reference.observed_width : reference?.width;
  const height = observed ? reference.observed_height : reference?.height;
  if (Number.isFinite(width) && Number.isFinite(height)) parts.push((observed ? '实际观察画面：' : '源站可用画面：') + width + '×' + height);
  if (Number.isFinite(reference?.timestamp_seconds)) parts.push('选定时间：' + reference.timestamp_seconds + ' 秒');
  if (Number.isFinite(reference?.actual_pts)) parts.push('实际帧时间：' + reference.actual_pts + ' 秒');
  if (observed) parts.push('已抽帧观察；连续运动和声音尚未验证');
  else parts.push('链接已登记，尚未读取参考画面');
  return parts.join(' · ');
}

export function renderRemoteReference(doc, reference, {frameUrl = null, title = '源视频参考'} = {}) {
  const figure = doc.createElement('figure'); figure.className = 'picture-figure video-figure';
  const caption = doc.createElement('figcaption'); caption.textContent = title;
  const description = doc.createElement('p'); description.className = 'small muted'; description.textContent = referenceDescription(reference);
  figure.append(caption, description);
  if (frameUrl) {
    const image = doc.createElement('img'); image.src = frameUrl; image.alt = '本次实验实际读取的参考帧'; image.loading = 'lazy';
    image.style && (image.style.maxWidth = '100%'); figure.append(image);
  }
  const url = sourcePageUrl(reference?.source_url);
  if (url) {
    const link = doc.createElement('a'); link.className = 'button secondary'; link.textContent = '打开源视频';
    link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; figure.append(link);
  }
  const retention = doc.createElement('p'); retention.className = 'small muted';
  retention.textContent = '需要时从源站读取，整条参考视频不长期保存；本次实际使用的帧作为观察证据保留。'; figure.append(retention);
  return figure;
}

export const REMOTE_REFERENCE_ERRORS = {
  remote_reference_unavailable: '暂时无法读取源视频，请检查链接或稍后重试。',
  remote_reference_source_unavailable: '源站暂时没有提供可读取的视频，请核对链接和源站访问状态。',
  remote_reference_fetch_failed: '读取源视频失败，请稍后使用原请求重试。',
  remote_reference_read_failed: '源视频传输中断，请稍后使用原请求重试。',
  remote_reference_access_required: '源站要求访问权限，需配置相应的源站登录权限后重试；当前没有用其他低清来源代替。',
  remote_reference_login_required: '源站要求登录后读取此画质，请配置源站访问权限后重试。',
  remote_reference_source_changed: '源站元数据或可用来源发生变化，请重新确认并登记参考来源。',
  remote_reference_quality_changed: '源站可用画质发生变化，尚未自动降画质；请重新确认参考来源。',
  remote_reference_quality_unavailable: '源站当前无法提供所需画质，尚未用低清画面代替。',
  remote_reference_provider_unsupported: '当前尚不支持这个视频网站，请使用已支持的源站链接或上传参考文件。',
  remote_reference_playback_unsupported: '源站当前播放格式尚无法读取，请核对源站视频后重试。',
  remote_reference_range_unsupported: '这个来源不支持按范围读取，尚未回退为下载整条视频；请更换来源。',
  remote_reference_byte_limit: '本次读取达到流量预算，尚未下载整条视频；请缩小观察范围或提高读取预算后再试。',
  remote_reference_deadline_exhausted: '本次源视频读取超时，可提高本次时间预算后重新尝试。',
  remote_reference_timestamp_invalid: '截图时间超出源视频范围，请核对时间点。',
  remote_reference_crop_invalid: '裁剪区域无效，请使用未超出画面的完整 0–1 比例范围。',
  remote_reference_frames_unavailable: '暂时没有取得有效参考帧，请核对片段范围后重试。',
  remote_reference_decoder_unavailable: '远程参考的解码工具暂不可用，请稍后重试。',
  remote_reference_url_invalid: '视频链接无效，请使用支持的 HTTPS 源站链接。',
};

export function remoteReferenceErrorMessage(error) {
  return typeof error?.code === 'string' && error.code.startsWith('remote_reference_')
    ? REMOTE_REFERENCE_ERRORS[error.code] ?? '远程参考尚未读取成功，请核对源站链接、时间范围和访问条件后重试。'
    : null;
}
