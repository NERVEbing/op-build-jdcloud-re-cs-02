# op-build-jdcloud-re-cs-02

京东云 RE-CS-02（雅典娜，qualcommax/ipq60xx，IPQ6010 + QCN9074）的固件构建工具。使用 [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt) 的 `main` 分支，在 x86_64 Linux 的 Docker 容器中编译。

仓库提供 `profiles/default` 默认示例，可直接构建。个人配置放在 `profiles/<name>/`；除 `default` 外均被 Git 忽略。

## 快速开始

```sh
./op image                               # 构建编译镜像
./op build default                       # 默认示例，产物位于 output/default/latest/
```

默认主机名为 `OpenWrt`，LAN 地址为 `192.168.1.1`。SSID 为 `OpenWrt`、`OpenWrt-IoT` 和 `OpenWrt-Guest`，密码均为公开示例值 `change-this-password`。

创建个人配置时，先复制默认示例，再修改 SSID、密钥和账号等参数：

```sh
cp -R profiles/default profiles/home
./op build home
```

## 命令

| 命令                                             | 说明                                                                |
| ------------------------------------------------ | ------------------------------------------------------------------- |
| `op image [--no-cache]`                          | 构建编译镜像（Ubuntu 24.04）                                        |
| `op list`                                        | 列出 profile 和可用功能                                              |
| `op info <profile>`                              | 显示解析后的配置，密钥打码                                          |
| `op setup <profile>`                             | 准备源码、feeds、配置和固件文件                                     |
| `op compile <profile> [-j N]`                    | 编译并收集产物；省略 `-j` 时按 CPU 和内存（约 2 GB/线程）计算并发数 |
| `op build <profile> [-j N]`                      | `setup` + `compile`                                                 |
| `op shell`                                       | 进入容器中的 `workspace/src`                                        |
| `op clean`                                       | 删除编译中间产物，保留 dl、ccache 和 feeds                          |
| `op distclean`                                   | 执行 `make distclean`，之后需重新 `setup`                           |
| `op push [-n] [user@host:/path] [-- rsync 选项]` | 用 rsync 同步到远程编译机；`-n` 只预览                              |

环境变量见 `./op --help`。编译镜像需要信任额外的 CA 证书时，将证书放入 `docker/certs/*.crt`，再执行 `./op image`。

使用远程编译机时，将 `.push.conf.example` 复制为 `.push.conf`，填好目标地址后执行 `./op push`。同步以本机文件为准，包含默认示例和所有本地个人 profile；远端的 `workspace/`、`output/`、`.git/`、`.push.conf` 和 `docker/certs/*.crt` 会保留。

远端必须使用绝对路径，目标目录必须为空或已经是本项目目录。额外的 rsync 选项放在 `--` 之后，例如 `./op push -- --bwlimit=10m`。

## Profile 配置

每个 profile 放在 `profiles/<name>/`：

| 文件               | 必需 | 作用                                                |
| ------------------ | ---- | --------------------------------------------------- |
| `profile.toml`     | 是   | 功能开关、系统参数和功能参数                        |
| `config.seed`      | 否   | 额外的 Kconfig 选项                                 |
| `files/`           | 否   | 固件覆盖文件，最后应用，可覆盖任何文件              |
| `hooks/<stage>.sh` | 否   | 构建 hook：`pre-feeds`、`post-feeds`、`post-config` |
| `banner`           | 否   | 替换登录横幅                                        |

完整参数参考见 [`examples/profile/profile.toml`](examples/profile/profile.toml)：

```toml
features = ["wifi", "pppoe", "tools"]   # 未列出的功能会从固件中移除

[system]
hostname = "OpenWrt"                     # 字母、数字和连字符
lan_ip = "192.168.1.1"                   # LAN 掩码为 /24
# dist = "ImmortalWrt"                   # 发行版名称
# version = "26.10"                      # 默认取构建日期（YY.MM）

# [source]                               # 覆盖 base/base.toml 中的上游源
# commit = "<40 位提交>"                  # 锁定上游提交

# [build]
# strict_config = false                  # defconfig 丢弃请求项时只警告

[pppoe]                                  # 功能参数表，表名即功能名
username = "account"
password = "password"
```

## 功能

