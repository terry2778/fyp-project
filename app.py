from __future__ import annotations

import threading
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

from pc3 import TaobaoCommentCrawler, default_output_dir


@dataclass
class Job:
    id: str
    url: str
    role: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = "queued"
    error: Optional[str] = None
    output_dir: Path = field(default_factory=default_output_dir)
    excel_path: Optional[Path] = None
    csv_path: Optional[Path] = None
    mbert_vis_path: Optional[Path] = None
    summary: Optional[dict] = None


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
RUNNING_JOB_ID: Optional[str] = None


def _job_dir(output_dir: Path, job_id: str) -> Path:
    return output_dir / "web_jobs" / job_id


def _job_meta_path(output_dir: Path, job_id: str) -> Path:
    return _job_dir(output_dir, job_id) / "job.json"


def _save_job_meta(job: Job):
    try:
        d = _job_dir(job.output_dir, job.id)
        d.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": job.id,
            "url": job.url,
            "role": job.role,
            "created_at": job.created_at,
            "status": job.status,
            "error": job.error,
            "has_mbert": bool(job.mbert_vis_path),
        }
        _job_meta_path(job.output_dir, job.id).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_jobs_from_disk():
    """
    由于 uvicorn --reload 会重启进程，内存 JOBS 会丢失。
    这里在启动时扫描 output_dir/web_jobs/*/job.json，把历史任务恢复回来。
    """
    base = default_output_dir() / "web_jobs"
    if not base.exists():
        return
    for p in base.iterdir():
        if not p.is_dir():
            continue
        meta_path = p / "job.json"
        job_id = p.name
        # 兼容旧任务：没有 job.json 时，也从文件存在性恢复出来
        if not meta_path.exists():
            job = Job(
                id=job_id,
                url="",
                role="buyer",
                created_at=datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
                status="done" if (p / "淘宝评论.csv").exists() or (p / "淘宝评论.xlsx").exists() else "unknown",
                error=None,
            )
            job.output_dir = default_output_dir()
            job_dir = _job_dir(job.output_dir, job_id)
            job.excel_path = job_dir / "淘宝评论.xlsx"
            job.csv_path = job_dir / "淘宝评论.csv"
            job.mbert_vis_path = job_dir / "mbert_vis.json"
            if job.csv_path.exists():
                try:
                    job.summary = build_summary_from_csv(job.csv_path)
                except Exception:
                    job.summary = None
            JOBS[job_id] = job
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            job_id = str(meta.get("id") or p.name)
            job = Job(
                id=job_id,
                url=str(meta.get("url") or ""),
                role=str(meta.get("role") or "buyer"),
                created_at=str(meta.get("created_at") or datetime.now().isoformat(timespec="seconds")),
                status=str(meta.get("status") or "unknown"),
                error=meta.get("error"),
            )
            job.output_dir = default_output_dir()
            job_dir = _job_dir(job.output_dir, job_id)
            job.excel_path = job_dir / "淘宝评论.xlsx"
            job.csv_path = job_dir / "淘宝评论.csv"
            job.mbert_vis_path = job_dir / "mbert_vis.json"
            if job.csv_path.exists():
                try:
                    job.summary = build_summary_from_csv(job.csv_path)
                except Exception:
                    job.summary = None
            JOBS[job_id] = job
        except Exception:
            continue


class CreateJobRequest(BaseModel):
    url: HttpUrl
    role: str = "buyer"  # buyer / seller
    mbert: bool = False  # 是否生成 mBERT 2D 可视化数据


