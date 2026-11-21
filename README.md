# Agent–Qwen 画布编辑

上传原图，圈出目标、涂写、添加文字或绘制移动箭头，点击「开始编辑」。如果图片已经带有说明，先选择「已有标注图」。网页显示每轮候选、逐项验收证据和最佳候选，支持下载。任务链接带完整 ID，刷新或使用「找回任务」可以继续查看。[本机画布](http://127.0.0.1:7865/) · [本机复核页](http://127.0.0.1:7865/review)

手机上提交后会跳至任务进度。结果区可以切换查看标注和候选图，点按图片可打开原尺寸。复核页支持跳到首个待复核案例，填写的判断和备注随草稿保存在当前浏览器，完成后可导出。

网页、单图命令和批处理共用 Python 执行器。两个视觉角色分别启动本机的 `codex exec`。Qwen-Image 2.1 通过 SSH 隧道在 cis-lab 上生成图像。

## 运行

在项目根目录使用 Python 3.10 或更新版本，安装 `requirements.txt`，并确保本机 Codex CLI 已登录、支持图像附件与 `--output-schema`。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m agent_qwen serve
python -m agent_qwen edit --canvas marked.png --source original.png --max-tries 3
python -m agent_qwen edit --canvas paper-examples/assets/fern-canvas.webp
python -m agent_qwen batch evaluation/cases.json --workers 2 --report runs/paper-batch.json
```

`serve` 默认监听 `127.0.0.1:7865`。已启动的用户服务可以直接使用。单图命令在开始时输出任务 ID，结束时输出状态、最佳图片路径和未通过项。退出码 0 表示通过，1 表示仍需澄清或验收未通过，2 表示错误。批处理按清单所在目录解析路径，每项必填 `id` 和 `canvas`，可选 `source`、`ink`、`strokes`、`note` 和 `max_tries`。

```bash
python -m agent_qwen edit --resume JOB_ID --answer "下层从左数第四只，移动到上层最右侧"
python -m agent_qwen edit --resume JOB_ID
```

第一个命令回答澄清问题。第二个命令从已保存的检查点继续，适用于中断或服务恢复。网页提供相同的入口。一个进程持有任务锁时，其他入口可以读取进度。

## 生成与验收

- 提示词 Agent 读取标注、可用原图和原始像素坐标，确定目标、位置、需保护的对象、英文指令及验收条件。看不清目标时先提问，生成次数为零。
- 首轮使用现有 40 步工作流和种子 42。有干净原图时将它交给 Qwen，其他情况使用合成标注图。重试依次使用 43、44 等种子。
- 每轮独立验收整张候选图。目标、效果、相邻对象、笔迹和局部画质全部通过才能交付。移动还要逐项检查起点和终点。`unclear` 会阻止通过。
- 选错目标或改坏邻居时从原始输入重做，提示词 Agent 收到具体失败位置和证据。有干净原图且允许修改的轮廓可辨时，裁出带上下文的区域生成，仅在轮廓内部羽化合成。移动分别覆盖两个端点。
- 主体等检查全部通过而仅有笔迹残留时，清理对应候选一次。能够定位笔迹时，按该区域合成。重做和清理都计入生成上限。程序先比较语义检查通过数，再比较总通过数和显式失败数，平分保留较早候选。
- `exhausted` 在网页标为「未通过验收」。`error` 保留错误、已有候选和 ComfyUI 的 `prompt_id`。验收格式错误至多再验一次。已提交的生成任务断线后继续轮询同一 ID。

## 文件与配置

默认记录在 `runs/<任务ID>/`。输入包括 `inputs/canvas.png`、可用的 `source.png`、网页生成的透明 `ink.png` 和 `strokes.json`。笔迹的 `width`、`height`、`points` 使用原图像素。每轮的 `attempt-N/` 包含实际工作流、提示词、生成图片、整图候选、验收请求和输出。局部重做还保存裁剪输入。`state.json` 记录冻结意图、生成端点、种子、实际输入、回退依据、最佳候选和最终状态。

| 配置项 | 默认值 |
| --- | --- |
| `AGENT_QWEN_COMFY_URL` | `http://127.0.0.1:28188`，转发到 cis-lab |
| `AGENT_QWEN_RUNS` 或 `--runs` | 当前目录下的 `runs` |
| `AGENT_QWEN_CODEX`、`AGENT_QWEN_CODEX_MODEL` | PATH 中的 `codex` 和本机已配置模型 |
| `AGENT_QWEN_CODEX_EFFORT` | `medium` |
| `AGENT_QWEN_CODEX_TIMEOUT` | 每个视觉调用 240 秒 |
| `AGENT_QWEN_COMFY_TIMEOUT` | 每轮等待生成结果 900 秒 |

这台机器上的 ComfyUI 也可通过 `AGENT_QWEN_COMFY_URL=http://127.0.0.1:8188` 显式选择。部署路径与服务命令见[部署记录](deployment/README.md)。

## 验证

```bash
python -m unittest discover -s tests -v
python -m evaluation.calibrate --out runs/calibration-check
python -m agent_qwen batch evaluation/cases.json --report evaluation/batch.json
python -m evaluation.review
```

校准使用既有候选。案例清单只包含用户输入。独立的事后复核工具读取作者参考图，生成可打开原尺寸图、填写结论、导出记录的对照表。浏览器实测脚本为 `python -m evaluation.browser --out runs/browser-check`，另需 Playwright 和 Chromium，可用 `AGENT_QWEN_CHROMIUM` 指定浏览器可执行文件。九个案例的通过率、误放行、生成次数与耗时见[实测报告](evaluation/REPORT.md)。
