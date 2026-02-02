## 本地前端界面（淘宝评论爬取）

### 1) 安装依赖

在 VS Code 终端（PowerShell）执行：

```bash
pip install -r requirements.txt
```

### 2) 启动服务

```bash
python -m uvicorn app:app --reload
```

### 3) 打开网页

浏览器打开：

`http://127.0.0.1:8000`

### 4) 登录说明（重要）

- 第一次运行任务时会弹出 Edge 窗口，请**手动登录淘宝一次**。
- 之后程序会复用 `桌面/淘宝评论_最终版/edge_profile` 来保持登录态。

### 5) 输出位置

- 每次任务的结果会保存到：
  - `桌面/淘宝评论_最终版/web_jobs/<job_id>/淘宝评论.xlsx`
  - `桌面/淘宝评论_最终版/web_jobs/<job_id>/淘宝评论.csv`

### 6) mBERT 建模与可视化（可选）

- 在网页里勾选“生成 mBERT 可视化”，任务完成后会额外生成：
  - `桌面/淘宝评论_最终版/web_jobs/<job_id>/mbert_vis.json`
- 页面右侧会出现“查看散点图（mBERT）”按钮，用 2D 散点展示评论语义分布（后端用 mBERT embedding + PCA 降维）。
- 注意：首次运行会从 HuggingFace 下载模型权重，**需要能访问外网**，且会比较慢。

