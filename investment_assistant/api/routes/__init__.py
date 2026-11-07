"""v3.1 页面路由（spec §12）：today、briefs、plans、ledger、chats、settings。

`api.app.create_app` 在两个兜底路由之前 include 这里的每个 router——新的 `/api/...`
router 必须登记在那里、且在兜底之前，否则会被拦成 404。"""

from investment_assistant.api.routes import (  # noqa: F401 - create_app 经包属性取各 router
    briefs,
    chats,
    ledger,
    plans,
    settings,
    today,
)
