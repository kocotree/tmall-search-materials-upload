# 错误处理与恢复规则

| Code | 含义 | 默认动作 |
| --- | --- | --- |
| `DATA_SCHEMA` | CSV/XLSX 必需字段缺失或变化 | 阻断整批 |
| `MISSING_PRODUCT_ID` | 商品没有远端 ID | 阻断该商品 |
| `DUPLICATE_PRODUCT_ID` | 多条来源记录共享 ID | 阻断所有重复行 |
| `RULE_CONFLICT` | 月度来源冲突 | 转人工审核 |
| `ASSET_NOT_FOUND` | 没有匹配素材 | 阻断对应坑位 |
| `ASSET_INVALID` | 数量、比例、尺寸、格式或可读性失败 | 修复本地素材，不上传 |
| `LICENSE_UNKNOWN` | 使用权未确认 | 阻断对应坑位 |
| `STORE_IDENTITY_MISMATCH` | 当前店铺不是目标店铺 | 立即停止整批 |
| `PRODUCT_MISMATCH` | 页面商品 ID 与任务不同 | 立即停止当前批次 |
| `REMOTE_SLOT_CONFLICT` | 批准后的目标坑位已发生变化 | 任务退回审核 |
| `SELECTOR_INVALID` | 必需页面元素不存在或不可见 | 商品资格不变；受影响坑位进入 `needs_manual_review`，停止受影响页面且不写入零值 |
| `AUTH_EXPIRED` | 登录失效 | 暂停整批，用户重新登录 |
| `HUMAN_CHECK` | 验证码、扫码、短信或风控 | 暂停整批，等待用户处理 |
| `UPLOAD_REJECTED` | 页面拒绝文件或字段 | 记录原始提示，转人工审核 |
| `PUBLISH_UNCERTAIN` | 点击发布后没有可信结果 | 禁止重发，先远端回查 |
| `REMOTE_EVIDENCE_MISMATCH` | 远端记录与批准指纹/坑位/时间不一致 | 保持不确定并转人工 |
| `MODERATION_FAILED` | 平台审核失败 | 记录平台原因，转人工处理 |
| `STATE_MISSING` | `run.sqlite3`、任务状态行或状态证据缺失/损坏 | 禁止自动上传，先做可信远端核验 |

## 重试边界

- 最终发布动作之前的瞬时页面或网络失败最多重试两次，也可以选择零次重试并转人工。
- 发布按钮在一个 `material_item` 上最多点击一次。
- 点击发布后的超时、页面关闭或成功信号缺失一律进入 `publish_uncertain`。
- `publish_uncertain` 必须通过正确店铺、精确商品 ID、目标坑位、批准内容指纹和提交时间窗口回查。
- `run.sqlite3` 及 manifest 中每个 task 的状态行必须存在；流程不得静默创建新状态库后重排队。
- 只有远端素材列表可见、精确检索成功且没有对应记录时，才能返回 `REMOTE_ABSENCE_CONFIRMED`；重新发布仍需要新的显式执行决定。
- `REMOTE_ABSENCE_CONFIRMED` 不会使原 task 回到可上传状态。若人工决定重提，必须生成新 task ID、重新生成批准清单并再次显式执行；原 task 保留审计历史。

## 审批失效范围

- 单个 `material_item` 的媒体、标题、描述、动作或坑位变化：只撤销该 item 的批准。
- 单项撤批使用 `approved → ready_for_review / APPROVED_CONTENT_CHANGED`，其他 item 保持原状态。
- 目标店铺、manifest schema、manifest 总哈希或批次输入哈希变化：阻断整批。
- 批次阻断只设置 run 级门禁并将 item 标记为 held，不批量改写 item 状态；解除门禁前必须重核店铺、manifest 和未执行项内容哈希。
- 已有远端素材 ID 或可信远端证据的 item 不得再次进入上传队列。

所有异常必须记录 task ID、商品 ID、目标店铺、旧状态、新状态、原因码、时间、尝试次数和页面证据。
