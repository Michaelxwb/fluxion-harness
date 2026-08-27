"""ADR-WF-001 build-vs-buy PoC harness（TASK-001 落地）。

目录职责：
- test_adapter_invariants.py — B-01（unit）/ E-01（integration）契约验收
- test_harness_framework.py — PoC 断言框架自检（trace correlator / retention mock / 口径聚合）
- poc_workflow.py — 5-step workflow 定义 + 7 口径 + trace 断言框架（TASK-003/004/002 复用）
"""
