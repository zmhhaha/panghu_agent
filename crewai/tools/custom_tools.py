from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type


class FileReadInput(BaseModel):
    """Input for reading a file."""
    file_path: str = Field(..., description="要读取的文件路径")


class FileReadTool(BaseTool):
    name: str = "file_read"
    description: str = "读取本地文件内容"
    args_schema: Type[BaseModel] = FileReadInput

    def _run(self, file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            return f"文件内容 ({file_path}):\n{content}"
        except Exception as e:
            return f"读取文件失败: {e}"


class FileWriteInput(BaseModel):
    """Input for writing a file."""
    file_path: str = Field(..., description="要写入的文件路径")
    content: str = Field(..., description="要写入的内容")


class FileWriteTool(BaseTool):
    name: str = "file_write"
    description: str = "将内容写入本地文件"
    args_schema: Type[BaseModel] = FileWriteInput

    def _run(self, file_path: str, content: str) -> str:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"文件已写入: {file_path}"
        except Exception as e:
            return f"写入文件失败: {e}"
