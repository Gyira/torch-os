# 健康系统（2026-10-31）

> 生活运营下与恋爱系统平级的二级分区。目标不是「健身记录」，而是
> **低成本记录全维度数据 → 每日聚合 → 跨维度关联洞察**；私人数据全部落在
> `02_生活运营/02_健康系统/`，应用状态落在 `<个人数据库>/.torch-os/health/`。

## 1. 为什么不用上一版的 Prisma / Supabase 方案

设计稿假设的是独立 Next.js 应用（Prisma + PostgreSQL + Edge Function）。torch-os
的事实是：

- 权威源是 Vault 里的 Markdown，SQLite 只是索引/投影，删掉可以重建；
- 单人、本地、离线优先，没有多用户并发，也不需要云端聚合任务；
- 四库是唯一结构，健康数据如果另起一套数据库，就会出现「数据在哪」的二义性。

所以这一版保留设计稿的三条主线，但换成当前技术栈：

| 设计稿 | 这里的实现 |
|---|---|
| `MetricDefinition` / `MetricEntry` 可配置指标 | `backend/app/health_catalog.py` 里的声明表 + 每日 Markdown 的 `section.field`；前端目录由 `GET /api/health/catalog` 下发 |
| `DailySummary` 每日聚合表 | 不落第二份事实：每次读取由 `health_score.score_day()` 现场计算；写入时把评分写回 Markdown frontmatter，方便在 Obsidian 里直接看到 |
| Supabase Edge Function / pg_cron | 无定时任务：记录发生时才计算；洞察在打开页面时按需计算 |
| Recharts | 原生 SVG 图表（`frontend/health-model.js`，纯函数、可单测） |

新增一个指标：在 `health_catalog.py` 里加一行 `_metric(...)`，表单、趋势选择器、
评分与总览会同时出现该指标——不改 schema、不迁移数据库。用户自己的指标走
「设置 → 自定义指标」，记录写进每日笔记的 `custom` 映射，趋势里可以选。

## 2. 数据位置（全部在 `02_生活运营/02_健康系统/`）

```text
02_生活运营/02_健康系统/
├── 00_健康报告/                 私人医生生成的周报/月报（AI 产物，memory: false）
├── 01_每日健康/YYYY-MM-DD.md    一天一篇；frontmatter 存结构化数值，正文是可读摘要
├── 02_身体档案/身体档案（profile）.md
│                                身高/生日/性别、病史/过敏/用药、目标、自定义指标、评分权重
├── 03_体检与检验/检验记录（labs）.md
│                                一张 Markdown 表：日期/分类/项目/数值/单位/参考区间/异常
└── 04_训练记录/                 预留目录；训练明细暂存在每日记录的 training 列表里
```

每日记录的 frontmatter（schema `health-day-v1`）：

```yaml
type: health_day
date: 2026-10-31
schema: health-day-v1
score: 82
score_breakdown: { sleep: 86, nutrition: 74, activity: 79 }
sleep:   { bed: "23:40", wake: "07:10", duration_min: 450, quality: 4 }
body:    { weight_kg: 73.4, body_fat_pct: 18.2 }
vitals:  { resting_hr: 58, systolic: 118, diastolic: 76 }
nutrition: { water_ml: 2200, caffeine_mg: 80 }
activity: { steps: 8200, sedentary_min: 300 }
mind:    { mood: 4, stress: 2, energy: 3, mindful_min: 10 }
meals:   [ { id, meal_type, name, calories, protein_g, carbs_g, fat_g } ]
training: [ { id, kind, minutes, rpe, soreness, mood_after, volume_kg,
              exercises: [ { exercise_id, name, sets: [ { reps, weight_kg } ] } ] } ]
custom:  { 腰臀比: 0.86 }
note: 今天状态不错。
```

- 正文由后端按数据重写（人读摘要），**不承载第二份事实**；在 Obsidian 里改
  frontmatter 合法，下一次写入会按新值重算并重写正文。
