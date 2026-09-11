# 本地应用 + 服务器开发中间件

## 运行边界

本地保留 Web/API、单并发 Celery Worker、Scheduler、DSH、MCP 和妙笔协作服务。七个中间件（PostgreSQL、Redis、RabbitMQ、MinIO、OpenSearch、Qdrant、FalkorDB）运行在 `10.5.113.232` 的独立开发项目中。Worker仍属于本地后端，文档解析/向量模型使用时会有额外内存开销；本次没有把最新后端代码部署到服务器。

Worker限制单并发，子进程超过512MiB后在任务完成时回收，不会中途杀掉解析。BGE等本地模型仍会在查询/加工期间占用内存；不能把“迁走中间件”宣传为本机不再加载任何模型。Docker Desktop显示的约8GiB是虚拟机上限，不等于当前容器实际占用，本次不修改影响其他项目的全局虚拟机设置。

服务器目录：`/home/tianqi/dev-middleware`；Compose项目：`chuanshen-local-dev`。

线上应用仍为 `http://10.5.113.232:9002/`，其项目、库、对象目录、索引和图谱完全不与本地开发共用。当前索引/图谱名称包含空间ID，但没有环境前缀，且开发数据保留原ID；因此本次采用同服务器不同实例/存储目录的隔离，不能只改PostgreSQL数据库名后直连线上搜索集群。

| 中间件 | 开发隔离 | 服务器监听 |
|---|---|---|
| PostgreSQL | 独立实例、`semantica_dev`数据库及账号 | 127.0.0.1:25432 |
| Redis | 独立实例，DB0/DB1 | 127.0.0.1:26379 |
| RabbitMQ | 独立实例、`semantica_dev`账号和vhost | 127.0.0.1:25672 |
| MinIO | 独立实例/磁盘目录，保留knowledge桶内原对象 | 127.0.0.1:29000 |
| OpenSearch | 独立实例/数据目录，保留原索引 | 127.0.0.1:29200 |
| Qdrant | 独立实例/数据目录，保留原集合 | 127.0.0.1:26333 |
| FalkorDB | 独立实例/数据目录，保留原图谱 | 127.0.0.1:26380 |

所有端口仅监听服务器回环地址，不发布到公网/局域网。本地小型`dev-tunnel`容器建立SSH加密隧道，只在项目Docker网络内监听。本地Web入口仍为 `http://localhost:8080/`。

## 日常操作

已经配置好当前机器后，在项目目录执行：

```bash
bash scripts/development/remote_dev.sh start
bash scripts/development/remote_dev.sh status
bash scripts/development/remote_dev.sh stop
```

本机 `compose.override.yaml` 是指向 `compose.remote-dev.yaml` 的本地软链接。因此普通 `docker compose up -d` 也默认使用远端中间件；七个本地中间件进入非默认profile，音视频ASR为按需profile，避免重启时自动占用内存。不要执行 `--profile local-middleware` 来混开两套连接。

应用代码仍在本机修改和构建：`docker compose build api` 后运行上述start。生产部署脚本显式指定基础与production两个文件，不读取本机override。

`.local-dev/app.env` 是应用运行配置的私有快照，包含原APP加密密钥和新的开发连接凭据。修改运行配置时应明确修改它；仅修改根`.env`不一定影响远端开发模式。模型配置仍由平台数据库管理。私有配置、SSH私钥、数据库备份均被Git和Docker构建上下文排除，不能上传GitHub。

## 首次安装和迁移

1. 审计服务器资源、现有线上实例/端口与本机任务；禁止覆盖已有开发目标目录。
2. 为当前开发机生成专用Ed25519密钥并核实服务器主机密钥。`prepare_remote_env.mjs`生成随机开发凭据，拒绝覆盖已有配置，不输出Secret。
3. 将`deploy/compose.dev-middleware.yaml`与生成的remote.env安全复制到服务器目录；后者命名为`.env`，权限600。不要复制本地整份.env到线上项目。
4. 专用SSH账号通过`install_tunnel_user.sh`安装：只允许公钥、指定七个目的端口、本地转发；禁止命令、PTY、反向转发、Agent/X11转发。`/home/tianqi`仍然私有，仅为该账号设置穿越ACL。
5. 在无运行/预取任务且消息队列已空的状态下暂停应用写入，并在Worker退出后复查队列。PostgreSQL用`pg_dump -Fc --no-owner --no-acl`逻辑备份。Redis/FalkorDB执行SAVE，正常停止中间件，再只读归档其独立卷。Qdrant等含预分配稀疏文件的目录使用GNU `tar --sparse`，不能用普通归档逐字节扫描大量空洞。
6. 只向空的开发目录恢复MinIO、OpenSearch、Qdrant、Redis、FalkorDB数据；使用相同中间件版本。PostgreSQL在新库使用`pg_restore --exit-on-error --no-owner --no-acl`。RabbitMQ在确认队列为空后新建账号/vhost，不跨节点复制Mnesia。
7. 运行`middleware_inventory.py`，比较所有数据库表计数、对象ETag摘要、全文索引数量、向量点数量和图谱节点/边数量。通过后再启动本地应用。
8. 执行真实上传、三通道检索、浏览器和文件导出验证。安装本机override软链接，记录结果。

