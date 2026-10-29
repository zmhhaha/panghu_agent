"""Trusted time and installed-entrypoint context, never delivery authority."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path

SKILL_ROOT = Path('/opt/ppt-master/skills/ppt-master')


def parse_execution_deadline(value: object) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise ValueError('Invalid execution deadline')
    try:
        deadline = datetime.fromisoformat(value)
        if deadline.utcoffset() is None:
            raise ValueError('Naive execution deadline')
        return deadline.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Invalid execution deadline') from None


@dataclass(frozen=True)
class ExecutionBudget:
    phase: str
    relative_limit: int
    cutoff_monotonic: float
    cutoff_epoch: float

    @classmethod
    def create(cls, task: dict, limit: int, started: float, epoch: float):
        cutoff = epoch + limit
        if 'execution_deadline' in task:
            cutoff = min(cutoff, parse_execution_deadline(task['execution_deadline']).timestamp())
        return cls(task['phase'], limit, started + (cutoff - epoch), cutoff)

    def remaining(self, monotonic: float) -> float:
        return max(0.0, self.cutoff_monotonic - monotonic)

    def instructions(self, monotonic: float) -> str:
        remaining = math.floor(self.remaining(monotonic))
        if remaining < 1:
            raise ValueError('Execution budget expired before admission')
        reserve = min(300 if self.phase in ('generate', 'revise') else 90,
                      max(1, remaining // 3))
        if self.phase in ('generate', 'revise'):
            closeout_steps = '检查、finalize、原生导出、postflight和真实manifest交接'
        elif self.phase == 'prepare':
            closeout_steps = '本阶段规范与锁校验、恢复依赖清单和prepared或needs_input交接；不生成页面或导出'
        else:
            closeout_steps = '本阶段规划合同核对和awaiting_confirmation或needs_input交接；不越过当前确认门制作后阶段产物'
        absolute = datetime.fromtimestamp(self.cutoff_epoch, timezone.utc).isoformat()
        closeout = datetime.fromtimestamp(self.cutoff_epoch - reserve, timezone.utc).isoformat()
        return (f'\n可信执行时间预算：当前阶段 {self.phase}；原相对上限 {self.relative_limit} 秒；'
                f'有效绝对截止时间 {absolute}（UTC）。启动和恢复已消耗的时间不会补回，'
                f'进入模型时最多剩余 {remaining} 秒；轮询、恢复会话或历史说明均不得重置或延长截止时间。\n'
                f'至少预留最后 {reserve} 秒完成{closeout_steps}。'
                f'在 {closeout}（UTC）前结束可选润色并进入本阶段收尾。主要工作批次结束后核对系统时间，'
                '不要到硬停止时才开始交接。时限不是跳过任何确认门或质量门的理由；'
                '若剩余时间不足以真实完成必需步骤，及时按现有schema写needs_input并列出实际已有产物和未完成步骤，'
                '不得伪造PPTX、报告、candidate或已交付状态。\n')


def installed_workflow_instructions(root: Path = SKILL_ROOT) -> str:
    """Only bounded names in the immutable image, never restored project files."""
    if root.is_symlink() or not root.is_dir() or not (root / 'SKILL.md').is_file():
        raise RuntimeError('Installed PPT workflow unavailable')
    entries = ['SKILL.md']
    for directory, pattern in (('scripts', '*.py'), ('workflows', '*.md'),
                               ('workflows/stages', '*.md'), ('references', '*.md')):
        folder = root / directory
        if folder.is_symlink() or not folder.is_dir():
            continue
        for path in sorted(folder.glob(pattern)):
            if path.is_symlink() or not path.is_file():
                continue
            name = path.relative_to(root).as_posix()
            if len(name) > 180 or any(ord(c) < 32 for c in name):
                raise RuntimeError('Installed PPT entrypoint name rejected')
            entries.append(name)
            if len(entries) > 256:
                raise RuntimeError('Installed PPT entrypoint limit exceeded')
    return ('\n当前固定镜像的官方入口目录（只列实际存在的文件，根目录 '
            + str(root) + '）：\n' + '\n'.join(entries)
            + '\n恢复会话中的旧路径不能覆盖当前镜像。工具或文档路径缺失时，先对照本目录和当前所属工作流，'
            '不要猜测或连续尝试近似脚本名，也不要自行安装替代工具。目录不是额外工具授权；'
            '已经被拒绝、超时或待授权的操作仍按原停止交接规则处理。\n')