- 写入协议与 `markdown_store` 一致：读 revision → 改内存 → 比对磁盘字节 →
  临时文件 + `os.replace`；别处改过就 409，绝不覆盖。
- 有入睡/起床时间但没有时长时自动补 `duration_min`（跨午夜按 +24h）。
- 餐次与营养分区的关系：**有餐次记录时热量/蛋白/碳水/脂肪以餐次合计为准**；
  饮水、咖啡因、酒精始终取营养分区。没有餐次时才用营养分区手填的总量。

应用状态：

- `.torch-os/health/insights.json`：洞察已读 id（不是个人健康数据，只是界面状态）；
- 与 `.torch-os/love/` 同层，不进 Vault 扫描，也不会被普通备份快照单独带走。

## 3. 评分与洞察

### 3.1 睡眠评分（0–100）

时长 40% + 深睡占比 25% + REM 占比 15% + 入睡耗时 10% + 主观质量 10%。
缺失的分项按剩余权重归一化；没有时长就没有睡眠评分。

### 3.2 健康指数（六维）

| 维度 | 计算 | 缺数据 |
|---|---|---|
| 睡眠 | 时长达标 60% + 睡眠评分 40% | 无时长 → 不参与 |
| 营养 | 热量达标 40% + 蛋白达标 40% + 饮水 20% | 全缺 → 不参与 |
| 活动 | 步数达标 70% + 久坐惩罚 30% | 无步数 → 不参与 |
| 心理 | 情绪 50% + 压力反向 30% + 正念 20% | 全缺 → 不参与 |
| 身体 | BMI 50% + 体脂 30% + 腰围 20%，向目标体重移动 ±5 | 无体重 → 不参与 |
| 训练 | 近 7 天次数 70% + 容量趋势 30% | 记录 <3 天且无训练 → 不参与 |

综合分按仍在的维度加权归一化（默认权重 sleep .25 / nutrition .2 / activity .2 /
mental .15 / body .1 / training .1）。**缺数据不当 0 分**，响应里用
`insufficient` 明确列出。评分只用于观察趋势，不是医学评估。

### 3.3 关联洞察

`health_score.insights()` 内置 5 类规则：睡眠→次日训练、步数→情绪、
蛋白→体重（Pearson）、训练→次日静息心率、工作日/周末睡眠差。
硬门槛：总记录 ≥7 天，每组样本 ≥5（蛋白相关 ≥14 对）；不满足就不生成。
每条带 `sample_size` 与日期范围；相关性不写成因果。

## 4. API

