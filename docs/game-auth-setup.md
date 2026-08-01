# 游戏试玩评价 agent — 访问凭据配置

生产环境的游戏（qianfu.panghuer.top、tewu.panghuer.top 等）都通过
`oauth2-proxy`（Casdoor OIDC）保护。浏览器要访问它们，必须持有登录后的
会话 cookie（默认名 `_oauth2_proxy`）。

本 agent 的设计是：**由环境变量注入已登录的 cookie，不在容器里演 OAuth 交互**。

## 1. 获取登录 cookie

任选一种方式，从「已登录这些游戏的浏览器」里导出 `_oauth2_proxy` cookie：

### 方式 A：Chrome 开发者工具（最简单）
1. 用 Chrome 打开 `https://qianfu.panghuer.top`，正常登录一次。
2. 按 F12 → Application（应用）→ Cookies → 选 `https://qianfu.panghuer.top`
3. 找到 `_oauth2_proxy` 那行，复制它的 **Value**。

### 方式 B：用 Playwright 脚本导出
```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # 用你已登录的 Chrome profile 启动（headful 弹窗，手动登录一次）
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=r"C:\path\to\your\chrome\profile",
        headless=False,
    )
    ctx.storage_state(path="storage_state.json")   # 导出
```

## 2. 注入到 K8s 集群

把 cookie 值作为 Secret `game-auth` 注入（namespace 是 `game-review-agent`）：

```bash
# 单个 cookie
kubectl create secret generic game-auth -n game-review-agent \
  --from-literal=GAME_AUTH_COOKIE="name=_oauth2_proxy;value=<你的cookie值>;domain=.panghuer.top;path=/;secure" \
  --kubeconfig=/etc/kubernetes/super-admin.conf

# 或者多个 cookie（不同域名），用 JSON 数组
kubectl create secret generic game-auth -n game-review-agent \
  --from-literal=GAME_AUTH_COOKIES_JSON='[{"name":"_oauth2_proxy","value":"...","domain":".panghuer.top","path":"/","secure":true}]' \
  --kubeconfig=/etc/kubernetes/super-admin.conf
```

## 3. 生效与验证

```bash
# 重启 API pod 使 Secret 生效
kubectl rollout restart deploy/api -n game-review-agent \
  --kubeconfig=/etc/kubernetes/super-admin.conf

# 查看是否已注入
kubectl exec -it deploy/api -n game-review-agent -- env | grep GAME_AUTH
```

## 4. 轮换（重要）

- cookie 有效期 **720 小时（30 天）**，到期后访问受保护页面会跳到登录页。
- agent 检测到 `/oauth2/sign_in` 重定向时，会在试玩日志里明确报
  `"该环境未配置登录凭据，无法访问受保护页面"`。
- 到期后重新执行第 1、2 步即可。建议设置 25 天左右的轮换提醒。

## 安全红线

- ❌ 不要把整个浏览器 profile 目录（如 `.qianfu-browser-test`）复制进容器
- ❌ 不要用环境变量注入 OAuth 账号密码让 agent 走交互登录
- ✅ 只注入登录后的 cookie，且经 K8s Secret 挂载，不落盘
