#!/usr/bin/env python3
"""道法自然 — 以老子思想阐述文本/思路。
用法: python main.py "你的思考或文本"
"""
import sys
import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

# 把 panghu_agent 根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# 首次运行引导
if not os.path.exists(env_path):
    print("=" * 60)
    print("  首次运行检测到没有 .env 配置文件")
    print("=" * 60)
    # 模型调用统一走集群内 llm-service：本地跑同样只配这三个变量
    base_url = input(
        "请输入 llm-service 基址 (默认 http://llm-service.llm.svc.cluster.local/v1): "
    ).strip() or "http://llm-service.llm.svc.cluster.local/v1"
    model = input("请输入模型别名 (默认 deepseek-guarded): ").strip() or "deepseek-guarded"
    token = input("请输入 LLM_SERVICE_TOKEN: ").strip()

    with open(env_path, "w", encoding="utf-8") as fp:
        fp.writelines([
            f"LLM_BASE_URL={base_url}\n",
            f"LLM_MODEL={model}\n",
            f"LLM_SERVICE_TOKEN={token}\n",
        ])
    print(f"\n配置文件已保存到 {env_path}\n")

# 加载 .env 文件
load_dotenv(env_path)

# 检查关键变量是否缺失
# 只有令牌是必需的：LLM_BASE_URL / LLM_MODEL 在 crew.py 里有默认值
missing = []
if not os.getenv("LLM_SERVICE_TOKEN"):
    missing.append("LLM_SERVICE_TOKEN")

if missing:
    print("\n以下配置项缺失，请补充:")
    for missing_var in missing:
        val = input(f"  请输入 {missing_var}: ").strip()
        with open(env_path, "a", encoding="utf-8") as fp:
            fp.write(f"{missing_var}={val}\n")
        os.environ[missing_var] = val
    load_dotenv(env_path, override=True)

from daofaziran_agent.crew import create_daofaziran_crew


def main():
    # 从命令行获取输入文本
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = "人生于世，常为外物所累，不知何所从来，亦不知何所从去。欲求自在，反而愈陷愈深。"

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  道法自然 — 以老子思想阐述文本")
    print(f"{sep}")
    print(f"📜 输入文本:\n{text}")
    print(f"{sep}")
    print(f"  Pipeline: 解经 → 悟道 → 述道")
    print(f"{sep}\n")

    # 创建并运行 Crew
    crew = create_daofaziran_crew()
    result = crew.kickoff(inputs={"text": text})

    # 保存结果到本地文件
    report_path = os.path.join(os.path.dirname(__file__), "output.md")
    report_content = str(result)
    with open(report_path, "w", encoding="utf-8") as fp:
        fp.write(report_content)

    print(f"\n{sep}")
    print(f"  ✅ 阐述完成！")
    print(f"  文章已保存到: {report_path}")
    print(f"{sep}\n")

    # 打印最终结果
    print(report_content)


if __name__ == "__main__":
    main()
