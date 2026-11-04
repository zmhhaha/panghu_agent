export const researchTimeZone='Asia/Shanghai';

export function researchTime(value?:string|null,fallback='尚未记录'):string {
  if(!value)return fallback;
  const instant=new Date(value);
  if(!Number.isFinite(instant.getTime()))return fallback;
  return `${instant.toLocaleString('zh-CN',{timeZone:researchTimeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})}（北京时间）`;
}
