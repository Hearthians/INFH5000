# cis-lab 部署

以下示例用 `192.0.2.10` 代表服务器的校园网 IP。校园网浏览器访问 cis-lab 的 `192.0.2.10:7865`。该地址由 systemd 用户套接字监听，经过 cis-lab 的 `127.0.0.1:27865` 和反向 SSH 隧道连接本机画布 `127.0.0.1:7865`。编辑页和复核页分别使用该入口的 `/` 和 `/review` 路径。本机运行网页、任务记录与两个 Codex 视觉角色，使用期间需要保持这台机器在线。

生成使用 cis-lab 共享的 `sketchedit-qwen.service`。执行器访问本机 `127.0.0.1:28188`，独立的 SSH 用户服务把这个端口转发到 cis-lab 的 `127.0.0.1:18188`。不同客户端的 ComfyUI 输入和输出通过 `agent-qwen/<任务ID>/attempt-N` 隔开。

| 远端项目 | 位置 |
| --- | --- |
| ComfyUI 0.42.1 和 Python 环境 | `~/work/sketchedit-qwen/{ComfyUI,venv}` |
| 扩散模型、文本编码器、VAE | `/data2/<user>/artifacts/sketchedit-qwen/models/` |
| ComfyUI 输入和输出 | `/data2/<user>/runs/sketchedit-qwen/{input,output}/` |
| GPU 选择 | `~/work/sketchedit-qwen/device.env` |
| 服务日志 | `journalctl --user -u sketchedit-qwen.service` |

本项目在 cis-lab 的入口为 `~/work/agent-qwen/`。其中 `example.api.json` 是一次真实成功的 40 步编辑工作流，`verified.json` 记录 ComfyUI 的任务 ID、模型哈希和下载图像哈希。该目录的 `ComfyUI`、`venv`、`models` 和 `runtime` 链接到共用安装。本机网页用户服务可从 `~/.config/agent-qwen/network.env` 加载 Codex 出站代理。本地与隧道 HTTP 请求使用直连。[公开验证摘要](verified-summary.json)

本机用户服务和远端监听均已启用。查看当前进程、两条隧道和远端设备可运行以下命令。

```bash
systemctl --user status agent-qwen-canvas agent-qwen-tunnel agent-qwen-campus-tunnel
ssh cis-lab 'systemctl --user status sketchedit-qwen agent-qwen-campus.socket'
curl --noproxy '*' http://127.0.0.1:28188/system_stats
curl --noproxy '*' -I http://192.0.2.10:7865/review
journalctl --user -u agent-qwen-canvas -u agent-qwen-tunnel -u agent-qwen-campus-tunnel -n 30
```

重装本机用户服务时，从项目根目录执行以下命令。单元文件使用 `cis-lab` SSH 别名与项目路径。初次部署时，将 `deployment/cis-lab/agent-qwen-campus.socket.example` 复制为同目录下的 `agent-qwen-campus.socket`，并将 `ListenStream` 中的示例地址改为服务器的校园网 IP。

```bash
install -m 644 deployment/agent-qwen-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-qwen-tunnel agent-qwen-canvas agent-qwen-campus-tunnel
rsync -a deployment/cis-lab/agent-qwen-campus.{socket,service} cis-lab:.config/systemd/user/
ssh cis-lab 'systemctl --user daemon-reload && systemctl --user enable --now agent-qwen-campus.socket'
```

工作流使用 `qwen_image_2.1_int8_convrot.safetensors`、`qwen3vl_8b_int8_convrot.safetensors` 和 `qwen_image_2.1_vae_bf16.safetensors`。在 cis-lab 上可从模型目录执行 `sha256sum -c ~/work/sketchedit-qwen/weights.sha256` 复核权重。
