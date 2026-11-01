# 健康系统（2026-11-01 改版，v3）

> 生活运营下与恋爱系统平级的二级分区。目标不是「健身打卡」，而是
> **用普通人的语言记录睡眠、身体、饮食、运动 → 没更新时沿用上次 → 一个当前健康指数 → 私人医生基于最新数据问诊和写报告**。
> 私人数据全部落在 `02_生活运营/02_健康系统/`，应用状态落在 `<个人数据库>/.torch-os/health/`。

## 1. 七个页面

| 页面 | 作用 |
|---|---|
| 今日记录 | 当天/补记的四个入口：睡了多久、体重变化、饮食记录、运动记录；四维进度条与七天周卡片 |
| 身体情况 | 最新体重、体脂、肌肉量、腰围等；体重趋势与历史 |
| 睡眠情况 | 最新睡眠时长、入睡/起床、质量、睡眠评分；时长趋势与历史 |
| 饮食情况 | 最近一次饮食、餐次明细、热量与三大营养素、饮水；热量趋势 |
| 运动记录 | 本周运动分钟/次数/消耗、最近一次运动、步数；运动趋势；精细训练入口 |
| 健康档案 | 档案封面 + 基本档案 + 身体/睡眠/饮食/运动最新值 + 检查与用药 + 目标 + 自定义指标 + 报告 |
| 私人医生 | 页内问诊（读取健康系统数据）+ 固定「健康报告」本子（按日期归档） |

已删除的旧页面：今日总览、身体与体征、睡眠、饮食营养、日常活动、心理健康、训练、
体检与医疗、趋势看板、关联洞察、健康报告、设置。旧数据不删除，见第 7 节。

## 2. 数据位置

```text
02_生活运营/02_健康系统/
├── 00_健康报告/                 私人医生生成的健康报告；固定本子，不可删除
├── 01_每日健康/YYYY-MM-DD.md    一天一篇；frontmatter 存结构化数值，正文是可读摘要
├── 02_身体档案/身体档案（profile）.md
│                                姓名/性别/生日/身高/血型、病史/过敏/用药、目标、自定义指标
├── 03_体检与检验/检验记录（labs）.md
│                                日期/分类/项目/数值/单位/参考区间/异常
├── 04_训练记录/                 预留目录；训练明细仍在每日记录的 training 列表里
└── 05_问诊记录/YYYY-MM-DD 问诊记录.md
                                 私人医生每次问答自动追加；同一天同一篇
```

> 2026-11-01 起 `02_生活运营/03_体检与医疗` 退役：启动时内容并入
> `02_健康系统/03_体检与检验`，空目录删除；生活运营其余目录压紧编号：
> `03_证件与事务` / `04_财务报告` / `05_生活建议` / `06_家庭与智能家居`。
> 健康系统产生的所有私人数据都只在这一个目录下，不再散落到生活运营顶层。

页面与数据目录的对应关系（一个数据域只落一处，七页共享同一份权威记录）：

| 前端页面 | 数据目录 |
|---|---|
| 今日记录 | `01_每日健康/YYYY-MM-DD.md` |
| 睡眠情况 / 饮食情况 / 运动记录 | 同一篇 `01_每日健康/YYYY-MM-DD.md` 的 `sleep` / `nutrition` / `activity` + `training` |
| 身体情况 | `02_身体档案` + 每日 `body` |
| 健康档案 | 聚合 `02_身体档案`、`03_体检与检验`、每日记录与 `00_健康报告`（本身不另存副本） |
| 私人医生 / 健康报告 | `00_健康报告` |
| 私人医生问诊 | `05_问诊记录/YYYY-MM-DD 问诊记录.md`（每次问答自动追加） |
| 精细训练明细 | 每日记录的 `training`；`04_训练记录` 仅作未来独立训练文件的预留目录 |

每日记录 frontmatter（schema 仍为 `health-day-v1`，新字段向后兼容）：