前置的 `{date}` 都是 `YYYY-MM-DD`；所有写接口在只读模式下返回 409。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health/catalog` | 指标目录（分组/单位/范围/步长/评分档位） |
| GET | `/api/health/overview?day=` | 今日总览：档案、目录、今日记录+评分、最近 7 天、目标、缺失项、检验异常、最近报告 |
| GET | `/api/health/day/{day}` | 某一天的完整记录 + 评分 + 目标 |
| PATCH | `/api/health/day/{day}` | `{revision, changes:{section:{field:value}}}`；`null` 删除字段 |
| GET | `/api/health/history?days=&end=` | 一段时间的每日记录（训练历史/列表用） |
| POST | `/api/health/day/{day}/meals` | 添加餐次（热量/宏量自动合计） |
| DELETE | `/api/health/day/{day}/meals/{id}` | 删除餐次 |
| POST | `/api/health/day/{day}/training` | 添加训练（组记录，自动算容量） |
| DELETE | `/api/health/day/{day}/training/{id}` | 删除训练 |
| GET/PUT | `/api/health/profile` | 身体档案与目标；PUT 需要 `revision` |
| GET | `/api/health/labs` | 检验记录 + 异常计数 + 预设项目 |
| POST | `/api/health/labs` | 添加一条检验；带 `revision` 乐观锁 |
| DELETE | `/api/health/labs/{id}?revision=` | 删除一条检验 |
| GET | `/api/health/trends?metrics=a,b&days=&end=` | 任意数值指标 + `score.total`/`sleep.score`/`body.bmi`/`training.*`/`custom.*` |
| GET | `/api/health/insights?days=` | 规则洞察 + 已读状态 |
| POST | `/api/health/insights/read` | `{id, read}` 标记已读/未读 |
| GET | `/api/health/exercises?q=&body=&equipment=` | 本地动作库搜索（文本复用 openGym） |
| GET | `/api/health/reports` | 已保存的健康报告 |
| POST | `/api/health/report` | `{kind:"week"|"month", confirm:true}`；私人医生生成并落盘 |

## 5. 私人医生（AI 角色）

- 角色卡 `dsh/roles/health_doctor.md`，id `health_doctor`，名称 **私人医生**，
  与其它角色共用 dsh 网关与角色抽屉；
- `report` 模式：读健康系统摘要，输出周报/月报 JSON；`advice` 模式：页面里的
  对话，先结论再给最多三条调整；
- 页面按钮「私人医生」打开角色抽屉，默认选择器 `[{"entry":"health"}]`；
- 摘要来自 `health_store.summary()`（最近 30/90 天 + 档案 + 检验异常），
  **不发送原始每日正文**，也不进入记忆候选（报告 `memory: false`）；
- AI 不可用时报告返回 409，记录与图表不受影响。

## 6. 前端

- 入口：生活运营 →「健康系统」二级分区（与「恋爱系统」平级）；顶部 hash `#health`
  也能直达；
- 12 页：今日总览、身体与体征、睡眠、饮食营养、日常活动、心理健康、训练、
  体检与医疗、趋势看板、关联洞察、健康报告、设置；
- 图表是 `frontend/health-model.js` 里的纯 SVG 字符串生成器（可 `node --test`），
  页面只负责取数与拼装；**不使用内联 style**（工作台 CSP `style-src 'self'`），
  进度条用原生 `<progress>`，圆环用 SVG 表现属性；
- 动作库：`backend/app/health_assets/exercises.json`（6554 条 openGym 文本中保留 5526 条
  唯一名称，中文优先，附部位/器械/目标肌群/一句描述）。**不包含 openGym 的图片
  与动画**，那是它自己的授权素材；需要重建时运行
  `python scripts/import_opengym_exercises.py`。

## 7. 边界与未做

- 不做诊断、不开药；检验异常只按参考区间提示，持续异常建议线下就医；
- 不做体检报告 PDF/图片解析与 OCR；报告附件仍可放 `03_体检与医疗` 或用
  Obsidian 附件；
- 不做训练计划（Plan/PlanDay/PlanExercise）与组间休息计时；当前是「记录」不是
  「教练」；
- 不做 CSV 批量导入、进度照片时间轴、Web Notification 提醒；下一阶段再加；
- 旧 `01_健康与体重/YYYY-MM.md` 月度表不再写入；`/api/ops/health/*` 仍然可读
  （兼容旧库），但新数据只进 `02_健康系统`；
- 医疗数据不因导出/备份而特殊排除：它和四库其它 Markdown 一样在本机库里；
  导出功能若需要「排除医疗」开关，需在导出层单独实现（当前未做）。

## 8. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_health.py -q
node --experimental-vm-modules --test frontend/tests/health-model.test.mjs
```

后端覆盖：Markdown 落盘路径、分区校验、revision 冲突、餐次合计、训练容量、
档案/目标、BMI/BMR、缺失维度剔除、检验异常、摘要、总览/趋势/洞察/报告 API、
只读 409、动作库搜索。前端覆盖：格式化、日期、指标提取、图表转义、评分档位、
CSP（无内联样式）与 `<progress>`/圆环输出。
