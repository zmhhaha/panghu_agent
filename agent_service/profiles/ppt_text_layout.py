"""Image-owned native-text authoring examples, never source or approval data.

The example is also consumed by the pinned exporter regression on Linux. Keeping
it in SVG makes frame ownership and independent title/body placement executable
contracts rather than an assertion that prompt wording guarantees visual quality.
"""

TEXT_LAYOUT_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" lang="zh-CN">
  <rect x="0" y="0" width="1280" height="720" fill="#F8FAFC"/>
  <text x="48" y="74" font-family="Noto Sans CJK SC" font-size="36" fill="#0F172A" data-pptx-frame="48 28 1184 64" data-pptx-text-baseline="metrics-v1">Native editable text</text>
  <rect x="48" y="140" width="568" height="208" rx="12" fill="#E2E8F0"/>
  <text x="72" y="188" font-family="Noto Sans CJK SC" font-size="28" font-weight="bold" fill="#0F172A" data-pptx-frame="72 156 520 48" data-pptx-text-baseline="metrics-v1">tools</text>
  <text x="72" y="244" font-family="Noto Sans CJK SC" font-size="22" fill="#334155" data-pptx-frame="72 214 520 42" data-pptx-text-baseline="metrics-v1">示意说明第一行</text>
  <text x="72" y="290" font-family="Noto Sans CJK SC" font-size="22" fill="#334155" data-pptx-frame="72 260 520 42" data-pptx-text-baseline="metrics-v1">独立正文框保留行距</text>
  <rect x="664" y="140" width="568" height="208" rx="12" fill="#E2E8F0"/>
  <text x="688" y="188" font-family="Noto Sans CJK SC" font-size="28" font-weight="bold" fill="#0F172A" data-pptx-frame="688 156 520 48" data-pptx-text-baseline="metrics-v1">Consumer</text>
  <text x="688" y="244" font-family="Noto Sans CJK SC" font-size="22" fill="#334155" data-pptx-frame="688 214 520 42" data-pptx-text-baseline="metrics-v1">标题与正文各占自己的空间</text>
  <rect x="48" y="388" width="568" height="208" rx="12" fill="#E2E8F0"/>
  <text x="72" y="436" font-family="Noto Sans CJK SC" font-size="28" font-weight="bold" fill="#0F172A" data-pptx-frame="72 404 520 48" data-pptx-text-baseline="metrics-v1">deriveMessages</text>
  <text x="72" y="492" font-family="Noto Sans CJK SC" font-size="22" fill="#334155" data-pptx-frame="72 462 520 42" data-pptx-text-baseline="metrics-v1">标识符完整保留，不拆出末尾字母</text>
  <rect x="664" y="388" width="568" height="208" rx="12" fill="#E2E8F0"/>
  <text x="688" y="436" font-family="Noto Sans CJK SC" font-size="28" font-weight="bold" fill="#0F172A" data-pptx-frame="688 404 520 48" data-pptx-text-baseline="metrics-v1">core/session</text>
  <text x="688" y="492" font-family="Noto Sans CJK SC" font-size="22" fill="#334155" data-pptx-frame="688 462 520 42" data-pptx-text-baseline="metrics-v1">路径与中文说明分别布局</text>
  <text x="48" y="664" font-family="Noto Sans CJK SC" font-size="26" fill="#0F172A" data-pptx-frame="48 630 1184 48" data-pptx-text-baseline="metrics-v1">agent/assistant-stream</text>
</svg>'''

TEXT_LAYOUT_GUIDANCE = (
    'Native text layout is part of authoring, not a rasterization or export cleanup step. '
    'For every new or relaid title, code label and body block, put its own explicit '
    'data-pptx-frame and metrics-v1 baseline on the text element itself. The frame must '
    'fit inside the intended card with padding; parent/card bounds do not provide text width. '
    'Keep short English words, identifiers and paths whole on one line, including trailing '
    'letters. Give their native text frames spare width for the actual font and substitutions; '
    'never size a frame to a tight source-SVG ink bound. Separate code/title text from Chinese '
    'description text so an English wrap cannot push into the body. If a title needs two '
    'intentional lines, author both lines explicitly, reserve their full line heights and gap, '
    'and move the body below them. Widen/rearrange the card or simplify nonessential wording '
    'when needed; do not hide overflow, shrink text without a legibility check, or convert '
    'editable text into outlines or images. Keep the official default preserved-line text '
    'flow; do not use global --reflow-text as an overflow fix or patch DrawingML after export. '
    'The following synthetic example demonstrates separate native frames only. Its labels '
    'and descriptions are not task facts, a required deck layout, or a visual-pass claim.\n'
    '<text-layout-syntax-example>\n' + TEXT_LAYOUT_SVG + '\n</text-layout-syntax-example>\n'
    'After native export, check the actual final-PPTX render as well as the source preview '
    'for word fragments, title/body collisions and text crossing card boundaries. Correct '
    'the authored source and rerun required checks when either output is wrong. A source '
    'preview alone cannot attest the downloaded native PPTX.\n'
)
