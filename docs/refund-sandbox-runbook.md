# V1.0 HTTP 退款沙箱操作说明

本版使用项目内的 HTTP 支付沙箱，没有接入微信、支付宝、Stripe 或真实资金。
普通退款接口仍要求客户 JWT 和申请归属；运维接口要求数据库中的 ADMIN 角色。
沙箱服务使用独立服务密钥，不接收客户 JWT。以下服务命令由你在 Cursor 中手动运行。

## 1. 数据库和配置

新增的两份 migration 是 `20260919090000_refund_audit_and_recovery` 和
`20260919100000_http_refund_sandbox`，均在 `packages/database/prisma/migrations`。
它们与之前的执行记录和 webhook migration 一起按序部署，包含审计 trigger，不能用 `db push` 替代。

先确认 Prisma 的 `DATABASE_URL` 指向开发库 `ecommerce_agents`。
Prisma 读取根 `.env`，再由 `packages/database/.env` 覆盖；Python 读取自己的配置，
请确保 `apps/agent-core/.env` 的 `DATABASE_URL` 指向同一开发库。
自动测试另用 `.env.test.local` 的 `TEST_DATABASE_URL`，两者不能混用。

在项目根目录运行：

```bash
pnpm --filter @ecommerce-agent-system/database exec prisma migrate status
pnpm --filter @ecommerce-agent-system/database exec prisma migrate deploy
```

本次实现只在隔离测试 schema 应用过 migration，未代你迁移开发库，也未清空数据。
部署前保留数据库备份；回退代码时保留审计、任务和沙箱历史表，不能删除它们“重新开始”。
旧代码不会写完整的审计来源，签名协议也不同，因此不要混用旧版回调发送端。

在 `apps/agent-core/.env` 添加：

```dotenv
REFUND_SANDBOX_BASE_URL=http://127.0.0.1:8001
REFUND_SANDBOX_API_KEY=替换为至少16字符的本地随机值
REFUND_SANDBOX_WEBHOOK_SECRET=替换为另一个本地随机值
REFUND_SANDBOX_CALLBACK_URL=http://127.0.0.1:8000/v1/refund-webhooks/sandbox
REFUND_SANDBOX_REQUEST_TIMEOUT_SECONDS=10
REFUND_RECOVERY_MAX_ATTEMPTS=5
REFUND_RECOVERY_LEASE_SECONDS=120
REFUND_RECOVERY_RETRY_SECONDS=60
AGENT_CORE_ENVIRONMENT=development
```

两个进程在同一目录启动，共享这些本地配置。租约应明显大于 HTTP 请求超时；人工重投
包含一次查询和一次提交，预留两次网络调用的时间。租约过短不会让旧结果覆盖新结果，
但会增加无效查询和人工处理。
不填 `REFUND_SANDBOX_BASE_URL` 仍使用早期内存沙箱，它不用于本节的重启/幂等验收。
本地沙箱拒绝 `production` 环境。不要将它当成可公开部署的支付网关。

## 2. 在 Cursor 启动两个进程

