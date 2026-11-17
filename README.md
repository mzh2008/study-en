# 职说AI · 隔离工程基座

本仓库目前是 DV-11 的 **T1 隔离实现**，只使用合成身份与内容。产品 SRS v0.3 尚待业务签字，DV-10 v0.2 内容全部为 `DRAFT/releaseAllowed=false`。没有真实微信登录、音频、供应商、支付、用户数据或公开发布授权。

## 本机运行

需要 Python 3.11+，仅使用标准库：

```sh
DV_SANDBOX=1 DV_DB=./sandbox.sqlite3 python3 app/server.py
python3 -m unittest discover -s tests -v
```

服务只监听 `127.0.0.1:8080`；访问 `http://127.0.0.1:8080/ops` 可操作本机内容审核演示。`miniprogram/` 是微信开发者工具内的隔离小程序页面，默认指向本机地址；真机与微信线上环境不能使用本地 HTTP 或合成登录。不要把 `DV_SANDBOX=1` 配置用于公开服务。

## T1 接口

所有写接口仅在隔离模式启用。`POST /v1/auth/fixture-login` 接受 `fixture:*` 合成 subject，返回一小时 bearer token；其余接口需 `Authorization: Bearer <token>`。学习者走 `POST /v1/onboarding`（`adult=true`、`consentVersion=sandbox-v1`、`track=development|support`、`goalMinutes=3|5|10|15`），再用 `GET /v1/me`、`GET /v1/course`、`POST /v1/privacy/withdraw`。退出同意后训练接口立即拒绝。

编辑身份 `fixture:editor` 使用 `POST /v1/admin/import/{scenario|day|rubric|exam}` 导入单个 JSON 对象。以 `(kind,id,version)` 和规范化 JSON SHA-256 不可变存储：相同对象重复导入幂等，不同对象同版本返回 409。独立身份 `fixture:reviewer` 使用 `POST /v1/admin/review/{kind}/{id}/{version}`；自审与 `releaseAllowed=false` 均拒绝。学习者 `GET /v1/content/{kind}/{id}/{version}` 只读取已发布内容，场景、变体、课程日只返回公开白名单。`POST /v1/course/complete-fixture` 要求 `fixture-evidence:*`，仅用于合成证据测试课程日序，不可当真实训练证据。

## 测试路径

1. 用 `fixture:editor` 导入 `ops/` 页面预填的 DV-10 风格草稿。预期导入 200；同对象重试 `idempotent=true`；修改原对象后沿用版本导入预期 409。
2. 用 `fixture:reviewer` 审核此草稿，预期 `draft_not_release_allowed`。学习者即使同意后查询也返回 404。
3. 单元与 HTTP 测试覆盖双身份授权、成年/同意、撤回、不可变版本、私有字段隔离、自审拒绝、课程证据与重复提交。仓库 CI 使用 Python 3.11 执行。

## 兼容性与未完成项

当前数据库是新建隔离 SQLite schema，无生产迁移。DV-10 原始 v0.2 对象尚未批量导入、哈希未重算；字段字典中的 `rubricVersion`/`languageRubricVersion` 是 rubric ID 引用，后续会话需另固定 rubric 的 `(rubricId,version,objectHash)`。T2 语音供应商、可信交付证据与事务权益账本，T3 双维评价/复练/考试，T4 订单/申诉/统计/隐私删除仍待实现。接入这些模块前不得将 `complete-fixture` 视为有效训练事件。生产身份、内容发布、支付、数据地域、留存、退款和部署回退须另行设计并按 DV-7 门禁批准。
