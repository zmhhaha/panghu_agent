import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {THREE_VERSION,THREE_ASSET_SHA256,THREE_LICENSE_SHA256} from './engine_runtime.mjs';
const sha = p => createHash('sha256').update(readFileSync(p)).digest('hex');
const command = (file, args) => execFileSync(file, args, {encoding:'utf8',timeout:10000,maxBuffer:2*1024*1024}).trim();
export function collectRuntime(){
 const dpkg = command('dpkg-query',['-W','-f=${Package}=${Version}\n']);
 const three=sha('/opt/assets/three.module.min.js'),license=sha('/opt/assets/three.LICENSE.txt');
 if(three!==THREE_ASSET_SHA256||license!==THREE_LICENSE_SHA256)throw Error('runtime_three_asset_mismatch');
 return {schema:'video.motion-runtime.v2',
 hyperframes:JSON.parse(readFileSync('/opt/renderer/node_modules/hyperframes/package.json')).version,
 hyperframes_source:'2905162d925fc87fb8242e24ed0bbf025d307ea1',
 node:process.versions.node,browser:command('/opt/browser/headless_shell',['--version']),
 ffmpeg:command('ffmpeg',['-version']).split('\n')[0],
 gsap:JSON.parse(readFileSync('/opt/renderer/node_modules/gsap/package.json')).version,
 gsap_sha256:sha('/opt/assets/gsap.min.js'),font_sha256:sha('/opt/assets/NotoSansCJK-Regular.ttc'),
 three:THREE_VERSION,three_sha256:three,three_license_sha256:license,
 engine_runtime_sha256:sha('/opt/renderer/engine_runtime.mjs'),
 creative_host_sha256:sha('/opt/renderer/creative.mjs'),
 npm_lock_sha256:sha('/opt/renderer/package-lock.json'),
 dpkg_sha256:createHash('sha256').update(dpkg).digest('hex')};
}
if(process.argv[1]===fileURLToPath(import.meta.url))console.log(JSON.stringify(collectRuntime(),null,2));
