"""Regenerate the deliberately synthetic demo rasters, never real project output."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

folder = Path(__file__).resolve().parent
font_path = Path('C:/Windows/Fonts/msyh.ttc')
def font(size):
    return ImageFont.truetype(str(font_path), size)

for number in (1, 2):
    image = Image.new('RGB', (1280, 720), '#f7f8f3')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 22, 720), fill='#176a58')
    draw.text((70, 48), 'PPT WORKBENCH  /  SYNTHETIC PREVIEW', font=font(18), fill='#427468')
    draw.text((70, 106), '从现状到下一步' if number == 1 else '让每一步都可核验', font=font(52), fill='#173d35')
    draw.text((72, 184), '合成演示页面 · 用于检查界面布局与交互', font=font(24), fill='#5b7169')
    if number == 1:
        for i, (title, description, amount) in enumerate([('明确目标', '整理资料与表达对象', 240), ('逐步推进', '确认方案与制作方向', 320), ('检查交付', '预览、编辑与版本记录', 400)]):
            x = 70 + i * 388
            draw.rounded_rectangle((x, 270, x + 360, 575), radius=18, fill='#e7efe7')
            draw.text((x + 26, 298), f'0{i + 1}', font=font(26), fill='#277461')
            draw.text((x + 26, 352), title, font=font(34), fill='#183f35')
            draw.text((x + 26, 408), description, font=font(23), fill='#5b7169')
            draw.rounded_rectangle((x + 26, 498, x + 26 + amount // 2, 514), radius=7, fill='#4b9a82')
    else:
        for i, (title, description) in enumerate([('查看预览', '检查文字、图表与版式'), ('记录问题', '绑定页码与当前版本'), ('桌面验证', '打开、编辑、保存、重开')]):
            y = 280 + i * 96
            draw.rounded_rectangle((70, y, 1180, y + 78), radius=12, fill='#e7efe7')
            draw.text((100, y + 19), f'0{i + 1}  {title}', font=font(29), fill='#176a58')
            draw.text((450, y + 23), description, font=font(25), fill='#536e62')
    draw.line((70, 630, 1180, 630), fill='#ccdad0', width=2)
    draw.text((70, 651), '仅供界面演示，不代表生成结果或验收证据', font=font(18), fill='#63796e')
    draw.text((1140, 650), f'{number:02d}', font=font(24), fill='#176a58')
    image.save(folder / f'demo-slide-{number}.png', optimize=True)
