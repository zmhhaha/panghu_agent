// Hostile-prototype protocol tests with DOM doubles; not real-browser evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../../video_workbench/motion_renderer/hyperframes_film_realm_guard.mjs',import.meta.url),'utf8')
 .replace('export function installFilmRealmGuard','function installFilmRealmGuard');
const setup=`
globalThis.window=globalThis;globalThis.navigator={serviceWorker:undefined};
class Node {get isConnected(){return true;}}
class DOMRectReadOnly {get width(){return 100;}get height(){return 100;}}
class CSSStyleDeclaration {getPropertyValue(name){return name==='opacity'?'1':name==='visibility'?'visible':'block';}}
class Element extends Node {getBoundingClientRect(){return new DOMRectReadOnly();}}
class HTMLCanvasElement extends Element {get width(){return 100;}get height(){return 100;}getContext(){return null;}}
class Document {querySelector(){return null;}getAnimations(){return [];}}
class Animation {get playState(){return 'paused';}}
globalThis.document=new Document();globalThis.getComputedStyle=()=>new CSSStyleDeclaration();
globalThis.requestAnimationFrame=()=>1;globalThis.cancelAnimationFrame=()=>{};
`;
const poison=`
Array.prototype.map=function(){return [{kind:'webgl2',visible:true,width:100,height:100,software:true}];};
Array.prototype.some=function(){return true;};Array.prototype.includes=function(){return true;};
Function.prototype.call=function(){throw Error('poisoned_call');};
Object.values=function(){return [];};
Set.prototype.add=function(){throw Error('poisoned_set_add');};Set.prototype.delete=function(){throw Error('poisoned_set_delete');};
Object.defineProperty(Set.prototype,'size',{get(){return 999;}});
Number=function(){return 999;};String=function(){return 'fake';};RegExp.prototype.test=function(){return true;};
`;
for(const engine of ['canvas','three'])test(`poisoned shared prototypes cannot invent a ${engine} context`,()=>{
 const context=vm.createContext({});
 vm.runInContext(setup+source+`const observe=installFilmRealmGuard('${engine}');`+poison+
  `try{globalThis.result=observe();}catch(error){globalThis.failure=error.message;}`,context);
 assert.equal(context.result,undefined);assert.equal(context.failure,'film_realm_native_context_missing');
});
test('a real registered context double remains correct despite map/call/Number/Set poisoning',()=>{
 const context=vm.createContext({});
 vm.runInContext(setup+`HTMLCanvasElement.prototype.getContext=function(){return {nativeFixture:true};};`+source+
  `const observe=installFilmRealmGuard('canvas');const canvas=new HTMLCanvasElement();canvas.getContext('2d');`+poison+
  `globalThis.result=observe();`,context);
 assert.equal(context.result.contexts.length,1);assert.equal(context.result.contexts[0].kind,'2d');
 assert.equal(context.result.contexts[0].visible,true);assert.equal(context.result.pending_animation_frames,0);
});
test('pending native RAF is still detected after Set size/delete poisoning',()=>{
 const context=vm.createContext({});
 vm.runInContext(setup+source+`const observe=installFilmRealmGuard('html-svg');requestAnimationFrame(()=>{});`+poison+
  `try{observe();}catch(error){globalThis.failure=error.message;}`,context);
 assert.equal(context.failure,'film_realm_pending_animation_frame');
});
