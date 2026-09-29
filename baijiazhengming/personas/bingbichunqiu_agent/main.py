#!/usr/bin/env python3
"""秉笔春秋 CLI。用法: python baijiazhengming/personas/bingbichunqiu_agent/main.py "史事或问题"。

给了 `REFERENCE_FILE=ref.md` 就把那个文件当参考素材。线上的素材由框架从 RAG 检索后
作为 `{reference}` 注入（见 tools/rag_client.py），本机没有这一步 —— 但**必须传**，
task 描述里的占位符不传就没人替换。
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


# 本文件在 baijiazhengming/personas/<slug>_agent/ 下，parents[3] 是 panghu_agent/
ROOT_DIR = str(Path(__file__).resolve().parents[3])
sys.path.insert(0, ROOT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from baijiazhengming.personas.bingbichunqiu_agent.crew import create_bingbichunqiu_crew


def main():
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:]).strip()
    else:
        text = input("请写下要查考的史事或问题：").strip()

    if not text:
        raise SystemExit("史事或问题不能为空")

    # 本机的参考素材（见文件头）。线上由框架检索注入。
    reference = ""
    ref_file = os.getenv("REFERENCE_FILE", "").strip()
    if ref_file:
        with open(ref_file, encoding="utf-8") as fp:
            reference = fp.read()

    crew = create_bingbichunqiu_crew()
    result = crew.kickoff(inputs={"text": text, "reference": reference})
    print(str(result))


if __name__ == "__main__":
    main()
