# GX APS 排产系统（多产线）

GX 工厂两阶段（成型+贴标）智能排产系统，支持 **3 条独立产线（L1/L2/L3）** 的协同展示（9 台机器甘特图），并集成 AI 自然语言交互。

## 核心概念

| 术语 | 定义 |
|------|------|
| **Container** | 以 `poreference` 标识的货柜，同一 Container 包含多个订单 |
| **订单完成时间** | 订单从 LABEL 机台下线的时间 |
| **货柜可交付时间** | Container 内最后一个订单完成的时间 |
| **交期 (duedate)** | 订单应交日期，在 duedate 当天 24:00 前完成即为准时 |
| **客户代码** | 订单 name 字段中第一个 `-` 之前的部分（如 DE#、SQ#） |

## 产线配置（摘要）

| 设备 | 机器 | 产能 |
|------|------|------|
| 成型机 | ROTARY-* | 按产线配置 |
| 贴标机 | LABEL-* | 按产线配置 |
| 换色时间 | - | 12 小时 |

SKU 与机器配置见 `问题.md` 与 `backend/process/line_config.json`。

## 快速开始

### 1. 安装依赖

```bash
# 后端
pip install -r backend/requirements.txt

# 前端
cd frontend && npm install
```

### 2. 配置环境变量

创建 `.env` 文件：

```bash
# LLM（OpenAI-compatible）
LLM_API_BASE=https://www.packyapi.com
LLM_API_KEY=your-api-key
LLM_MODEL=gemini-3-flash-preview
LLM_TEMPERATURE=1.0

# GX ERP（必需：拉取订单/库存快照）
GX_ERP_API_URL=http://<host>:<port>/api/v1/aps-gx
GX_ERP_TOKEN=your-token
GX_ERP_IS_TEST=true

# Postgres（可选；Railway 部署推荐）
DATABASE_URL=postgresql://user:pass@host:port/dbname

# 可选：是否在每次 reschedule 时自动同步 ERP（ERP 拉取可能较慢）
APS_AUTO_SYNC_ERP=false
```

前端（Next.js）需要知道后端 API 地址：

```bash
# Railway 部署时必填（指向后端服务 URL）
NEXT_PUBLIC_API_URL=https://<your-backend>.up.railway.app
```

如启用 Postgres（设置了 `DATABASE_URL`），先初始化表结构：

```bash
PYTHONPATH=backend alembic -c backend/alembic.ini upgrade head
```

## Railway 部署（推荐：前后端两个 Service）

后端（FastAPI）：
- Root Directory: `backend`
- 使用 `backend/railway.toml`（已内置 `preDeployCommand` 跑 Alembic + `startCommand` 启动 Uvicorn）
- 必需环境变量：`DATABASE_URL`、`LLM_API_BASE`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_TEMPERATURE`

前端（Next.js）：
- Root Directory: `frontend`
- 必需环境变量：`NEXT_PUBLIC_API_URL`（指向后端 Service 的公开 URL）

### 3. 准备数据

从 GX ERP 拉取快照（需要先启动后端）：

```bash
curl -X POST "http://localhost:8000/api/erp/sync?isTest=true"
# 生成：backend/process/orders_erp.json、backend/process/inventory_erp.json
```

### 4. 生成排产方案

```bash
# 推荐：生成 ALL（L1/L2/L3）合并排产 + 9 机台甘特图
PYTHONPATH=backend python backend/process/generate_all_lines_schedule.py --max-hours 8000

# 仅生成 L2（旧版单产线脚本，保留）
# PYTHONPATH=backend python backend/process/generate_schedule.py \
#     --start "2026-01-19 00:00" \
#     --out backend/process/schedule_result.json \
#     --chain-search-days 60
```

### 5. 生成甘特图

```bash
# multi-line 脚本会自动写入：
# - backend/process/schedule_result.json
# - backend/process/schedule_gantt.html
#
# 如需手动渲染（基于已有 schedule_result.json）：
PYTHONPATH=backend python backend/process/visualize_schedule.py --schedule backend/process/schedule_result.json --out backend/process/schedule_gantt.html
```

### 6. 启动 Web 服务（可选）

```bash
# 后端 API（端口 8000）
PYTHONPATH=backend uvicorn ai.api:app --reload --port 8000 --no-access-log

# 前端（端口 3000）
cd frontend && npm run dev
```

访问：`http://localhost:3000`

提示：
- 开发模式下可先手动同步一次 ERP 快照，再手动重建排产：
  - `curl -X POST "http://localhost:8000/api/erp/sync?isTest=true"`
  - `curl -X POST "http://localhost:8000/api/schedule/regenerate"`
- 如需对某个货柜/订单设置“加急/交期覆盖”（不改 ERP 快照），可在本地 overrides 中维护：
  - 示例文件：`backend/process/overrides.example.json`
  - 实际文件（本地）：`backend/process/overrides.json`（已加入 `.gitignore`）

## AI 交互功能

### 订单查询

| 示例 | 说明 |
|------|------|
| "查询 1218288 订单" | 按订单 ID 查询 |
| "DE#515894 订单什么时候完成？" | 按 PO 参考号查询 |
| "DE# 客户有哪些延迟订单？" | 按客户代码查询 |
| "哪些订单延迟了？" | 查询所有延期订单 |

### 货柜查询

