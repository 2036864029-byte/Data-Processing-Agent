# 交付目标与 NAS 挂载

默认交付位置是服务器磁盘上的 `output_dir`（默认 `var/output`），用户经 HTTP
下载（见 README「HTTP 端点」）。需要落到 NAS / 共享盘时，**优先走 OS 挂载 +
`output_dir` 指挂载点**：零代码、无凭证管理、权限与配额都交给操作系统。

直接经 WebDAV/SMB 协议写入的后端插件按需再做；当前版本不提供。

## 1. 宿主机挂载

### NFS（Linux 服务器）

```bash
sudo apt install nfs-common                    # Debian/Ubuntu
sudo mkdir -p /mnt/nas/finance
sudo mount -t nfs nas.example.com:/export/finance /mnt/nas/finance
```

写入 `/etc/fstab` 保证重启后自动挂载：

```
nas.example.com:/export/finance /mnt/nas/finance nfs defaults,_netdev,hard,timeo=600 0 0
```

### SMB / CIFS

```bash
sudo apt install cifs-utils
sudo mkdir -p /mnt/nas/finance
sudo mount -t cifs //nas.example.com/finance /mnt/nas/finance \
  -o credentials=/etc/dataagent/nas.cred,uid=1000,gid=1000,vers=3.0
```

`/etc/dataagent/nas.cred`（权限 `0600`）：

```
username=dataagent
password=<密码>
domain=<域>
```

`/etc/fstab`：

```
//nas.example.com/finance /mnt/nas/finance cifs credentials=/etc/dataagent/nas.cred,uid=1000,gid=1000,vers=3.0,_netdev 0 0
```

> `_netdev` 让系统等网络就绪后再挂载；挂载失败时目录为空，DataAgent 会判定
> 目标不可达并回退默认输出根，不会在本地盘上静默创建同名目录。

### 权限自检

```bash
sudo -u <运行用户> test -w /mnt/nas/finance && echo writable
```

## 2. Docker 部署

宿主机挂载完成后，把挂载点 bind mount 进容器，API 与 worker 必须看到同一路径：

```yaml
services:
  api:
    volumes:
      - /mnt/nas/finance:/data/finance
  worker:
    volumes:
      - /mnt/nas/finance:/data/finance
```

容器内路径与 `deliver_targets[].path` / `output_dir` 要一致，例如
`/data/finance`。若希望所有任务默认落 NAS，设置 `DATAAGENT_OUTPUT_DIR=/data/finance`；
若只希望指定任务落 NAS，保留默认 `output_dir`，改用下面的目标登记 + 自然语言路由。

## 3. 登记交付目标

在 `config.yaml`（或 `DATAAGENT_CONFIG` 指向的文件）中登记 name、路径与别名：

```yaml
deliver_targets:
  nas_finance:
    path: /data/finance
    aliases: ["公司 NAS 财报库", "财报库"]
```

- `path`：已挂载且运行用户可写的目录；配置阶段不要求存在（可先登记后挂载）。
- `aliases`：用户在任务指令里可能提到的说法，建议写有区分度的短语。

把 `config.yaml` 挂进容器（或替换镜像内配置），API 与 worker 都要加载同一份。

## 4. 自然语言路由规则（P0）

任务创建时传入交付指令（`create_builtin_runner(..., deliver_to="存到公司 NAS 财报库")`），
路由在输出根注入点解析：

1. **未指定** → 默认 `output_dir`。
2. 名称与别名做子串匹配，忽略大小写与空白；**最长匹配优先**。
3. 命中且路径存在、是目录、可写 → 使用该路径。
4. 以下情况**一律回退默认输出根**，并写结构化日志
   `delivery_target_fallback`（字段：`requested / target / reason / output_dir / detail`）：

| reason | 触发条件 |
| ---- | ---- |
| `no_match` | 指令里没有匹配到任何已登记目标 |
| `ambiguous` | 多个目标命中且匹配长度相同 |
| `unreachable` | 目标不存在 / 未挂载 / 不是目录 / 不可写 |

命中时写 `delivery_target_routed`（INFO），回退写 WARNING。原因会原样进入
日志与后续任务提交接口的响应，不会静默改道。

## 5. 已知限制

- P0 是确定性规则，不理解否定句（"不要存到 NAS" 仍会命中），也不做语义消歧；
  这是 M3 模型解析交付意图的预留位。
- 路径不可达的判定基于 `exists / is_dir / os.access`；网络盘在写入瞬间掉线仍可能
  失败，此时任务按既有错误上报，不会改写交付等级。
- 直接 WebDAV/SMB 后端（不经 OS 挂载）未实现；需要时按 `ObjectStore` 抽象追加后端，
  但交付落盘目前直接走文件系统，接入前需先评估。
