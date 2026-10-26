import { useState } from 'react';
import { FileText, Trash2 } from 'lucide-react';
import { api } from './api';
import type { Project } from './domain';
import { parseFailureMessage, parseStatusLabel, parseSummary } from './parse';

type Props = {
 project: Project;
 disabled: boolean;
 perform: (action: () => Promise<unknown>) => Promise<void>;
 showSummary?: boolean;
};

export function SourceFiles({project,disabled,perform,showSummary=true}:Props) {
 const [confirming,setConfirming]=useState<string|null>(null);
 return <ul className="assets">{project.assets.map(asset=><li key={asset.id}>
  <FileText size={19} aria-hidden="true"/>
  <div>
   <div className="asset-heading"><div>
    <strong>{asset.display_name}</strong>
    <small>{(asset.bytes/1024**2).toFixed(2)} MiB · <span role="status" aria-label={`${asset.display_name}解析状态`}>{parseStatusLabel(asset.parse_status)}</span></small>
   </div><button className="danger compact asset-delete" type="button" disabled={disabled} aria-label={`删除文件 ${asset.display_name}`} aria-expanded={confirming===asset.id} onClick={()=>{if(!disabled)setConfirming(asset.id);}}><Trash2 size={15} aria-hidden="true"/>删除文件</button></div>
   {confirming===asset.id&&<div className="asset-remove-confirm" role="group" aria-label={`删除文件确认 ${asset.display_name}`}>
    <strong>确认删除文件“{asset.display_name}”？</strong>
    <p>之后规划不再使用此文件，需要重新规划与确认；已有历史版本不受影响。</p>
    <div className="actions"><button className="secondary compact" type="button" disabled={disabled} onClick={()=>setConfirming(null)}>取消</button><button className="danger compact" type="button" disabled={disabled} onClick={()=>{if(!disabled)void perform(()=>api.removeAsset(project,asset.id));}}><Trash2 size={15} aria-hidden="true"/>确认删除</button></div>
   </div>}
   {showSummary&&parseSummary(asset)&&<p className="plain-text" aria-label={`${asset.display_name}文本摘要`}>{parseSummary(asset)}</p>}
   {asset.parse_status==='failed'&&<p className="field-error" role="alert">{parseFailureMessage(asset.parse_error??asset.error_code)}</p>}
  </div>
 </li>)}</ul>;
}