两个终端都先进入 `apps/agent-core`，分别执行：

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
.venv/bin/python -m uvicorn app.refund_sandbox_app:app --host 127.0.0.1 --port 8001
```

这两个命令是手动验收步骤；Codex 没有运行它们。HTTP 沙箱自身不初始化聊天或 embedding 模型，
无需 OpenAI 密钥。主应用仍遵循现有聊天/RAG 生命周期配置。

## 3. 一笔退款的手动闭环

可以用 Cursor REST Client、Postman 或两个服务的 `/docs`。
`/docs` 是学习开发工具，不要在截图或聊天中暴露密钥/Token。
通过 `POST /v1/auth/login` 分别登录客户和管理员；三角色 seed 账号见
`packages/database/README.md`。业务请求携带 `Authorization: Bearer <access_token>`。

先使用既有界面或 API 完成申请、客户确认和管理员审批：

| 身份 | 请求 | 正文/观察结果 |
| --- | --- | --- |
| 客户 | `POST /v1/orders/{order_id}/refund-applications` | `{"request_id":"manual-v1-refund-001","requested_amount":"10.00","requested_currency":"CNY"}`；保存返回的申请 `id` |
| 客户 | `POST /v1/refund-applications/{id}/confirm` | 进入待审批 |
| ADMIN | `POST /v1/refund-applications/{id}/review` | `{"decision":"APPROVED","review_note":"HTTP 沙箱验收"}` |
| 客户 | `POST /v1/refund-applications/{id}/execute` | 返回 `PROCESSING`；重复调用不生成另一笔退款 |

金额必须符合实际订单风控；示例并不绕过已存在的申请或订单归属。
执行接口不接收客户端金额，始终使用数据库中的已批准金额和 `refund:{id}` 幂等键。

接着操作端口 8001 的 HTTP 沙箱。所有沙箱请求带
`Authorization: Bearer <REFUND_SANDBOX_API_KEY>`；Swagger 中填入 authorization header 参数即可。

1. `GET /refunds/refund:{id}`：应为 `PROCESSING`。
2. `POST /refunds/refund:{id}/settle`，正文 `{"status":"SUCCEEDED"}`：得到稳定的 `event_id` 和模拟引用号。
3. `POST /events/{event_id}/deliver`：应返回 `delivery: ACKNOWLEDGED`。
4. 回到客户接口 `GET /v1/refund-applications/{id}/execution`：应为 `SUCCEEDED`，金额不变。
5. 管理员 `GET /v1/refund-operations/{id}`：查看当前执行、补偿任务和审计事件。

重复 settle 返回同一事件，重复 deliver 不再次改变资金状态。沙箱重启后记录仍在。
反向 settle 成 `FAILED` 返回 409，不覆盖终态。要验收失败链，使用另一份合法已批准申请，
settle 时明确指定 `FAILED`；网络超时本身不能作为 FAILED 的证据。

## 4. 丢失回调与补偿

另一笔退款只执行 settle、不执行 deliver。领取退款时，数据库会原子创建补偿任务，
初次延迟 60 秒。任务到期后，在 `apps/agent-core` 手动运行一批：

```bash
.venv/bin/python -m app.refund_reconciliation_cli --scheduled --limit 10
```

该命令处理到期任务后退出，不启动 Worker。输出 `checked/errors` 是本批查询统计，
`checked` 不代表退款成功；未知和处理中结果可能仍在等待，须查看执行状态及人工队列。
如需持续补偿，后续由你配置外部调度器定期调用本命令；本次未创建任何定时服务。

调度机制：短事务领取 → 提交租约 → HTTP 查询原键 → 短事务校验租约并落库。
只有当前未过期的 token 可写结果。失败采用指数退避（默认 60、120、240…秒，上限一小时），
默认五次后转人工；进程在最后一次领取后崩溃也会在租约到期后的下次调度进入人工队列。
数据库不可用时保留租约，等待数据库恢复后回收，不在内存中宣称成功。

旧的只读人工核对仍可用，适合诊断，但不替代有次数上限的计划任务：

```bash
.venv/bin/python -m app.refund_reconciliation_cli --older-than-seconds 600 --limit 10
```

## 5. 人工队列、原键重投与矛盾处理

以下操作只允许 ADMIN，所有写入都要求非空且不超过 500 字符的 `note`，记录真实操作人。

| API | 用途 |
| --- | --- |
| `GET /v1/refund-operations?limit=50` | 最早需要处理的人工队列，最多 100 条；处理后再获取下一批 |
| `GET /v1/refund-operations/{id}?after_id=0&limit=50` | 事务快照内读取执行、任务和审计，使用返回的 `next_after_id` 继续翻页 |
| `POST /v1/refund-operations/{id}/resume` | 人工排查后恢复有限次数的**查询**，正文 `{"note":"已确认渠道恢复"}` |
| `POST /v1/refund-operations/{id}/resubmit` | 补查耗尽且持续 NOT_FOUND、执行仍 RUNNING 时，明确授权原键重投，正文 `{"note":"已核实沙箱未收到原请求"}` |
| `POST /v1/refund-operations/{id}/acknowledge-conflict` | 核查终态矛盾后关闭人工项，正文 `{"note":"已向渠道核实，记录工单依据"}`；保留原终态及矛盾证据 |

写操作成功为 204；不符合当前状态为 409；数据库/提供方未知为脱敏 503。
未认证 401，普通客户及 SUPPORT 403。审计 `id/next_after_id` 用字符串，避免前端整数精度丢失。

重投先领取人工任务，再向沙箱查询；已有结果则直接使用，无结果才提交**相同键和相同金额**。
它依赖本项目 HTTP 沙箱永久保存幂等记录的契约，不可直接套到有短期幂等缓存的真实支付渠道。
两个管理员同时重投只有一个领到租约；原请求晚到也会在沙箱唯一键处合并。
已经终态的退款不允许重投，未知状态不会自动换新键。查询超时不满足 NOT_FOUND 的重投条件。

`acknowledge-conflict` 只是审计确认，不根据一条矛盾通知改写已记录资金终态。
如果未来真实账本确有差异，必须设计经授权的会计调整流程，不能以 SQL 强改或重退掩盖差异。

## 6. 回调协议与运行边界

回调路径不变：`POST /v1/refund-webhooks/sandbox`。
正文包括 `event_id`、`idempotency_key`、`status`、`provider_reference`。
签名已升级：

```text
X-Refund-Timestamp: Unix 秒字符串
X-Refund-Signature: HMAC-SHA256(secret, timestamp + "." + 原始请求字节) 的十六进制值
```

允许正负 300 秒时钟偏差，请保持机器时钟同步。缺失/过期签名 401，过大正文 413，
合法签名但无效内容 400，尚无可接收执行记录或数据库失败 503，原子提交后才返回 204。
同一事件重试使用原 event_id、原内容和新的签名时间戳；不要改 event_id 绕过冲突。
旧版“仅正文签名”发送端需要同步升级。普通客户执行/恢复响应字段保持不变。

审计防篡改 trigger 覆盖普通 UPDATE/DELETE/TRUNCATE；它不能对抗拥有禁用 trigger/DDL 权限
的数据库超级管理员。生产部署仍需最小数据库权限、备份、独立留存和真实渠道联调。
自动化验证使用进程内 HTTP、Fake 支付与临时测试 schema；真实 HTTP 端口联调和浏览器
人工验收仍需按本说明完成，不能因此宣称真实支付接入完成。
