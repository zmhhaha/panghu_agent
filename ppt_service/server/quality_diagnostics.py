"""Fixed, bounded explanations for a stopped independent validator rejection.

Only the reviewed code is accepted from private evidence. Text, paths, URLs,
exception messages and candidate-authored reports are never public diagnostics.
"""
from __future__ import annotations

from uuid import UUID


# stage, title, description, recovery advice, retry stage. These are explanations,
# not a claim that a retry is authorized or that any presentation was accepted.
_RULES = {
    'external_svg_resource': ('source_binding', '页面引用检查未通过',
        '页面包含校验器不允许的外部资源引用。',
        '管理员应核对引用的类型与校验规则；参考链接和加载图片需分别处理，无需据此重写整份要求。', 'validation'),
    'unsafe_svg_source': ('source_binding', '页面源文件检查未通过',
        '页面含有不支持的主动内容或文档声明。',
        '请管理员检查页面源文件并移除不支持的主动内容，保留原资料与方案。', 'generation'),
    'unsafe_svg_data_resource': ('source_binding', '内嵌资源检查未通过',
        '页面的内嵌资源格式或大小不符合要求。',
        '请管理员检查实际资源格式与限制，修复对应页面后再验证。', 'generation'),
    'candidate_integrity_failed': ('candidate_integrity', '产物结构检查未通过',
        '候选文件的结构、引用或内容清单未通过独立检查。',
        '管理员应查看保留的检查证据，修复对应文件；此结果不能说明用户要求有误。', 'validation'),
    'independent_quality_gate_failed': ('official_quality', '官方质量检查未通过',
        '官方检查程序未通过；其详细报告已供管理员核对。',
        '请管理员查看具体检查项目与实际文件后定向修复，避免无依据重新规划。', 'validation'),
    'quality_report_roster_unverified': ('official_quality', '质量报告不完整',
        '检查报告中的页面数量或记录格式与预期不一致。',
        '请管理员核对检查工具输出与页面清单，再运行相应检查。', 'validation'),
    'approved_spec_lock_changed': ('approved_specification', '批准内容核对未通过',
        '制作文件中的设计规范或执行锁与已批准内容不一致。',
        '应恢复已批准文件；确需修改规范时，先生成并确认新的批准版本。', 'generation'),
    'render_source_hash_mismatch': ('source_binding', '页面来源核对未通过',
        '页面文件与产物清单中的内容摘要不一致。',
        '请管理员核对冻结候选与文件清单，保留失败记录后修复对应文件。', 'validation'),
    'render_resource_hash_mismatch': ('source_binding', '本地资源核对未通过',
        '页面引用的本地资源与声明的文件摘要不一致。',
        '请管理员核对实际资源及清单，不要直接调整用户要求。', 'validation'),
    'render_roster_mismatch': ('source_binding', '页面清单核对未通过',
        '页面数量、顺序或文件清单与制作要求不一致。',
        '请管理员核对实际页面和清单，再检查受影响页面。', 'validation'),
    'invalid_svg_canvas': ('source_binding', '页面画布检查未通过',
        '页面画布的尺寸或格式不符合渲染要求。',
        '修复对应页面的画布定义后重新验证。', 'generation'),
    'independent_command_timeout': ('independent_render', '独立检查工具超时',
        '独立检查程序未在配置时限内完成。',
        '请管理员先检查渲染环境、资源与检查日志；无需因此改写用户内容。', 'validation'),
    'independent_render_failed': ('independent_render', '独立渲染未完成',
        '独立渲染程序未生成完整的页面结果。',
        '请管理员查看渲染退出结果与保留的检查报告，修复环境或对应页面。', 'validation'),
    'quality_gate_failed': ('independent_validation', '独立检查未通过',
        '本次独立检查未能完成有效验收，原始证据已保留供管理员核对。',
        '先核对具体检查证据，再决定修复文件或检查环境，避免无依据修改要求。', 'validation'),
}


def known_code(value):
    return type(value) is str and value in _RULES


def public_diagnostic(value, *, job_id):
    """Reconstruct safe text from a stored code; ignore every unknown field."""
    if (type(value) is not dict or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1):
        return None
    code = value.get('error_code')
    if not known_code(code) or value.get('publication_authorized') is not False:
        return None
    try:
        if str(UUID(job_id)) != job_id:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    stage, title, message, suggestion, retry = _RULES[code]
    return {'schema_version': 1, 'error_code': code, 'stage': stage,
        'title': title, 'message': message, 'suggestion': suggestion,
        'retry_stage': retry, 'publication_authorized': False,
        'diagnostic_download_url': f'/api/v1/jobs/{job_id}/diagnostics'}
