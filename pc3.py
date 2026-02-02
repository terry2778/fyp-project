from __future__ import annotations

from DrissionPage import ChromiumPage, ChromiumOptions
import pandas as pd
import time
import re
import json
import sys
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional
import requests
import pymysql

from data_processor import (
    process_comments, build_topic_model, mbert_build_viz,
    connect_to_database, save_raw_to_db, save_processed_to_db, save_analysis_to_db
)

def _ensure_utf8_stdio():
    """
    Windows 控制台常见默认编码为 GBK，遇到 '❌/✅/📊' 等字符会 print() 直接报错。
    这里尽量把 stdout/stderr 调整为 utf-8，且用 errors='replace' 保证不崩溃。
    """
    for s in (sys.stdout, sys.stderr):
        try:
            if hasattr(s, "reconfigure"):
                s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

_ensure_utf8_stdio()

def default_output_dir() -> Path:
    desktop_path = Path.home() / "Desktop"
    data_dir = desktop_path / "淘宝评论_数据库版"
    data_dir.mkdir(exist_ok=True)
    return data_dir

class DatabaseConnector:
    """数据库连接管理器"""
    
    def __init__(self, db_config: dict = None):
        self.db_config = db_config or {
            'host': 'localhost',
            'port': 3306,
            'user': 'root',
            'password': '123456',  # 修改为你的密码
            'database': 'taobao_comments'
        }
        self.connection = None
        
        if db_config:
            self.db_config.update(db_config)
    
    def connect(self):
        """连接到数据库"""
        try:
            self.connection = connect_to_database(**self.db_config)
            return self.connection is not None
        except Exception as e:
            print(f"❌ 数据库连接失败: {e}")
            return False
    
    def close(self):
        """关闭数据库连接"""
        if self.connection:
            self.connection.close()
            print("✅ 数据库连接已关闭")
    
    def check_tables(self):
        """检查表是否存在"""
        if not self.connection:
            return False
        
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                table_names = [list(table.values())[0] for table in tables]
                
                required_tables = ['raw_reviews', 'processed_reviews', 'analysis_results']
                for table in required_tables:
                    if table in table_names:
                        print(f"✅ 表 {table} 存在")
                    else:
                        print(f"❌ 表 {table} 不存在，请创建该表")
                        return False
                return True
        except Exception as e:
            print(f"❌ 检查表失败: {e}")
            return False
    
    def save_raw_comments(self, item_id: str, url: str, comments: list[dict], data_source: str = 'browser'):
        """保存原始评论"""
        if not self.connection or not comments:
            return 0
        
        # 添加数据来源
        for comment in comments:
            comment['data_source'] = data_source
        
        return save_raw_to_db(self.connection, item_id, url, comments)
    
    def save_processed_comments(self, item_id: str, comments: list[dict]):
        """保存处理后的评论"""
        if not self.connection or not comments:
            return 0
        
        return save_processed_to_db(self.connection, item_id, comments)
    
    def save_analysis_results(self, item_id: str, comments: list[dict], 
                            topic_model=None, mbert_viz=None):
        """保存分析结果"""
        if not self.connection or not comments:
            return False
        
        return save_analysis_to_db(self.connection, item_id, comments, topic_model, mbert_viz)

