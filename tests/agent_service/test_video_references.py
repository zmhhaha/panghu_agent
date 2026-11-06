"""Literal provenance survives collection without exposing credential links."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.video_references import (preserve_plan_references,
    preserve_project_references, preserve_visual_references, reference_urls)


def scene(description, **extra):
    return dict(visual_description=description, narration='旁白保持原样，不朗读来源。', **extra)


def test_url_parser_preserves_balanced_paths_and_removes_surrounding_markdown():
    wiki = 'https://en.wikipedia.org/wiki/Agent_(software)'
    nested = 'https://example.org/a(b(c)d)?lang=zh#section-2'
    ipv6 = 'https://[2001:db8::1]/docs'
    assert reference_urls(f'[参考]({wiki})。再看（{nested}）和 {ipv6}。') == [wiki, nested, ipv6]
    assert reference_urls(wiki, wiki, f'见 {wiki}，仍是同一来源。') == [wiki]
    assert reference_urls('“https://example.com/repo”和“https://example.com/repo/docs.md”') == [
        'https://example.com/repo', 'https://example.com/repo/docs.md']


def test_missing_reference_uses_exact_url_identity_instead_of_prefix_substring():
    result = preserve_visual_references('原已有 https://example.com/docs2', 'https://example.com/docs')
    assert reference_urls(result) == ['https://example.com/docs2', 'https://example.com/docs']
    assert result.endswith('https://example.com/docs')
    assert preserve_visual_references(result, 'https://example.com/docs') == result


@pytest.mark.parametrize('url', [
    'https://user:SECRET@example.com/docs', 'https://SECRET@example.com/docs',
    'https://example.com/file?token=SECRET', 'https://example.com/auth?code=SECRET&state=public',
    'https://example.com/api?api_key=SECRET', 'https://example.com/a?X-Amz-Signature=SECRET',
    'https://example.com/a?X-Goog-Credential=SECRET', 'https://example.com/a?sig=SECRET',
    'https://example.com/#access_token=SECRET', 'https://example.com/#/callback?code=SECRET',
    'https://example.com/?%61ccess%5Ftoken=SECRET', 'https://example.com/#Bearer%20SECRET',
    'https://example.com/?q=public;password=SECRET',
])
def test_sensitive_urls_are_explicitly_rejected_without_echoing_secrets(url):
    with pytest.raises(ServiceError) as failure:
        reference_urls('仅内部登录用 ' + url)
    assert failure.value.code == 'video_reference_sensitive_url'
    assert failure.value.status == 422
    assert 'SECRET' not in str(failure.value) and url not in str(failure.value)


@pytest.mark.parametrize('url', ['https://', 'https://example.com:broken/a', 'https://[broken]/a'])
def test_invalid_url_is_not_silently_dropped(url):
    with pytest.raises(ServiceError, match='video_reference_invalid'):
        reference_urls(url)


def test_long_reference_is_not_silently_dropped_or_truncated():
    valid = 'https://example.com/' + 'a' * (512 - len('https://example.com/'))
    assert reference_urls(valid) == [valid]
    with pytest.raises(ServiceError, match='video_reference_too_long'):
        reference_urls(valid + 'a')


def test_public_reference_queries_and_anchors_are_kept_exactly():
    url = 'https://example.com/docs?version=8bd0733&lang=zh-CN#step-and-turn'
    assert reference_urls(url) == [url]
    assert preserve_visual_references('来源：' + url, url) == '来源：' + url


def test_project_references_keep_existing_distribution_and_fill_missing_from_last_scene():
    urls = [f'https://example.com/{i}' for i in range(4)]
    original = [scene('第一镜已分配 ' + urls[0], scene_id='one'),
                scene('第二镜已分配 ' + urls[1], scene_id='two'),
                scene('最后一镜', scene_id='three')]
    frozen = deepcopy(original)
    result = preserve_project_references(original, '资料：' + ' '.join(urls))
    assert original == frozen, 'The caller owns the original result; failures and success must not mutate it'
    assert result[0] == frozen[0] and result[1] == frozen[1]
    assert reference_urls(result[2]['visual_description']) == urls[2:]
    assert [row['narration'] for row in result] == [row['narration'] for row in original]
    assert [row['scene_id'] for row in result] == ['one', 'two', 'three']
    assert preserve_project_references(result, ' '.join(urls)) == result


def test_project_three_missing_urls_are_distributed_across_two_scenes():
    urls = [f'https://example.com/{i}' for i in range(3)]
    result = preserve_project_references([scene('第一镜'), scene('第二镜')], ' '.join(urls))
    assert reference_urls(result[1]['visual_description']) == urls[:2]
    assert reference_urls(result[0]['visual_description']) == urls[2:]
    assert all(len(reference_urls(row['visual_description'])) <= 2 for row in result)


def test_project_skips_a_text_full_last_scene_without_changing_it():
    original = [scene('前一镜'), scene('满' * 200)]
    result = preserve_project_references(original, 'https://example.com/docs', maximum=200)
    assert result[1] == original[1]
    assert reference_urls(result[0]['visual_description']) == ['https://example.com/docs']


@pytest.mark.parametrize('scenes,text,maximum', [
    ([], 'https://example.com/docs', 8000),
    ([scene('第一镜')], 'https://a.example/1 https://b.example/2 https://c.example/3', 8000),
    ([scene('满' * 200)], 'https://example.com/docs', 200),
    ([scene('https://a.example/1 https://b.example/2 https://c.example/3')], '', 8000),
])
def test_project_capacity_failure_is_atomic_and_explicit(scenes, text, maximum):
    frozen = deepcopy(scenes)
    with pytest.raises(ServiceError, match='video_references_require_split'):
        preserve_project_references(scenes, text, maximum=maximum)
    assert scenes == frozen


def test_single_scene_already_over_reference_capacity_is_not_silently_accepted():
    with pytest.raises(ServiceError, match='video_references_require_split'):
        preserve_visual_references('https://a.example/1 https://b.example/2 https://c.example/3')


def test_plan_reference_enrichment_preserves_literal_input_and_rejects_invented_source():
    url = 'https://example.com/docs?v=8bd0733#architecture'
    original = dict(title='原图解', sources=[dict(url=url, label='固定版本的架构说明')])
    result = preserve_plan_references(original, dict(visual_description='来源 ' + url))
    assert result == original and result is not original
    with pytest.raises(ServiceError, match='motion_reference_not_in_input'):
        preserve_plan_references(original, dict(visual_description='没有提供任何来源'))


def test_sensitive_link_is_rejected_at_every_preservation_boundary():
    private = 'https://example.com/file?token=SECRET'
    for action in (lambda: preserve_visual_references('画面', private),
                   lambda: preserve_project_references([scene('画面')], private),
                   lambda: preserve_plan_references({}, dict(visual_description=private))):
        with pytest.raises(ServiceError, match='video_reference_sensitive_url'):
            action()
def test_source_dates_and_versions_survive_project_distribution():
    from agent_service.profiles.video_references import preserve_project_references, preserve_plan_references
    reference = 'https://example.com/architecture.md'
    result = preserve_project_references([{'visual_description': '画面', 'narration': '不变。'}],
        reference + '\n固定版本8bd0733；核对日期2026-11-04')
    description = result[0]['visual_description']
    assert reference in description
    assert '固定版本8bd0733' in description
    assert '核对日期2026-11-04' in description
    plan = preserve_plan_references({}, {'visual_description': description, 'instruction': ''})
    assert '核对日期2026-11-04' in plan['sources'][0]['label']
    assert result[0]['narration'] == '不变。'

