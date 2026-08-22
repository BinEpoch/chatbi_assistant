from sqlalchemy import create_engine, inspect, text
import pandas as pd
import os

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

    def get_table_names(self) -> list:
        """返回所有表名"""
        return self.ins.get_table_names()

    def get_table_fields(self, table_name: str):
        """返回某表字段（DataFrame，列名: name/type/primary_key）"""
        columns = self.ins.get_columns(table_name)
        dataframe = pd.DataFrame(columns)
        return dataframe.to_markdown(index=False)

    def get_table_sample(self, table_name: str, limit: int = 3):
        """返回某表前 N 行样例数据（DataFrame）"""
        if table_name in self.get_table_names():
            with self.engine.connect() as conn:
                df = pd.read_sql(text(f"select * from {table_name} limit {limit}"),conn)
            return df.to_markdown(index=False)
        else:
            return f"表{table_name}不存在"

    def get_data_relations(self) -> list[dict]:
        """返回所有外键关系（list[dict]）"""
        result = []
        for table in self.get_table_names():
            for item in self.ins.get_foreign_keys(table):
                item["source_table"] = table
                result.append(item)
        return result

    def execute_sql(self, sql: str):
        if not sql.strip().upper().startswith("SELECT") and not sql.strip().upper().startswith("WITH"):
            return f"sql{sql}语句不合法，支持查询语句"
        if sql.strip().rstrip(";").count(";") > 0:
            return f"sql:{sql}语句不合法,只支持单条查询"
        else:
            with self.engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
            return df.to_markdown(index=False)


if __name__ == '__main__':
    DB_URL = "sqlite:////Users/bin/Downloads/ai/test_demo/projects/chatbi_assistant/data/chinook.db"
    parser = DBParser(DB_URL)
    print(parser.get_table_names())
    print(parser.get_table_fields("invoices"))  # DataFrame
    print(parser.get_table_sample("invoices"))  # DataFrame
    print(parser.get_data_relations())  # list[dict]