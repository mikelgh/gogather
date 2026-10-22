"""票价验价测试：编号提取 / 结构化解析 / 对比报告。"""
from app import price_check as pc


class FakeFlyai:
    """flyai 模块替身：记录调用参数，按 (origin, destination) 返回预置结果。"""

    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def _search(self, **kwargs):
        self.calls.append(kwargs)
        key = (kwargs.get("origin"), kwargs.get("destination"))
        return self.results.get(key, {"data": {"itemList": []}})

    def search_train(self, **kwargs):
        return self._search(**kwargs)

    def search_flight(self, **kwargs):
        return self._search(**kwargs)


def _offer(no, price):
    return {"ticketPrice": price, "journeys": [
        {"segments": [{"marketingTransportNo": no, "depDateTime": "2026-11-01 23:00",
                       "arrDateTime": "2026-11-02 01:35", "seatClassName": "经济舱"}]}
    ]}


class TestExtractTransportNos:
    def test_flight_and_train(self):
        nos = pc._extract_transport_nos("广州飞往三亚（AQ1113）；返程高铁G7502")
        assert "AQ1113" in nos
        assert "G7502" in nos

    def test_dedup(self):
        assert pc._extract_transport_nos("AQ1113 AQ1113") == ["AQ1113"]

    def test_no_match(self):
        assert pc._extract_transport_nos("西湖漫步，灵隐寺祈福") == []

    def test_digits_inside_word_not_matched(self):
        assert pc._extract_transport_nos("COVID19 Variant") == []


class TestParseOffers:
    def test_structured_item_list(self):
        data = {"itemList": [_offer("AQ1112", 549)]}
        offers = pc.parse_offers(data)
        assert offers["AQ1112"]["price"] == 549
        assert offers["AQ1112"]["dep"] == "2026-11-01 23:00"

    def test_non_dict(self):
        assert pc.parse_offers("plain text") == {}
        assert pc.parse_offers(None) == {}


class TestVerifyPlanPrices:
    def _plan(self):
        return {
            "params": {"city": "三亚", "origin": "广州",
                       "check_in": "2026-11-01", "check_out": "2026-11-03"},
            "timeline": [
                {"time_range": "Day 1 23:00-01:35", "title": "广州飞往三亚（AQ1113）", "price": "¥549/人"},
                {"time_range": "Day 3 22:20-00:10", "title": "三亚飞往广州（AQ1112）", "price": "¥600/人"},
                {"time_range": "Day 2 09:00-11:00", "title": "天涯海角", "price": "¥81/人"},
            ],
        }

    def test_unchanged_and_changed(self):
        fake = FakeFlyai(results={
            ("广州", "三亚"): {"data": {"itemList": [_offer("AQ1113", 551)]}},
            ("三亚", "广州"): {"data": {"itemList": [_offer("AQ1112", 700)]}},
        })
        report = pc.verify_plan_prices(self._plan(), fake)
        by_no = {r["transport_no"]: r for r in report["items"]}
        assert len(report["items"]) == 2  # 非交通项不参与
        assert by_no["AQ1113"]["status"] == "unchanged"
        assert by_no["AQ1113"]["day"] == 1
        assert by_no["AQ1112"]["status"] == "changed"
        assert by_no["AQ1112"]["new_price"] == 700
        assert by_no["AQ1112"]["diff"] == 100
        # 只有航班号 → 只发起 2 条查询（去程+返程）
        assert len(fake.calls) == 2

    def test_not_found(self):
        fake = FakeFlyai()  # 全部返回空
        report = pc.verify_plan_prices(self._plan(), fake)
        assert all(r["status"] == "not_found" for r in report["items"])
        assert report["summary"]["not_found"] == 2

    def test_no_old_price(self):
        plan = self._plan()
        plan["timeline"][0]["price"] = "点击预订链接查看实时价格"
        fake = FakeFlyai(results={("广州", "三亚"): {"data": {"itemList": [_offer("AQ1113", 551)]}}})
        report = pc.verify_plan_prices(plan, fake)
        by_no = {r["transport_no"]: r for r in report["items"]}
        assert by_no["AQ1113"]["status"] == "no_old_price"
        assert by_no["AQ1113"]["new_price"] == 551

    def test_no_transport_items(self):
        plan = {"params": {"city": "杭州"}, "timeline": [{"title": "西湖漫步", "price": "免费"}]}
        fake = FakeFlyai()
        report = pc.verify_plan_prices(plan, fake)
        assert report["items"] == []
        assert len(fake.calls) == 0  # 无交通项不发起任何查询

    def test_offer_without_price(self):
        plan = {
            "params": {"city": "三亚", "origin": "广州", "check_in": "2026-11-01", "check_out": "2026-11-03"},
            "timeline": [{"time_range": "Day 1 23:00", "title": "广州飞往三亚（AQ1113）", "price": "¥549/人"}],
        }
        fake = FakeFlyai(results={("广州", "三亚"): {"data": {"itemList": [{"ticketPrice": None, "journeys": [{"segments": [{"marketingTransportNo": "AQ1113"}]}]}]}}})
        report = pc.verify_plan_prices(plan, fake)
        assert report["items"][0]["status"] == "no_price"

    def test_train_numbers_trigger_train_queries(self):
        plan = {
            "params": {"city": "上海", "origin": "杭州", "check_in": "2026-11-01", "check_out": "2026-11-02"},
            "timeline": [{"time_range": "Day 2 22:22-23:14", "title": "杭州至上海（高铁G7536）", "price": "¥73/人"}],
        }
        fake = FakeFlyai(results={("杭州", "上海"): {"data": {"itemList": [_offer("G7536", 73)]}}})
        report = pc.verify_plan_prices(plan, fake)
        assert report["items"][0]["status"] == "unchanged"
        assert report["items"][0]["kind"] == "train"
        assert all(c.get("origin") == "杭州" or c.get("origin") == "上海" for c in fake.calls)
        assert len(fake.calls) == 2