| 示例 | 说明 |
|------|------|
| "SEC515910 货柜什么时候能交付？" | 查询货柜完成时间 |
| "这个货柜里有哪些订单？" | 查询货柜内订单 |
| "DE# 客户有哪些货柜？" | 按客户查询货柜 |

### 重新排产

| 示例 | 说明 |
|------|------|
| "1218288 订单必须在 2026-01-26 完成" | 修改单个订单交期并重排 |
| "锁定 SEC515910 货柜的产能" | 优先锁定单个货柜 |
| "锁定 SEC515910 和 SEC515911 两个货柜" | 多货柜优先锁定 |

### 方案对比

| 示例 | 说明 |
|------|------|
| "新方案对其他订单有什么影响？" | 对比重排前后差异 |
| "准时率变化多少？" | 查看 KPI 变化 |

## 界面布局

```
┌──────────────────────────────────────────────────────────────────┐
│  L2 排产系统                                                  ⚙️  │
├───────────┬──────────────────────────────────┬──────────────────┤
│ [+ 新对话] │  📊 甘特图预览          🔄  ⛶  │ ▼ Progress       │
│           │ ┌────────────────────────────┐  │ ☑ 任务完成       │
│ 💬 对话1  │ │    [嵌入式甘特图]          │  │                  │
│ 💬 对话2  │ └────────────────────────────┘  │ ▼ Artifacts      │
│           ├──────────────────────────────────┤ 📊 gantt.html    │
│           │  用户消息（气泡）                │ 📄 schedule.json │
│           │  AI 回复（无气泡）              │                  │
│           │  ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐  │ ▼ Context        │
│           │  │ >_ query_orders      ∨  │  │ 📦 orders.json   │
│           │  └─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘  │                  │
├───────────┼──────────────────────────────────┤                  │
│           │ [输入消息...]            [发送] │                  │
└───────────┴──────────────────────────────────┴──────────────────┘
```

## 数据格式

### 订单输入 (orders.json)

```json
{
  "data": [{
    "c_orderline_id": 123456,
    "poreference": "SEC515910",
    "sku": "S12G9C",
    "quantity": 5000,
    "duedate": "20/01/2026 00:00",
    "name": "DE#-DSB700c-S12G9C",
    "remark": "备注"
  }]
}
```

### 库存输入 (inventory.json)

```json
{
  "data": [{
    "materialcode": "S12G9C",
    "quantity": 10000
  }]
}
```

### KPI 输出

| 指标 | 说明 |
|------|------|
| `containers_on_time_rate` | 货柜准时交付率（甘特图显示为"准时率"） |
| `total_container_tardiness_days` | 货柜总延迟天数（甘特图显示为"延误天数"） |
| `on_time_rate` | 订单准时率 |
| `total_tardiness_h` | 订单总延迟（小时） |
| `setup_count` | 换色次数 |

> **注意**：甘特图顶部显示的"准时率"和"延误天数"均为 **Container 级别**指标。

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat` | POST | 同步对话 |
| `/api/chat/stream` | POST | SSE 流式对话 |
| `/api/schedule` | GET | 获取排产结果 |
| `/api/schedule/gantt` | GET | 获取甘特图 HTML |
| `/api/schedule/kpi` | GET | 获取 KPI 指标 |
| `/api/erp/orders` | GET | 从 ERP 拉取订单（支持 `?isTest=true`） |
| `/api/erp/inventory` | GET | 从 ERP 拉取库存（支持 `?isTest=true`） |
| `/api/erp/demand-history` | GET | 从 ERP 拉取历史需求（支持 `?isTest=true`） |
| `/api/erp/sync` | POST | 从 ERP 拉取并写入 `backend/process/orders_erp.json`、`backend/process/inventory_erp.json` |
| `/health` | GET | 健康检查 |

## 目录结构

```
├── backend/                    # Python 后端（FastAPI + 排产算法）
│   ├── process/                # 排产核心算法（多产线）
│   ├── ai/                     # AI 交互系统（API + Agent + Tools）
│   ├── alembic/                # 数据库迁移
│   ├── alembic.ini
│   ├── requirements.txt        # Python 依赖
│   ├── scripts/                # 后端脚本
│   └── tests/                  # 后端测试
├── frontend/                   # Next.js 前端
│   └── src/
│       ├── components/
│       │   ├── layout/         # 三栏布局
│       │   ├── workspace/      # 甘特图预览
│       │   ├── chat/           # 聊天组件
│       │   └── panels/         # 右侧面板
│       ├── hooks/
│       └── stores/
├── .env                        # 环境变量
└── docs/                       # 文档 & 方案记录
```

## 排产算法

### 成型工序
- 按 W → C → V 顺序生产，最小化换色次数
- 颜色切换需要 12 小时停机时间
- 自动搜索最优换色时间窗口（`--chain-search-days`）

### 贴标工序
- 两台贴标机并行调度
- 订单按 `(-priority, deadline, order_id)` 排序
- `priority=1` 表示优先锁定，始终最先处理
- 逐小时模拟，确保库存约束：`库存 ≥ 0`

### 优先锁定功能
支持将指定 Container 的所有订单设为最高优先级：
- 锁定的订单会被优先安排到最早的可用时间
- 可同时锁定多个 Container
- 自动生成对比报告显示影响范围
