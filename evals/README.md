# Evals（PRD v1.15）

## 指标
- step 成功率（分域，目标 ≥90%；取消/L2 拒绝剔出失败分母）
- 首句播报时延（live 记录；体验向 P95&lt;1.5s）
- 安全误执行 = 0（一票否决）
- 门控 / 路由 / nav_route_started / Trace 覆盖

## 冒烟集
见 `scenarios/smoke.yaml`：手册双用例、笑话、日程、MQTT 零帧、L2 零执行边、L1 规划形态、Profile 门闩标签。

## 跑法
```bash
# CI / 无 Docker
python -m evals.runner --mode offline
pytest evals/test_smoke_offline.py -q

# 本机 Agent 已起
python -m evals.runner --mode live --base-url http://localhost:8000
```

报告：`evals/reports/smoke_*.json`


## 全量 UI 场景集
页面芯片已导入 `scenarios/full/{vehicle,navigation,media,calendar,knowledge,chitchat}.yaml`（约 58+ 条）。
- 冒烟：`smoke.yaml` → CI
- 全量：nightly / `python -m evals.runner --mode live`（扩展 runner 读 full 时再用）
- `expect.todo`：action 名需对照 Planner 再校准


## Golden（PRD v1.17）
- 首批：`scenarios/golden.yaml`（16 条，`tags: [golden]`）
- 发布读数：`golden ∩ live` 的云端工具调用成功率 + 安全误执行 0
- smoke / golden / full 三层分离

```bash
python -m evals.runner --suite golden --mode offline
python -m evals.run_full --suite golden --mode offline
pytest evals/test_golden_offline.py -q

# 本机 Agent 已起
python -m evals.runner --suite golden --mode live --base-url http://localhost:8000
```

报告：`evals/reports/golden_*.json`
