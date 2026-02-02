"""数据清洗、关键词提取、情感分析模块"""
from __future__ import annotations

import re
import math
import json
import pymysql
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

try:
    import jieba
    import jieba.analyse
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

try:
    from snownlp import SnowNLP
    SNOWNLP_AVAILABLE = True
except ImportError:
    SNOWNLP_AVAILABLE = False

# ==================== 数据库功能 ====================

def connect_to_database(host='localhost', port=3306, user='root', 
                       password='', database='taobao_comments'):
    """连接到MySQL数据库"""
    try:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        print(f"✅ 数据库连接成功: {host}:{port}/{database}")
        return connection
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return None

def save_raw_to_db(connection, item_id: str, url: str, comments: List[Dict]):
    """保存原始数据到raw_reviews表"""
    if not connection or not comments:
        return 0
    
    try:
        with connection.cursor() as cursor:
            inserted_count = 0
            for comment in comments:
                sql = """
                INSERT INTO raw_reviews 
                (item_id, original_url, seq_number, username, comment_content, 
                 comment_time, purchase_info, crawl_time, data_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                """
                cursor.execute(sql, (
                    item_id,
                    url,
                    comment.get('序号', 0),
                    comment.get('用户名', '匿名用户'),
                    comment.get('评论内容', ''),
                    comment.get('评论时间', ''),
                    comment.get('购买信息', ''),
                    'api'  # 数据来源
                ))
                inserted_count += 1
            
            connection.commit()
            print(f"✅ 已保存 {inserted_count} 条原始数据到 raw_reviews 表")
            return inserted_count
    except Exception as e:
        print(f"❌ 保存原始数据失败: {e}")
        connection.rollback()
        return 0

