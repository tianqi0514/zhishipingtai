from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_brand_and_password_toggle_are_present() -> None:
    html = (ROOT / "apps/api/static/index.html").read_text(encoding="utf-8")

    assert "<title>传神智库</title>" in html
    assert "<h1>传神智库</h1>" in html
    assert 'id="login-password"' in html
    assert 'id="password-toggle"' in html
    assert 'aria-label="显示密码"' in html
    assert 'class="brand-symbol"' in html


def test_login_401_keeps_the_server_error_message() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")

    assert "path!=='/auth/login'" in javascript
    assert "typeof data?.detail==='string'?data.detail" in javascript
    assert "setPasswordVisible" in javascript


def test_browser_regression_contracts_are_present() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")

    conversation_menu = javascript.split("async function conversationMenu", 1)[1].split(
        "async function openFragment", 1
    )[0]
    assert "prompt(" not in javascript
    assert "subject_type" in javascript
    assert "subject_id" in javascript
    assert "grant-delete" in javascript
    for model_field in ("timeout", "retry", "concurrency", "temperature", "max_tokens"):
        assert f"field('{model_field}'" in javascript
    assert "conversation-clear" in conversation_menu
    assert "conversation-delete" in conversation_menu
    assert "state.documentsMode==='jobs'" in javascript
    assert "event_type==='tool_finished'" in javascript
    assert "已停止生成" in javascript
    assert "score('全文',x.keyword_score" in javascript
    assert "score('向量',x.vector_score" in javascript
    assert "score('图谱',x.graph_score" in javascript
    assert ".shell>aside{background:#fff" in stylesheet


def test_knowledge_chat_exposes_observable_dsh_work_without_private_reasoning() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")

    for event_type in (
        "turn_started",
        "step_started",
        "retrieval_started",
        "tool_started",
        "tool_finished",
        "retrieval_ranked",
        "turn_completed",
        "turn_failed",
        "turn_cancelled",
    ):
        assert event_type in javascript
    assert "已思考" in javascript
    assert "不展示模型私有思维链" in javascript
    assert "requestAnimationFrame(flushChatDelta)" in javascript
    assert "返回最新回答" in javascript
    assert "chatPanelTab:'process'" in javascript
    assert "[['process','执行过程'],['trace','检索轨迹'],['evidence','召回依据']]" in javascript
    assert "<details><summary>检索范围与高级选项" not in javascript
    assert "grid-template-columns:205px minmax(360px,1fr) 380px" in stylesheet
    assert ".agent-step{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto" in stylesheet
    assert ".chat-inspector-body{min-height:0;overflow:auto" in stylesheet


def test_bounded_views_keep_their_own_scroll_containers() -> None:
    javascript = (ROOT / "apps/api/static/app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "apps/api/static/style.css").read_text(encoding="utf-8")

    assert "function page(title,html,viewClass='')" in javascript
    assert "content.className=viewClass" in javascript
    assert "`,'chat-view')" in javascript
    assert "#content.chat-view{overflow:hidden}" in stylesheet
    assert ".chat-layout{height:100%;min-height:0" in stylesheet
    assert ".chat-workspace{height:100%;min-width:0;min-height:0" in stylesheet
    assert "#chat-main{position:relative;min-width:0;min-height:0" in stylesheet
    assert ".chat-messages{min-width:0;min-height:0;overflow-y:auto" in stylesheet
    assert "#modal-form{max-height:calc(100vh - 24px)" in stylesheet
    assert ".login-wrap{height:100vh;height:100dvh;min-height:0" in stylesheet
    assert "place-items:center;overflow:auto" in stylesheet
