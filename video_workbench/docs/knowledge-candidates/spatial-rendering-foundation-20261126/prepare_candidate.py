"""Create/validate local, unpublished candidates using existing project stores.

This script never applies Wiki proposals, publishes skills, adopts effects,
renders media, calls a model, accesses production, or changes another directory.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[3]
sys.path.insert(0,str(REPO))
from agent_service.contracts import canonical_bytes,digest
from agent_service.animation_skills_contract import build_skill_package,validate_skill_package
from agent_service.animation_knowledge_contract import validate_snapshot,validate_knowledge_proposals,validate_experience
from video_workbench.animation_knowledge import AnimationKnowledgeStore
from video_workbench.animation_method_store import AnimationMethodStore,_method
from video_workbench.animation_method_research import validate_research

OWNER='local-spatial-research'
IMPLEMENTATION_COMMIT='ae06a9ec6d1100bed857956ec524dbada7ab60d1'
UPSTREAM_V1='16a9bd434c40c50707ceab3987e0ca1ecaf3fd42'
UPSTREAM_V2='c37fc23b28f9e9a00a9a1a27f93e8b4dfacd6245'
BASE_SOURCE='1b6693b620f576dacf2b328367999a00458b8440f3f86ff825e996b153734be0'
STUDY_SOURCE='1b6324d28ef35cac7b1d3d252c409ae7ea7885bda1e1527ecd728ee0d7ab451d'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8',newline='\n')
def read(path):return json.loads(Path(path).read_bytes())

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-root',type=Path,default=Path('D:/codex-work/operations/video-BV179WZ6REYX-research-20261126'))
    parser.add_argument('--operations-root',type=Path,default=Path('D:/codex-work/operations/video-spatial-foundation-20261126'))
    parser.add_argument('--store-root',type=Path,default=Path('D:/codex-work/operations/video-spatial-foundation-20261126/knowledge-candidates/spatial-rendering-foundation-20261126'))
    args=parser.parse_args();research=args.research_root;ops=args.operations_root
    args.store_root.mkdir(parents=True,exist_ok=True)
    example=REPO/'video_workbench/examples/spatial-foundation-study'
    native=read(example/'evidence.json')
    assert native['source_sha256']==STUDY_SOURCE and native['baseline_source_sha256']==BASE_SOURCE
    assert native['frames']==240 and native['duration_seconds']==10 and native['fps']==24
    assert native['full_video_decoded'] and native['no_policy_pixel_regression']['all_video_frame_bytes_identical']
    assert native['appearance_budget_case']['canonical_accounting']=='charged_before_material_evaluation_no_refund'
    assert native['production_model_calls']==0 and native['model_review']=='not_run' and native['artistic_acceptance'] is False
    assert all(all(row['checks'][k] for k in ('repeat_passed','reverse_passed','reload_passed')) for row in native['per_case_native_observation'])
    for name,identity in native['artifact_files_sha256'].items():assert sha(example/name)==identity

    files={str(p.relative_to(ROOT/'skill')).replace('\\','/'):p.read_text(encoding='utf-8')
           for p in sorted((ROOT/'skill').rglob('*')) if p.is_file()}
    runtime=dict(engines=['canvas'],scene_space_artwork_api_revision=3,
        required_host_features=['independent_object_coverage','authored_shared_xyz_fixed_orthographic',
            'absolute_frame_clock','static_object_caches','evaluateLighting','sampleMaterial','sampleAtmosphere'],
        implementation_scope='Local native Canvas host only; no GPU or other-engine artistic admission')
    package=build_skill_package(skill_id='spatial-ink-and-camera-cues',version=1,kind='expression',
        files=files,runtime_requirements=runtime)
    validate_skill_package(package);save(ROOT/'skill-package.json',package)

    frames=[]
    for i,p in enumerate(sorted((research/'frames-4s').glob('frame-*.jpg'))):
        frames.append(dict(at_seconds=i*4,evidence_sha256=sha(p)))
    assert len(frames)==131
    references=[dict(
        research_id='spatial-foundation-bv179wz6reyx',
        title='BV179WZ6REYX：七个成像开关与单镜头代码动画的研究',
        source=dict(url='https://www.bilibili.com/video/BV179WZ6REYX/',
            media_identity=dict(platform='bilibili',external_id='BV179WZ6REYX',duration_seconds=524,
                sha256=sha(research/'source-video.mp4'),title='这些「AI 做的」视频凭什么像真的？我把它拆成了7个开关')),
        observation=dict(frames=frames,motion_intervals=[],audio_intervals=[],limitations=[
            '公开研究副本852×480；未检查原4K像素。131张四秒采样覆盖全时线，不等于连续看完整视频。',
            '完整音频用机器ASR转写，专名经画面/源码校正；未将ASR声明为直接听过声音。',
            '作者宣称调研12作品；本次未逐个复现，其模型/星数/成本仅为转述。']),
        raw_observations=[
            '00:44–01:22：按指定时间绘图，浏览器逐帧截图编码；同一时间应与机器快慢及前帧历史无关。',
            '01:39–01:47：赤龙90秒单HTML最终2,013,558字节，公开源码当前大小吻合；小程序体积不等于运行或视频成本。',
            '02:11–03:35：直射光/硬影、多尺度材质、法线和粗糙度、环境光与反射、软影与接触暗部，分项同镜头比较。',
            '03:36–04:31：景深是光圈/焦平面采样，运动模糊是快门区间多时刻平均，“冲洗”是色调映射/调色/暗角/颗粒。',
            '04:47–05:32：山湖与机甲城增加空气/光柱，实际室外比七项多一项；BLACKOUT说明硬影描线也是风格选择。',
            '05:40–07:17：一句话背后可能是长提示词、导演/技术手册与检查工具，多个时刻拼图和具体弱点复盘；费用为他人自述未独立复测。',
            '07:18–08:44：人物/手/布发、模板套路与成本局限；工具包只管画面，纯代码视觉不代表旁白/配乐没有生成模型。',
            '本候选提炼结构、光向、纹理、空气、镜头与同内容对照；没有按视频重新生成新题材。'],
        provenance=dict(scope='external_video_research_not_local_render_validation',
            chronology='Original first-version video; later repository v2 is a distinct source record.',
            transcript_sha256=sha(research/'transcript-whisper-medium.txt'),
            research_report_sha256=sha(research/'research-report.zh-CN.md'),
            complete_audio_transcription=True,direct_audio_listening=False,
            continuous_video_playback=False,full_timeline_sampled=True,external_urls_runtime_dependencies=False)),
        dict(research_id='spatial-foundation-zhenshi-code',
        title='真实感开关公开源码：首版机制与后续修订边界',
        source=dict(url=f'https://github.com/syydaniel/zhenshi-kaiguan/tree/{UPSTREAM_V2}',
            media_identity=dict(platform='github',external_id='syydaniel/zhenshi-kaiguan@'+UPSTREAM_V2)),
        observation=dict(frames=[],motion_intervals=[],audio_intervals=[],limitations=[
            '只读源码/文档，未运行这套渲染器或复测Apple M3 Max性能。',
            '视频UP与GitHub匿名账户没有独立身份交叉证明；仅确认材料与片名/三个示例一致。',
            'MIT仅指所读仓库许可；上游各仓库许可需分别核对。']),
        raw_observations=[
            f'首版{UPSTREAM_V1}：原生WebGL2/GLSL距离场或高度场；不是一个Three.js后处理插件。',
            '代码确认N·L、向主光遮挡、粗糙度/Fresnel近似、AO、薄透镜采样、快门时间采样和ACES近似；不是完整PBR或全局路径追踪。',
            '山湖4096² RG32F高度/植被图约128MiB，加载时烘焙后逐帧查表；分块gl.finish用于避免单次重绘看门狗，不减少总工作量。',
            f'后续{UPSTREAM_V2}新增真实尺度/镜头/运动/灰模与检查，修正首版过大光圈；这些不能倒写为原视频已具备。',
            '后续三次测试仍自报玩具车/模型火车/面团山；正确开关不能补救造型和构图。',
            'render默认30fps而示例快门/颗粒基准24fps；CLAY参数必须场景实际实现；示例默认灰模不能仅凭URL启用。',
            'README限定单镜头；声音、分镜、多镜头剪辑、字幕不在范围；Windows/GPU/驱动要另测。'],
        provenance=dict(scope='source_and_author_report_not_independent_performance_benchmark',
            v1_commit=UPSTREAM_V1,v2_commit=UPSTREAM_V2,license='MIT',
            audit_sha256=sha(research/'materials-audit.md'),external_urls_runtime_dependencies=False))]
    for index,body in enumerate(references):validate_research(body);save(ROOT/f'research/reference-{index+1}.json',body)
    method_store=AnimationMethodStore(args.store_root/'methods.sqlite3')
    research_records=[method_store.create_research(OWNER,body,'spatial-research-'+str(i+1)+'-v1') for i,body in enumerate(references)]

    native_fact=dict(scope='local_native_host_measured_not_container_or_production_model',
        implementation_commit=IMPLEMENTATION_COMMIT,baseline_source_sha256=BASE_SOURCE,source_sha256=STUDY_SOURCE,
        evidence_sha256=sha(example/'evidence.json'),evidence_repository_path='video_workbench/examples/spatial-foundation-study/evidence.json',
        artifact_files_sha256=native['artifact_files_sha256'],frames=240,fps=24,duration_seconds=10,
        width=1280,height=720,full_video_decoded=True,repeat_reverse_reload_cases=11,
        no_policy_pixel_regression=deepcopy(native['no_policy_pixel_regression']),
        canonical_budget_case=deepcopy(native['appearance_budget_case']),
        geometry_field_coverage_and_world_unchanged=True,
        film_objects_painted=10,film_frames_composited=247,film_cache_hits=2470,
        material_queries=18945,lighting_queries=18945,atmosphere_queries=3,
        measured_native_probe_seconds=native['total_measured_native_probe_seconds'],
        authority_validation=deepcopy(native['independent_authority_validation']),
        production_model_calls=0,model_review='not_run',artistic_acceptance=False,
        continuous_human_review=False,audio_observed=False,gpu_render_verified=False,
        other_engine_render_verified=False,transfer_verified=False)
    failure_fact=dict(scope='preserved_observed_art_failures_and_audited_technical_risk',
        bold_trials_adopted=0,bold_trials=4,baseline_retained='version_35_shared_mountain_unity',
        art_failures=['大黑域令主山成剪影、云带像白绳。','灰阶岩面或分离黑瓣破坏山骨。','宽笔/竖条/峰尖空壳未超越35版。'],
        still_open=['削木纹笔簇','亮肩阶面','峰顶露纸','砂纸状近岸'],
        technical_repairs=['诊断逐像素fillRect在非整数密度产生棋盘缝；改局部ImageData。','对照拼图曾底部裁切，已修复。'],
        audited_risk='仅tools wrapper计量可被原helper/fragment直接导入绕过；风险来自代码审计，不声称生产发生超额。',
        verified_correction='宿主先注入深冻结策略；canonical helper/budget执行前扣记；3项独立原生权限测试通过。',
        reference_failure_record='video_workbench/examples/shared-mountain-bold-experiment/evidence.json',
        artistic_acceptance=False,production_incident_claimed=False)
    experience_inputs=[
        dict(kind='reference_research',source_id='spatial-foundation:BV179WZ6REYX:v1',document=references[0]),
        dict(kind='source_audit',source_id='spatial-foundation:zhenshi-kaiguan:'+UPSTREAM_V2,document=references[1]),
        dict(kind='native_validation',source_id='spatial-foundation:ae06a9e:canonical-final',document=native_fact),
        dict(kind='failure',source_id='spatial-foundation:retained-failures-20261126',document=failure_fact)]
    save(ROOT/'payloads/experience-inputs.json',experience_inputs)
    knowledge=AnimationKnowledgeStore(args.store_root/'knowledge.sqlite3')
    experiences=[knowledge.record_experience(OWNER,**value) for value in experience_inputs]
    by_kind={record['kind']:record['id'] for record in experiences}
    snapshot=knowledge.snapshot(OWNER,page_ids=[],experience_ids=[r['id'] for r in experiences])
    save(ROOT/'records/research-records.json',research_records)
    save(ROOT/'records/experiences.json',experiences)
    save(ROOT/'records/snapshot.json',snapshot)

    themes=[
      ('spatial-light-and-coverage','共享表面先于光墨，淡墨实体仍须独立覆盖',
       '先保持共同基座、可见面、深度和独立实体覆盖，再由同坐标法线/主光指导湿笔、皴擦和勾骨。长笔沿途复核表面/明暗，不能只判断起点。浅墨颜料稀薄不等于山体透明，遮挡是宿主职责。该方法不是完整自阴影或光源重建。',
       '只加噪点、全图明暗渐变或描全部网格边，不能修复错形体和错覆盖。'),
      ('spatial-anchored-texture','结构纹理绑定坐标与尺度，三频只作材质线索',
       '为运动物选择对象坐标，为共同地形选择明确共享世界坐标；固定seed/频率和三尺度含义。噪声接入笔触墨量、干湿或真实材质通道，不能把随机点当作岩层。需要改变轮廓的细节仍由形体或真实笔触表达。',
       '纹理在表面滑动、均匀竖排线与任意墨点均可能削弱体积；现有样片的削木纹仍未艺术通过。'),
      ('spatial-air-and-reserves','空气调整颜料对比，云白与实体覆盖分别处理',
       '按明确作者距离/已知深度应用有限空气；transmittance只进入颜色/颜料，不取消实体coverage。云还承担方向、厚度、留白和前后穿插，不重复施加同一雾alpha，不用屏幕Y冒充真实深度。',
       '一条水平白带、浓雾洗平主体或把远山设透明，不等于可靠景深；水墨留白不是细节缺失bug。'),
      ('spatial-camera-and-reuse','先定镜头与边界，再探索缓存世界',
       '在初始计划决定静镜头、推进、平移或局部动画，不设必须动镜头。冻结世界/种子/资源并复用对象缓存，按绝对时间求视点。逐层检查完整取景范围/最大zoom与overscan，固定正交2.5D不声称任意绕行背面。',
       '靠不断生成新山树代替镜头、只看端点不看全范围、位图无限放大或越界后补画，都可能浪费成本并破坏一致性。'),
      ('spatial-same-content-comparison','同内容对照先于迁移，计量与艺术认可分开',
       '固定同内容/几何/布局/seed/时间/机位/分辨率/资源，分别关闭一项引导并独立冻结条件；保存全图、诊断与具体弱点。技术确定性/解码/成本证据不等于连续视听或艺术认可。当前内容未达预期前不默认换内容。',
       '另一题材的分数不能替代当前山水用户认可；多变量迭代也不能全部归因于单一参数。')]
    evidence_ids=sorted(by_kind.values())
    proposals=[]
    for page_id,title,content,counterexample in themes:
        proposals.append(dict(page_id=page_id,expected_revision=0,status='provisional',change_kind='create',relations=[],
          body=dict(title=title,content=content,
            applicability=['具有独立实体覆盖、绝对时钟、明确表面/层关系的Canvas绘画场景；本次只验证既有山水。'],
            limitations=['候选未发布/未采用。','未验证完整PBR、GPU性能、其它引擎、连续视听或跨题材迁移。','当前山水艺术认可仍为false。'],
            counterexamples=[dict(description=counterexample,evidence_ids=[by_kind['failure']])]),
          evidence_ids=evidence_ids,change_note='整理外部研究与ae06a9e本地实测；保留失败与范围，候选归档，不apply或发布。'))
    proposals=validate_knowledge_proposals(proposals,snapshot)
    proposal_record=knowledge.record_proposals(OWNER,'spatial-foundation:ae06a9e:knowledge-candidate-v1',proposals,snapshot_id=snapshot['id'])
    save(ROOT/'payloads/knowledge-proposals.json',proposals)
    save(ROOT/'records/proposal-record.json',proposal_record)

    method=dict(schema='video.animation-method.v2',title='共享表面的光墨、纹理、空气与缓存镜头候选',
      problem='已有山水形体、纹理和镜头组织脱节；需要在同内容上解释通用引导如何参与绘画并避免无谓重生成。',
      guidance=files['SKILL.md'].split('---',2)[2].strip(),
      applicability='具备API3独立覆盖、共享固定正交表面、surface helper与静态缓存的Canvas场景。其它引擎仅原则可参考，未由本候选验收。',
      limitations=['本地技术通过不等于艺术通过；用户尚未认可此山水效果。','完整连续运动、声音、GPU、全PBR/真实自阴影和跨题材迁移均未验证。','运行包无外部URL依赖；来源只在独立研究记录。','本候选没有正式采用、发布或生产消费证明。'],
      criteria=[dict(id='shared-structure',description='共同形体与比例先成立，颜料淡化不改变不透明实体覆盖。',required=True),
        dict(id='surface-guided-ink',description='同一主光/结构区组织笔触，纹理绑定坐标与尺度，诊断不作为最终灰阶底稿。',required=True),
        dict(id='camera-envelope',description='相机选择在初始计划明确，完整取景范围有覆盖，有限2.5D不冒充环绕。',required=True),
        dict(id='absolute-reuse',description='同时间重建一致，复用已画对象；实际帧数与解码可核对。',required=True,requires_motion=True),
        dict(id='same-content-evidence',description='同内容同机位同时间对照，估算/实际work分开，失败和未测范围保留。',required=True),
        dict(id='user-artistic-acceptance',description='当前山水的整体笔墨和空间须由用户实际比较认可；不同题材分数不能替代。',required=True)],
      runtime_requirements=dict(engine='canvas',artwork_api_revision=3,
        scope='fixed_orthographic_authored_surface_and_native_host',required_features=runtime['required_host_features']),
      skill_package=package,research_record_ids=[r['id'] for r in research_records])
    _method(method)
    candidate=method_store.create_method(OWNER,method,'spatial-method-ae06a9e-v1')
    assert candidate['status']=='candidate'
    save(ROOT/'payloads/method-candidate.json',method)
    save(ROOT/'records/method-candidate.json',candidate)
    for record in experiences+[proposal_record]:validate_experience(record,owner=OWNER)
    validate_snapshot(snapshot,owner=OWNER)
    assert knowledge.snapshot(OWNER,page_ids=[],experience_ids=[r['id'] for r in experiences])==snapshot
    assert knowledge.record_proposals(OWNER,'spatial-foundation:ae06a9e:knowledge-candidate-v1',proposals,snapshot_id=snapshot['id'])==proposal_record
    assert method_store.create_method(OWNER,method,'spatial-method-ae06a9e-v1')==candidate
    reopened_knowledge=AnimationKnowledgeStore(knowledge.path)
    reopened_methods=AnimationMethodStore(method_store.path)
    assert reopened_knowledge.get_snapshot(OWNER,snapshot['id'])==snapshot
    assert reopened_knowledge.get_experience(OWNER,proposal_record['id'])==proposal_record
    assert reopened_methods.get_method(OWNER,candidate['id'])==candidate
    with sqlite3.connect(knowledge.path) as db:
        assert db.execute('SELECT COUNT(*) FROM knowledge_revisions').fetchone()[0]==0
    with sqlite3.connect(method_store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM animation_method_publications').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM animation_method_acceptances').fetchone()[0]==0

    provenance=dict(schema='video.spatial-knowledge-provenance.v1',implementation_commit=IMPLEMENTATION_COMMIT,
      record_scope='Local operator-curated research and observed native evidence; not a model-authored learning run.',
      facts=dict(external_research=references,local_native=native_fact,failures=failure_fact),
      inferences=['共享结构、坐标绑定纹理、有限空气与复用镜头可作为后续画法组织方法；不声称跨题材迁移已经成功。'],
      untested=['完整PBR/真实自阴影','HDR环境照明','物理DOF/快门模糊','GPU或其他引擎成片','连续视听与声音','用户山水艺术认可','跨影片参数学习/跨题材迁移'],
      no_new_generation=True,no_new_model_call=True,foreign_content_validation_used=False,
      original_media_not_copied_into_runtime_package=True,
      sqlite_scope='Independent local candidate databases only, with local-spatial-research owner.')
    save(ROOT/'research/provenance.json',provenance)
    online_plan=dict(schema='video.local-candidate-archive-plan.v1',execute_automatically=False,
      status='prepared_for_root_review_not_executed',local_owner=OWNER,
      target_owner_policy='Use the authenticated actual owner; never reuse the local snapshot or local server IDs.',
      steps=[dict(api='AnimationMethodStore.create_research',payload_files=['research/reference-1.json','research/reference-2.json'],
          result_binding='Preserve returned server record IDs/content hashes; do not reuse local UUIDs.'),
        dict(api='AnimationMethodStore.create_method',payload_file='payloads/method-candidate.json',
          result_binding='Replace research_record_ids with returned same-owner server IDs; package and guidance stay identical; keep status candidate.'),
        dict(api='AnimationKnowledgeStore.record_experience',payload_file='payloads/experience-inputs.json',
          result_binding='Record exact immutable documents under actual owner; preserve returned evidence identities.'),
        dict(api='AnimationKnowledgeStore.snapshot',payload=dict(page_ids='Existing target page IDs after an actual owner-scoped read, or [] only if all are absent',experience_ids='IDs returned by preceding step'),
          result_binding='Freeze actual target revisions before recording proposals; do not carry local snapshot SHA.'),
        dict(api='AnimationKnowledgeStore.record_proposals',payload_file='payloads/knowledge-proposals.json',
          result_binding='Rebind evidence/counterexample IDs and actual frozen snapshot_id; expected_revision=0/create only while target page is absent, otherwise use actual revision/revise.')],
      prohibited_actions=['apply_proposals','method acceptance','effect adoption','Skill publication','new generation','cross-content validation'],
      service_scope='Internal existing store workflow; not a fabricated public HTTP endpoint or a commission/model-operation import.')
    save(ROOT/'payloads/online-archive-plan.json',online_plan)
    indexed=[p for p in ROOT.rglob('*') if p.is_file() and p.name not in {'manifest.json','validation.json'} and '__pycache__' not in p.parts]
    manifest=dict(schema='video.local-knowledge-candidate-bundle.v1',status='candidate',publication_status='unpublished',
      owner=OWNER,storage_scope='isolated_local_only',skill_package_sha256=package['sha256'],
      method_candidate_id=candidate['id'],method_content_sha256=candidate['content_sha256'],
      snapshot_id=snapshot['id'],snapshot_sha256=snapshot['sha256'],proposal_id=proposal_record['id'],proposal_sha256=proposal_record['sha256'],
      provisional_pages=len(proposals),experience_count=len(experiences),research_record_count=len(research_records),
      effect_adopted=False,artistic_acceptance=False,wikiskill_published=False,pages_applied=0,
      online_archive_executed=False,production_consumption_verified=False,
      files_sha256={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in sorted(indexed)})
    manifest['sha256']=digest(manifest);save(ROOT/'manifest.json',manifest)
    receipt=dict(schema='video.local-knowledge-candidate-validation.v1',passed=True,
      checks=['native source/artifact identities match existing ae06a9e evidence','existing skill package build/validate',
       'existing research and method validators','existing store candidate creation','experience/snapshot/proposal validation',
       'reopen/idempotent replay','zero Wiki page revisions','zero method publications/acceptances','URL-free self-contained runtime files'],
      manifest_sha256=manifest['sha256'],skill_package_sha256=package['sha256'],
      method_candidate_id=candidate['id'],proposal_id=proposal_record['id'],
      store_paths=dict(methods=str(args.store_root/'methods.sqlite3'),knowledge=str(args.store_root/'knowledge.sqlite3')),
      no_network=True,new_renders=0,new_model_calls=0,pages_applied=0,published_versions=0,effects_adopted=0,
      local_validation_not_server_import=True,artistic_acceptance=False)
    save(ROOT/'validation.json',receipt)
    print(json.dumps(dict(path=str(ROOT),package_sha256=package['sha256'],manifest_sha256=manifest['sha256'],
      proposal_id=proposal_record['id'],method_candidate_id=candidate['id'],published=False,applied=False),ensure_ascii=False))

if __name__=='__main__':main()
