"""部署入口：百家争鸣共享 UI。"""

import os

from baijiazhengming.ui import demo

__all__ = ["demo"]


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=8).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
