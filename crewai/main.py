#!/usr/bin/env python3
"""研究/分析助手 - 多Agent协作。
用法：python main.py "你的调研主题"
"""

import sys
import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), ".env")

# ============================================================
#  首次运行引导：自动创建 .env 并提示用户填写
# ============================================================
if not os.path.exists(env_path):
    print("=" * 60)
    print("  首次运行检测到没有 .env 配置文件")
    print("=" * 60)
    provider = input("请选择模型提供商 (openai / anthropic / deepseek / custom，默认 openai): ").strip() or "openai"

    config_lines = [f"PROVIDER={provider}" + "\n"]

    if provider == "custom":
        base_url = input("请输入 API 地址 (如 http://localhost:11434/v1): ").strip() or "http://localhost:11434/v1"
        api_key = input("请输入 API Key (如无可直接回车): ").strip()
        model = input("请输入模型名称 (如 qwen2.5:7b，默认 gpt-4o-mini): ").strip() or "gpt-4o-mini"
        config_lines.append(f"CUSTOM_API_BASE={base_url}" + "\n")
        config_lines.append(f"CUSTOM_API_KEY={api_key}" + "\n")
        config_lines.append(f"CUSTOM_MODEL={model}" + "\n")
    elif provider in ("openai", "deepseek"):
        key = input("请输入 OpenAI / DeepSeek API Key: ").strip()
        config_lines.append(f"OPENAI_API_KEY={key}" + "\n")
    elif provider == "anthropic":
        key = input("请输入 Anthropic API Key: ").strip()
        config_lines.append(f"ANTHROPIC_API_KEY={key}" + "\n")

    # 网页搜索使用免费的 DuckDuckGo，无需额外 API Key

    with open(env_path, "w", encoding="utf-8") as fp:
        fp.writelines(config_lines)
    print(f"\n配置文件已保存到 {env_path}\n")

# 加载 .env 文件
load_dotenv(env_path)

# 检查关键变量是否缺失，缺失则交互式补全
provider = os.getenv("PROVIDER", "openai").lower()

missing = []
if provider == "custom":
    if not os.getenv("CUSTOM_API_BASE"):
        missing.append("CUSTOM_API_BASE")
    if not os.getenv("CUSTOM_MODEL"):
        missing.append("CUSTOM_MODEL")
elif provider in ("openai", "deepseek"):
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
elif provider == "anthropic":
    if not os.getenv("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")

# 网页搜索使用免费的 DuckDuckGo，无需额外配置

if missing:
    missing_var = "dummy"
    print("\n以下配置项缺失，请补充:")
    for missing_var in missing:
        val = input(f"  请输入 {missing_var}: ").strip()
        with open(env_path, "a", encoding="utf-8") as fp:
            fp.write(f"{missing_var}={val}" + "\n")
        os.environ[missing_var] = val
    load_dotenv(env_path, override=True)

from crew import create_research_crew


def main():
    # 从命令行获取调研主题
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        topic = "2026年多Agent协作框架的发展现状与趋势"

    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  研究助手启动")
    print(f"  调研主题: {topic}")
    print(f"{sep}\n")

    # 创建并运行 Crew
    crew = create_research_crew()
    result = crew.kickoff(inputs={"topic": topic})

    print(f"\n{sep}")
    print(f"  调研完成！")
    print(f"  报告已保存到: report.md")
    print(f"{sep}\n")

    # 打印最终结果摘要
    print(result)


if __name__ == "__main__":
    main()
