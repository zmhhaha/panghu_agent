import test from 'node:test';
import assert from 'node:assert/strict';
import {filmRealmMessage,createFilmRealmReplyReceiver} from '../../video_workbench/motion_renderer/hyperframes_film_channel.mjs';
const identity={module_id:'canvas',module_sha256:'a'.repeat(64)};
function fixture(){
 const pending=new Map(),fatals=[];let connected=false;
 const receive=createFilmRealmReplyReceiver({identity,pending,onConnected(){connected=true;},onFatal(error){fatals.push(error);}});
 const request=id=>{const promise=new Promise((resolve,reject)=>pending.set(id,{resolve,reject}));promise.catch(()=>{});return promise;};
 return {pending,fatals,receive,request,connected:()=>connected};
}
for(const stage of ['import','mount','render','dispose'])test(`${stage} rejection preserves fixed identity and original error without timeout`,async()=>{
 const f=fixture();f.receive(filmRealmMessage(identity,{type:'connected'}));assert.equal(f.connected(),true);
 const operation=f.request(1),reply=filmRealmMessage(identity,{id:1,ok:false,error:'fixture_'+stage+'_failed'});
 assert.equal(reply.module_id,identity.module_id);assert.equal(reply.module_sha256,identity.module_sha256);
 f.receive(reply);await assert.rejects(operation,new RegExp('fixture_'+stage+'_failed'));
 assert.equal(f.pending.size,0);assert.equal(f.fatals.length,0);
});
for(const change of ['missing_identity','wrong_sha','wrong_id','no_ok','empty_error','duplicate_connection'])
 test(`malformed ${change} stops channel and rejects all pending operations immediately`,async()=>{
  const f=fixture();f.receive(filmRealmMessage(identity,{type:'connected'}));
  const first=f.request(1),second=f.request(2);
  let reply=filmRealmMessage(identity,{id:1,ok:true});
  if(change==='missing_identity')delete reply.module_id;
  if(change==='wrong_sha')reply.module_sha256='f'.repeat(64);
  if(change==='wrong_id')reply.id=99;
  if(change==='no_ok')delete reply.ok;
  if(change==='empty_error')reply={...reply,ok:false,error:''};
  if(change==='duplicate_connection')reply=filmRealmMessage(identity,{type:'connected'});
  f.receive(reply);await assert.rejects(first,/film_realm_/);await assert.rejects(second,/film_realm_/);
  assert.equal(f.pending.size,0);assert.equal(f.fatals.length,1);
  f.receive(filmRealmMessage(identity,{id:1,ok:true}));assert.equal(f.fatals.length,1);
 });
test('successful response resolves only its exact pending request',async()=>{
 const f=fixture();f.receive(filmRealmMessage(identity,{type:'connected'}));const operation=f.request(1);
 const reply=filmRealmMessage(identity,{id:1,ok:true,observation:{contexts:[]}});f.receive(reply);
 assert.deepEqual(await operation,reply);assert.equal(f.pending.size,0);
});