```yaml
type: health_day
date: 2026-10-31
schema: health-day-v1
sleep: { bed: "23:40", wake: "07:10", duration_min: 450, quality: 4 }
body: { weight_kg: 108.8, body_fat_pct: 30.4, muscle_kg: 74.8, waist_cm: 102.8 }
nutrition: { food_text: "早上鸡蛋牛奶，中午鸡胸米饭", water_ml: 1900 }
activity: { steps: 8200, exercise_text: "跑步 1 小时，跑步机 500 大卡" }
meals: [ { id, meal_type, name, portion, calories, protein_g, carbs_g, fat_g, source } ]
training: [ { id, kind, minutes, calories, distance_km, steps, note, source, exercises } ]
custom: { 腰臀比: 0.9 }
note: 今天状态不错。
```

- **不再把健康指数写进 frontmatter**：评分是读取时算的派生值，避免 Obsidian 里
  看到的旧六维分数和新版四维分数对不上。
- 写入协议仍是：读 revision → 改内存 → 对比磁盘字节 → 临时文件 + `os.replace`；
  别处改过就 409，绝不覆盖。
- 有入睡/起床时间但没有时长时自动补 `duration_min`（跨午夜按 +24h）。
- 有餐次记录时热量/蛋白/碳水/脂肪以餐次合计为准；没有餐次时才用营养分区手填总量。
- 运动记录统一放在 `training` 列表里；自由描述放在 `activity.exercise_text`，
  步数放在 `activity.steps`。

## 3. 沿用规则（当天没更新，不显示空白）

每个分区在选定日期（含）之前找**最近一次非空记录**；只在读取时使用，**不会把旧数据
复制成今天的新记录**。前端显示「沿用 X 月 X 日」，并标出时效：

| 数据 | 有效期 | 过期后 |
|---|---|---|
| 睡眠 | 3 天 | 显示「该更新了」，不计入健康指数 |
| 身体（体重等） | 30 天 | 同上 |
| 饮食 | 7 天 | 同上 |
| 运动 | 14 天 | 同上 |

`overview.effective` 返回每项的 `{date, age, values, previous}`；`age` 是距选定日期的
天数，`previous` 是再往前一次，用于「比上次」。周卡片按周一至周日显示；点某一天只
切换选中日期、不自动移动周卡片；上一周/下一周/本周按钮和右上角日历负责换周。

## 4. 四维健康指数

只保留能在新界面录入的四项，权重默认：

| 维度 | 默认权重 | 计算（读取时） |
|---|---|---|
| 睡眠 | 30% | 时长达标 60% + 睡眠评分 40%（没有深睡/REM 时按剩余项归一化） |
| 身体 | 25% | BMI 50% + 体脂 30% + 腰围 20%；向目标体重移动 ±5 |
| 饮食 | 20% | 热量达标 40% + 蛋白达标 40% + 饮水 20% |
| 运动 | 25% | 近 7 天次数 35% + 分钟 35% + 步数 30%；只有步数时按步数单独算 |

- 缺数据的维度不参与加权，按剩余维度归一化，响应里用 `insufficient` 列出；
  **缺数据不当 0 分**。
- 每项带 `sources: {date, age, window, fresh, carried}`；过期项直接不计入。
- 旧档案只有六维权重（缺 `movement`）时，新版直接采用上面的默认权重。
- 评分只用于观察趋势，不是医学评估。私人医生报告里也会说明这一点。

## 5. 饮食 / 运动的 AI 估算

- 每日记录里的「吃了什么」「做了什么运动」是**一句话描述**，不是必须精确到热量。
- 点保存时如果只有描述、没有餐次/数字，会调用 dsh 的 `health_estimator` 角色估算：
  - 饮食：拆成 1–8 条食物，给份量、热量、蛋白、碳水、脂肪；
  - 运动：拆成 1–5 条运动，给类型、分钟、消耗、距离/步数。
- **AI 只提议**：返回可编辑的估算卡片，确认后才写入当天记录；也可以返回修改。
- 估算失败或不想估算时，可以直接保存文字；之后仍可补精确数字。
- 内置约 60 种常见食物（`health_catalog.FOOD_PRESETS`），可以搜索后点选，
  按常见份量填一份；用户仍可改数字。精确填写（餐次明细、训练组数）保留，但不是必填。
- 发送给 dsh 的内容只有这段描述和必要的档案估算上下文；不发送其他日记录。

## 6. 健康档案与私人医生

### 6.1 健康档案

