#!/usr/bin/env python3
"""周公解梦 CLI。用法: python zhougongjiemeng_agent/main.py "你的梦境"。"""
import os
import sys

from dotenv import load_dotenv


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from zhougongjiemeng_agent.crew import create_zhougongjiemeng_crew


def main():
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:]).strip()
    else:
        text = input("请讲讲你的梦：").strip()

    if not text:
        raise SystemExit("梦境内容不能为空")

    crew = create_zhougongjiemeng_crew()
    result = crew.kickoff(inputs={"text": text})
    print(str(result))


if __name__ == "__main__":
    main()
