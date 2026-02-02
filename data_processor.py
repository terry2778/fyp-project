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

# ==================== 原有的文本处理功能 ====================
# 原有代码保持不变，从 clean_text 函数开始...
