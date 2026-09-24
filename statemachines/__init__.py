"""State machine definitions for myERP domain entities.

2026-09-24 PR-3 重构：本 package 不再 eager-import 子模块，避免触发 dormant
state machine（`PartStateMachine` / `OutsourceQuoteStateMachine`）的 dormant
model 依赖（`TPartEvent` / `TOutsourceQuoteEvent` 等已删除）。

活跃 state machine 由 `model.*.sm` 属性 lazy import：
- `statemachines.assembly::AssemblyStateMachine` — `model.assembly.TAssembly.sm`
- `statemachines.part::PartStateMachine` — `model.part.TPart.sm` /
  `model.part_batch.TPartBatch.sm`（历史复用）
- `statemachines.delivery_note::DeliveryNoteStateMachine` —
  `model.delivery_note.TDeliveryNote.sm`（历史）
"""
