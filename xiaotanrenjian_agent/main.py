#!/usr/bin/env python3
"""笑谈人间 CLI。用法: python xiaotanrenjian_agent/main.py "想聊的话题"。"""
import os
import sys

from dotenv import load_dotenv


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from xiaotanrenjian_agent.crew import create_xiaotanrenjian_crew


def main():
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:]).strip()
    else:
        text = input("想聊点什么：").strip()

    if not text:
        raise SystemExit("话题不能为空")

    crew = create_xiaotanrenjian_crew()
    result = crew.kickoff(inputs={"text": text})
    print(str(result))


if __name__ == "__main__":
    main()
