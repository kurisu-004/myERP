"""2026-09-17 重构：v1 业务路由下线后，schema 子包不再跨模块做 forward-ref
rebuild（`PartBatchTree*` 等历史 cross-ref 随 `schema.part` /
`schema.assembly` 一并移至 `_archive/schema/`）。这里只保留占位 export。"""