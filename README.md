# Busy Beaver Lab

用于研究图灵机停机行为的本地实验室。提供英文 GUI 与面向 agent 的终端接口，支持二符号和多符号机器、独立实验归档、精确回放及证据验证。

## 功能

- 1–32 个工作状态、2–32 种整数符号（0–255），空白固定为 0，H 为停机状态。
- 每台机器一个 JSON 配置；每次实验独立保存参数、配置副本、源码、环境记录、检查点、轨迹与结果。
- 单步、连续运行、暂停、恢复，以及步数、活跃时间、非空格数量预算。
- 区分 `HALTED`、`NON_HALTING`、`UNKNOWN`；完整配置循环包含读写头位置和实际符号值。
- 纸带运动、状态与转移高亮、精确回放、时空图；GUI 导出 PNG，终端导出 SVG。
- agent 接口默认输出单个 JSON 对象，使用稳定退出码；支持配置校验与导入、批量实验、后台控制和证据验证。
- 兼容旧 schema-v1 二符号配置、行为摘要及历史检查点。

## 安装

当前启动脚本面向 Windows / PowerShell，需要 Conda。实验内核、终端接口与本地服务使用 Python 3.12；不需要 GPU、前端构建工具或外部 CDN。

在仓库根目录执行：

```powershell
$env:CONDA_PKGS_DIRS = Join-Path $PWD '.conda/pkgs'
conda env create --prefix ./.conda/env --file environment.yml
```

更新环境：

```powershell
conda env update --prefix ./.conda/env --file environment.yml
```

脚本直接调用仓库环境中的 Python，无需先激活。当前 Conda 24.11.3 的 `conda run` 对含括号的路径存在兼容问题，因此本项目不依赖该启动方式。

`environment.yml` 是依赖声明，`pip-freeze.txt` 保存当前 Python 包版本，`conda-win-64.lock.txt` 保存 Windows Conda 包的准确构建。该 Conda 锁文件仅适用于 win-64；其他平台需通过依赖声明重新创建环境。

## GUI

```powershell
./start-lab.ps1
```

打开 [本地实验室](http://127.0.0.1:8765)。默认选择二状态基准，勾选 **Start paused for single-step observation** 后点 **Create experiment**，通过 **Single step** 或 **Run / resume** 观察运行。

端口可配置：`./start-lab.ps1 -Port 8766`。页面和终端可以共享同一个服务；同一仓库只运行一个服务实例。

## Agent / 终端接口

短实验无需启动服务或浏览器：

```powershell
./bb-lab.ps1 capabilities
./bb-lab.ps1 machines list
./bb-lab.ps1 run tm5_three_symbols --max-steps 1000 --max-seconds 30
./bb-lab.ps1 batch halt_immediately three_symbol_cycle right_drifter --max-steps 1000
```

通过返回值中的运行 ID 查询、验证和导出：

```powershell
$response = ./bb-lab.ps1 run tm5_three_symbols --max-steps 1000 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw $response.error.message }
$runId = $response.data.run.run_id
./bb-lab.ps1 frame tm5_three_symbols $runId --step 1
./bb-lab.ps1 verify tm5_three_symbols $runId
./bb-lab.ps1 export tm5_three_symbols $runId
```

需要跨命令的后台运行与控制时，先启动本地服务，再使用：

```powershell
$response = ./bb-lab.ps1 --server http://127.0.0.1:8765 run tm5_three_symbols --max-steps 1000 --detach --start-paused | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw $response.error.message }
$runId = $response.data.run_id
./bb-lab.ps1 --server http://127.0.0.1:8765 control tm5_three_symbols $runId step
./bb-lab.ps1 --server http://127.0.0.1:8765 control tm5_three_symbols $runId resume
./bb-lab.ps1 --server http://127.0.0.1:8765 wait tm5_three_symbols $runId --wait-seconds 30
```

全局选项 `--server`、`--pretty` 必须位于子命令之前。除 `--help` 外，stdout 为单个 JSON 对象；`UNKNOWN` 是合法实验结论，退出码为 0。等待超时返回 6，不会取消实验。大整数坐标与计数使用十进制字符串。

完整说明：[终端接口](docs/agent-cli.md) · [agent 项目提示词](AGENT_PROMPT.md) · [agent 指令入口](AGENTS.md)。

## 配置与实验文件

多符号机器使用 schema-v2，转移表需覆盖每个状态与每种声明符号。初始非空白格使用 `initial.cells`，例如 `{"position":"-2","symbol":2}`；空列表代表空白纸带。可通过 GUI 创建模板，或使用 `machines template` 生成定义，再校验和导入。

```text
machines/<machine_id>.json
results/<machine_id>/<run_id>/
  machine.json            本次配置副本
  run.json                参数、版本与任务状态
  result.json             最终结论与统计
  snapshot.json           最近持久化配置
  checkpoints/            精确回放与恢复检查点
  traces/                 采样轨迹
  evidence/               证据及独立验证记录
  visualizations/         PNG / SVG 与采样元数据
  source/                 本次运行源码副本
```

环境声明和包清单也会复制到每次运行目录。`ones` 仅统计符号 1，`nonblank_count` 统计全部非空格，`symbol_counts` 分符号计数。空白 0 的总量无限，不报告有限总数。

实验结果、Conda 环境及缓存不提交 Git；仓库保存代码、配置、文档与依赖清单。重复实验创建新运行目录，修改机器行为使用新 ID。

## 内置基准

| 配置 | 状态 / 符号 | 已验证行为 |
|---|---|---|
| `halt_immediately` | 1 / 2 | 1 步停机 |
| `two_step_cycle` | 2 / 2 | 2 步完整配置循环 |
| `right_drifter` | 1 / 2 | 基础判定器下达到预算后保持未决 |
| `bb2_champion` | 2 / 2 | 6 步停机，留下 4 个 1 |
| `tm5_three_symbols` | 5 / 3 | 5 步停机，3 个非空格（1 个符号 1、2 个符号 2） |
| `three_symbol_cycle` | 4 / 3 | 4 步完整配置循环 |

仓库还包含五状态二符号演示、测试和冠军配置；配置的名称不替代实际运行结果或完整空间证明。

## 验证

```powershell
& '.\.conda\env\python.exe' -m unittest discover -s tests -v
```

当前 26 项测试覆盖二符号兼容、多符号运行、循环证据、恢复、回放、JSON 契约、退出码、并发导入防覆盖及无图形环境导出。

服务启动后，可运行 agent 接口真实联调：

```powershell
& '.\.conda\env\python.exe' tests/cli_service_smoke.py
```

GUI 的浏览器检查为可选，需要 Node.js、本地 Chrome 与测试工具：

```powershell
npm.cmd install --prefix .conda/browser-tools --cache .conda/npm-cache --no-audit --no-fund playwright-core
node tests/browser_smoke.cjs
```

联调和浏览器检查会创建独立实验记录。应用运行本身不需要这些浏览器测试依赖。

## 研究边界

预算耗尽、漂移或图形重复都不能当作不停机证明。当前判定器支持直接停机和完整配置循环；独立证据验证最多重放一百万步，超过预算标记为未验证。轨迹图可能采样，精确回放使用检查点重算，不补造中间配置。

当前后台使用线程管理最多四个实验；非空格预算是资源代理指标，并非操作系统级内存限制。CPU 多进程、Rust 加速、更强判定器、枚举覆盖审计和 GPU 实验属于后续阶段。批量跑一组机器不能证明海狸数，也不能解决一般停机问题。

详细设计及格式约定见 [实验室设计文档](docs/lab-design.md)。