class TaobaoCommentCrawler:
    """淘宝评论爬虫类（带数据库支持）"""
    
    def __init__(
        self,
        target_url: str,
        output_dir: Optional[Path] = None,
        max_comments: int = 100,
        status_callback: Optional[Callable[[str], None]] = None,
        keep_login_profile_dir: Optional[Path] = None,
        enable_analysis: bool = True,
        enable_mbert_viz: bool = False,
        db_config: Optional[dict] = None  # 新增：数据库配置
    ):
        self.target_url = target_url
        self.output_dir = output_dir or default_output_dir()
        self.output_dir.mkdir(exist_ok=True)
        self.excel_path = self.output_dir / "淘宝评论.xlsx"
        self.csv_path = self.output_dir / "淘宝评论.csv"
        self.max_comments = max_comments
        self.status_callback = status_callback
        self.keep_login_profile_dir = keep_login_profile_dir
        self.enable_analysis = enable_analysis
        self.enable_mbert_viz = enable_mbert_viz
        self.mbert_vis_path = self.output_dir / "mbert_vis.json"
        
        # 初始化数据库连接器
        self.db_connector = DatabaseConnector(db_config)
        if db_config:
            if self.db_connector.connect():
                self.db_connector.check_tables()
            else:
                print("⚠️  数据库连接失败，数据将仅保存到本地文件")
        
        # 提取商品ID
        self.item_id = self._extract_item_id()

        # DrissionPage 需要能找到浏览器可执行文件；这里优先自动使用本机 Edge
        self.browser = ChromiumPage(self._build_edge_options())
        self.comments_data = []
        self.setup_browser()

    def _build_edge_options(self):
        """构建 Edge 浏览器配置（自动探测 msedge.exe 路径）"""
        candidate_paths = [
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            # 少数情况下 Edge 安装在用户目录（这里给一个兜底）
            str(Path.home() / r"AppData\Local\Microsoft\Edge\Application\msedge.exe"),
        ]

        browser_path = next((p for p in candidate_paths if Path(p).exists()), None)
        if not browser_path:
            raise FileNotFoundError(
                "未找到 Edge 可执行文件（msedge.exe）。\n"
                "请确认已安装 Edge，或在 pc3.py 的 _build_edge_options() 里把路径改成你电脑上的实际路径。\n"
                "常见路径：\n"
                r"- C:\Program Files\Microsoft\Edge\Application\msedge.exe" "\n"
                r"- C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            )

        co = ChromiumOptions()
        co.set_browser_path(browser_path)

        # 复用用户数据目录以保持登录（第一次手动登录，之后自动保持）
        if self.keep_login_profile_dir is not None:
            self.keep_login_profile_dir.mkdir(exist_ok=True)
            co.set_user_data_path(str(self.keep_login_profile_dir))
            co.set_local_port(9222)

        return co

    def _status(self, msg: str):
        if self.status_callback:
            try:
                self.status_callback(msg)
            except Exception:
                pass
    
    def setup_browser(self):
        """设置浏览器"""
        self.browser.set.user_agent(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        try:
            self.browser.set.window_size(1400, 900)
        except:
            pass

    # ---------------------------
    # 极速模式：优先走评论接口分页（比滚动页面快很多）
    # ---------------------------
    def _extract_item_id(self) -> Optional[str]:
        try:
            qs = parse_qs(urlparse(self.target_url).query)
            if "id" in qs and qs["id"]:
                return qs["id"][0]
        except Exception:
            pass
        return None

    def _parse_jsonp(self, text: str) -> Optional[dict]:
        text = (text or "").strip()
        if not text:
            return None
        # jsonp12345({...})
        if "(" in text and text.endswith(")"):
            try:
                payload = text[text.find("(") + 1 : -1]
                return json.loads(payload)
            except Exception:
                return None
        try:
            return json.loads(text)
        except Exception:
            return None

    def try_fetch_comments_via_api(self) -> list[dict]:
        """尽量用 rate 接口拉取评论（快）；失败则返回空列表。"""
        self._status("api_fetching")
        item_id = self._extract_item_id()
        if not item_id:
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.target_url,
        }

        results: list[dict] = []
        page = 1
        page_size = 20

        # 备注：这个接口在不同商品/店铺上可能会变化；失败时我们会回退到浏览器滚动方案
        while len(results) < self.max_comments and page <= 50:
            params = {
                "itemId": item_id,
                "currentPage": page,
                "pageSize": page_size,
                "order": 3,
                "callback": "jsonp",
            }
            try:
                r = requests.get(
                    "https://rate.tmall.com/list_detail_rate.htm",
                    params=params,
                    headers=headers,
                    timeout=12,
                )
                data = self._parse_jsonp(r.text)
                if not data:
                    break

                rate_detail = data.get("rateDetail") or {}
                rate_list = rate_detail.get("rateList") or []
                if not rate_list:
                    break

                for it in rate_list:
                    if len(results) >= self.max_comments:
                        break
                    results.append(
                        {
                            "序号": len(results) + 1,
                            "用户名": (it.get("displayUserNick") or it.get("userNick") or "匿名用户")[:30],
                            "评论内容": (it.get("rateContent") or "").strip()[:500],
                            "评论时间": (it.get("rateDate") or "").strip(),
                            "购买信息": (it.get("auctionSku") or it.get("sku") or "").strip(),
                        }
                    )

                page += 1
                # 接口模式一般不需要太大延迟，轻微放慢避免风控
                time.sleep(0.25)
            except Exception:
                break

        # 过滤掉空内容
        results = [c for c in results if c.get("评论内容")]
        return results
    
    def find_and_click_element(self, element_type, identifier, description):
        """查找并点击元素"""
        print(f"查找{description}...")
        
        strategies = [
            lambda: self.browser.ele(f'xpath:{identifier}', timeout=5),
            lambda: self.browser.ele(f'css:{identifier}', timeout=5),
            lambda: self.browser.ele(f'text()={identifier}', timeout=5) if element_type == 'text' else None,
            lambda: self.browser.ele(f'@{identifier}', timeout=5) if element_type == 'attr' else None,
        ]
        
        for strategy in strategies:
            try:
                element = strategy()
                if element:
                    print(f"  找到{description}: {identifier}")
                    element.click()
                    time.sleep(1)
                    return True
            except:
                continue
        
        print(f"  未找到{description}")
        return False
    
    def navigate_to_comments(self):
        """导航到评论页面"""
        self._status("navigating")
        print("\n1. 导航到评论页面")
        
        print(f"访问页面...")
        self.browser.get(self.target_url)
        time.sleep(2)
        
        navigation_strategies = [
            ('xpath', '//*[contains(text(), "评价")]', '"评价"标签'),
            ('xpath', '//*[contains(text(), "用户评价")]', '"用户评价"标签'),
            ('xpath', '//*[contains(text(), "宝贝评价")]', '"宝贝评价"标签'),
            ('xpath', '//*[contains(text(), "评论")]', '"评论"标签'),
            ('xpath', '//*[contains(text(), "查看全部评价")]', '"查看全部评价"按钮'),
            ('css', '.tb-tab-anchor[data-spm="user-evaluation"]', '评价CSS选择器'),
        ]
        
        for strategy_type, identifier, description in navigation_strategies:
            if self.find_and_click_element(strategy_type, identifier, description):
                print(f"  成功进入评论页面")
                time.sleep(1)
                return True
        
        print("  未能进入评论页面，尝试在当前页面查找评论")
        return False
    
    def load_all_comments(self):
        """加载全部评论"""
        self._status("loading")
        print("\n2. 加载全部评论")
        
        initial_count = self.count_comments()
        print(f"  初始评论数: {initial_count}")
        
        load_attempts = 0
        max_attempts = 10
        no_new_comments_count = 0
        
        while load_attempts < max_attempts:
            load_attempts += 1
            
            print(f"  尝试 {load_attempts}: 滚动加载")
            self.scroll_for_comments()
            
            if load_attempts % 2 == 0:
                self.click_load_more_buttons()
            
            time.sleep(1.2)
            current_count = self.count_comments()
            if current_count > initial_count:
                print(f"    发现新评论: {current_count - initial_count} 条")
                initial_count = current_count
                no_new_comments_count = 0
            else:
                no_new_comments_count += 1
                print(f"    未发现新评论 (连续 {no_new_comments_count} 次)")
            
            if no_new_comments_count >= 4:
                print("    连续多次未发现新评论，停止加载")
                break
            
            time.sleep(0.8)
        
        print(f"  最终评论数: {initial_count}")
    
    def count_comments(self):
        """统计评论数量"""
        selectors = [
            'xpath://div[contains(@class, "Comment-")]',
            'xpath://div[contains(@class, "comment-")]',
            'xpath://div[contains(@class, "rate-")]',
            'xpath://div[contains(@class, "tb-rev-item")]',
        ]
        
        for selector in selectors:
            try:
                elements = self.browser.eles(selector)
                if elements:
                    return len(elements)
            except:
                continue
        
        return 0
    
    def scroll_for_comments(self):
        """滚动以加载评论"""
        scroll_strategies = [
            (400, "评论区上"),
            (800, "评论区中"),
            (1200, "评论区下"),
            (1600, "页面底部"),
        ]
        
        for scroll_amount, description in scroll_strategies:
            print(f"    滚动{description}: {scroll_amount}px")
            self.browser.scroll.down(scroll_amount)
            time.sleep(1.0)
    
    def click_load_more_buttons(self):
        """点击加载更多按钮"""
        button_selectors = [
            'xpath://*[contains(text(), "加载更多")]',
            'xpath://*[contains(text(), "查看更多")]',
            'xpath://*[contains(text(), "查看全部")]',
            'xpath://button[contains(@class, "load-more")]',
            'xpath://a[contains(@class, "more")]',
        ]
        
        for selector in button_selectors:
            try:
                buttons = self.browser.eles(selector)
                if buttons:
                    buttons[0].click()
                    time.sleep(0.8)
                    return True
            except:
                continue
        
        return False
    
    def extract_comments_by_position(self):
        """通过元素位置提取评论"""
        self._status("extracting")
        print("\n3. 提取评论")
        
        comment_containers = self.locate_comment_containers()
        
        if not comment_containers:
            print("  未找到评论容器")
            return []
        
        print(f"  找到 {len(comment_containers)} 个评论容器")
        
        all_comments = []
        
        for i, container in enumerate(comment_containers[: self.max_comments]):
            try:
                comment_data = self.extract_single_comment(container, i+1)
                if comment_data and comment_data.get('评论内容'):
                    all_comments.append(comment_data)
                    print(f"  提取评论 {len(all_comments)}: {comment_data['用户名'][:10]}...")
            except Exception as e:
                continue
        
        return all_comments
    
    def locate_comment_containers(self):
        """定位评论容器"""
        location_strategies = [
            lambda: self.browser.eles('xpath://div[contains(@class, "Comment-")]'),
            lambda: self.browser.eles('css:div[class*="Comment-"]'),
            lambda: self.find_elements_near_text('评价', 'div'),
            lambda: self.browser.eles('css:.tb-rev-item'),
            lambda: self.browser.eles('xpath://div[contains(@class, "rate-card") or contains(@class, "comment-item")]'),
        ]
        
        for strategy in location_strategies:
            try:
                elements = strategy()
                if elements and len(elements) > 0:
                    print(f"  使用策略找到 {len(elements)} 个元素")
                    return elements
            except:
                continue
        
        return []
    
    def find_elements_near_text(self, text, tag_name='div'):
        """查找文本附近的元素"""
        try:
            text_elements = self.browser.eles(f'xpath://*[contains(text(), "{text}")]')
            
            nearby_elements = []
            for text_element in text_elements:
                parent = text_element.parent
                for _ in range(3):
                    if parent:
                        siblings = parent.eles(f'xpath:.//{tag_name}')
                        nearby_elements.extend(siblings)
                        parent = parent.parent
            
            return nearby_elements
        except:
            return []
    
    def extract_single_comment(self, container, index):
        """提取单个评论"""
        comment_data = {
            '序号': index,
            '用户名': '匿名用户',
            '评论内容': '',
            '评论时间': '',
            '购买信息': ''
        }
        
        try:
            # 提取用户名
            comment_data['用户名'] = self.extract_username_from_container(container)
            
            # 提取评论内容
            comment_data['评论内容'] = self.extract_comment_content(container)
            
            # 提取评论时间
            comment_data['评论时间'] = self.extract_comment_time(container)
            
            # 提取购买信息
            comment_data['购买信息'] = self.extract_purchase_info(container)
            
            return comment_data
            
        except Exception as e:
            return None
    
    def extract_username_from_container(self, container):
        """从容器提取用户名"""
        strategies = [
            lambda: self.find_in_container(container, 'css:.header-nYbpA78v span'),
            lambda: self.find_in_container(container, 'css:[class*="user"]'),
            lambda: self.find_in_container(container, 'css:[class*="name"]'),
            lambda: self.find_first_short_text(container),
        ]
        
        for strategy in strategies:
            try:
                username = strategy()
                if username and username != '匿名用户':
                    return username[:30]
            except:
                continue
        
        return '匿名用户'
    
    def find_in_container(self, container, selector):
        """在容器内查找元素"""
        try:
            element = container.ele(selector)
            if element:
                text = element.text.strip() if hasattr(element, 'text') and element.text else ""
                if text and 2 <= len(text) <= 20:
                    return text
        except:
            pass
        return None
    
    def find_first_short_text(self, container):
        """查找第一个短文本"""
        try:
            all_text = container.text if hasattr(container, 'text') else ""
            lines = all_text.split('\n')
            
            for line in lines:
                line = line.strip()
                if 2 <= len(line) <= 15 and not re.search(r'[\d-]{8,}', line):
                    return line
        except:
            pass
        
        return None
    
    def extract_comment_content(self, container):
        """提取评论内容"""
        strategies = [
            lambda: self.find_content_by_class(container, 'content-uono'),
            lambda: self.find_content_by_class(container, 'contentWrapper'),
            lambda: self.find_long_text_in_container(container),
        ]
        
        for strategy in strategies:
            try:
                content = strategy()
                if content and len(content) > 10:
                    return content[:500]
            except:
                continue
        
        return ''
    
    def find_content_by_class(self, container, class_part):
        """通过类名查找内容"""
        try:
            elements = container.eles(f'css:[class*="{class_part}"]')
            for element in elements:
                title = element.attr('title')
                if title and len(title) > 10:
                    return title
                
                text = element.text.strip() if hasattr(element, 'text') and element.text else ""
                if text and len(text) > 10:
                    return text
        except:
            pass
        
        return ''
    
    def find_long_text_in_container(self, container):
        """查找容器内的长文本"""
        try:
            all_text = container.text if hasattr(container, 'text') else ""
            lines = [line.strip() for line in all_text.split('\n') if len(line.strip()) > 20]
            
            if lines:
                return max(lines, key=len)
        except:
            pass
        
        return ''
    
    def extract_comment_time(self, container):
        """提取评论时间"""
        try:
            container_text = container.text if hasattr(container, 'text') else ""
            
            patterns = [
                r'(\d{4}-\d{1,2}-\d{1,2})',
                r'(\d{1,2}月\d{1,2}日)',
                r'(\d{1,2}-\d{1,2})',
                r'(\d{1,2}/\d{1,2}/\d{4})',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, container_text)
                if match:
                    return match.group(1)
        except:
            pass
        
        return ''
    
    def extract_purchase_info(self, container):
        """提取购买信息"""
        try:
            container_text = container.text if hasattr(container, 'text') else ""
            
            patterns = [
                r'已购[：:]\s*([^\n]+)',
                r'购买[：:]\s*([^\n]+)',
                r'规格[：:]\s*([^\n]+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, container_text)
                if match:
                    return match.group(1).strip()
        except:
            pass
        
        return ''
    
    def save_to_excel_and_db(self, comments):
        """保存到Excel和数据库"""
        if not comments:
            print("\n[ERROR] 没有评论可保存")
            return False
        
        try:
            # 1. 保存原始数据到数据库
            if self.db_connector.connection:
                raw_count = self.db_connector.save_raw_comments(
                    self.item_id, self.target_url, comments, 'browser'
                )
                if raw_count > 0:
                    print(f"✅ 原始数据已保存到 raw_reviews 表，共 {raw_count} 条")
            
            # 2. 清洗、关键词提取、情感分析
            comments = process_comments(comments, enable_analysis=self.enable_analysis)

            df = pd.DataFrame(comments)

            # 列顺序：序号、用户名、评论时间、购买信息、评论内容、清洗后评论、关键词、情感倾向、情感分数
            base_cols = ['序号', '用户名', '评论时间', '购买信息', '评论内容']
            extra_cols = ['清洗后评论', '清洗全文', '属性关键词', '关键词', '情感倾向', '情感分数'] if self.enable_analysis else []

            # 3. 主题模型（多主题）
            topic_model = None
            try:
                self._status("topic")
                tm = build_topic_model(comments, text_key="清洗全文", n_topics=6)
                if tm.doc_topics:
                    df["主题"] = tm.doc_topics
                    df["主题分布"] = tm.doc_topic_scores
                    topic_model = tm
                    
                    # 保存主题关键词，便于前端/离线查看
                    (self.output_dir / "topic_model.json").write_text(
                        json.dumps({"topics": tm.topics}, ensure_ascii=False),
                        encoding="utf-8",
                    )
            except Exception as e:
                print(f"主题模型生成失败：{e}")

            # 4. mBERT 建模 + 可视化坐标（可选）
            mbert_viz = None
            if self.enable_mbert_viz:
                try:
                    self._status("mbert")
                    viz = mbert_build_viz(comments)
                    mbert_viz = viz
                    
                    # 写入 json（供 web 端可视化）
                    payload = {"meta": viz.meta, "points": viz.points}
                    self.mbert_vis_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

                    # 把坐标/cluster 也写回 df，便于导出
                    if viz.points:
                        # 现在 viz.points 与 comments/df 等长，直接对齐写回
                        df["mbert_x"] = [p.get("x") for p in viz.points]
                        df["mbert_y"] = [p.get("y") for p in viz.points]
                        df["cluster"] = [p.get("cluster") for p in viz.points]
                except Exception as e:
                    print(f"mBERT 可视化生成失败：{e}")

            # 5. 保存处理后的数据到数据库
            if self.db_connector.connection and self.enable_analysis:
                processed_count = self.db_connector.save_processed_comments(self.item_id, comments)
                if processed_count > 0:
                    print(f"✅ 处理后数据已保存到 processed_reviews 表，共 {processed_count} 条")
            
            # 6. 保存分析结果到数据库
            if self.db_connector.connection and self.enable_analysis:
                self.db_connector.save_analysis_results(self.item_id, comments, topic_model, mbert_viz)
            
            # 7. 设置列顺序并保存到Excel
            column_order = base_cols + extra_cols
            # 如果生成了 mbert 列，就把它们放到最后
            if "mbert_x" in df.columns and "mbert_y" in df.columns:
                column_order += ["mbert_x", "mbert_y", "cluster"]
            if "主题" in df.columns:
                column_order += ["主题", "主题分布"]
            
            df = df[[c for c in column_order if c in df.columns]]
            
            # 8. 保存到Excel
            self._status("saving")
            df.to_excel(self.excel_path, index=False, engine='openpyxl')
            
            print(f"\n[OK] 成功保存 {len(df)} 条评论到: {self.excel_path}")
            
            # 9. 显示数据预览
            print("\n[PREVIEW] 数据预览:")
            print("=" * 80)
            for _, row in df.head(10).iterrows():
                print(f"{row['序号']}. [{row['用户名']}]", end="")
                if row['评论时间']:
                    print(f" ({row['评论时间']})", end="")
                if '情感倾向' in row and row['情感倾向']:
                    print(f" [情感:{row['情感倾向']}]", end="")
                print()
                if row.get('购买信息'):
                    print(f"   购买: {str(row['购买信息'])[:60]}...")
                print(f"   内容: {str(row['评论内容'])[:80]}...")
                if row.get('关键词'):
                    print(f"   关键词: {row['关键词']}")
                print()
            
            # 10. 保存为CSV
            df.to_csv(self.csv_path, index=False, encoding='utf-8-sig')
            print(f"[FILE] 同时保存为CSV: {self.csv_path}")

            # 11. 情感统计
            if self.enable_analysis and '情感倾向' in df.columns:
                counts = df['情感倾向'].value_counts()
                pos = counts.get('正', 0)
                neg = counts.get('负', 0)
                neu = counts.get('中', 0)
                print(f"\n[STATS] 情感统计: 正面 {pos} 条 | 负面 {neg} 条 | 中性 {neu} 条")
            
            return True
            
        except Exception as e:
            print(f"保存数据时出错: {str(e)}")
            return False
    
    def save_debug_info(self):
        """保存调试信息"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            screenshot_path = self.output_dir / f'screenshot_{timestamp}.png'
            self.browser.get_screenshot(as_bytes=False, path=str(screenshot_path))
            print(f"[FILE] 页面截图已保存: {screenshot_path}")
            
            html_path = self.output_dir / f'page_structure_{timestamp}.html'
            html_content = self.browser.html if hasattr(self.browser, 'html') else ""
            
            if html_content:
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content[:50000])
                
                print(f"[FILE] 页面结构已保存: {html_path}")
            
        except Exception as e:
            print(f"保存调试信息出错: {str(e)}")
    
    def run(self):
        """运行爬虫"""
        start_time = datetime.now()
        self._status("starting")
        print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            # 0. 极速模式：优先直接请求评论接口（成功则跳过浏览器滚动）
            fast_comments = self.try_fetch_comments_via_api()
            if fast_comments:
                print(f"\n[FAST] 极速模式获取到 {len(fast_comments)} 条评论，开始保存...")
                
                # 保存API获取的数据到数据库
                if self.db_connector.connection:
                    self.db_connector.save_raw_comments(
                        self.item_id, self.target_url, fast_comments, 'api'
                    )
                
                self.save_to_excel_and_db(fast_comments)
                print(f"\n[DONE] 爬取完成!")
                print(f"总耗时: {(datetime.now() - start_time).seconds}秒")
                print(f"有效评论数: {len(fast_comments)}")
                self._status("done")
                
                # 关闭数据库连接
                if self.db_connector.connection:
                    self.db_connector.close()
                return

            # 1. 导航到评论页面
            self.navigate_to_comments()
            
            # 2. 加载全部评论
            self.load_all_comments()
            
            # 3. 通过元素位置提取评论
            comments = self.extract_comments_by_position()
            
            # 4. 保存结果
            if comments:
                self.save_to_excel_and_db(comments)
                
                print(f"\n[DONE] 爬取完成!")
                print(f"总耗时: {(datetime.now() - start_time).seconds}秒")
                print(f"有效评论数: {len(comments)}")
                
                # 显示统计信息
                print("\n[STATS] 统计信息:")
                print(f"- 平均评论长度: {sum(len(c['评论内容']) for c in comments)/len(comments):.1f} 字符")
                
                # 用户名统计
                usernames = [c['用户名'] for c in comments]
                unique_users = len(set(usernames))
                print(f"- 独立用户数: {unique_users}")
                
                # 数据库统计
                if self.db_connector.connection:
                    print(f"\n[DB] 数据已保存到数据库:")
                    print(f"- raw_reviews: {len(comments)} 条原始评论")
                    if self.enable_analysis:
                        print(f"- processed_reviews: {len(comments)} 条处理后评论")
                        print(f"- analysis_results: 已保存分析结果")
            
            else:
                print("\n[ERROR] 未能提取到任何评论")
                self.save_debug_info()
                
                print("\n💡 建议:")
                print("1. 检查是否需要登录淘宝账号")
                print("2. 手动打开页面确认评论是否可见")
                print("3. 查看保存的截图和页面结构分析")
                self._status("no_comments")
        
        except Exception as e:
            print(f"\n[ERROR] 程序执行出错: {str(e)}")
            import traceback
            traceback.print_exc()
            
            self.save_debug_info()
            self._status("error")
        
        finally:
            try:
                self.browser.close()
                print("\n浏览器已关闭")
            except:
                pass
            
            # 关闭数据库连接
            if self.db_connector.connection:
                self.db_connector.close()
        
        self._status("done")
        print(f"\n结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)

# 主程序
if __name__ == "__main__":
    # 数据库配置（根据你的MySQL设置修改）
    DB_CONFIG = {
        'host': 'localhost',
        'port': 3306,
        'user': 'root',           # 你的MySQL用户名
        'password': '123456',     # 你的MySQL密码（修改为你自己的密码）
        'database': 'taobao_comments'  # 你的数据库名
    }
    
    # 默认示例商品链接
    target_url = 'https://item.taobao.com/item.htm?id=903871222461'
    out_dir = default_output_dir()

    print("=" * 80)
    print("淘宝评论爬虫 - MySQL数据库版")
    print("=" * 80)
    print(f"目标URL: {target_url[:80]}...")
    print(f"数据保存到: {out_dir / '淘宝评论.xlsx'}")
    print(f"数据库配置: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"数据库名: {DB_CONFIG['database']}")
    print(f"数据表: raw_reviews, processed_reviews, analysis_results")
    print("=" * 80)
    print("淘宝评论爬虫启动...")

    crawler = TaobaoCommentCrawler(
        target_url=target_url,
        output_dir=out_dir,
        max_comments=30,
        keep_login_profile_dir=out_dir / "edge_profile",
        db_config=DB_CONFIG,  # 传入数据库配置
        enable_analysis=True,  # 启用分析功能
        enable_mbert_viz=False  # 可选：启用mBERT可视化
    )
    crawler.run()