def save_processed_to_db(connection, item_id: str, comments: List[Dict]):
    """保存处理后的数据到processed_reviews表"""
    if not connection or not comments:
        return 0
    
    try:
        with connection.cursor() as cursor:
            inserted_count = 0
            
            for comment in comments:
                # 首先查找对应的原始评论ID
                cursor.execute(
                    "SELECT id FROM raw_reviews WHERE item_id = %s AND seq_number = %s ORDER BY id DESC LIMIT 1",
                    (item_id, comment.get('序号', 0))
                )
                raw_review = cursor.fetchone()
                raw_review_id = raw_review['id'] if raw_review else None
                
                sql = """
                INSERT INTO processed_reviews 
                (raw_review_id, item_id, seq_number, username, original_content, 
                 cleaned_content, full_cleaned_content, attribute_keywords, keywords,
                 sentiment, sentiment_score, comment_time, purchase_info, topic,
                 topic_distribution, process_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                cursor.execute(sql, (
                    raw_review_id,
                    item_id,
                    comment.get('序号', 0),
                    comment.get('用户名', '匿名用户'),
                    comment.get('评论内容', ''),
                    comment.get('清洗后评论', ''),
                    comment.get('清洗全文', ''),
                    comment.get('属性关键词', ''),
                    comment.get('关键词', ''),
                    comment.get('情感倾向', ''),
                    comment.get('情感分数', 0.0),
                    comment.get('评论时间', ''),
                    comment.get('购买信息', ''),
                    comment.get('主题', ''),
                    json.dumps(comment.get('主题分布', [])) if comment.get('主题分布') else None
                ))
                inserted_count += 1
            
            connection.commit()
            print(f"✅ 已保存 {inserted_count} 条处理后数据到 processed_reviews 表")
            return inserted_count
    except Exception as e:
        print(f"❌ 保存处理后数据失败: {e}")
        connection.rollback()
        return 0

def save_analysis_to_db(connection, item_id: str, comments: List[Dict], 
                       topic_model=None, mbert_viz=None):
    """保存分析结果到analysis_results表"""
    if not connection:
        return False
    
    try:
        # 计算统计信息
        total = len(comments)
        positive = sum(1 for c in comments if c.get('情感倾向') == '正')
        negative = sum(1 for c in comments if c.get('情感倾向') == '负')
        neutral = total - positive - negative
        
        # 提取关键词统计
        all_keywords = []
        for comment in comments:
            keywords = comment.get('关键词', '')
            if keywords:
                all_keywords.extend([kw.strip() for kw in keywords.split(' ') if kw.strip()])
        
        keyword_freq = {}
        for keyword in all_keywords:
            keyword_freq[keyword] = keyword_freq.get(keyword, 0) + 1
        
        top_keywords = dict(sorted(keyword_freq.items(), key=lambda x: x[1], reverse=True)[:10])
        
        # 构建分析数据
        analysis_data = {
            'sentiment_summary': {
                'total': total,
                'positive': positive,
                'negative': negative,
                'neutral': neutral,
                'positive_rate': round(positive/total*100, 2) if total > 0 else 0,
                'negative_rate': round(negative/total*100, 2) if total > 0 else 0,
                'neutral_rate': round(neutral/total*100, 2) if total > 0 else 0
            },
            'keyword_analysis': {
                'total_keywords': len(all_keywords),
                'unique_keywords': len(set(all_keywords)),
                'top_keywords': top_keywords
            },
            'topic_analysis': topic_model.__dict__ if topic_model else {},
            'mbert_viz': mbert_viz.meta if mbert_viz else {},
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        with connection.cursor() as cursor:
            # 检查是否已有记录
            cursor.execute(
                "SELECT id FROM analysis_results WHERE item_id = %s",
                (item_id,)
            )
            existing = cursor.fetchone()
            
            if existing:
                # 更新现有记录
                sql = """
                UPDATE analysis_results 
                SET total_comments = %s, positive_count = %s, negative_count = %s,
                    neutral_count = %s, top_keywords = %s, analysis_data = %s,
                    update_time = NOW()
                WHERE item_id = %s
                """
                cursor.execute(sql, (
                    total, positive, negative, neutral,
                    json.dumps(top_keywords, ensure_ascii=False),
                    json.dumps(analysis_data, ensure_ascii=False),
                    item_id
                ))
            else:
                # 插入新记录
                sql = """
                INSERT INTO analysis_results 
                (item_id, total_comments, positive_count, negative_count,
                 neutral_count, top_keywords, analysis_data, create_time, update_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """
                cursor.execute(sql, (
                    item_id, total, positive, negative, neutral,
                    json.dumps(top_keywords, ensure_ascii=False),
                    json.dumps(analysis_data, ensure_ascii=False)
                ))
            
            connection.commit()
            print(f"✅ 分析结果已保存到 analysis_results 表")
            return True
            
    except Exception as e:
        print(f"❌ 保存分析结果失败: {e}")
        connection.rollback()
        return False

def clean_text(text: str) -> str:
    """清洗评论文本：去除广告、emoji、多余空白、无意义符号"""
    if not text or not isinstance(text, str):
        return ""
    s = text.strip()
    # 去除常见广告/营销词（保留正常表述如“在淘宝买的”）
    ads = [
        r"https?://[^\s]+",
        r"【[^】]{2,30}】",  # 方括号广告
        r"加微信[：:]\s*[a-zA-Z0-9_-]+",
        r"QQ[：:]\s*\d{5,}",
        r"微信[：:]\s*[a-zA-Z0-9_-]{6,}",
        r"好评返现|晒图返现|追评返现|联系客服领红包",
    ]
    for p in ads:
        s = re.sub(p, "", s, flags=re.IGNORECASE)
    # 去除 emoji 及特殊符号（保留中文、英文、数字、常用标点）
    s = re.sub(r"[^\u4e00-\u9fff\u3000-\u303fa-zA-Z0-9\s，。！？、；：""''（）\-\.]", "", s)
    # 合并多余空白
    s = re.sub(r"\s+", " ", s).strip()
    return s[:500]


def extract_keywords(text: str, top_k: int = 5) -> str:
    """从评论文本中提取关键词（TF-IDF）"""
    if not JIEBA_AVAILABLE or not text or len(text.strip()) < 4:
        return ""
    try:
        words = jieba.analyse.extract_tags(text, topK=top_k, withWeight=False)
        return " ".join(words) if words else ""
    except Exception:
        return ""


# 简单情感词表（可按需要继续扩充）
_POS_WORDS = {
    "好", "很好", "不错", "满意", "喜欢", "推荐", "值得", "划算", "超值", "棒", "赞", "惊喜",
    "漂亮", "好看", "合适", "舒适", "舒服", "柔软", "香", "新鲜", "正品", "真实", "靠谱",
    "快", "很快", "及时", "给力", "贴心", "周到", "耐心", "专业",
}
_NEG_WORDS = {
    "差", "很差", "失望", "不满意", "不喜欢", "不推荐", "不值", "坑", "垃圾", "糟糕", "一般",
    "慢", "很慢", "破", "坏", "漏", "少", "缺", "假", "劣质", "刺鼻", "异味", "脏",
    "生气", "无语", "退货", "投诉",
}


def _load_sentiment_lexicon() -> tuple[set[str], set[str]]:
    """
    从同目录的 sentiment_lexicon.json 加载可扩展情感词表，并与内置词表合并。
    文件格式：
      { "pos": ["好", ...], "neg": ["差", ...] }
    """
    try:
        path = Path(__file__).parent / "sentiment_lexicon.json"
        if not path.exists():
            return _POS_WORDS, _NEG_WORDS
        data = json.loads(path.read_text(encoding="utf-8"))
        pos = set(w.strip() for w in (data.get("pos") or []) if isinstance(w, str) and w.strip())
        neg = set(w.strip() for w in (data.get("neg") or []) if isinstance(w, str) and w.strip())
        return _POS_WORDS.union(pos), _NEG_WORDS.union(neg)
    except Exception:
        return _POS_WORDS, _NEG_WORDS


# 可扩展情感词表（启动时加载一次）
POS_WORDS, NEG_WORDS = _load_sentiment_lexicon()


def extract_sentiment_words(text: str) -> str:
    """
    从清洗后的文本里只保留“情感词关键词”（正/负向词）。
    返回格式：正面：好、满意、快；负面：差、慢（分门别类，便于阅读）
    """
    s = (text or "").strip()
    if not s:
        return ""

    pos_found: list[str] = []
    neg_found: list[str] = []

    if JIEBA_AVAILABLE:
        try:
            for w in jieba.cut(s, cut_all=False):
                w = (w or "").strip()
                if not w:
                    continue
                if w in POS_WORDS:
                    pos_found.append(w)
                elif w in NEG_WORDS:
                    neg_found.append(w)
        except Exception:
            pos_found, neg_found = [], []

    # jieba 不可用或没切出：兜底用子串匹配
    if not pos_found and not neg_found:
        for w in POS_WORDS.union(NEG_WORDS):
            if w and w in s:
                if w in POS_WORDS:
                    pos_found.append(w)
                else:
                    neg_found.append(w)

    # 去重并保序
    def _uniq(lst):
        seen = set()
        out = []
        for x in lst:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    pos_uniq = _uniq(pos_found)
    neg_uniq = _uniq(neg_found)

    parts = []
    if pos_uniq:
        parts.append("正面：" + "、".join(pos_uniq))
    if neg_uniq:
        parts.append("负面：" + "、".join(neg_uniq))
    return "；".join(parts) if parts else ""


def extract_attribute_keywords(text: str, top_k: int = 6) -> str:
    """
    提取“商品属性关键词”（偏名词/名词短语），用于回答“大家主要在讨论什么属性”。
    - 依赖 jieba.posseg；如果不可用则退化为 TF-IDF 关键词
    """
    s = (text or "").strip()
    if not s:
        return ""

    # 常见泛化停用词（可按需要扩展）
    stop = {
        "东西", "产品", "宝贝", "卖家", "店家", "客服", "物流", "包装",
        "这个", "那个", "真的", "感觉", "还是", "就是", "非常", "比较", "有点",
        "一样", "一般", "可以", "不错", "满意", "喜欢",
    }

    if JIEBA_AVAILABLE:
        try:
            import jieba.posseg as pseg

            cands: list[str] = []
            for w, flag in pseg.cut(s):
                w = (w or "").strip()
                if not w or len(w) < 2:
                    continue
                if w in stop:
                    continue
                # n:名词 nr/ns/nt 等, vn:名动词
                if flag.startswith("n") or flag in {"vn"}:
                    cands.append(w)
            # 简单计数取 TopK
            if cands:
                from collections import Counter

                cnt = Counter(cands)
                return " ".join([w for w, _ in cnt.most_common(top_k)])
        except Exception:
            pass

    # 兜底：用 TF-IDF 关键词
    return extract_keywords(s, top_k=top_k)


@dataclass
class TopicModelResult:
    topics: list[dict]          # [{topic_id, keywords:[...], weight}]
    doc_topics: list[int]       # 每条评论的主题 id（与输入等长）
    doc_topic_scores: list[str] # 每条评论的主题分布（压缩成字符串，便于导出）


def build_topic_model(
    comments: list[dict],
    *,
    text_key: str = "清洗全文",
    fallback_text_key: str = "评论内容",
    n_topics: int = 6,
    top_words: int = 10,
    seed: int = 42,
) -> TopicModelResult:
    """
    多维主题模型（NMF on TF-IDF）：
    - 输出每条评论的主题标签（topic_id）
    - 输出每条评论的主题分布（Top3 主题:权重）
    - 输出每个主题的关键词
    """
    items = comments or []
    if not items:
        return TopicModelResult(topics=[], doc_topics=[], doc_topic_scores=[])

    # 1) 准备语料：用 jieba 分词后用空格拼接，方便 sklearn 处理
    docs: list[str] = []
    for c in items:
        t = (c.get(text_key) or "").strip()
        if not t:
            t = (c.get(fallback_text_key) or "").strip()
        t = (t or "").strip()
        if not t:
            docs.append("")
            continue
        if JIEBA_AVAILABLE:
            try:
                toks = [x.strip() for x in jieba.cut(t) if x and x.strip()]
                docs.append(" ".join(toks))
            except Exception:
                docs.append(t)
        else:
            docs.append(t)

    # 2) 只对非空文档建模，保证不丢行：空文档主题置 -1
    valid_idx = [i for i, d in enumerate(docs) if d.strip()]
    if not valid_idx:
        return TopicModelResult(topics=[], doc_topics=[-1] * len(items), doc_topic_scores=[""] * len(items))

    valid_docs = [docs[i] for i in valid_idx]

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import NMF
        import numpy as np
    except Exception as e:
        raise RuntimeError("主题模型需要 scikit-learn（已在 requirements.txt 中），请先安装依赖。") from e

    vec = TfidfVectorizer(
        tokenizer=str.split,
        lowercase=False,
        min_df=1,
        max_df=0.95,
    )
    X = vec.fit_transform(valid_docs)

    k = max(2, min(int(n_topics), X.shape[0]))
    nmf = NMF(n_components=k, random_state=seed, init="nndsvda", max_iter=400)
    W = nmf.fit_transform(X)  # [n_docs, k]
    H = nmf.components_       # [k, n_terms]

    feature_names = vec.get_feature_names_out()
    topics = []
    for tid in range(k):
        top_idx = H[tid].argsort()[::-1][:top_words]
        keywords = [str(feature_names[j]) for j in top_idx if H[tid][j] > 0]
        topics.append({"topic_id": tid, "keywords": keywords})

    # 3) 对齐回原始长度
    doc_topics = [-1] * len(items)
    doc_topic_scores = [""] * len(items)
    for local_i, i in enumerate(valid_idx):
        row = W[local_i]
        tid = int(row.argmax())
        doc_topics[i] = tid
        # Top3 分布压缩为字符串：t0:0.32;t3:0.21;t1:0.10
        top3 = row.argsort()[::-1][:3]
        s = ";".join([f"t{int(j)}:{float(row[j]):.4f}" for j in top3 if row[j] > 0])
        doc_topic_scores[i] = s

    return TopicModelResult(topics=topics, doc_topics=doc_topics, doc_topic_scores=doc_topic_scores)


def sentiment_analysis(text: str) -> tuple[str, float]:
    """
    情感分析：返回 (情感倾向, 分数)
    倾向：正/负/中；分数 0~1，>0.6 偏正，<0.4 偏负
    """
    if not SNOWNLP_AVAILABLE or not text or len(text.strip()) < 2:
        return "中", 0.5
    try:
        score = SnowNLP(text).sentiments
        if score >= 0.6:
            label = "正"
        elif score <= 0.4:
            label = "负"
        else:
            label = "中"
        return label, round(score, 4)
    except Exception:
        return "中", 0.5


def process_comments(comments: list[dict], enable_analysis: bool = True) -> list[dict]:
    """
    对评论列表进行清洗、关键词提取、情感分析，并追加新列
    """
    if not comments:
        return []

    for c in comments:
        raw = (c.get("评论内容") or "").strip()
        cleaned = clean_text(raw)
        # 按你的要求：清洗后只保留“情感词关键词”
        sentiment_words = extract_sentiment_words(cleaned or raw)
        c["清洗后评论"] = sentiment_words
        # 同时保留一份“清洗全文”，用于属性/主题建模（不影响你要求的清洗后评论展示）
        c["清洗全文"] = cleaned if cleaned else raw[:500]
        # 商品属性关键词
        c["属性关键词"] = extract_attribute_keywords(c["清洗全文"], top_k=6)

        if enable_analysis:
            c["关键词"] = extract_keywords(cleaned or raw, top_k=5)
            label, score = sentiment_analysis(cleaned or raw)
            c["情感倾向"] = label
            c["情感分数"] = score
        else:
            c["关键词"] = ""
            c["情感倾向"] = ""
            c["情感分数"] = 0.5

    return comments


@dataclass
class MBertVizResult:
    """mBERT 可视化结果（用于前端散点图）"""

    points: list[dict]
    meta: dict


def _mean_pool(last_hidden_state, attention_mask):
    # last_hidden_state: [B, T, H]
    # attention_mask: [B, T]
    import torch

    mask = attention_mask.unsqueeze(-1).type_as(last_hidden_state)  # [B,T,1]
    summed = (last_hidden_state * mask).sum(dim=1)  # [B,H]
    denom = mask.sum(dim=1).clamp(min=1e-9)  # [B,1]
    return summed / denom


def mbert_build_viz(
    comments: list[dict],
    *,
    text_key: str = "清洗后评论",
    fallback_text_key: str = "评论内容",
    model_name: str = "bert-base-multilingual-cased",
    batch_size: int = 16,
    max_length: int = 96,
    seed: int = 42,
) -> MBertVizResult:
    """
    用 mBERT 将评论编码为向量，并降维到 2D（PCA），供前端可视化。

    - 输出 points: [{x,y,text,raw_text,sentiment,label,cluster}, ...]
    - 降维：优先 PCA（稳定、依赖少）
    - 聚类：KMeans（k<=5，且不超过样本数）
    """
    # 1) 组装文本（重要：不丢弃任何评论；空文本保留，坐标置空）
    items = comments or []
    aligned_texts: list[str] = []
    aligned_raws: list[str] = []
    aligned_sentiments: list[str] = []
    valid_indices: list[int] = []
    valid_texts: list[str] = []

    for idx, c in enumerate(items):
        t = (c.get(text_key) or "").strip()
        raw = (c.get(fallback_text_key) or "").strip()
        if not t:
            t = raw
        t = (t or "").strip()

        aligned_texts.append(t)
        aligned_raws.append(raw)
        aligned_sentiments.append((c.get("情感倾向") or "").strip())

        if t:
            valid_indices.append(idx)
            valid_texts.append(t)

    if not items:
        return MBertVizResult(points=[], meta={"model": model_name, "n": 0})

    if not valid_texts:
        # 全是空文本：仍然返回等长 points，方便前端/导出对齐
        points = []
        for i in range(len(items)):
            points.append(
                {
                    "i": i + 1,
                    "x": None,
                    "y": None,
                    "text": aligned_texts[i] or "",
                    "raw_text": aligned_raws[i] or "",
                    "sentiment": aligned_sentiments[i] or "",
                    "cluster": None,
                }
            )
        return MBertVizResult(points=points, meta={"model": model_name, "n": len(points), "device": "unknown"})

    # 2) 加载模型（可选依赖：transformers/torch）
    try:
        import torch
        from transformers import AutoModel, AutoTokenizer
    except Exception as e:
        raise RuntimeError(
            "未安装 mBERT 依赖，请先执行：pip install -r requirements.txt（需要 transformers + torch）"
        ) from e

    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(seed)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)
    model.to(device)
    model.eval()

    # 3) 批量编码（只对非空文本计算 embedding）
    embs = []
    with torch.no_grad():
        for i in range(0, len(valid_texts), batch_size):
            batch = valid_texts[i : i + batch_size]
            encoded = tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            encoded = {k: v.to(device) for k, v in encoded.items()}
            out = model(**encoded)
            pooled = _mean_pool(out.last_hidden_state, encoded["attention_mask"])  # [B,H]
            embs.append(pooled.detach().cpu())
    emb = torch.cat(embs, dim=0).numpy()

    # 4) 降维到 2D：PCA（可选依赖：scikit-learn）
    try:
        from sklearn.decomposition import PCA
    except Exception as e:
        raise RuntimeError("未安装 scikit-learn，请执行：pip install -r requirements.txt") from e

    if emb.shape[0] == 1:
        xy_valid = [[0.0, 0.0]]
    else:
        pca = PCA(n_components=2, random_state=seed)
        xy_valid = pca.fit_transform(emb).tolist()

    # 5) 聚类（可选）：KMeans
    clusters_valid = [0] * len(valid_texts)
    if len(valid_texts) >= 3:
        try:
            from sklearn.cluster import KMeans

            k = min(5, max(2, int(math.sqrt(len(valid_texts)))))
            k = min(k, len(valid_texts))
            km = KMeans(n_clusters=k, random_state=seed, n_init="auto")
            clusters_valid = km.fit_predict(emb).tolist()
        except Exception:
            clusters_valid = [0] * len(valid_texts)

    # 6) 组织输出点（与输入 comments 等长，对齐不丢数据）
    coords_x: list[Optional[float]] = [None] * len(items)
    coords_y: list[Optional[float]] = [None] * len(items)
    clusters: list[Optional[int]] = [None] * len(items)
    for local_i, idx in enumerate(valid_indices):
        coord = xy_valid[local_i]
        coords_x[idx] = float(coord[0])
        coords_y[idx] = float(coord[1])
        clusters[idx] = int(clusters_valid[local_i])

    points: list[dict] = []
    for i in range(len(items)):
        points.append(
            {
                "i": i + 1,
                "x": coords_x[i],
                "y": coords_y[i],
                "text": aligned_texts[i] or "",
                "raw_text": aligned_raws[i] or "",
                "sentiment": aligned_sentiments[i] or "",
                "cluster": clusters[i],
            }
        )

    return MBertVizResult(
        points=points,
        meta={
            "model": model_name,
            "device": device,
            "n": len(points),
            "text_key": text_key,
            "valid_n": len(valid_texts),
        },
    )


