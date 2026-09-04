from sqlalchemy import create_engine, inspect, text
import pandas as pd
import os
import time
import sqlglot
from sqlglot import exp


class TTLCache:
    """简易 TTL 缓存: key -> (value, 过期时间戳)。
    用于元数据(表名/字段/外键)缓存——这些信息几乎不变,
    避免每次工具调用都重新做数据库内省(introspection)。"""

    def __init__(self, ttl: int = 300):
        self.ttl = ttl
        self._store = {}
        self.hits = 0
        self.misses = 0

    def get(self, key):
        item = self._store.get(key)
        if item is None:
            self.misses += 1
            return None
        value, expire_at = item
        if time.time() > expire_at:
            del self._store[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key, value):
        self._store[key] = (value, time.time() + self.ttl)


class DBParser:
    def __init__(self, db_url):
        # 传 db_url，建 engine + inspect
        # 判断 db_type（sqlite/mysql）
        self.db_url = db_url
        if "sqlite" in self.db_url:
            self.db_type = "sqlite"
            # 新增：sqlite 文件不存在就抛异常，避免静默建空库
            db_path = db_url.replace("sqlite:///", "")
            if not db_path.startswith("/"):
                if not os.path.exists(db_path):
                    raise FileNotFoundError(f"数据库文件不存在: {db_path}（当前 cwd={os.getcwd()}）")
                self.db_url = "sqlite:///" + os.path.abspath(db_path)
        elif "mysql" in self.db_url:
            self.db_type = "mysql"
        self.engine = create_engine(self.db_url)
        self.ins = inspect(self.engine)
        # 元数据缓存: TTL 默认 300 秒, 可用环境变量 META_TTL 调整
        self._meta_cache = TTLCache(ttl=int(os.getenv("META_TTL", "300")))

    def get_table_names(self) -> list:
        """返回所有表名(带 TTL 缓存)"""
        cached = self._meta_cache.get("tables")
        if cached is not None:
            return cached
        names = self.ins.get_table_names()
        self._meta_cache.set("tables", names)
        return names

    def get_table_fields(self, table_name: str):
        """返回某表字段（DataFrame，列名: name/type/primary_key）(带 TTL 缓存)"""
        key = "fields:%s" % table_name
        cached = self._meta_cache.get(key)
        if cached is not None:
            return cached
        columns = self.ins.get_columns(table_name)
        markdown = pd.DataFrame(columns).to_markdown(index=False)
        self._meta_cache.set(key, markdown)
        return markdown

    def get_table_sample(self, table_name: str, limit: int = 3):
        """返回某表前 N 行样例数据（DataFrame）"""
        if table_name in self.get_table_names():
            with self.engine.connect() as conn:
                df = pd.read_sql(text(f"select * from {table_name} limit {limit}"),conn)
            return df.to_markdown(index=False)
        else:
            return f"表{table_name}不存在"

    def get_data_relations(self) -> list[dict]:
        """返回所有外键关系（list[dict]）(带 TTL 缓存)"""
        cached = self._meta_cache.get("relations")
        if cached is not None:
            return cached
        result = []
        for table in self.get_table_names():
            for item in self.ins.get_foreign_keys(table):
                item["source_table"] = table
                result.append(item)
        self._meta_cache.set("relations", result)
        return result

    def _validate_sql_ast(self, sql: str) -> tuple[bool, str]:
        """用 sqlglot AST 校验 SQL 是否只读"""
        try:
            expressions = sqlglot.parse(sql)
        except Exception as e:
            return False, f"SQL 语法错误{e}"
        if not expressions:
            return False, f"空 SQL"
        for expr in expressions:
            if expr is None:
                return False, f"SQL 解析失败,可能是语法错误"
            for node in expr.find_all((exp.Delete, exp.Update, exp.Drop, exp.Insert, exp.Alter, exp.Create)):
                return False, f"检测到破坏性操作: {type(node).__name__}"
        return True, f"SQL 合法"



    def execute_sql(self, sql: str):
        """
        生成 SQL; 报错时把错误回灌给 LLM 重试(与线上 Agent 自愈逻辑一致)。
        返回 (最终sql, 重试记录列表)
    """
        # 第一层:AST 校验(新增)
        flag, msg = self._validate_sql_ast(sql)
        if not flag:
            return msg
        # 第二层:字符串白名单(保留,两层防御)
        if not sql.strip().upper().startswith("SELECT") and not sql.strip().upper().startswith("WITH"):
            return f"sql{sql}语句不合法，支持查询语句"
        if sql.strip().rstrip(";").count(";") > 0:
            return f"sql:{sql}语句不合法,只支持单条查询"
        else:
            # 第三层:执行
            try:
                with self.engine.connect() as conn:
                    df = pd.read_sql(text(sql), conn)
                # return df.to_markdown(index=False)
            except Exception as e:
                return f"SQL执行失败: {e}。请根据报错检查字段名/表名/语法后重新生成 SQL"
            return {"columns": df.columns.tolist(), "rows": df.values.tolist()}

if __name__ == '__main__':
    DB_URL = "sqlite:////Users/bin/Downloads/ai/test_demo/projects/chatbi_assistant/data/chinook.db"
    parser = DBParser(DB_URL)
    print(parser.get_table_names())
    print(parser.get_table_fields("invoices"))  # DataFrame
    print(parser.get_table_sample("invoices"))  # DataFrame
    print(parser.get_data_relations())  # list[dict]
    print(parser.execute_sql("SELECT * FROM customers LIMIT 1"))
    # 测破坏性 SQL
    print(parser.execute_sql("DELETE FROM customers"))
    print(parser.execute_sql("DROP TABLE customers"))
    print(parser.execute_sql("SELECT * FROM customers; DROP TABLE customers;"))
    print(parser.execute_sql(""))