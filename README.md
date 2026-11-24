# 四台 GB10 部署 DeepSeek V4.1 Flash 1017

四台 FusionXpark GB10 通过四根高速线缆组成闭环，SGLang 使用 TP4、EP1 提供一个 OpenAI 兼容接口。主节点同时提供 Open WebUI 和 DeepSeek Harness（DSH）。模型为官方 FP8 / FP4 混合精度版本，专家计算日志确认启用 W4A8。

| 实测项目 | 四机结果 |
|---|---|
| 单路代码生成，第 129–641 token 稳定区间 | 五次中位数 **88.73 token/s**，最高 **94.24** |
| 八路代码生成，全程合计 | 五次中位数 **290.02 token/s**，最高 **301.46** |
| 1,001,299 token 冷输入 | **268.14 秒** |
| 回答质量 | **72/75** |
| 四段直连 RDMA 有效带宽 | **185.06–185.18 Gb/s**，物理端口协商速率 200 Gb/s |

两项生成速度的最高值出现在不同轮次。推理配置、逐方向网络测量和测量口径见[公开验收报告](docs/acceptance.md)。仓库保存脚本、配置示例和脱敏结果。权重、容器、用户账户和密钥由使用方在自己的设备上准备。

## 部署四机服务

1. 准备四台 ARM64 GB10，每台 128 GB 统一内存、2 TB 级本地 NVMe，安装支持 GB10 的 DGX OS、NVIDIA 驱动、Docker 和 NVIDIA Container Toolkit。作业开始前每台数据盘至少留出 650 GiB，另计容器镜像和构建缓存。使用相同的普通 Linux 用户名，确保各节点之间能够通过 SSH 访问。
2. 在主节点将本仓库克隆至 **~/spark-deploy**。在四台节点分别运行 `bash scripts/bootstrap-node.sh`（先把脚本复制到工作节点）。确保服务用户可运行 `docker info`，当前会话已取得相应权限。
3. 在主节点的仓库目录运行 `python3 scripts/cluster.py init`，按 rank 0、1、2、3 输入本站管理地址。执行 `python3 scripts/cluster.py network-preview`，核对接口、地址池、闭环接线及管理网络的独立性，再执行 `network-apply`。以上两个动作均通过同一 cluster.py 入口执行。最后执行 `python3 scripts/cluster.py distribute`。
4. 配置高速链路上的控制网。每台将 [control.json.example](docs/control.json.example) 复制到自己的 `~/spark-deploy/state/control.json`，填写相同的四个控制地址，顺序与 rank 一致。按[示例](docs/control.conf.example)为每台准备不同的配置，填入本机控制地址和每条 VXLAN 的编号、本端高速地址、对端高速地址、本端接口。选物理环中的三段连成一棵树，例如节点 2–1–4–3；每条连接的两端使用相同编号，地址次序相反。物理 RDMA 数据仍使用四段。示例使用文档保留地址，部署时选择本站空闲网段。
5. 各节点执行下列安装命令。配置文件由 root 持有。检查 `ip -br addr show sparkctrl0`，再从每台对其他三个控制地址执行 `ping -I sparkctrl0`。工作节点也需要主节点上的 spark-cluster-control.service 文件。

```bash
sudo install -m 644 node-control.conf /etc/spark-cluster-control.conf
sudo install -D -m 755 scripts/control-network.sh /usr/local/libexec/spark-cluster-control
sudo install -m 644 spark-cluster-control.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spark-cluster-control.service
bash scripts/install-runtime.sh
```

6. 在主节点执行下列命令。三份镜像分别按固定 OCI 摘要获取。模型每节点保存完整的 83 个文件及本 rank 的两份 Engram 文件。首次下载会产生约 510.31 GB/台的模型流量。已有完整本地副本时，可先复制到各节点的 `data/model` 再运行 prepare 完成校验。

```bash
python3 scripts/cluster.py preflight
python3 scripts/cluster.py pull
python3 scripts/cluster.py check
python3 scripts/cluster.py prepare
python3 scripts/cluster.py pack
python3 scripts/cluster.py collective
sudo install -m 644 deepseek-cluster@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "deepseek-cluster@$USER.service"
python3 scripts/cluster.py verify
python3 scripts/cluster.py ui
```

初始化、分发、打包和创建 WebUI 按首次部署顺序执行。现有集群使用 `status`、`logs`、`verify` 检查运行状态。模型 API 基础地址为 `http://<主节点管理地址>:8000/v1`，模型名称为 `deepseek-v4.1-flash-1017`。使用初始化时生成的 Bearer 密钥。网页聊天地址为 `http://<主节点管理地址>:3000`。

## 安装智能体

在主节点运行 `bash dsh/install.sh`。DSH Web 通过回环接口调用集群模型，并以用户级 systemd 服务运行。在客户端用 `ssh -N -L 13081:127.0.0.1:3080 用户名@主节点` 建立隧道。在另一个已认证的 SSH 会话内读取该用户的 `$XDG_RUNTIME_DIR/gb10-dsh/launch-url`，在个人浏览器打开该地址，将端口 3080 改为 13081，保留查询参数。首次认证后浏览器显示普通会话页面。工作目录为 `~/spark-deploy/dsh/workspace`。

## 版本与来源

模型版本为 `1bae0e1088993b9b82de08f59de5be5a34e42170`。SGLang、NCCL 伴随镜像和 Open WebUI 的完整摘要在 [common.py](scripts/common.py)。运行时另加载校验后的 SparkRing NCCL 2.30.7 双 PCI 域库。推理启动参数在 [container-start.sh](scripts/container-start.sh)。现场使用阈值 0.05，原帖为 0.10。

部署脚本由现场安装教程和四节点改动整理。适配器依据 MiaAI-Lab 的 DeepSeek-v4.1-Flash-DGX-Sparks 项目，参考提交 `64b02dea8681fb1d8e3a659ea432a8e9e9ba3995`。上游许可和版权说明保存在 [upstream](upstream/)。性能对照来源为该项目的 issue 59。公开版本将管理和控制网络参数改为使用方配置。现场验收基于实际部署，公开参数模板尚未在第二套四机环境重新安装验证。