| 功能          | 内容                                                                     | 参数                            |
| ------------- | ------------------------------------------------------------------------ | ------------------------------- |
| `wifi`        | ath11k 驱动与固件、wpad-openssl、iwinfo；配置 SSID、信道、独立网段和隔离 | 见下文                          |
| `pppoe`       | WAN 口 PPPoE 拨号                                                        | `username`、`password`          |
| `zerotier`    | luci-app-zerotier，加入指定网络，WAN 重新上线后重启 ZeroTier             | `networks`（16 位网络 ID 列表） |
| `ssh-lan`     | SSH 只监听 LAN 地址，允许密码登录                                        | —                               |
| `athena-led`  | 前面板 LED 屏                                                            | —                               |
| `nikki`       | Nikki 代理（feed `v1.26.2`）                                             | —                               |
| `wolultra`    | 网络唤醒                                                                 | —                               |
| `watchcat`    | 网络看门狗                                                               | —                               |
| `fullconenat` | 全锥形 NAT                                                               | —                               |
| `zram`        | zram 交换分区                                                            | —                               |
| `tools`       | bash、curl、htop、iperf3、磁盘与 USB 工具等                              | —                               |

构建时会关闭未启用的功能，包括上游默认开启的无线驱动、无线固件和 LED 屏应用。

### wifi

```toml
[wifi]
country = "CN"
quiet_log = true                 # hostapd 加 -q，各 radio 日志级别设为 4
radios.radio0 = { channel = 149, htmode = "HE80" }
networks.guest = { subnet = "172.16.1.1/24", access = "internet" }

[[wifi.ap]]
name = "guest"                   # wireless 段名，小写字母开头
radio = "radio2"
ssid = "Guest"                   # 1–32 字节
key = "change-this-password"
encryption = "sae-mixed"         # psk2（默认）| psk-mixed | sae | sae-mixed | none
network = "guest"                # lan（默认）或上面定义的网段
```

| radio    | 硬件                 | 可用信道（CN）                             | 最大带宽 |
| -------- | -------------------- | ------------------------------------------ | -------- |
| `radio0` | IPQ6018 板载 5 GHz   | 149、153、157、161、165（165 只能用 HE20） | HE80     |
| `radio1` | IPQ6018 板载 2.4 GHz | 1–13                                       | HE40     |
| `radio2` | QCN9074 5 GHz        | 36、40、44、48、52、56、60、64             | HE160    |

配置了 AP 的 radio 会启用，其余 radio 会停用。`channel` 和 `htmode` 未填写时保留上游默认值。

`networks` 用于添加 /24 网段。网关地址不能落在 DHCP 地址池 `.100–.199` 内。网段之间不能重叠，也不能与 LAN 重叠。

- `access = "lan"`：加入 LAN 防火墙区域。
- `access = "internet"`：建立独立防火墙区域，只允许 DHCP、到网关的 DNS，以及经 WAN（`wan` 或 `pppoe-wan`）访问公网 IPv4。客户端之间互相隔离。

## 定制与构建说明

配置和文件按顺序应用，后者覆盖前者；hook 按顺序执行：

| 维度     | 顺序                                                                                       |
| -------- | ------------------------------------------------------------------------------------------ |
| Kconfig  | `base/config.seed` → 目标设备 → 各功能 → profile `config.seed` → 版本信息                  |
| 固件文件 | `base/files` → 各功能 `files` → 渲染的参数与规则 → post-config hook 生成 → profile `files` |
| hook     | `base/hooks` → 各功能 `hooks` → profile `hooks`                                            |

- 目标设备由 `base/base.toml` 指定，功能所需的包由 `features` 管理。这些 Kconfig 项不能写入 `config.seed`。
- 自定义首次启动脚本放在 profile 的 `files/etc/uci-defaults/` 中，建议使用 `90-` 及以上编号（内置脚本使用 `10-` 至 `80-`）。
- 刷机后的首次启动（包括保留配置升级）会按 profile 重建无线、拨号、ZeroTier 和 SSH 配置。
- 源码在 `workspace/src` 中，各 profile 共用。下载和编译缓存分别在 `workspace/dl`、`workspace/ccache`。修改配置或切换 profile 后，重新执行 `setup`，也可直接执行 `build`。
- 产物在 `output/<profile>/<时间戳>/`，`output/<profile>/latest` 指向最新一次构建，保留最近 2 次。目录中包含固件镜像、`.config`、`manifest`、`buildinfo` 和 `<profile>-sources.tsv`。
- 使用个人 profile 构建的固件包含 Wi-Fi 密钥和拨号密码，请勿公开分享。

## 开发与测试

```sh
uv run --python 3.12 python -m unittest discover -s tests -v
shellcheck op base/hooks/*.sh features/*/hooks/*.sh tests/firmware/run.sh
shellcheck -x -s sh base/files/etc/uci-defaults/* features/*/files/etc/uci-defaults/* \
    features/*/files/etc/hotplug.d/*/* tests/firmware/cases.sh
bash tests/firmware/run.sh               # 在 ImmortalWrt rootfs 容器中运行固件脚本测试
```

修改渲染逻辑或 `tests/firmware/profile.toml` 后，执行 `uv run --python 3.12 python tests/firmware/render.py` 更新测试夹具。
