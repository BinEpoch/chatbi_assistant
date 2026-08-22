import asyncio
from tools import list_tables, describe_table, get_table_sample, execute_sql


async def main():
    r1 = await list_tables.ainvoke({})
    print("list_tables:", r1)
    print("---")

    r2 = await describe_table.ainvoke({"table_name": "invoices"})
    print("describe_table:", r2)
    print("---")

    r3 = await get_table_sample.ainvoke({"table_name": "invoices", "invoices": 3})
    print("get_table_sample:", r3)
    print("---")

    r4 = await execute_sql.ainvoke({"sql": "SELECT COUNT(*) FROM invoices"})
    print("execute_sql:", r4)


if __name__ == '__main__':
    asyncio.run(main())