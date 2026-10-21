#!/usr/bin/env python3
"""
性能测试脚本：验证 flyai 并行查询 + 百炼快速模型 的提速效果
"""
import requests
import time
import json

BASE_URL = "https://gogather.partystack.cn"

def test_plan_generation():
    """测试方案生成性能"""
    print("=" * 60)
    print("GoGather 性能测试")
    print("=" * 60)
    
    # 测试用例：成都 30 人聚会
    test_cases = [
        {
            "name": "成都聚会（标准）",
            "message": "我们 30 个人想去成都聚会，从广州出发，11 月 15 日入住，11 月 17 日退房，预算舒适型",
        },
        {
            "name": "三亚团建（含亲子）",
            "message": "公司 20 人去三亚团建，带 5 个小孩（3-8 岁），12 月 1 日到 12 月 3 日，经济型预算",
        },
    ]
    
    for case in test_cases:
        print(f"\n测试用例：{case['name']}")
        print(f"输入：{case['message']}")
        print("-" * 60)
        
        # 需要先登录获取 token（这里假设用户手动提供）
        print("⚠️  需要先登录获取认证 token")
        print("请在浏览器中登录后，从 localStorage 复制 token")
        break

if __name__ == "__main__":
    test_plan_generation()
