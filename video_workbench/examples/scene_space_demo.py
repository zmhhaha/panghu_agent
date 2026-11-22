"""Generate geometry diagnostics for one world and automatically compiled shots.

Run from the repository root. This builds no painting or encoded video and
does not contact a model, production service, library or WikiSkill publisher.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from agent_service.contracts import canonical_bytes
from video_workbench.scene_space_plan import automatic_plan, default_budget, freeze_plan
from video_workbench.scene_space_preview import write_plan_preview, write_world_preview


def build_demo(world):
    """Three creative intents share one inert world; no per-shot edit endpoint."""
    identity = world['id']
    center = dict(x=700,y=400)
    return automatic_plan([deepcopy(world)],id='scene-space-demo',
        viewport=dict(width=1280,height=720),duration_frames=4320,
        budget=default_budget(),intents=[
            dict(world_id=identity,weight=7,view='hold',focus=center,zoom=1),
            dict(world_id=identity,weight=5,view='push',focus=center,
                zoom=1,end_zoom=1.12,max_zoom=1.12,transition='continuous'),
            dict(world_id=identity,weight=6,view='hold',focus=center,
                zoom=1.12,transition='continuous',local_animation=[
                    dict(id='leaf-opacity',target_id='leaf-group',property='opacity',
                        keyframes=[dict(at=0,value=1),dict(at=.5,value=.75),dict(at=1,value=1)])]),
        ])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world',type=Path,default=Path(__file__).resolve().parents[2]/
        'tests/fixtures/scene_space/world.json')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    world=json.loads(args.world.read_text(encoding='utf-8'))
    plan=build_demo(world)
    args.output.mkdir(parents=True,exist_ok=False)
    world_report=write_world_preview(world,args.output/'world',show_grid=True)
    plan_report=write_plan_preview(plan,args.output/'shots')
    (args.output/'plan.json').write_bytes(canonical_bytes(plan)+b'\n')
    invalid=deepcopy(world)
    invalid['objects'][0]['parent_id']=invalid['objects'][0]['id']
    invalid_report=write_world_preview(invalid,args.output/'invalid-example')
    frozen=freeze_plan(plan)
    print(json.dumps(dict(status=plan_report['status'],
        output=str(args.output.resolve()),worlds=len(plan['worlds']),
        shots=len(plan['shots']),execution_intervals=len(frozen['intervals']),
        duration_frames=plan['duration_frames'],sampled_layout_frames=plan_report['frames'],
        world_sha256=world_report['world_sha256'],plan_sha256=frozen['plan_sha256'],
        negative_example=invalid_report['status'],
        artifact_scope='layout_diagnostics_only_no_video',
        model_calls=0,recipes_executed=0),ensure_ascii=True))


if __name__=='__main__':
    main()
