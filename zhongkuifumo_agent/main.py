#!/usr/bin/env python3
"""钟馗伏魔 CLI。用法: python zhongkuifumo_agent/main.py "要禀告判官的事"。

给了 `REFERENCE_FILE=ref.md` 就把那个文件当参考素材。线上的素材由框架从 RAG 检索后
作为 `{reference}` 注入（见 tools/rag_client.py），本机没有这一步 —— 但**必须传**，
task 描述里的占位符不传就没人替换。
"""
import os
import sys

from dotenv import load_dotenv


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from zhongkuifumo_agent.crew import create_zhongkuifumo_crew


def main():
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:]).strip()
    else:
        text = input("禀告判官：").strip()

    if not text:
        raise SystemExit("禀告内容不能为空")

    # 本机的参考素材（见文件头）。线上由框架检索注入。
    reference = ""
    ref_file = os.getenv("REFERENCE_FILE", "").strip()
    if ref_file:
        with open(ref_file, encoding="utf-8") as fp:
            reference = fp.read()

    crew = create_zhongkuifumo_crew()
    result = crew.kickoff(inputs={"text": text, "reference": reference})
    print(str(result))


if __name__ == "__main__":
    main()