本次保留application-data、DSH Session日志和协作状态在本机应用卷中，原中间件卷完整保留但停止；没有删除Volume。新写入的数据只进入服务器开发库，不回写本地旧卷。

## 网络故障与回退

必须能访问内网服务器SSH。隧道每15秒探测，失联后退出，由Docker重启重连；底层恢复后SQL连接池会检查并重建失效连接。服务不可达时应显示真实错误，不能自动连接线上库，也不能无提示切回本地旧数据。

不能把保留的旧卷当作最新数据：切回纯本地前，须再次暂停写入，将服务器开发数据反向备份并验证后恢复；或明确接受回到迁移时快照。确认迁移完成之后，才可以移除本机override软链接并按基础Compose启动。不得使用`down -v`或清空线上目录实现回退。

重启服务器开发中间件使用其专用目录的`docker compose up -d --wait`。禁止在该步骤进入线上项目目录。网络连接、数据库密码不出现在用户页面；浏览器仍只访问FastAPI。

## 本次实际验收（2026-09-11）

- 迁移前后113张数据库表的记录数量一致；155个MinIO对象、13,715,931字节及对象名/ETag校验摘要一致；33个OpenSearch索引、25个Qdrant集合各自点数、90个FalkorDB图谱各自节点/边数一致。
- 两次盘点线上原平台：数据库表计数、存储对象摘要、索引、向量集合及图谱统计全部未变化。原线上14个服务保持健康，本轮没有重启线上业务服务。
- 本地七个中间件均停止，卷未删除；默认Compose仅启动七个应用/隧道服务。服务器新增七个独立开发中间件均healthy，数据库及数据目录实查均在`/home/tianqi/dev-middleware/data/`。
- 登录后全文、向量、图谱单通道分别真实召回3条既有安置点材料。保留原妙笔文稿与模型路由，默认“内网DeepSeek V4 Flash”真实最小请求成功，耗时3,827ms。
- 新建明确标记的验收空间`a09bb8e1-33ae-498e-8e55-bec085b4f769`，真实上传Markdown；解析与知识加工均succeeded，生成1个Chunk，新内容全文和向量各召回1条。这验证了本地Worker通过远端RabbitMQ/Redis、数据库、MinIO和搜索服务完成写入，不只是端口连通。
- 妙笔浏览器脚本13组通过：真实登录、章节依据与原文、三个桌面分辨率、DOCX/PDF/依据报告下载及逐引用核对、刷新恢复；Console/pageerror为0。继承原有文稿与DSH历史，不将本轮模型最小请求当作完整新Agent生成验收。
- 隧道重新启动后，本地`/health/ready`恢复200；专用账号执行命令被拒绝、转发到线上9002被拒绝。服务使用独立SSH密钥自动重连，不依赖临时root登录会话。
- 部署安全与开发隔离自动化14项通过（最终0.84秒），Shell语法和Compose配置校验通过。测试边界：这不是全平台功能/生产压测。
- 迁移前本地容器合计约2.53GiB，其中七个中间件约1.54GiB；验证后应用服务采样约1.19GiB，隧道约2MiB。负载与模型缓存不同，不能承诺恒定占用。Worker已经改为单并发和任务结束后回收高内存子进程。

复现命令：`python < scripts/development/middleware_inventory.py`须在对应API镜像环境运行；`verify_remote_application.py`为真实应用冒烟，会新增独立标记测试数据。浏览器使用`node scripts/miaobi/browser_semantic_writing.mjs`。详细盘点和备份只保留在受保护的`.local-dev/`目录，不提交客户数据或Secret。
