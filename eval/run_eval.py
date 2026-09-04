# -*- coding: utf-8 -*-
"""
ChatBI Text2SQL 评测 runner
============================
对 eval/eval_dataset.jsonl 中的问题，让 LLM 基于 Chinook 库 schema 生成 SQL，
用「执行准确率」(Execution Accuracy, 参考 Spider/BIRD 评测协议) 打分：
  - 生成的 SQL 先过与 db_parser 相同的两层安全校验（sqlglot AST + SELECT/WITH 白名单）
  - 执行金标准 SQL 与生成 SQL，对比结果行（多重集合比较，忽略列名与行序，浮点按 4 位舍入）

附带安全回归：eval/safety_cases.jsonl 中的破坏性 SQL 必须全部被校验层拦截。

用法:
    cd backend 所在项目根目录
    python3 eval/run_eval.py                  # 全量 30 题
    python3 eval/run_eval.py --limit 5        # 冒烟跑 5 题
    python3 eval/run_eval.py --golden-only    # 只校验金标准 SQL，不调用 LLM
环境变量（可选，默认沿用项目 agent_service.py 的配置）:
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import sqlglot
from sqlglot import exp
from openai import OpenAI

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "chinook.db"
DATASET = Path(__file__).resolve().parent / "eval_dataset.jsonl"
SAFETY = Path(__file__).resolve().parent / "safety_cases.jsonl"

sys.path.insert(0, str(BASE_DIR))
from backend.schema_retriever import SchemaRetriever

# ---------- schema ----------
def load_schema(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return "\n\n".join(r[0] for r in rows)

# ---------- 安全校验（与 backend/db_parser.py 两层防御逻辑保持一致） ----------
def validate_sql(sql: str):
    """返回 (is_valid, reason)"""
    # 第一层: sqlglot AST 校验
    try:
        expressions = sqlglot.parse(sql)
    except Exception as e:
        return False, "SQL语法错误: %s" % e
    if not expressions:
        return False, "空SQL"
    for expr in expressions:
        if expr is None:
            return False, "SQL解析失败"
        for node in expr.find_all(
            (exp.Delete, exp.Update, exp.Drop, exp.Insert, exp.Alter, exp.Create)
        ):
            return False, "检测到破坏性操作: %s" % type(node).__name__
    # 第二层: 字符串白名单 + 单条语句
    if not sql.strip().upper().startswith("SELECT") and not sql.strip().upper().startswith("WITH"):
        return False, "非查询语句"
    if sql.strip().rstrip(";").count(";") > 0:
        return False, "多语句"
    return True, "ok"

# ---------- 执行与结果对比 ----------
def run_sql(conn: sqlite3.Connection, sql: str):
    cur = conn.execute(sql)
    return cur.fetchall()

def normalize_cell(v):
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, str):
        return v.strip()
    return v

def result_multiset(rows):
    return sorted(repr(tuple(normalize_cell(c) for c in row)) for row in rows)

def exec_match(conn, golden, generated) -> tuple[bool, str]:
    try:
        g = result_multiset(run_sql(conn, golden))
    except Exception as e:
        return False, "金标准SQL执行失败: %s" % e
    try:
        p = result_multiset(run_sql(conn, generated))
    except Exception as e:
        return False, "生成SQL执行失败: %s" % e
    return (g == p), "ok" if g == p else "结果不一致"

# ---------- LLM 生成 SQL ----------
def extract_sql(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    # 去掉可能的解释文字,只保留第一个分号前的内容
    semi = text.find(";")
    if semi != -1:
        text = text[: semi + 1]
    return text.strip()

def chat_create(client, **kw):
    """带 429 限流退避重试的 LLM 请求: 遇限流指数退避, 最多重试 6 次"""
    for i in range(6):
        try:
            return client.chat.completions.create(**kw)
        except Exception as e:
            if "429" in str(e) and i < 5:
                wait = 15 * (i + 1)  # 15s/30s/45s/60s/75s
                print("  [限流 429] 第 %d 次退避, 等待 %ds ..." % (i + 1, wait))
                time.sleep(wait)
                continue
            raise

SYSTEM_PROMPT = (
    "你是一个 SQL 生成器。根据给出的 SQLite 库表结构和用户问题，"
    "生成一条可执行的只读查询 SQL（SELECT 或 WITH 开头）。"
    "只输出 SQL 本身，不要任何解释、不要 markdown 代码块。"
    "日期字段为 DATETIME 字符串，SQLite 中用 strftime 处理。"
)

def gen_sql(client, model, schema, question):
    user = "库表结构:\n%s\n\n用户问题: %s\n\n只输出一条 SELECT/WITH SQL:" % (schema, question)
    resp = chat_create(
        client,
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": user}],
        temperature=0,
    )
    return extract_sql(resp.choices[0].message.content or "")

def gen_sql_retry(client, model, schema, question, conn,  max_retry=2):
    """生成 SQL; 报错时把错误回灌给 LLM 重试(与线上 Agent 自愈逻辑一致)。
       返回 (最终sql, 重试记录列表)"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "库表结构:\n%s\n\n用户问题: %s\n\n只输出一条 SELECT/WITH SQL:" % (schema, question)}]
    attempts = []
    sql = ""
    for i in range(max_retry + 1):
        resp = chat_create(
            client,
            model=model,
            messages=messages,
            temperature=0,
        )
        sql = extract_sql(resp.choices[0].message.content or '')
        valid, vmsg = validate_sql(sql)
        if valid:
            try:
                run_sql(conn, sql)
                return sql, attempts   # 执行成功, 提前返回
            except Exception as e:
                err = f"SQL执行报错: {e}"
        else:
            err = f"SQL未通过安全校验: {vmsg}"
        attempts.append({"round": i, "error": err, "sql": sql})
        messages.append({"role": "assistant", "content": sql})
        messages.append({"role": "user", "content": f"你生成的 SQL 有问题: {err}。请修正后重新只输出一条 SQL。"})
    return sql, attempts  # 重试耗尽, 返回最后一次


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题,0=全量")
    ap.add_argument("--model", default=os.environ.get("LLM_MODEL", "gpt-4o-mini"))
    ap.add_argument("--golden-only", action="store_true", help="只校验金标准SQL,不调LLM")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--retrieval", action="store_true", help="用 Schema Retrieval 检索 top-k 表, 不灌全量 schema")
    ap.add_argument("--topk", type=int, default=5, help="检索召回的表数量")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    schema = load_schema(conn)
    retriever = SchemaRetriever.build(conn) if args.retrieval else None
    full_len = len(schema)
    items = [json.loads(l) for l in DATASET.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        items = items[: args.limit]

    # 0) 金标准自检
    bad = []
    for it in items:
        ok, msg = validate_sql(it["golden_sql"])
        if ok:
            try:
                run_sql(conn, it["golden_sql"])
            except Exception as e:
                ok, msg = False, str(e)
        if not ok:
            bad.append((it["id"], msg))
    if bad:
        print("[金标准自检失败]", bad)
        sys.exit(1)
    print("[金标准自检] %d 条 SQL 全部可执行" % len(items))

    if args.golden_only:
        return

    # 1) 安全回归: 破坏性 SQL 必须全被拦截
    safety_cases = [json.loads(l) for l in SAFETY.read_text(encoding="utf-8").splitlines() if l.strip()]
    s_pass = sum(1 for c in safety_cases if not validate_sql(c["sql"])[0])
    print("[安全回归] 拦截 %d/%d 条破坏性 SQL" % (s_pass, len(safety_cases)))

    # 2) LLM 生成 + 评测
    client = OpenAI(
        base_url=os.environ.get("LLM_BASE_URL", "https://aigc.sankuai.com/v1/openai/native"),
        api_key=os.environ.get("LLM_API_KEY", "22026200394732511280"),
    )

    # def eval_one(it):
    #     try:
    #         sql = gen_sql(client, args.model, schema, it["question"])
    #     except Exception as e:
    #         return {**it, "generated_sql": "", "valid": False, "match": False, "reason": "LLM调用失败: %s" % e}
    #     valid, vmsg = validate_sql(sql)
    #     if not valid:
    #         return {**it, "generated_sql": sql, "valid": False, "match": False, "reason": "安全校验拦截: " + vmsg}
    #     match, mmsg = exec_match(conn, it["golden_sql"], sql)
    #     return {**it, "generated_sql": sql, "valid": True, "match": match, "reason": mmsg}
    #

    def eval_one(it):
        q_schema = retriever.retrieve(it["question"], args.topk) if retriever else schema
        try:
            sql, attempts = gen_sql_retry(client, args.model, q_schema, it["question"], conn)
        except Exception as e:
            return {**it, "generated_sql": "", "valid": False, "match": False, "reason": "LLM调用失败: %s" % e, "retries": [], "schema_len": len(q_schema)}
        valid, vmsg = validate_sql(sql)
        if not valid:
            return {**it, "generated_sql": sql, "valid": False, "match": False, "reason": "安全校验拦截: " + vmsg, "retries": attempts, "schema_len": len(q_schema)}
        match, mmsg = exec_match(conn, it["golden_sql"], sql)
        return {**it, "generated_sql": sql, "valid": True, "match": match, "reason": mmsg, "retries": attempts, "schema_len": len(q_schema)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(eval_one, items))

    # 3) 指标
    n = len(results)
    valid_n = sum(r["valid"] for r in results)
    match_n = sum(r["match"] for r in results)
    first_err = sum(1 for r in results if r.get("retries"))
    fixed = sum(1 for r in results if r.get("retries") and r["match"])
    print("首轮报错题数:     %d (重试后答对: %d)" % (first_err, fixed))
    print("\n========== 评测结果 ==========")
    print("题目数:           %d" % n)
    print("SQL 合法率:       %.1f%% (%d/%d)" % (100.0 * valid_n / n, valid_n, n))
    print("执行准确率(Exec Acc): %.1f%% (%d/%d)" % (100.0 * match_n / n, match_n, n))
    avg_schema = sum(r["schema_len"] for r in results) / n
    print("平均 schema 长度:  %d 字符 (全量 %d 字符, 节省 %.0f%%)" % (avg_schema, full_len, 100 * (1 - avg_schema / full_len)))

    print("\n未通过明细:")
    for r in results:
        if not r["match"]:
            print("- [%s#%s] %s" % (r["difficulty"], r["id"], r["question"]))
            print("  原因: %s" % r["reason"])
            print("  生成: %s" % r["generated_sql"][:120].replace("\n", " "))
            print("  金标准: %s" % r["golden_sql"][:120])

    # 4) 报告落盘
    out = Path(__file__).resolve().parent / "eval_report.json"
    out.write_text(json.dumps(
        {"metrics": {"total": n, "valid": valid_n, "exec_acc": match_n,
                     "exec_acc_rate": round(match_n / n, 4),
                     "safety_blocked": s_pass, "safety_total": len(safety_cases),
                     "model": args.model},
         "details": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n报告已写入: %s" % out)

if __name__ == "__main__":
    main()
