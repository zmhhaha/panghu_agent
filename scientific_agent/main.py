#!/usr/bin/env python3
"""科学综述助手 - 多Agent协作系统综述。
用法: python main.py "你的研究主题"
"""
import sys
import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

# 把 panghu_agent 根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ============================================================
#  首次运行引导：自动创建 .env 并提示用户填写
# ============================================================
if not os.path.exists(env_path):
    print("=" * 60)
    print("  首次运行检测到没有 .env 配置文件")
    print("=" * 60)
    # 模型调用统一走集群内 llm-service：本地跑同样只配这三个变量
    base_url = input(
        "请输入 llm-service 基址 (默认 http://llm-service.llm.svc.cluster.local/v1): "
    ).strip() or "http://llm-service.llm.svc.cluster.local/v1"
    model = input("请输入模型别名 (默认 deepseek-trusted；deepseek-guarded 禁 tools 会 400): ").strip() or "deepseek-trusted"
    token = input("请输入 LLM_SERVICE_TOKEN: ").strip()

    # 所有学术搜索工具均为免费 API，无需额外 Key

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

from scientific_agent.crew import create_scientific_crew


def main():
    # 从命令行获取研究主题
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        topic = "深度学习在医学影像分析中的应用进展"

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  科研综述助手启动")
    print(f"  研究主题: {topic}")
    print(f"  Pipeline: 文献检索 → 文献筛选 → 数据提取 → 综合分析 → 综述撰写")
    print(f"{sep}\n")

    # 创建并运行 Crew
    crew = create_scientific_crew()
    result = crew.kickoff(inputs={"topic": topic})

    # 保存综述报告到本地文件
    report_path = os.path.join(os.path.dirname(__file__), "review.md")
    report_content = str(result)
    with open(report_path, "w", encoding="utf-8") as fp:
        fp.write(report_content)

    print(f"\n{sep}")
    print(f"  综述撰写完成！")
    print(f"  综述报告已保存到: {report_path}")
    print(f"{sep}\n")

    # 打印最终结果
    print(report_content)


if __name__ == "__main__":
    main()
