# myERP Frontend

基于 **Vite + Vue 3 + TypeScript + Element Plus** 的后台管理系统前端。

## 功能

- 左侧菜单栏（可展开/收起）
- 顶栏：面包屑 + 用户头像下拉
- 零件一览：条件查询区可折叠，表格列排序，库存预警，新增/查看/编辑/删除

## 主题色

藏青 `#1e4d8b` · 蓝 `#2c6cb8` · 浅蓝 `#4a8fd6` · 灰 `#f5f7fa` · 白 `#ffffff`

## 启动

```bash
cd frontend
npm install
npm run dev          # 开发 http://localhost:5173
npm run typecheck    # vue-tsc 类型检查
npm run build        # 构建
```

## 目录结构

```
src/
├── api/             # 接口 & mock（.ts）
├── layouts/         # 布局组件
├── router/          # 路由（含 RouteMeta 类型扩展）
├── styles/          # 全局样式 & 主题变量
├── types/           # 业务/组件类型
├── views/           # 页面
│   ├── parts/       # 零件模块
│   └── Dashboard.vue
├── App.vue
├── env.d.ts
└── main.ts
```

## 后端联调

`vite.config.js` 已配置 `/api` 代理到 `http://127.0.0.1:8000`（对应项目根目录的 FastAPI 后端）。
