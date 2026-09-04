# -*- coding: utf-8 -*-
"""
Schema Retrieval：表结构检索层
================================
解决"库表多时全量 schema 灌 prompt 导致 token 爆炸/噪音干扰"的问题。

思路（RAG 思想在 Text2SQL 场景的应用）:
  离线: 每张表生成一份"schema 文档"(表名+字段+主外键), 调 embedding 接口向量化,
        存入 Faiss 索引并落盘(data/schema_index/), 二次运行不重复调接口
  在线: 用户问题 -> 向量化 -> Faiss 检索 top-k 张最相关的表 -> 只把这些表的 schema 拼进 prompt

用法:
    python3 backend/schema_retriever.py    # 建索引 + 检索效果演示
"""
import json
import os
import sqlite3
from pathlib import Path

import faiss
import numpy as np
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_DIR = BASE_DIR / "data" / "schema_index"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")


def get_client() -> OpenAI:
    """OpenAI 兼容客户端(与 agent_service.py 保持同一网关配置)"""
    return OpenAI(
        base_url=os.environ.get("LLM_BASE_URL", "https://aigc.sankuai.com/v1/openai/native"),
        api_key=os.environ.get("LLM_API_KEY", "22026200394732511280"),
    )


def _embed(client: OpenAI, texts: list) -> np.ndarray:
    """调用 embedding 接口, 返回 L2 归一化后的向量矩阵 (n, dim)。
    归一化之后, IndexFlatIP 的内积就等价于余弦相似度。"""
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    vecs = np.array([d.embedding for d in resp.data], dtype="float32")
    faiss.normalize_L2(vecs)
    return vecs


def build_table_docs(conn: sqlite3.Connection) -> list:
    """每张表生成一份文档: [{"table": 表名, "text": schema 文本}, ...]"""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]
    docs = []
    for t in tables:
        cols = conn.execute("PRAGMA table_info(%s)" % t).fetchall()
        # cols 每行: (cid, name, type, notnull, dflt_value, pk)
        col_lines = ["  %s %s%s" % (c[1], c[2], " 主键" if c[5] else "") for c in cols]
        fks = conn.execute("PRAGMA foreign_key_list(%s)" % t).fetchall()
        # fks 每行: (id, seq, 被引用表, 本表字段, 被引用字段, ...)
        fk_lines = ["  %s.%s -> %s.%s" % (t, f[3], f[2], f[4] or f[3]) for f in fks]
        text = "表名: %s\n字段:\n%s\n外键关系:\n%s" % (
            t, "\n".join(col_lines), "\n".join(fk_lines) if fk_lines else "  无")
        docs.append({"table": t, "text": text})
    return docs


class SchemaRetriever:
    def __init__(self, index, docs):
        self.index = index      # faiss 索引
        self.docs = docs        # 与索引行号对齐的 schema 文档列表

    @classmethod
    def build(cls, conn: sqlite3.Connection, rebuild: bool = False) -> "SchemaRetriever":
        """建索引。带落盘缓存: 索引文件存在且未指定 rebuild 时直接加载。"""
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        index_file = INDEX_DIR / "schema.index"
        meta_file = INDEX_DIR / "schema_meta.json"
        if index_file.exists() and meta_file.exists() and not rebuild:
            index = faiss.read_index(str(index_file))
            docs = json.loads(meta_file.read_text(encoding="utf-8"))
            return cls(index, docs)
        docs = build_table_docs(conn)
        client = get_client()
        vecs = _embed(client, [d["text"] for d in docs])
        index = faiss.IndexFlatIP(vecs.shape[1])
        index.add(vecs)
        faiss.write_index(index, str(index_file))
        meta_file.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
        return cls(index, docs)

    def retrieve_tables(self, question: str, k: int = 3, client: OpenAI = None) -> list:
        """问题 -> 命中的 top-k 表文档列表(按相似度降序)"""
        client = client or get_client()
        vecs = _embed(client, [question])
        scores, ids = self.index.search(vecs, min(k, len(self.docs)))
        return [self.docs[i] for i in ids[0] if 0 <= i < len(self.docs)]

    def retrieve(self, question: str, k: int = 3, client: OpenAI = None) -> str:
        """问题 -> top-k 相关表的 schema 文本(直接可拼进 prompt)"""
        parts = [d["text"] for d in self.retrieve_tables(question, k, client)]
        return "\n\n".join(parts)


if __name__ == "__main__":
    db = BASE_DIR / "data" / "chinook.db"
    conn = sqlite3.connect(str(db))
    r = SchemaRetriever.build(conn, rebuild=True)
    print("索引表数: %d, 落盘目录: %s" % (len(r.docs), INDEX_DIR))
    for q in ["每个月的订单总金额是多少", "销售量最高的歌手是谁", "哪些员工有直接下属"]:
        hits = r.retrieve_tables(q, k=3)
        print("\n问题: %s" % q)
        print("  命中表: %s" % [h["table"] for h in hits])
