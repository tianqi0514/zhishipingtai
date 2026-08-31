# 文件格式支持矩阵

状态说明：`Docker 实测` 表示在交付镜像内读取真实文件；`路由/安全测试` 表示解析路由与安全规则自动化通过但未逐扩展名做本轮 Docker 实件；`协议模型实测` 表示真实 HTTP/multipart 调用了本地兼容服务，不代表外部商业账号验证。

| 格式 | 实现 | 系统/模型依赖 | 当前验证 |
|---|---|---|---|
| txt | Semantica/Text | 无 | Docker 实测 |
| md | Semantica/Text | 无 | Docker 实测 |
| rst、log、yaml/yml、toml、ini/conf | Text 薄路由 | 无 | 路由/安全测试 |
| py | Semantica Code | 无 | Docker 实测 |
| js/jsx、ts/tsx、java、c/cpp/h/hpp、cs、rb、go、rs、php、sql、sh/bash、css | Semantica Code | 无 | 路由/安全测试 |
| html | Semantica HTML | 无 | Docker 实测 |
| htm | Semantica HTML | 无 | 路由/安全测试 |
| csv | Semantica CSV | 无 | Docker 实测 |
| json | Semantica JSON | 无 | Docker 实测 |
| jsonl | JSONL 薄适配器 | 无 | Docker 实测 |
| xml | Semantica XML | 无 | Docker 实测 |
| parquet | Semantica/Arrow 列式解析 | PyArrow | Docker 实测 |
| arrow | Semantica/Arrow 列式解析 | PyArrow | Docker 实测 |
| feather | Semantica/Arrow 列式解析 | PyArrow | 路由测试 |
| 普通 PDF | Semantica PDF | 无 | Docker 实测，页码保留 |
| 扫描 PDF | Semantica Docling/OCR | Docling、RapidOCR/Tesseract 中英 | Docker 实测，OCR 文本通过 |
| docx | Semantica DOCX | 无 | Docker 实测，段落/表格 |
| doc | LibreOffice 转 DOCX → Semantica | LibreOffice headless | Docker 实测 |
| pptx | Semantica PPTX | 无 | Docker 实测，幻灯片定位 |
| ppt | LibreOffice 转 PPTX → Semantica | LibreOffice headless | Docker 实测 |
| xlsx | Semantica Excel | 无 | Docker 实测，工作表定位 |
| xls | LibreOffice 转 XLSX → Semantica | LibreOffice headless | Docker 实测 |
| xlsm | Semantica Excel | 无，不执行宏 | 路由/安全测试 |
| odt、ods、odp | ODF 薄适配器 | odfpy | 路由/解析单测 |
| rtf | RTF 薄适配器 | striprtf | 路由/解析单测 |
| png、jpg/jpeg | Semantica Image + OCR | Tesseract；Vision 可选 | Docker OCR 实测；Vision 协议模型实测 |
| webp、tif/tiff、bmp、gif | Semantica Image + OCR | Pillow/Tesseract；Vision 可选 | 路由/安全测试 |
| eml | Semantica Email + 附件递归 | 无 | Docker 实测，正文/头/附件 |
| msg | MSG 薄适配器 + 附件递归 | extract-msg | 路由/解析单测 |
| epub | EPUB 薄适配器 | ebooklib | Docker 实测 |
| wav、mp3 | Semantica Media 元数据；ASR 转写 | ffmpeg；ASR 配置 | Docker 元数据实测；ASR 协议模型实测 |
| flac、aac、m4a、ogg | 同上 | ffmpeg；ASR 配置 | 路由/依赖测试 |
| mp4 | 音轨/关键帧/元数据 | ffmpeg；ASR/Vision 可选 | Docker 元数据实测；ASR/Vision 协议模型实测 |
| mov、avi、mkv、webm | 同上 | ffmpeg；ASR/Vision 可选 | 路由/依赖测试 |
| zip | 安全 ZIP 薄适配器，递归进入现有解析链 | 无 | Docker 实测，路径穿越/数量/大小/压缩比/深度单测 |

所有上传都会交叉校验扩展名、MIME 和 Magic Bytes，并按媒介类型限制大小。统一 `ContentElement` 保留来源文件、页/幻灯片/工作表/时间区间、结构路径、解析器、OCR/ASR/Vision 信息和附件父级。

没有 ASR 配置时，音视频仅返回真实元数据并标记 `transcription_status=not_configured`；没有 Vision 配置时不生成图片描述。系统不会伪造转写或描述。

本轮 `multimodal_live.py` 实际通过 26 个文件：Arrow、CSV、DOC、DOCX、EML、EPUB、HTML、JPG、JSON、JSONL、Markdown、MP3、MP4、Parquet、普通 PDF、PNG、PPT、PPTX、Python、TXT、WAV、XLS、XLSX、XML、ZIP、扫描 PDF。
