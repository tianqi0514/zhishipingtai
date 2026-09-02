# 本地 SenseVoice/FunASR 部署

## 默认部署

`Dockerfile.asr` 锁定 FunASR `1.4.13`、PyTorch/Torchaudio CPU `2.5.1`，Compose 服务名为 `asr-runtime`，模型为 SenseVoice。服务仅在 Docker 网络监听 `8001`，模型缓存使用 `asr-model-cache` Volume。

```bash
docker compose build asr-runtime
docker compose up -d asr-runtime
docker compose ps asr-runtime
docker compose logs -f asr-runtime
```

首次启动要下载模型权重，健康检查允许最长 10 分钟。下载后重启复用 Volume。平台的默认 ASR 模型配置使用 OpenAI 兼容地址 `http://asr-runtime:8001/v1`，测试连接会提交真实最小音频，不以健康接口代替推理。

## 数据路径

视频音轨先由 ffmpeg 在 Worker 临时目录转换为 16kHz、单声道 PCM WAV，再按策略分段；每段调用内部 ASR。平台保存清洗后的文本、绝对时间区间、语言、说话人（若运行时提供）、置信度、词级时间（若提供）和 SenseVoice 事件标签，不保存密钥。

## CPU/GPU

当前验收环境为 Intel i7/16GB、Docker 8GB，正式基线使用 CPU，避免与 OpenSearch/Docling 争抢显存。GPU 环境可通过专用镜像替换 torch 构建并把 `FUNASR_DEVICE` 改为 CUDA；必须重新执行模型连接、1/5/30/60 分钟资源和并发回归后才能上线，不能只修改环境变量即宣称 GPU 可用。

## 故障处理

- `not_configured`：策略未关联有效 ASR，媒体元数据仍保留。
- 空语音：按 `metadata_only`、`empty_transcript` 或 `fail` 处理。
- 超时/429/服务重启：任务记录标准化错误，可从处理记录重试；已成功 OCR/Vision 不因 partial 模式丢失。
- 容器重启：缓存卷不删除，平台历史转写和 ContentElement 在 PostgreSQL/MinIO 中保持。
