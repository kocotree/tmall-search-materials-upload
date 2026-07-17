# 素材要求

## 通用要求

- 原始文件只读；裁剪或转码结果写入独立派生目录。
- 每个文件记录来源系统、绝对路径、商品 ID、货号、授权状态和 SHA-256。
- 授权状态不是 `confirmed` 时使用 `LICENSE_UNKNOWN` 阻断。
- 零字节、无法读取、不支持的格式和重复指纹不得进入审核清单。
- 商品 ID 是首选匹配键，货号是后备键；名称匹配只能产生人工确认候选。

## 图文素材

- 每个图文坑位包含 3–9 张图片。
- 支持 JPG、JPEG、PNG 和 WebP。
- 接受 3:4 或 1:1；同一图文坑位不得混合比例。
- 比例容差由 `config/media-policy.example.yaml` 的运行时副本配置。
- 校验文件可读性、文件大小、宽高、比例、同组一致性、零字节、重复指纹和授权状态。

## 视频素材

运行时策略必须完整提供以下字段：容器格式、编码、最短/最长时长、最小分辨率、允许比例、最大文件大小、封面要求、水印规则。任何字段为空时返回 `VIDEO_POLICY_INCOMPLETE`，禁止自动发布视频。

视频元数据必须来自可信探测器或已验证素材清单，并至少包含编码、时长、宽、高、封面存在状态和水印存在状态。无法取得时返回 `VIDEO_METADATA_UNAVAILABLE`。

对应错误码包括：

- `VIDEO_FORMAT_INVALID`
- `VIDEO_CODEC_INVALID`
- `VIDEO_DURATION_INVALID`
- `VIDEO_RESOLUTION_INVALID`
- `VIDEO_ASPECT_RATIO_INVALID`
- `VIDEO_SIZE_INVALID`
- `VIDEO_COVER_MISSING`
- `VIDEO_WATERMARK_NOT_ALLOWED`

## 当前生产门禁

`config/media-policy.example.yaml` 中的视频字段故意保持为空，表示当前生产规格尚未由用户确认。在用户用官方后台规则补齐运行时配置前，图片任务可以继续预检，视频任务必须保持 blocked；不得用猜测值替代。
