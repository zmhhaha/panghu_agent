// Pure reply routing, shared by the dedicated parent and realm. No global port.
export function filmRealmMessage(identity,payload){
 return {...payload,module_id:identity.module_id,module_sha256:identity.module_sha256};
}
export function createFilmRealmReplyReceiver({identity,pending,onConnected,onFatal}){
 let connected=false,failed=false;
 function stop(error){
  if(failed)return;failed=true;
  for(const request of pending.values())request.reject(error);
  pending.clear();onFatal(error);
 }
 return result=>{
  if(failed)return;
  if(!result||typeof result!=='object'||result.module_id!==identity.module_id
   ||result.module_sha256!==identity.module_sha256){stop(Error('film_realm_identity_mismatch'));return;}
  if(result.type==='connected'){
   if(connected){stop(Error('film_realm_duplicate_connection'));return;}
   connected=true;onConnected();return;
  }
  if(!connected||!Number.isInteger(result.id)||result.id<=0||typeof result.ok!=='boolean'
   ||!pending.has(result.id)||(result.ok===false&&(typeof result.error!=='string'||!result.error||result.error.length>240))){
   stop(Error('film_realm_reply_protocol_invalid'));return;
  }
  const request=pending.get(result.id);pending.delete(result.id);
  if(result.ok)request.resolve(result);else request.reject(Error(result.error));
 };
}
