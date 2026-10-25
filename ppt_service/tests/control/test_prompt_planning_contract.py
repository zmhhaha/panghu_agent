"""The model's documented planning shapes must cross the real service boundary."""
import copy
import json

import pytest

from ppt_service.control.planning import project_planning
from ppt_service.worker.prompts import stage_instructions
from ppt_service.worker.preview import PYTHON, VISUAL_REVIEW


@pytest.mark.parametrize('phase', ['plan_initial', 'plan_options'])
def test_prompt_json_example_projects_without_inventing_confirmation(phase):
    instruction = stage_instructions(phase)
    example = instruction.split('```json\n', 1)[1].split('\n```', 1)[0]
    manifest = {'phase': phase, 'status': 'awaiting_confirmation',
                'input_snapshot_sha256': 'a' * 64, 'plan': json.loads(example)}
    before = copy.deepcopy(manifest)
    dto = project_planning(manifest, source_manifest_sha256='b' * 64,
                           expected_page_count=3)
    assert manifest == before
    assert dto['design']['source_projection']['confirmation_authority'] == 'website_database_only'
    assert 'confirmed_at' not in dto['design']
    if phase == 'plan_initial':
        assert dto['outline']['communication']['confirmation_received'] is False
        assert dto['outline']['communication']['template_choice']['value'] == 'free_design'
    else:
        assert len(dto['design']['directions']) == 3
        assert len(dto['outline']['pages']) == 3
        assert all(d['page_plan'] == dto['outline'] for d in dto['design']['directions'])


def test_stage2_example_is_a_shape_and_can_carry_actual_eight_page_task():
    instruction = stage_instructions('plan_options')
    plan = json.loads(instruction.split('```json\n', 1)[1].split('\n```', 1)[0])
    row = plan['page_plan']['pages'][0]
    plan['page_plan']['pages'] = [dict(row, ordinal=i, title=f'Actual page {i}') for i in range(1, 9)]
    dto = project_planning({'phase': 'plan_options', 'status': 'awaiting_confirmation', 'plan': plan},
                           expected_page_count=8)
    assert len(dto['outline']['pages']) == 8
    assert '全部N页' in instruction


@pytest.mark.parametrize('phase', ['plan_initial', 'plan_options', 'prepare', 'generate', 'revise'])
def test_every_phase_uses_fixed_official_python_without_bypassing_approval(phase):
    instruction = stage_instructions(phase)
    assert f'固定解释器 {PYTHON}' in instruction
    assert f'{PYTHON} {VISUAL_REVIEW} <project> --server-url <controller_url>' in instruction
    assert '禁止依赖shell中python、python3或pip的PATH解析' in instruction
    assert '不自行安装包或浏览器' in instruction
    assert '同一操作已被拒绝、超时或待授权时' in instruction
    assert '不得更换解释器或改写命令重试' in instruction
    assert '视觉门未通过不得finalize/导出' in instruction
