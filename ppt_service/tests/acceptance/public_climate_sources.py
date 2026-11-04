"""Freeze NASA public inputs for a normal six-page owner-workflow acceptance deck.

Run on the server only. Produces source inputs, not a PPTX or acceptance result.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen


DATA_URL = "https://data.giss.nasa.gov/gistemp/tabledata_v4/GLB.Ts+dSST.csv"
ABOUT_URL = "https://data.giss.nasa.gov/gistemp/"
YEARS = tuple(range(2019, 2025))


def download(url: str, maximum: int) -> bytes:
    request = Request(url, headers={"User-Agent": "PPT-public-source-acceptance/1.0"})
    with urlopen(request, timeout=30) as response:
        if response.url.split("/", 3)[:3] != url.split("/", 3)[:3]:
            raise ValueError("unexpected_source_origin")
        data = response.read(maximum + 1)
    if not data or len(data) > maximum:
        raise ValueError("source_size_limit")
    return data


def prepare(output: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    output.mkdir(parents=True, exist_ok=False)
    data = download(DATA_URL, 1024 * 1024)
    about = download(ABOUT_URL, 4 * 1024 * 1024)
    about_text = about.decode("utf-8")
    if "1951" not in about_text or "1980" not in about_text:
        raise ValueError("reference_period_not_confirmed")
    lines = data.decode("utf-8-sig").splitlines()
    if not lines or lines[0] != "Land-Ocean: Global Means":
        raise ValueError("unexpected_dataset")
    selected = {}
    for row in csv.DictReader(io.StringIO("\n".join(lines[1:]))):
        if int(row["Year"]) in YEARS:
            year = int(row["Year"])
            if year in selected or row["J-D"] == "***":
                raise ValueError("incomplete_or_duplicate_annual_data")
            value = float(row["J-D"])
            if not -5 < value < 5:
                raise ValueError("unexpected_annual_anomaly")
            selected[year] = value
    if tuple(sorted(selected)) != YEARS:
        raise ValueError("year_roster_incomplete")
    (output / "NASA-GISTEMP-frozen.csv").write_bytes(data)
    (output / "NASA-GISTEMP-about.html").write_bytes(about)
    image = Image.new("RGB", (1440, 900), "#f8fafc")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    title_font = ImageFont.truetype(font_path, 38)
    font = ImageFont.truetype(font_path, 28)
    small = ImageFont.truetype(font_path, 22)
    draw.text((80, 45), "NASA GISTEMP v4: selected annual anomalies", fill="#15283c", font=title_font)
    draw.text((80, 105), "Global land-ocean; degrees C relative to 1951-1980", fill="#42566c", font=small)
    left, top, right, bottom = 150, 190, 1330, 710
    maximum = max(1.5, max(selected.values()) + 0.2)
    for index in range(4):
        value = maximum * index / 3
        y = bottom - value / maximum * (bottom - top)
        draw.line((left, y, right, y), fill="#d7e0e9", width=2)
        draw.text((55, y - 15), f"{value:.2f}", fill="#42566c", font=small)
    for index, year in enumerate(YEARS):
        x = left + 115 + index * 190
        y = bottom - selected[year] / maximum * (bottom - top)
        draw.rectangle((x - 50, y, x + 50, bottom), fill="#167b92")
        draw.text((x - 40, y - 45), f"{selected[year]:.2f}", fill="#15283c", font=font)
        draw.text((x - 40, bottom + 24), str(year), fill="#15283c", font=font)
    draw.text((80, 805), "Source: data.giss.nasa.gov/gistemp/ (frozen input snapshot)", fill="#42566c", font=small)
    draw.text((80, 845), "Selected years illustrate values; they are not a full trend or attribution analysis.", fill="#42566c", font=small)
    image_path = output / "NASA-GISTEMP-public-data.png"
    image.save(image_path)
    rows = "\n".join(f"| {year} | {selected[year]:.2f} |" for year in YEARS)
    markdown = f"""# NASA 公开温度数据：六年数值与解释边界

## 来源及口径
NASA GISS Surface Temperature Analysis (GISTEMP v4)。本资料冻结数据源的全球陆海表面年度温度距平，J-D 列为年度值，单位为摄氏度，参考期为1951—1980年。距平是相对参考期的差值，不是全球绝对温度，也不是相邻两年的增幅。

公开CSV：{DATA_URL}
官方说明：{ABOUT_URL}
CSV SHA256：{hashlib.sha256(data).hexdigest()}
本资料选择2019—2024年六个完整年度。选择时间窗只用于展示准确的年度数值，不代表完整长期趋势分析，也不提供因果归因。本文未提供置信区间、地区差异或未来预测；演示稿必须明确说明未提供，不得补造数字或引用。

## 用于原生可编辑图表的完整数据
| 年份 | 全球陆海年度温度距平（摄氏度） |
| --- | --- |
{rows}

图表必须保留全部六个值及单位，不能把正距平解释为年度增长率。需要演示原生可编辑数据图表，必须保留可编辑数据和嵌入工作簿；截图、SVG形状拼图和整页图片均不算原生数据图表。

## 配套位图
一并上传的 NASA-GISTEMP-public-data.png 是由上表真实数值绘制的示意图，并非NASA原图。需在演示稿中作为图片实际使用并明确来源与性质，同时另设原生可编辑数据图表。两者用于说明图片展示与可编辑数据图表的区别，不能把本示意图包装成官方机构出图。

## 六页建议与忠实性要求
1. 题目与问题：怎样正确阅读温度距平？说明数据来源和所选时间窗。
2. 口径与长中文解释：保留“距平是相对1951—1980年参考期的温度差，正值表示高于该参考期；它既不等于全球绝对温度，也不能直接解释为相邻年份的增幅。理解参考期、统计范围和单位，是比较不同年度数值之前必须完成的步骤。”允许合理分段，避免刻意拆碎短标签。
3. 精确六年数值：使用全部六行数据，保留两位小数和单位，不增添未给出的数据。
4. 原生可编辑图表：对上表绘制可编辑柱状图，保留源数据与嵌入工作簿，并注明所选年份不能独自证明完整长期趋势或因果关系。
5. 图片与表达：插入配套PNG，在图片外保留可编辑中文说明，解释图片和原生数据图表在修改能力上的差异。
6. 结论与信息缺口：总结口径与可支持的观察，明确本资料未提供置信区间、地区分解和未来预测；如需这些分析，应要求补充资料，而不是自行联网或编造。

每页提供来源说明与讲解备注。按6页中文科普审核稿制作，读者为初学者。保持原生文字、适度留白和清晰分组；只用已上传材料，不联网补齐，不修改任何质量门。第一次成稿之后另提交一次明确页码和基线的修改，用于验证版本不可变与编辑能力。
"""
    (output / "NASA-公开资料复杂稿验收.md").write_text(markdown, encoding="utf-8")
    manifest = {
        "schema": "ppt.public-climate-inputs.v1",
        "source_urls": [DATA_URL, ABOUT_URL],
        "years": list(YEARS),
        "annual_anomalies_c": selected,
        "reference_period": "1951-1980",
        "generated_image_is_nasa_original": False,
        "files": {
            p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in output.iterdir()
        },
        "acceptance_passed": False,
    }
    (output / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), ensure_ascii=False, indent=2))
