# 基准测试

后端（在仓库根目录）：
```bash
.venv/bin/python benchmarks/run.py            # 全部（recordings/media/settings/pose/api）
.venv/bin/python benchmarks/run.py api media  # 指定项
```

前端（在 frontend/ 目录）：
```bash
bun run build && bun run bench   # 产物体积 + 热路径函数吞吐
```

基线参考（WSL2 / Python 3.12 / 2026-09）：
- serialize_all(646 任务): ~1.3ms | GET /api/*: ~800 RPS（TestClient）
- 前端产物: ~834KB（gzip ~233KB），mpegts.js 按需加载