`GET /api/health/archive` 聚合：档案、每个分区最近一次（不限时效，标明日期）、
检验记录、用药、目标、自定义指标、最新报告。页面是一张可滚动的档案：
封面、基本档案、身体/睡眠/饮食/运动、检查与用药、目标、自定义指标、报告。
基本档案、目标、检验、用药、自定义指标都可以在档案里编辑；每日数值仍去对应页面记录。

### 6.2 私人医生

- 页内聊天，读取健康系统摘要（档案、最近 90 天日记录、检验、报告标题），
  走 `health_doctor` 角色的 `advice` 模式；不诊断、不开药、不替代就医。
- 「生成健康报告」不再分周报/月报：基于最近 30 天记录 + 最新档案 + 检验记录，
  分析当前状态，生成一份报告。
- 报告落在 `00_健康报告/`，固定本子「健康报告」，标题格式 `YYYY-MM-DD 健康报告`
  （同一天重复生成会加 `（2）`）；单份报告可以删除，删除进
  `.torch-os/笔记回收站`，本子本身不可删除。
- 旧周报/月报保留为历史文档，不会自动删除。

## 7. 旧数据与兼容

- 旧 `vitals`（生命体征）和 `mind`（情绪/心理）字段**原样保留在 Markdown 里**，
  但新版界面不展示、不参与评分；如需彻底删除请手动处理。
- 旧 `activity` 步数、`training` 训练明细继续可读；新流程把自由描述和训练明细
  合并在同一页。
- 旧报告（周报/月报）继续在「健康报告」本子里可读。
- `overview.today.score` 仍保留旧的六维「当日评分」供趋势/兼容接口使用；
  新界面显示的是 `overview.current` 四维「当前健康指数」。

## 8. API

前置的 `{date}` 都是 `YYYY-MM-DD`；所有写接口在只读模式下返回 409。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health/overview?day=` | 今日记录：档案、目录、当日记录、`effective` 沿用、`current` 四维指数、周卡片、缺失项、检验、报告 |
| GET | `/api/health/week?day=` | 某一周的 7 天摘要（周一起） |
| GET | `/api/health/archive?day=` | 健康档案聚合 |
| GET | `/api/health/doctor` | 私人医生数据范围、报告与问诊记录列表 |
| GET | `/api/health/consults` | 问诊记录列表 |
| POST | `/api/health/consult` | `{question, answer, day?}` 追加到当天问诊记录 |
| DELETE | `/api/health/consults?path=&revision=` | 删除单份问诊记录（软删除到笔记回收站） |
| GET | `/api/health/day/{day}` | 某一天完整记录 + 旧六维当日评分 |
| PATCH | `/api/health/day/{day}` | `{revision, changes:{section:{field:value}}}`；`null` 删除字段 |
| PUT | `/api/health/day/{day}/diet` | `{revision, food_text, water_ml, meals}` 一次保存饮食 |
| PUT | `/api/health/day/{day}/exercise` | `{revision, exercise_text, steps, sessions}` 一次保存运动 |
| POST/DELETE | `/api/health/day/{day}/meals`、`.../meals/{id}` | 单条餐次（兼容旧调用） |
| POST/DELETE | `/api/health/day/{day}/training`、`.../training/{id}` | 单条训练（精细记录/兼容旧调用） |
| GET | `/api/health/foods?q=` | 常吃食物预设 |
| POST | `/api/health/estimate` | `{kind:"food"|"exercise", text}` → AI 估算结果（不落盘） |
| GET/PUT | `/api/health/profile` | 身体档案与目标；PUT 需要 `revision` |
| GET/POST/DELETE | `/api/health/labs`、`/api/health/labs/{id}` | 检验记录 |
| GET | `/api/health/history?days=&end=` | 一段时间的每日记录（图表/历史） |
| GET | `/api/health/reports` | 健康报告列表 |
| POST | `/api/health/report` | `{confirm:true, days:30}` 生成报告 |
| DELETE | `/api/health/reports?path=&revision=` | 删除单份报告（软删除到笔记回收站） |

## 9. 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_health.py tests\test_ops_layout.py tests\test_ai_roles.py -q
node --test frontend\tests\health-model.test.mjs
```

前端健康页的 CSP 约束仍在 `frontend/tests/health-model.test.mjs` 检查：
不写内联 `style=`、不写 `<style>` 块；图表由 `frontend/health-model.js` 生成 SVG。
