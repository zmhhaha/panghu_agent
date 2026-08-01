"""通用游戏试玩浏览器工具集（不绑定任何具体游戏）。"""

from .browser import GameBrowser, detect_login_redirect
from .tools import make_game_tools

__all__ = ["GameBrowser", "detect_login_redirect", "make_game_tools"]
