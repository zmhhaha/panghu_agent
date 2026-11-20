import * as ink from './ink-landscape.mjs';

// 雨霽江渚 — an original, editable landscape. No reference image is used.
// The authored pressure paths below are real ink-landscape brush documents.
const W = 1600, H = 1000;
const canvas = document.getElementById('painting');
canvas.width = W; canvas.height = H;
const c = canvas.getContext('2d');
let state = 20261120;
const random = () => { state |= 0; state = state + 0x6D2B79F5 | 0; let t = Math.imul(state ^ state >>> 15, 1 | state); t ^= t + Math.imul(t ^ t >>> 7, 61 | t); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
const between = (a,b) => a+(b-a)*random();
const makeCanvas = () => { const a = document.createElement('canvas'); a.width=W; a.height=H; return a; };
const brushDocuments = [];
const pathPoints = (pts) => pts.map(p=>({x:p[0],y:p[1],pressure:p[2]??1}));
function brush(pts, width, opacity=.8, options={}) {
  const doc=ink.createBrushDocument(pathPoints(pts), {
    width, ink:'#28322e', opacity, dryness:.2, seed:Math.floor(random()*1e8),
    smoothing:'catmull-rom', gestureModel:'local-v1', finish:'open',
    controls:[{at:0,pressure:.8,side:.2,dryness:.24},{at:.45,pressure:1,side:.3,dryness:.35},{at:1,pressure:.18,side:.05,dryness:.48}],
    ...options,
  });
  brushDocuments.push(doc); return doc;
}
function renderBrushes(start=0) {
  ink.renderCanvas(c,{version:1,width:W,height:H,seed:20261120,background:'#f4f0e6',marks:brushDocuments.slice(start).map(d=>d.result)}, {clear:false,paperTexture:0});
}
function smoothPath(ctx, points) {
  ctx.moveTo(points[0][0],points[0][1]);
  for(let i=0;i<points.length-1;i++){
    const a=points[Math.max(0,i-1)],b=points[i],d=points[i+1],e=points[Math.min(points.length-1,i+2)];
    ctx.bezierCurveTo(b[0]+(d[0]-a[0])/6,b[1]+(d[1]-a[1])/6,d[0]-(e[0]-b[0])/6,d[1]-(e[1]-b[1])/6,d[0],d[1]);
  }
}
function nativeStroke(points, color, width) { c.beginPath(); smoothPath(c,points); c.strokeStyle=color;c.lineWidth=width;c.lineCap='round';c.lineJoin='round';c.stroke(); }

function paper() {
  c.fillStyle='#f2efe5';c.fillRect(0,0,W,H);
  const light=c.createRadialGradient(940,420,50,820,500,1050);
  light.addColorStop(0,'rgba(255,253,244,.67)'); light.addColorStop(.7,'rgba(247,243,232,.2)');light.addColorStop(1,'rgba(216,208,185,.13)');
  c.fillStyle=light;c.fillRect(0,0,W,H);
  // Fine fibers remain at paper scale instead of becoming visible landscape texture.
  for(let i=0;i<24000;i++){
    const x=random()*W,y=random()*H;
    c.fillStyle=random()<.6?'rgba(91,80,53,.018)':'rgba(255,255,249,.09)';
    c.fillRect(x,y,between(.35,1.2),between(.35,1.1));
  }
  for(let i=0;i<600;i++){
    const x=random()*W,y=random()*H;
    c.beginPath();c.moveTo(x,y);c.lineTo(x+between(1,4),y+between(-.5,.5));
    c.strokeStyle='rgba(110,99,72,.016)';c.lineWidth=.35;c.stroke();
  }
}

function mountain(points, bottom, color, alpha, blur=1.8, creases=[]) {
  const layer=makeCanvas(),m=layer.getContext('2d');
  const top=Math.min(...points.map(p=>p[1]));
  m.beginPath();smoothPath(m,points);m.lineTo(points.at(-1)[0]+20,bottom);m.lineTo(points[0][0]-20,bottom);m.closePath();
  const g=m.createLinearGradient(0,top,0,bottom);
  g.addColorStop(0,`rgba(${color},${alpha})`);g.addColorStop(.3,`rgba(${color},${alpha*.84})`);g.addColorStop(.68,`rgba(${color},${alpha*.42})`);g.addColorStop(1,`rgba(${color},0)`);
  m.fillStyle=g;m.fill();
  m.save();m.clip();
  for(const crease of creases){
    m.beginPath();smoothPath(m,crease);m.strokeStyle=`rgba(${color},${alpha*.08})`;m.lineWidth=between(10,24);m.lineCap='round';m.stroke();
  }
  m.restore();
  c.save();c.filter=`blur(${blur}px)`;c.drawImage(layer,0,0);c.restore();
  // Broken, extremely light edge deposits imply pigment at a wet boundary.
  for(let i=0;i<points.length-1;i++){
    if(random()<.4)continue;
    nativeStroke(points.slice(Math.max(0,i-1),Math.min(points.length,i+2)),`rgba(${color},${alpha*.11})`,.65);
  }
}

function distantLandscape() {
  mountain([[-70,462],[58,439],[164,420],[259,426],[355,403],[463,415],[556,440],[675,450],[790,470],[912,537],[1015,597]],597,'109,128,126',.105,3.2);
  mountain([[365,595],[442,537],[513,479],[580,435],[685,405],[757,360],[826,348],[897,317],[957,324],[1023,312],[1095,340],[1167,368],[1250,390],[1380,432],[1670,465]],595,'115,134,135',.13,3.2);
  mountain([[596,619],[675,552],[765,496],[848,454],[956,412],[1040,358],[1120,310],[1172,282],[1229,283],[1292,255],[1359,240],[1415,250],[1462,276],[1525,291],[1605,307],[1680,352]],619,'88,111,111',.24,2.6,[
    [[1285,273],[1270,327],[1215,381],[1183,445]],
    [[1404,267],[1408,333],[1368,392],[1336,475]],
    [[1471,303],[1485,370],[1477,450]],
    [[1148,311],[1153,366],[1115,421]],
  ]);
  mountain([[798,641],[891,582],[961,541],[1025,505],[1110,470],[1174,416],[1232,379],[1288,372],[1347,384],[1407,358],[1470,368],[1530,402],[1602,425],[1670,420]],641,'75,102,101',.16,2.2,[
    [[1245,392],[1239,438],[1193,506]],[[1392,384],[1370,452],[1327,523]],[[1509,407],[1504,457],[1547,530]],
  ]);
  // Cloud belts dissolve the feet of the hills into the water.
  const fog=c.createLinearGradient(0,444,0,631);fog.addColorStop(0,'rgba(245,242,232,0)');fog.addColorStop(.54,'rgba(245,242,232,.21)');fog.addColorStop(1,'rgba(245,242,232,.0)');
  c.fillStyle=fog;c.fillRect(400,444,1200,187);
  // A few distant reed islands establish the water's far scale.
  for(const a of [[676,555,69],[825,547,54],[1167,568,93],[1461,559,108]]){
    nativeStroke([[a[0],a[1]+1],[a[0]+a[2]*.25,a[1]-1],[a[0]+a[2]*.68,a[1]],[a[0]+a[2],a[1]+.7]],'rgba(83,105,101,.09)',1.1);
  }
}

function river() {
  // Deliberate broad blank water; very few horizontal breaths.
  const lines=[ [555,673,127,.033],[1230,637,137,.031],[804,759,76,.034],[1110,815,167,.026],[454,791,81,.035],[1323,721,84,.035],[683,904,191,.027],[1040,700,112,.027],[1350,899,127,.02],[346,708,73,.03] ];
  for(const [x,y,length,a] of lines)nativeStroke([[x,y],[x+length*.27,y-.5],[x+length*.73,y+.5],[x+length,y]],`rgba(85,108,102,${a})`,.8);
}

function foregroundBank() {
  c.beginPath();smoothPath(c,[[-70,849],[33,844],[101,853],[154,848],[213,867],[267,875],[306,902],[360,914],[415,940],[437,950],[447,968],[473,966],[488,986],[509,984],[519,1008],[546,1004],[565,1025]]);
  c.lineTo(-70,1040);c.closePath();
  const g=c.createLinearGradient(0,850,525,1000);g.addColorStop(0,'rgba(62,70,59,.55)');g.addColorStop(.4,'rgba(63,71,59,.37)');g.addColorStop(.72,'rgba(86,88,67,.18)');g.addColorStop(1,'rgba(86,88,67,0)');c.fillStyle=g;c.fill();
  // Warm wet pools and untouched paper break the broad bank's flatness.
  for(const [x,y,rx,ry,a] of [[43,920,75,28,.13],[148,931,67,23,.1],[281,961,73,19,.1],[67,986,99,22,.12]]) {
    const wash=c.createRadialGradient(x,y,0,x,y,rx);
    wash.addColorStop(0,`rgba(92,83,59,${a})`);wash.addColorStop(1,'rgba(92,83,59,0)');
    c.save();c.translate(x,y);c.scale(1,ry/rx);c.translate(-x,-y);c.fillStyle=wash;c.beginPath();c.arc(x,y,rx,0,Math.PI*2);c.fill();c.restore();
  }
  const washStart=brushDocuments.length;
  brush([[-30,850,1],[31,847,.8],[75,852,1],[127,847,.55],[169,857,.7],[223,869,.3]],8,.56,{dryness:.55});
  brush([[179,865,.7],[230,878,1],[262,878,.4],[294,901,.7],[323,905,.2]],6,.44,{dryness:.6});
  brush([[303,905,.5],[343,913,1],[380,933,.6],[415,941,.3]],5,.42,{dryness:.52});
  brush([[20,881,.9],[77,872,1],[110,884,.4]],12,.35,{dryness:.8});
  brush([[199,923,.5],[243,916,.8],[284,931,.5],[320,931,.1]],8,.3,{dryness:.79});
  brush([[54,946,.5],[97,930,1],[150,946,.2]],12,.24,{dryness:.8});
  // Connected pools and dry side strokes give the small bank an uneven ink weight.
  brush([[-12,869,.55],[19,870,1],[46,884,.8],[84,880,.3]],27,.29,{dryness:.59,axis:'side'});
  brush([[79,885,.3],[113,897,1],[149,905,.65],[173,922,.1]],20,.26,{dryness:.68,axis:'side'});
  brush([[215,897,.1],[241,904,.8],[254,925,.9],[297,945,.2]],17,.22,{dryness:.66,axis:'side'});
  brush([[330,940,.2],[354,950,.8],[371,967,.4],[411,984,.03]],13,.18,{dryness:.73,axis:'side'});
  brush([[9,927,.2],[43,937,.8],[66,963,.6],[108,979,.1]],25,.16,{dryness:.82,axis:'side'});
  brush([[-12,958,.1],[19,951,.8],[46,967,.5],[71,979,0]],12,.37,{ink:'#eee7d0',dryness:.78,axis:'side'});
  brush([[111,913,.1],[141,916,.6],[159,933,.7],[184,941,0]],10,.32,{ink:'#eee7d0',dryness:.77,axis:'side'});
  brush([[190,966,.1],[218,956,.8],[237,970,.5],[274,978,0]],10,.3,{ink:'#eee7d0',dryness:.85,axis:'side'});
  renderBrushes(washStart);
  for(const p of [ [[11,874],[38,892],[50,917]],[[132,884],[148,906],[181,930]],[[232,895],[241,914],[276,944]],[[288,926],[306,950],[345,970]],[[354,945],[373,965],[409,980]] ]) {
    nativeStroke(p,'rgba(38,50,41,.22)',1.6);
    nativeStroke(p.map(a=>[a[0]+3,a[1]+1]),'rgba(238,229,204,.44)',2.1);
  }
  // Weathered stones are described by a handful of directional strokes.
  for(const rock of [ [[-20,838],[16,814],[60,825],[85,846]],[[119,856],[140,835],[179,842],[197,860]],[[255,886],[268,869],[291,876],[307,899]],[[349,927],[372,908],[397,923],[415,941]] ]){
    c.beginPath();smoothPath(c,rock);c.lineTo(rock.at(-1)[0]-10,rock.at(-1)[1]+15);c.lineTo(rock[0][0],rock[0][1]+10);c.closePath();c.fillStyle='rgba(57,66,55,.22)';c.fill();
    nativeStroke(rock,'rgba(36,47,39,.38)',1.1);
  }
  // Root-side grass is restrained and follows the bank's sloping edge.
  for(let i=0;i<42;i++){
    const x=between(12,390), y=853+Math.max(0,x-105)*.27+between(0,23),h=between(7,25);
    nativeStroke([[x,y],[x+between(-6,5),y-h*.65],[x+between(-10,8),y-h]],`rgba(44,58,44,${between(.19,.43)})`,between(.6,1.3));
  }
}

function pine() {
  const from=brushDocuments.length;
  // One old, slanting trunk, with weight carried by its lower bend.
  brush([[63,906,1],[83,865,1],[91,812,.9],[116,766,.83],[153,727,.75],[182,682,.65],[227,642,.55],[258,602,.37],[309,571,.05]],26,.83,{dryness:.35,controls:[{at:0,pressure:1,side:.3,dryness:.22},{at:.45,pressure:.82,side:.65,dryness:.47},{at:.8,pressure:.43,side:.25,dryness:.42},{at:1,pressure:0,dryness:.6}]});
  brush([[88,865,.8],[121,825,.6],[139,787,.4],[165,765,.15]],9,.7,{dryness:.44});
  brush([[155,728,1],[116,704,.65],[77,668,.45],[28,645,.2],[-9,637,.04]],10,.81);
  brush([[183,682,.85],[226,686,.65],[265,669,.4],[309,663,.12]],10,.83);
  brush([[225,642,.9],[199,608,.65],[158,589,.42],[111,581,.08]],10,.85);
  brush([[254,606,.7],[294,597,.55],[327,566,.34],[375,551,.03]],9,.86);
  brush([[288,583,.6],[297,545,.45],[329,521,.04]],7,.82);
  brush([[118,768,.65],[76,759,.5],[46,728,.22],[7,715,.04]],7,.78);
  brush([[154,590,.5],[141,559,.22],[112,546,.03]],4,.8);
  brush([[268,668,.5],[280,642,.22],[307,633,.03]],4,.75);
  brush([[225,686,.5],[245,711,.2],[277,716,.03]],5,.75);
  brush([[329,566,.4],[350,580,.16],[385,577,.02]],4,.8);
  brush([[120,705,.5],[124,679,.2],[111,654,.02]],4,.74);
  // Exposed roots and a dry highlight down the twist of the trunk.
  brush([[95,860,.7],[132,865,.5],[169,881,.2],[203,883,.05]],7,.64,{dryness:.65});
  brush([[72,878,.75],[45,878,.4],[17,894,.02]],8,.62,{dryness:.6});
  renderBrushes(from);
  nativeStroke([[75,891],[93,855],[99,814],[128,770],[166,725],[187,686]],'rgba(222,215,193,.62)',2.2);
  nativeStroke([[87,880],[105,853],[111,813],[137,778]],'rgba(235,227,205,.42)',1);
  for(let i=0;i<21;i++){
    const t=i/20,x=80+103*t,y=875-192*t;
    nativeStroke([[x-4,y+4],[x+3,y],[x+8,y-1]],`rgba(27,41,32,${between(.24,.55)})`,between(.6,1.1));
  }
  // The foliage is drawn as directional fans, never as filled cloud stamps.
  const boughs=[
    [115,552,44,15,-2.1],[155,578,57,16,-1.4],[98,585,61,15,-1.85],
    [27,637,64,18,-2.0],[74,657,59,17,-1.7],[117,675,37,12,-1.45],
    [311,528,49,15,-1.7],[355,546,56,16,-1.35],[383,566,46,12,-.8],
    [294,589,45,14,-1.7],[328,576,54,15,-1.1],
    [271,651,42,13,-1.6],[312,660,51,14,-1.0],[281,706,47,12,-1.1],
    [26,718,45,12,-1.9],[65,738,37,10,-1.6],
  ];
  // A few condensed side-brush beds hold the canopy together. Their unequal
  // silhouettes are built from individual wet marks, leaving open needle tips.
  const bedStart=brushDocuments.length;
  const darkBeds=[[77,577,32,.78],[34,637,32,.84],[349,550,35,.87],[310,529,21,.71],[287,653,27,.74],[29,718,20,.57]];
  for(const [cx,cy,r,alpha] of darkBeds){
    for(let j=0;j<7;j++){
      const u=(j/6-.5),x=cx+u*r*1.6,y=cy-6+Math.sin(j*1.6)*3;
      const len=between(8,16),rise=between(3,10);
      brush([[x,y,.35],[x+len*.3,y-rise*.6,1],[x+len*.75,y-rise,.76],[x+len,y-rise*.8,.1]],between(7,12),alpha*between(.65,.96),{
        axis:'side',dryness:between(.18,.4),controls:[{at:0,pressure:.6,side:.7,dryness:.22},{at:.45,pressure:1,side:.8,dryness:.25},{at:1,pressure:.1,side:.5,dryness:.5}],
      });
    }
    for(let j=0;j<4;j++){
      const x=cx+between(-r*.65,r*.65),y=cy-between(8,17);
      brush([[x,y,.3],[x+3,y-2,1],[x+8,y-3,.1]],between(6,9),alpha*.8,{axis:'side',dryness:.34});
    }
  }
  renderBrushes(bedStart);
  for(const [cx,cy,spread,n,angle] of boughs){
    for(let j=0;j<n;j++){
      const u=(j/(n-1)-.5), x=cx+u*spread*1.55+between(-4,4),y=cy+Math.abs(u)*9+between(-4,5);
      const length=between(17,31),ang=angle+u*1.5+between(-.22,.22);
      const tx=x+Math.cos(ang)*length,ty=y+Math.sin(ang)*length;
      nativeStroke([[x,y],[x+(tx-x)*.55,y+(ty-y)*.6],[tx,ty]],`rgba(30,48,37,${between(.52,.85)})`,between(.75,1.6));
      // Two smaller needles split at the base, opening the pine's silhouette.
      for(let k=0;k<2;k++){
        const a=ang+between(-.7,.7),len=length*between(.62,1.02);
        nativeStroke([[x,y],[x+Math.cos(a)*len*.55,y+Math.sin(a)*len*.6],[x+Math.cos(a)*len,y+Math.sin(a)*len]],`rgba(29,47,35,${between(.33,.69)})`,between(.6,1.1));
      }
    }
  }
  // Several broader needle accents made by the MIT brush engine.
  const accents=brushDocuments.length;
  for(const [cx,cy,spread,n,angle] of boughs){
    for(let j=0;j<4;j++){
      const x=cx+between(-spread*.45,spread*.45),y=cy+between(-5,4),a=angle+between(-.6,.6),len=between(15,29);
      brush([[x,y,.8],[x+Math.cos(a)*len*.45,y+Math.sin(a)*len*.5,.6],[x+Math.cos(a)*len,y+Math.sin(a)*len,0]],1.8,between(.48,.75),{dryness:.45});
    }
  }
  renderBrushes(accents);
  // Condensed ink belongs to selected, unequal needle groups; the remaining
  // branch tips stay airy. Each short pressure path crosses the fan's grain.
  const foliageStart=brushDocuments.length;
  const inkGroups=[
    [80,580,33,22,.74,-1.8],[146,575,25,16,.62,-1.3],
    [31,637,36,22,.77,-1.85],[92,658,28,16,.66,-1.55],
    [315,530,24,16,.7,-1.55],[357,550,41,27,.8,-1.2],
    [318,575,29,18,.6,-1.1],[291,655,35,22,.67,-1.35],
    [275,704,22,12,.47,-1.5],[29,718,25,14,.63,-1.9],
  ];
  for(const [cx,cy,spread,n,alpha,angle] of inkGroups){
    for(let j=0;j<n;j++){
      const u=between(-1,1),x=cx+u*spread*.67,y=cy+between(-3,5)+Math.abs(u)*3;
      const a=angle+u*.47+between(-.45,.45),len=between(10,24);
      brush([[x,y,.4],[x+Math.cos(a)*len*.48,y+Math.sin(a)*len*.6,1],[x+Math.cos(a)*len,y+Math.sin(a)*len,0]],between(2.2,4.2),alpha*between(.6,1),{dryness:between(.2,.47)});
    }
    brush([[cx-spread*.5,cy+2,.1],[cx-spread*.12,cy+1,.7],[cx+spread*.2,cy-1,.8],[cx+spread*.51,cy+2,0]],3.4,alpha*.7,{dryness:.5});
  }
  renderBrushes(foliageStart);
}

function boat() {
  const x=1024,y=703;const start=brushDocuments.length;
  // Only thirty-eight pixels long: the human presence is a pause in the river.
  brush([[x-22,y-1,.2],[x-13,y+3,.7],[x+9,y+4,1],[x+24,y-1,.05]],3.4,.72,{dryness:.25});
  brush([[x-20,y-2,.4],[x-3,y,.7],[x+18,y-.5,.1]],1,.58);
  brush([[x-2,y-3,.8],[x-3,y-10,.75],[x,y-14,.05]],3.2,.82,{dryness:.15});
  brush([[x-3,y-10,.4],[x+5,y-5,.7],[x+8,y-4,.03]],1.8,.68);
  brush([[x+5,y-5,.4],[x+17,y+6,.5],[x+26,y+15,.02]],1,.58);
  renderBrushes(start);
  c.beginPath();c.ellipse(x-1,y-17,2.8,2.6,0,0,Math.PI*2);c.fillStyle='rgba(34,47,39,.75)';c.fill();
  for(let i=0;i<3;i++)nativeStroke([[x-28-i*3,y+10+i*5],[x-8,y+11+i*5],[x+20+i*5,y+10+i*5]],`rgba(69,91,83,${.09-i*.02})`,.7);
}

function inscription() {
  c.save();c.fillStyle='rgba(59,66,53,.63)';c.font='22px KaiTi, STKaiti, "Noto Serif CJK SC", serif';c.textAlign='center';
  for(const [i,char] of Array.from('雨霽江渚').entries())c.fillText(char,1461,674+i*27);
  c.fillStyle='rgba(141,57,43,.65)';c.fillRect(1453,786,16,17);
  c.strokeStyle='rgba(243,232,210,.79)';c.lineWidth=.9;c.strokeRect(1456,789,10,11);c.beginPath();c.moveTo(1461,789);c.lineTo(1461,800);c.moveTo(1456,794);c.lineTo(1466,794);c.stroke();c.restore();
}

export function render() {
  state=20261120;brushDocuments.length=0;
  paper();distantLandscape();river();foregroundBank();pine();boat();inscription();
  return canvas;
}
render();
window.artwork={title:'雨霽江渚',width:W,height:H,canvas,render,brushDocuments,ready:Promise.resolve(true),seek:()=>canvas};