app = FastAPI(title="Taobao Review Crawler UI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 创建静态目录并挂载静态文件
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 启动时恢复历史任务，避免 reload 后前端"啥也没有"
_load_jobs_from_disk()


@app.get("/")
async def read_index():
    """返回前端页面"""
    html_path = static_dir / "index.html"
    if html_path.exists():
        html_content = html_path.read_text(encoding="utf-8")
        return HTMLResponse(content=html_content, status_code=200)
    else:
        # 如果 index.html 不存在，返回一个简单页面
        html_content = """
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>淘宝评论爬虫</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background: #0b1220;
                    color: white;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .container {
                    text-align: center;
                    padding: 40px;
                    background: rgba(255,255,255,0.06);
                    border-radius: 12px;
                    border: 1px solid rgba(255,255,255,0.12);
                }
                h1 {
                    color: #4f8cff;
                }
                p {
                    color: rgba(255,255,255,0.7);
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>淘宝评论爬虫</h1>
                <p>后端服务器已启动，但前端文件未找到。</p>
                <p>请确保已将 index.html 和 app.js 文件放入 static 目录中。</p>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)


@app.get("/api/ping")
def ping():
    return {"ok": True}


def _run_job(job_id: str):
    global RUNNING_JOB_ID
    job = JOBS[job_id]
    job.status = "starting"
    job_dir = job.output_dir / "web_jobs" / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    _save_job_meta(job)

    def on_status(s: str):
        job.status = s
        _save_job_meta(job)

    try:
        # 数据库配置 - 根据你的MySQL设置修改
        db_config = {
            'host': 'localhost',
            'port': 3306,
            'user': 'root',
            'password': '',  # 修改为你的密码
            'database': 'taobao_comments'
        }
        
        crawler = TaobaoCommentCrawler(
            target_url=str(job.url),
            output_dir=job_dir,
            max_comments=20,
            status_callback=on_status,
            keep_login_profile_dir=job.output_dir / "edge_profile",
            enable_mbert_viz=job.mbert_vis_path is not None,
            db_config=db_config  # 添加数据库配置
        )
        crawler.run()
        job.excel_path = crawler.excel_path
        job.csv_path = crawler.csv_path
        # mBERT 可视化文件（若开启）
        if hasattr(crawler, "mbert_vis_path") and crawler.mbert_vis_path and crawler.mbert_vis_path.exists():
            job.mbert_vis_path = crawler.mbert_vis_path
        # 任务完成后生成摘要（供前端直接展示）
        try:
            if job.csv_path and job.csv_path.exists():
                job.summary = build_summary_from_csv(job.csv_path)
        except Exception as e:
            job.summary = {"error": f"summary_failed: {e}"}
        if job.status not in {"no_comments", "error"}:
            job.status = "done"
        _save_job_meta(job)
    except Exception as e:
        job.status = "error"
        job.error = str(e)
        _save_job_meta(job)
    finally:
        with JOBS_LOCK:
            if RUNNING_JOB_ID == job_id:
                RUNNING_JOB_ID = None


@app.post("/api/jobs")
def create_job(req: CreateJobRequest):
    if req.role not in {"buyer", "seller"}:
        raise HTTPException(status_code=400, detail="role must be buyer or seller")

    # 由于复用 Edge profile + 固定 9222 端口，为避免冲突，这里限制同一时间只能跑 1 个任务
    global RUNNING_JOB_ID
    with JOBS_LOCK:
        if RUNNING_JOB_ID is not None:
            raise HTTPException(status_code=409, detail=f"another job is running: {RUNNING_JOB_ID}")

        job_id = uuid.uuid4().hex
        RUNNING_JOB_ID = job_id
        job = Job(id=job_id, url=str(req.url), role=req.role)
        # 预先设置输出路径，便于恢复
        job_dir = _job_dir(job.output_dir, job_id)
        job.excel_path = job_dir / "淘宝评论.xlsx"
        job.csv_path = job_dir / "淘宝评论.csv"
        if req.mbert:
            # 先把路径占位出来，表示此任务需要生成
            job.mbert_vis_path = job.output_dir / "web_jobs" / job_id / "mbert_vis.json"
        JOBS[job_id] = job
        _save_job_meta(job)

    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    t.start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job.id,
        "url": job.url,
        "role": job.role,
        "created_at": job.created_at,
        "status": job.status,
        "error": job.error,
        "has_xlsx": bool(job.excel_path and job.excel_path.exists()),
        "has_csv": bool(job.csv_path and job.csv_path.exists()),
        "has_mbert": bool(job.mbert_vis_path and job.mbert_vis_path.exists()),
        "has_summary": bool(job.summary),
    }


def build_summary_from_csv(csv_path: Path) -> dict:
    import pandas as pd
    from collections import Counter

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    n = int(len(df))

    # 情感统计
    sentiment_counts = {}
    if "情感倾向" in df.columns:
        vc = df["情感倾向"].fillna("").astype(str).value_counts()
        sentiment_counts = {k: int(v) for k, v in vc.items() if k}

    # 关键词统计（把每条评论的"关键词"按空格拆分）
    top_keywords = []
    if "关键词" in df.columns:
        tokens = []
        for s in df["关键词"].fillna("").astype(str).tolist():
            tokens.extend([t for t in s.split() if t])
        c = Counter(tokens)
        top_keywords = [{"word": w, "count": int(cnt)} for w, cnt in c.most_common(15)]

    # 属性关键词统计
    top_attributes = []
    if "属性关键词" in df.columns:
        tokens = []
        for s in df["属性关键词"].fillna("").astype(str).tolist():
            tokens.extend([t for t in s.split() if t])
        c = Counter(tokens)
        top_attributes = [{"word": w, "count": int(cnt)} for w, cnt in c.most_common(15)]

    # 主题分布统计
    top_topics = []
    if "主题" in df.columns:
        vc = df["主题"].fillna(-1).astype(int).value_counts()
        top_topics = [{"topic": int(k), "count": int(v)} for k, v in vc.items() if int(k) >= 0]

    # 预览（最多 30 条）
    preview_cols = [
        c
        for c in ["序号", "用户名", "评论时间", "购买信息", "评论内容", "清洗后评论", "属性关键词", "主题", "情感倾向", "情感分数"]
        if c in df.columns
    ]
    preview = df[preview_cols].head(30).fillna("").to_dict(orient="records") if preview_cols else []

    return {
        "n": n,
        "sentiment_counts": sentiment_counts,
        "top_keywords": top_keywords,
        "top_attributes": top_attributes,
        "top_topics": top_topics,
        "preview_cols": preview_cols,
        "preview": preview,
    }


@app.get("/api/jobs/{job_id}/result")
def get_job_result(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not job.csv_path or not job.csv_path.exists():
        raise HTTPException(status_code=404, detail="result not found")
    if not job.summary:
        job.summary = build_summary_from_csv(job.csv_path)
    return {"job_id": job.id, "summary": job.summary}


@app.get("/api/jobs/{job_id}/download/xlsx")
def download_xlsx(job_id: str):
    job = JOBS.get(job_id)
    if not job or not job.excel_path or not job.excel_path.exists():
        raise HTTPException(status_code=404, detail="xlsx not found")
    return FileResponse(path=str(job.excel_path), filename=job.excel_path.name)


@app.get("/api/jobs/{job_id}/download/csv")
def download_csv(job_id: str):
    job = JOBS.get(job_id)
    if not job or not job.csv_path or not job.csv_path.exists():
        raise HTTPException(status_code=404, detail="csv not found")
    return FileResponse(path=str(job.csv_path), filename=job.csv_path.name)


@app.get("/api/jobs/{job_id}/download/mbert")
def download_mbert(job_id: str):
    job = JOBS.get(job_id)
    if not job or not job.mbert_vis_path or not job.mbert_vis_path.exists():
        raise HTTPException(status_code=404, detail="mbert json not found")
    return FileResponse(path=str(job.mbert_vis_path), filename=job.mbert_vis_path.name)


# 创建静态文件（如果不存在）
def create_static_files():
    """创建静态目录和前端文件"""
    # 确保静态目录存在
    static_dir.mkdir(exist_ok=True)
    

    index_path = static_dir / "index.html"
    if not index_path.exists():
        pass
    

    js_path = static_dir / "app.js"
    if not js_path.exists():
        pass


# 启动时创建静态文件
create_static_files()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)


