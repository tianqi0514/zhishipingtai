# 传神智库 CLI 接入手册

## 安装与登录

CLI 已安装在平台 API 镜像中，也可以在安装项目 Python 包后直接使用 chuanshen 命令。

```bash
chuanshen login --api-url http://服务器地址:8080/api/v1
chuanshen --help
```

登录命令会交互式读取账号和密码，并把短期令牌保存到当前用户可读的本地配置文件。也可以用 CHUANSHEN_API_URL 和 CHUANSHEN_TOKEN 环境变量临时注入连接信息。

## 检索与对话

```bash
chuanshen search "产品的主要定位是什么？" --space SPACE_ID --top-k 5
chuanshen chat "它支持哪些数据源？" --space SPACE_ID
chuanshen chat "第二项的依据在哪一页？" --conversation CONVERSATION_ID
chuanshen fragment CHUNK_ID
```

## 数据源与任务

```bash
chuanshen sync-source SOURCE_ID
chuanshen job JOB_ID
```

## 知识分析

```bash
chuanshen reason RULE_SET_ID --space SPACE_ID
chuanshen reason RULE_SET_ID --space SPACE_ID --publish
chuanshen sparql 'SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20' --space SPACE_ID
```

## 结构化经营数据

```bash
chuanshen structured-query '2026 年已完成订单销售总额是多少？' \
  --mapping-version MAPPING_VERSION_ID --max-rows 100
```

该命令不接收原始 SQL。平台会使用已激活本体映射生成并校验严格 Plan/IR，再确定性编译参数化只读查询；返回值包含真实结果、QueryRun 和结构化数据引用。

CLI 在超时、权限错误、非 JSON 响应或服务不可用时返回非零退出码，可直接用于脚本和流水线判断。不要在命令行参数、Shell 历史或仓库文件中明文保存令牌。
