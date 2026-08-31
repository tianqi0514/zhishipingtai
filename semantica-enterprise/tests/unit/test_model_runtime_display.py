from unittest.mock import MagicMock, patch

from apps.api.utils import serialize_row
from packages.platform.models import ModelConfig
from packages.semantica_adapter.models import test_model_connection as check_model_connection


def test_fastembed_is_reported_as_local_without_api_key() -> None:
    model = ModelConfig(
        tenant_id="tenant-1",
        name="BGE 中文向量",
        model_kind="embedding",
        provider="fastembed",
        model_name="BAAI/bge-small-zh-v1.5",
        api_key_encrypted=None,
    )

    assert serialize_row(model)["api_key_status"] == "本地运行 · 无需 API Key"


def test_local_embedding_connection_runs_a_real_embedding_call() -> None:
    embedder = MagicMock()
    embedder.method = "fastembed"
    embedder.embed_query.return_value = [0.1, 0.2, 0.3]
    with patch("packages.semantica_adapter.embedding.SemanticEmbedder", return_value=embedder):
        result = check_model_connection(
            model_kind="embedding",
            provider="fastembed",
            model_name="BAAI/bge-small-zh-v1.5",
            api_key=None,
            base_url=None,
            config={"method": "fastembed"},
        )

    embedder.embed_query.assert_called_once_with("知识平台连通性测试")
    assert result == {
        "status": "ok",
        "dimension": 3,
        "local": True,
        "method": "fastembed",
        "message": "本地模型实测成功（3 维，无需 API Key）",
    }
