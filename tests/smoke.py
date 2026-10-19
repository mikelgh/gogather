"""开发自测：验证 flyai 调用与规划编排端到端跑通。"""
import os
import sys

sys.path.insert(0, os.getcwd())

from app import flyai_client, planner


def main():
    print("=== flyai ai-search（原始调用）===")
    r = flyai_client.ai_search("杭州 西湖附近酒店 10月25日入住")
    data = flyai_client.extract_data(r)
    print("status:", r.get("status"), "| 字数:", len(data))
    print(data[:300])

    print("\n=== planner.plan（活动/聚会出行 模式）===")
    res = planner.plan(
        {
            "mode": "event",
            "city": "杭州",
            "check_in": "2026-10-25",
            "check_out": "2026-10-26",
            "vibe": "湖景",
        }
    )
    print("used_llm:", res["used_llm"], "| city:", res["city"], "| 区段数:", len(res["sections"]))
    print(res["markdown"][:600])


if __name__ == "__main__":
    main()
