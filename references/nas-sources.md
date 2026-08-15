# NAS 素材来源

项目通过 `config/nas-sources.yaml` 登记两个 SMB 共享根：

- `192.168.110.20 / 浙江酷趣` → `\\192.168.110.20\浙江酷趣`
- `192.168.124.85 / 视觉部` → `\\192.168.124.85\视觉部`

`share` 是必须挂载的 SMB 共享根；`subpaths` 是本次任务允许检索的共享内相对目录。不要把密码、Token 或 Secret 写入配置。

这些共享定义不再显示在任务配置页，也不会在页面启动时自动检测或连接。用户先在 Windows/Explorer 中完成所需的网络访问，然后直接在“本次图片源”中选择已经可见的 Y 盘、Z 盘或粘贴 UNC 路径。工作台只对用户最终填写的路径进行只读元数据检测；不会打开认证窗口、读取或保存认证信息，也不会静默执行 `net use`。

下面的命令和 `config/nas-sources.yaml` 仅用于开发排障或管理员诊断，不属于普通上传任务步骤：

诊断命令：

```text
tmall-materials nas-check --config config/nas-sources.yaml
tmall-materials nas-prepare --config config/nas-sources.yaml --source-id visual-department --allow-mount
tmall-materials nas-browse --config config/nas-sources.yaml --source-id zhejiang-kuqu
```

目录选择和任务数据使用稳定的 `source_id + relative_path`。系统拒绝绝对子路径、`..`、未允许目录以及解析后逃离共享根的符号链接。
