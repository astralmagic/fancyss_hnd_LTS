# fancyss HND LTS

基于 [hq450/fancyss](https://github.com/hq450/fancyss) `3.0` 分支的个人维护版本，面向 Asuswrt/Merlin 及带软件中心（≥384）的兼容固件。

- 保留上游全平台源码与构建能力。
- 预编译发布和实机维护范围为 `hnd`、`hnd_v8`，优先验证 `hnd_v8 full/lite`。
- 运行时在线更新、Xray 更新和规则更新均使用本仓库受控源，不自动跟随上游降级。
- 其他平台可继续从源码构建，但不作为本分支的稳定发布承诺。

## 插件特色

- 面向华硕 Merlin / 官改固件软件中心环境的透明代理插件，覆盖 `arm`、`hnd`、`hnd_v8`、`qca`、`mtk`、`ipq32`、`ipq64` 七个平台
- 支持 `SS`、`SSR`、`VMess`、`VLESS`、`Trojan`、`NaïveProxy`、`TUIC`、`Hysteria2`，以及自定义 `Xray/V2Ray JSON` 节点
- 支持手动添加、URI 导入、在线订阅三种节点输入方式，支持订阅过滤、二维码分享、订阅 UA 适配
- 提供 `chinadns-ng` 和 `smartdns` 两套 DNS 分流方案，并支持不同分流策略、动态解析 / 预解析切换
- 支持 `gfwlist`、大陆白名单、游戏、全局、回国、`Xray` 节点分流等多种代理模式
- 支持规则在线维护、节点分流规则集同步、客户端侧规则自动更新，以及 `geosite/geoip` 资产打包
- 支持 TCP / UDP 透明代理、QUIC 屏蔽、访问控制、IPv6 相关代理和 DNS 处理
- 支持单节点延迟检测、批量 Web 延迟测速、运行状态探测、简单故障转移、定时订阅 / 规则 / 二进制更新

## 支持机型/固件

> 以下为fancyss 3.0支持的机型/固件，点击机型可以前往相应固件下载地址
> 
> 最新固件下载地址：[https://fw.koolcenter.com/](https://fw.koolcenter.com/)

| 机型/固件下载                                                | 类型 | 平台            | CPU       | 架构  | linux内核 | fancyss版本 |
| ------------------------------------------------------------ | ---- | --------------- | --------- | ----- | --------- | ----------- |
| [R6300V2](https://fw.koolcenter.com/KoolCenter_Merlin_New_Gen_386/Netgear/R6300v2/) | 梅改 | 6.x.4708        | BCM4708   | armv7 | 2.6.36.4  | fancyss_arm |
| [RT-AC68U](https://www.koolcenter.com/posts/38)              | 梅改 | 6.x.4708        | BCM4708   | armv7 | 2.6.36.4  | fancyss_arm |
| [RT-AC88U](https://www.koolcenter.com/posts/39)              | 梅改 | 7.14.114.x      | BCM4709   | armv7 | 2.6.36.4  | fancyss_arm |
| [RT-AC3100](https://www.koolcenter.com/posts/40)             | 梅改 | 7.14.114.x      | BCM4709   | armv7 | 2.6.36.4  | fancyss_arm |
| [RT-AC5300](https://www.koolcenter.com/posts/41)             | 梅改 | 7.14.114.x      | BCM4709   | armv7 | 2.6.36.4  | fancyss_arm |
| [RT-AC86U](https://www.koolcenter.com/posts/36)              | 梅改 | hnd             | BCM4906   | armv8 | 4.1.27    | fancyss_hnd_v8 |
| [RT-AC86U](https://www.koolcenter.com/posts/139)             | 官改 | hnd             | BCM4906   | armv8 | 4.1.27    | fancyss_hnd_v8 |
| [GT-AC2900](https://fw.koolcenter.com/KoolCenter_Merlin_New_Gen_386/GT-AC2900/) | 梅改 | hnd             | BCM4906   | armv8 | 4.1.27    | fancyss_hnd_v8 |
| [GT-AC2900](https://www.koolcenter.com/posts/37)             | 官改 | hnd             | BCM4906   | armv8 | 4.1.27    | fancyss_hnd_v8 |
| [GT-AC5300](https://www.koolcenter.com/posts/12)             | 官改 | hnd             | BCM4908   | armv8 | 4.1.27    | fancyss_hnd_v8 |
| [RT-AX88U](https://www.koolcenter.com/posts/34)              | 梅改 | axhnd           | BCM4908   | armv8 | 4.1.51    | fancyss_hnd_v8 |
| [RT-AX88U](https://www.koolcenter.com/posts/142)             | 官改 | axhnd           | BCM4908   | armv8 | 4.1.51    | fancyss_hnd_v8 |
| [RAX80](https://www.koolcenter.com/posts/43)                 | 梅改 | axhnd           | BCM4908   | armv8 | 4.1.51    | fancyss_hnd_v8 |
| [GT-AX11000](https://www.koolcenter.com/posts/140)           | 官改 | axhnd           | BCM4908   | armv8 | 4.1.51    | fancyss_hnd_v8 |
| [GT-AX11000](https://www.koolcenter.com/posts/35)            | 梅改 | axhnd           | BCM4908   | armv8 | 4.1.51    | fancyss_hnd_v8 |
| [RT-AX92U](https://www.koolcenter.com/posts/20)              | 官改 | axhnd           | BCM4906   | armv8 | 4.1.51    | fancyss_hnd_v8 |
| [TUF-AX3000](https://www.koolcenter.com/posts/11)            | 官改 | axhnd.675x      | BCM6750   | armv7 | 4.1.52    | fancyss_hnd |
| [TUF-AX5400](https://www.koolcenter.com/posts/130)           | 梅改 | axhnd.675x      | BCM6750   | armv7 | 4.1.52    | fancyss_hnd |
| [TUF-AX5400](https://www.koolcenter.com/posts/141)           | 官改 | axhnd.675x      | BCM6750   | armv7 | 4.1.52    | fancyss_hnd |
| [RT-AX58U](https://www.koolcenter.com/posts/130)             | 梅改 | axhnd.675x      | BCM6750   | armv7 | 4.1.52    | fancyss_hnd |
| [RAX50](https://www.koolcenter.com/posts/130)                | 梅改 | axhnd.675x      | BCM6750   | armv7 | 4.1.52    | fancyss_hnd |
| [RT-AX82U](https://www.koolcenter.com/posts/18)              | 官改 | axhnd.675x      | BCM6750   | armv7 | 4.1.52    | fancyss_hnd |
| [RT-AX82U](https://www.koolcenter.com/posts/130)             | 梅改 | axhnd.675x      | BCM6750   | armv7 | 4.1.52    | fancyss_hnd |
| [ZenWiFi_XT8](https://www.koolcenter.com/posts/137)          | 官改 | axhnd.675x      | BCM6755   | armv7 | 4.1.52    | fancyss_hnd |
| [ZenWiFi_XT8](https://www.koolcenter.com/posts/130)          | 梅改 | axhnd.675x      | BCM6755   | armv7 | 4.1.52    | fancyss_hnd |
| [ZenWiFi_XD4](https://www.koolcenter.com/posts/21)           | 官改 | axhnd.675x      | BCM6755   | armv7 | 4.1.52    | fancyss_hnd |
| [RT-AX56U_V2](https://www.koolcenter.com/posts/16)           | 官改 | axhnd.675x      | BCM6755   | armv7 | 4.1.52    | fancyss_hnd |
| [RT-AX1800](https://www.koolcenter.com/posts/16)             | 梅改 | axhnd.675x      | BCM6755   | armv7 | 4.1.52    | fancyss_hnd |
| [RT-AX56U](https://www.koolcenter.com/posts/130)             | 梅改 | axhnd.675x      | BCM6755   | armv7 | 4.1.52    | fancyss_hnd |
| [RAX70](https://www.koolcenter.com/posts/130)                | 梅改 | axhnd.675x      | BCM6755   | armv7 | 4.1.52    | fancyss_hnd |
| [RT-AX68U](https://www.koolcenter.com/posts/136)             | 官改 | 5.02L.07p2axhnd | BCM4906   | armv8 | 4.1.52    | fancyss_hnd_v8 |
| [RT-AX68U](https://www.koolcenter.com/posts/33)              | 梅改 | 5.02L.07p2axhnd | BCM4906   | armv8 | 4.1.52    | fancyss_hnd_v8 |
| [RT-AX86U](https://www.koolcenter.com/posts/135)             | 官改 | 5.02L.07p2axhnd | BCM4908   | armv8 | 4.1.52    | fancyss_hnd_v8 |
| [RT-AX86U](https://www.koolcenter.com/posts/5)               | 梅改 | 5.02L.07p2axhnd | BCM4908   | armv8 | 4.1.52    | fancyss_hnd_v8 |
| [GT-AXE11000](https://www.koolcenter.com/posts/130)          | 梅改 | 5.02L.07p2axhnd | BCM4908   | armv8 | 4.1.52    | fancyss_hnd_v8 |
| [GT-AX6000](https://www.koolcenter.com/posts/125)            | 官改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| [GT-AX6000](https://www.koolcenter.com/posts/148)            | 梅改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| [ZenWiFi_Pro_XT12](https://www.koolcenter.com/posts/133)     | 官改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| [ZenWiFi_Pro_XT12](https://www.koolcenter.com/posts/149)     | 梅改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| [TUF-AX3000_V2](https://www.koolcenter.com/posts/161)        | 官改 | 5.04axhnd.675x  | BCM6756   | armv7 | 4.19.183  | fancyss_hnd |
| [RT-AX86U PRO](https://www.koolcenter.com/posts/228)         | 官改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| RT-AX86U PRO                                                 | 梅改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| GT-AX11000 PRO                                               | 官改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| GT-AX11000 PRO                                 | 梅改 | 5.04axhnd.675x  | BCM4912   | armv8 | 4.19.183  | fancyss_hnd_v8 |
| RT-BE86U | 官改 | 5.04behnd.4916 | BCM4916 | armv8 | 4.19.275 | fancyss_hnd_v8 |
| RT-BE88U | 官改 | 5.04behnd.4916 | BCM4916 | armv8 | 4.19.275 | fancyss_hnd_v8 |
| GT-BE96 | 官改 | 5.04behnd.4916 | BCM4916 | armv8 | 4.19.275 | fancyss_hnd_v8 |
| RT-BE96U | 梅改 | 5.04behnd.4916 | BCM4916 | armv8 | 4.19.275 | fancyss_hnd_v8 |
| GT-BE98_PRO | 梅改 | 5.04behnd.4916 | BCM4916 | armv8 | 4.19.275 | fancyss_hnd_v8 |
| [RT-AX89X](https://www.koolcenter.com/posts/126)             | 官改 | qca-ipq806x     | ipq8074/a | armv7[^2] | 4.4.60    | fancyss_qca |
| ZenWiFi_BD4 | 官改 | qca-ipq53xx | IPQ5322 | armv7 | 5.4.213 | fancyss_ipq32 |
| TUF-BE6500 | 官改 | qca-ipq53xx | IPQ5322 | armv8 | 5.4.213 | fancyss_ipq64 |
| TX-AX6000 | 官改 | mtk-MT798X | MT7986A | armv8 | 5.4.182 | fancyss_mtk |
| TUF-AX4200Q | 官改 | mtk-MT798X | MT7986A | armv8 | 5.4.182 | fancyss_mtk |
| GS7 | 官改 | mtk-7988_7990 | MT7988D | armv8 | 5.4.281 | fancyss_mtk |
| ZenWiFi_BT8P | 官改 | mtk-7988_7990 | MT7988D | armv8 | 5.4.281 | fancyss_mtk |
## 版本选择

fancyss 3.0 当前同时区分“平台包”和“包型”两个维度，选包时建议按下面顺序判断。

1. 先按平台选择包名前缀
   - `fancyss_arm`：老 `BCM470x` / `armv7` / `2.6` 内核机型
   - `fancyss_hnd`：`BCM675x/6755/6756` 等 32 位博通平台
   - `fancyss_hnd_v8`：`BCM4906/4908/4912/4916` 等 64 位博通平台
   - `fancyss_qca`：老 `QCA/IPQ807x` 平台
   - `fancyss_mtk`：`MT798x / MT7988` 平台
   - `fancyss_ipq32` / `fancyss_ipq64`：新 `QCA IPQ53xx` 平台

2. 再按 `full` / `lite` 选择包型
   - `full` 和 `lite` 共享绝大部分主功能：透明代理、DNS 分流、规则维护、订阅、Web 延迟测速、故障转移、节点分流，以及 `SS/SSR/VMess/VLESS/Trojan/Hysteria2/Xray JSON` 等主路径
   - `full` 额外保留 `NaïveProxy` / `TUIC` 相关前端、脚本和二进制，并带上 `ipt2socks`、`haveged`、`ss_v2ray.sh` 以及部分兼容资源
   - `lite` 主要是为了节省 `JFFS` 空间，裁掉 `NaïveProxy` / `TUIC` 相关资源和少量附属文件；如果你不使用这两类协议，优先选 `lite`

3. 选择建议
   - 需要 `NaïveProxy` 或 `TUIC`，请选择 `full`
   - `JFFS` 空间紧张，或者只使用 `SS/SSR/VMess/VLESS/Trojan/Hysteria2/Xray`，请选择 `lite`
   - 在线更新会保持当前平台和包型；要在 `full` 和 `lite` 之间切换，需要重新安装对应离线包
   - `RT-AX86U`、`GT-AX6000` 这类 armv8 博通机型，应优先选择 `fancyss_hnd_v8`

## 插件下载

本分支为 `hnd` 和 `hnd_v8` 提供预编译 full/lite 包。可在 `packages` 目录或 GitHub Releases 下载；历史版本仍保留在上游历史包仓库。

| 平台 | full | lite | 上游历史包 |
| --- | --- | --- | --- |
| hnd | [fancyss_hnd_full](https://raw.githubusercontent.com/astralmagic/fancyss_hnd_LTS/lts/packages/fancyss_hnd_full.tar.gz) | [fancyss_hnd_lite](https://raw.githubusercontent.com/astralmagic/fancyss_hnd_LTS/lts/packages/fancyss_hnd_lite.tar.gz) | [fancyss_hnd](https://github.com/hq450/fancyss_history_package/tree/master/fancyss_hnd) |
| hnd_v8 | [fancyss_hnd_v8_full](https://raw.githubusercontent.com/astralmagic/fancyss_hnd_LTS/lts/packages/fancyss_hnd_v8_full.tar.gz) | [fancyss_hnd_v8_lite](https://raw.githubusercontent.com/astralmagic/fancyss_hnd_LTS/lts/packages/fancyss_hnd_v8_lite.tar.gz) | [fancyss_hnd_v8](https://github.com/hq450/fancyss_history_package/tree/master/fancyss_hnd_v8) |

其他平台保留源码构建能力，但本分支不提供预编译发布或实机稳定性承诺。



## 插件安装

1. 离线安装：下载并校验好离线安装包后，在软件中心内使用**离线安装**/**手动安装**功能，选择安装包后上传并安装即可。

2. 命令安装：(以fancyss_hnd_lite.tar.gz为例，先下载好安装包，并将其上传到路由器的/tmp目录)

   ```bash
   mv /tmp/fancyss_hnd_lite.tar.gz /tmp/shadowsocks.tar.gz
   tar -zxvf /tmp/shadowsocks.tar.gz
   sh /tmp/shadowsocks/install.sh
   ```

## 使用文档

目前仓库内提供以下协议相关实践文档：

1. [TUIC 配置指南](./doc/tuic_usage_on_fancyss.md)
2. [NaiveProxy 配置实践](./doc/naive_usage_on_fancyss.md)

## 关于皮肤

目前插件皮肤支持以下版本：

asuswrt：经典asuswrt皮肤

rog：华硕红色rog皮肤

tuf：华硕橙色tuf皮肤

tx：华硕天选青色皮肤

## 注意事项

* 强烈建议使用chrome或者chrouium内核的浏览器！以保证最佳兼容性！
* 强烈建议在`最新版本的固件`和`最新版本软件中心`上使用fancyss_hnd！
* 插件会自动跟随当前固件的皮肤类型，支持assuwrt、rog、tuf三种皮肤。
* 一些机型的联名版，只要刷了官改/梅林改版固件的，均能安装本插件！

## 目录说明

- **fancyss/**：插件主体源码，也是 `build.sh` 的直接打包输入目录
- **binaries/**：上游二进制存档、更新脚本和本项目发布时要同步到各平台 `bin-*` 的文件
- **rules_ng/**：传统规则源，如 `gfwlist`、`chnroute`、`black_list` 等
- **rules_ng2/**：节点分流使用的新规则资产树，包含 `site/`、`ip/`、`meta/`、`dat/`
- **scripts/**：仓库级辅助脚本，主要负责 `geosite/geoip` 资产生成和本地工具链准备
- **tool/**：自研工具源码子项目（submodule），目前主要是 `geotool` 和 `xapi-tool`
- **packages/**：打包产物目录，包含各平台离线安装包和 `version.json.js`
- **doc/**：文档目录，`doc/` 根目录是用户文档，其余分类目录主要面向开发和维护

## 打包插件

> `build.sh` 会把 `fancyss/` 复制为临时目录 `shadowsocks/`，再按平台和包型裁剪二进制、脚本和 Web 页面，最终生成离线安装包。
>
> 打包前会自动同步传统规则、重建 `rules_ng2` 的 `geosite/geoip` 资产，并从 `binaries/` 中拷贝当前指定版本的核心二进制。

1. 使用 Linux 环境准备仓库，并带上 submodule 一起拉取

   ```bash
   git clone --recurse-submodules https://github.com/astralmagic/fancyss_hnd_LTS.git
   cd fancyss_hnd_LTS
   ```

2. 日常维护使用默认 `lts` 分支；候选修复先在独立 issue 分支验证，再合入 `lts`。

3. 根据需要修改以下内容
   - 插件运行逻辑：`fancyss/`
   - 规则资产：`rules_ng/`、`rules_ng2/`
   - 二进制版本：`binaries/`
   - geodata 生成脚本：`scripts/`

4. 运行打包脚本

   ```bash
   bash build.sh
   ```

5. 当前 `build.sh` 的实际行为
   - 默认面向 `hnd` 与 `hnd_v8`：先清空并重建 `packages/`，生成 `fancyss_hnd_{full,lite}.tar.gz`、`fancyss_hnd_v8_{full,lite}.tar.gz`（共 4 个 `release` 包，以及 2 个本地 `full debug` 包）和仅含 HND 校验字段的 `packages/version.json.js`
   - 若需从源码构建上游全部 7 个平台，可执行 `bash build.sh --all-platforms`

6. GitHub Actions 的 **Build HND packages** 会执行 `bash build.sh`，校验 HND 版本清单、包内核心二进制与工作区干净度，并在 Tag 发布时上传 HND/HND v8 的 full/lite 包及 `version.json.js`。

7. 如果你只想打某个平台、某种包型，或者不想同时生成 debug 包，直接修改 [build.sh](./build.sh) 里的 `make()` 即可

## 相关链接

- **本分支更新日志**：https://github.com/astralmagic/fancyss_hnd_LTS/blob/lts/Changelog.txt
- **上游项目**：https://github.com/hq450/fancyss
- **上游历史离线包**：https://github.com/hq450/fancyss_history_package
- **geotool**：https://github.com/hq450/geotool
- **xapi-tool**：https://github.com/hq450/xapi-tool
- **官改/梅改固件下载【官方网站】**：https://www.koolcenter.com
- **官改/梅改固件下载【固件镜像】**：https://fw.koolcenter.com

[^1]: RT-AC86U从384_81918_koolshare固件版本开始，使用的是asuswrt风格ui，而不是rog风格。
[^2]: RT-AX89X采用的SoC为ipq8074/ipq8074A，支持64位系统，但是其固件是32位系统。
