# NAS 素材来源

项目通过 `config/nas-sources.yaml` 登记两个 SMB 共享根：

- `192.168.110.20 / 浙江酷趣` → macOS `/Volumes/浙江酷趣`，Windows `\\192.168.110.20\浙江酷趣`
- `192.168.124.85 / 视觉部` → macOS `/Volumes/视觉部`，Windows `\\192.168.124.85\视觉部`

`share` 是必须挂载的 SMB 共享根；`subpaths` 是本次任务允许检索的共享内相对目录。不要把密码、Token 或 Secret 写入配置。

配置页启动时只读检查。未连接时，只有用户点击“连接 NAS”才打开 Finder/Explorer 的系统认证界面；程序不读取或保存认证信息，也不静默执行 `mount_smbfs`、`net use`。

诊断命令：

```text
tmall-materials nas-check --config config/nas-sources.yaml
tmall-materials nas-prepare --config config/nas-sources.yaml --source-id visual-department --allow-mount
tmall-materials nas-browse --config config/nas-sources.yaml --source-id zhejiang-kuqu
```

目录选择和任务数据使用稳定的 `source_id + relative_path`。系统拒绝绝对子路径、`..`、未允许目录以及解析后逃离共享根的符号链接。
