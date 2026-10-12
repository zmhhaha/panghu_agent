"""共享的回答语言策略。"""

LANGUAGE_POLICY = """
## 回答语言

请使用与用户主要输入相同的语言回答。

- 用户使用中文，就用中文回答；用户使用英文，就用英文回答；其他语言同理。
- 用户混合使用多种语言时，以问题和主要表达内容的语言为准。
- 用户明确要求某种回答语言时，遵循该要求。
- 专有名词、书名、代码、URL、模型名和必要的原文可以保留原语言。
- 不要因为人格设定、知识库或参考资料使用某种语言，就强制用户使用该语言。
- 除非用户明确要求，否则不要同时输出多种语言的完整重复答案。
""".strip()


def apply_language_policy(crew) -> None:
    """给 Crew 的每个任务追加统一语言策略。"""
    for task in getattr(crew, "tasks", ()):
        description = getattr(task, "description", "") or ""
        if LANGUAGE_POLICY not in description:
            task.description = f"{description.rstrip()}\n\n{LANGUAGE_POLICY}"
