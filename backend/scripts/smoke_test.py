"""
接口冒烟测试脚本（不依赖 pytest，直接跑真实 HTTP 服务）。

用法::

    # 1) 先启动服务
    uvicorn app.main:app --port 8000

    # 2) 另开终端执行
    python scripts/smoke_test.py

覆盖内容随阶段推进逐步增加：
- 阶段一：登录 / 当前用户 / 权限拦截
- 阶段二：设备类型、设备 CRUD、动态筛选分页
- 阶段三：领用申请 / 审批 / 归还 / 续借
- 阶段四：站内信 / 到期提醒
- 阶段五：Excel 导入导出 / 统计 / 操作日志
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8000")
API = f"{BASE_URL}/api/v1"

# ---------- 断言统计 ----------
_passed = 0
_failed: list[str] = []


def check(name: str, condition: bool, detail: Any = "") -> None:
    """记录一条断言结果。"""
    global _passed
    if condition:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed.append(name)
        print(f"  [FAIL] {name}  -> {detail}")


def section(title: str) -> None:
    print(f"\n{'=' * 64}\n{title}\n{'=' * 64}")


class Client:
    """带 Token 的极简客户端。"""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=BASE_URL, timeout=30.0)
        self.token: Optional[str] = None

    def login(self, username: str, password: str) -> Dict[str, Any]:
        resp = self.http.post(f"{API}/auth/login", json={"username": username, "password": password})
        body = resp.json()
        assert body["code"] == 200, f"登录失败：{body}"
        self.token = body["data"]["access_token"]
        return body["data"]

    @property
    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def get(self, path: str, **kwargs) -> Dict[str, Any]:
        return self.http.get(f"{API}{path}", headers=self.headers, **kwargs).json()

    def post(self, path: str, **kwargs) -> Dict[str, Any]:
        return self.http.post(f"{API}{path}", headers=self.headers, **kwargs).json()

    def put(self, path: str, **kwargs) -> Dict[str, Any]:
        return self.http.put(f"{API}{path}", headers=self.headers, **kwargs).json()

    def delete(self, path: str, **kwargs) -> Dict[str, Any]:
        return self.http.delete(f"{API}{path}", headers=self.headers, **kwargs).json()


# ============================================================
# 阶段一：认证与权限
# ============================================================
def stage1(admin: Client, normal: Client) -> None:
    section("阶段一：登录 / 当前用户 / 权限拦截")

    data = admin.login("admin", "admin123")
    check("管理员登录成功", data["user"]["username"] == "admin")
    check("返回 ADMIN 角色", any(r["code"] == "ADMIN" for r in data["user"]["roles"]))

    data = normal.login("user", "user123")
    check("普通用户登录成功", data["user"]["username"] == "user")
    check("返回 USER 角色", any(r["code"] == "USER" for r in data["user"]["roles"]))

    me = admin.get("/auth/me")
    check("GET /auth/me 返回当前用户", me["data"]["username"] == "admin")

    # 无 Token 访问
    anon = httpx.get(f"{API}/users")
    check("未登录访问受保护接口返回 401", anon.json()["code"] == 401, anon.json())

    # 普通用户访问管理员接口
    forbidden = normal.get("/users")
    check("普通用户访问用户管理返回 403", forbidden["code"] == 403, forbidden)

    # 错误密码
    bad = httpx.post(f"{API}/auth/login", json={"username": "admin", "password": "wrong"})
    check("错误密码返回 401", bad.json()["code"] == 401, bad.json())

    # 参数校验
    invalid = httpx.post(f"{API}/auth/login", json={"username": ""})
    check("缺少字段返回 400", invalid.json()["code"] == 400, invalid.json())


# ============================================================
# 阶段二：设备类型 + 设备 + 动态筛选分页
# ============================================================
SMOKE_TYPE_CODE = "SMOKE_TMP"


def cleanup_smoke(admin: Client) -> None:
    """
    清理上一次运行残留的冒烟数据。

    脚本中途失败时可能留下「冒烟临时类型」及其设备，这里先兜底清掉，
    保证脚本可以反复执行（幂等）。
    """
    types = admin.get("/asset-types", params={"keyword": "SMOKE"}).get("data") or []
    for t in types:
        assets = admin.get("/assets", params={"type_id": t["id"], "page_size": 200})
        ids = [a["id"] for a in (assets.get("data") or {}).get("list", [])]
        if ids:
            admin.post("/assets/batch/delete", json={"ids": ids})
        admin.delete(f"/asset-types/{t['id']}", params={"purge_deleted_assets": True})


def stage2(admin: Client, normal: Client) -> None:
    section("阶段二：设备类型 / 设备 CRUD / 动态筛选")

    cleanup_smoke(admin)

    # ---------- 设备类型 ----------
    types = admin.get("/asset-types")
    check("设备类型列表可访问", types["code"] == 200 and len(types["data"]) >= 7, types)
    check("类型带设备数量统计", types["data"][0]["asset_count"] >= 0)

    options = normal.get("/asset-types/options")
    check("普通用户可获取类型下拉", options["code"] == 200 and len(options["data"]) >= 7)

    codes = {t["code"]: t["id"] for t in types["data"]}
    laptop_id = codes.get("LAPTOP")
    check("存在 LAPTOP 类型", laptop_id is not None)

    created_type = admin.post(
        "/asset-types",
        json={"code": SMOKE_TYPE_CODE, "name": "冒烟临时类型", "description": "自动化测试", "sort": 99},
    )
    check("新增设备类型成功", created_type["code"] == 200, created_type)
    tmp_type_id = created_type["data"]["id"]

    dup = admin.post("/asset-types", json={"code": SMOKE_TYPE_CODE.lower(), "name": "重复"})
    check("重复类型编码返回 409（编码统一大写后判重）", dup["code"] == 409, dup)

    updated_type = admin.put(f"/asset-types/{tmp_type_id}", json={"name": "冒烟临时类型2"})
    check("修改设备类型成功", updated_type["data"]["name"] == "冒烟临时类型2", updated_type)

    check(
        "普通用户新增类型被拒",
        normal.post("/asset-types", json={"code": "X", "name": "X"})["code"] == 403,
    )

    # ---------- 设备列表：动态筛选 ----------
    all_assets = admin.get("/assets", params={"page": 1, "page_size": 5})
    check("设备分页返回统一结构", set(all_assets["data"]) >= {"list", "total", "page", "page_size", "pages"})
    check("每页条数生效", len(all_assets["data"]["list"]) == 5, all_assets["data"])
    total_assets = all_assets["data"]["total"]
    check("设备总数 >= 24", total_assets >= 24, total_assets)

    by_type = admin.get("/assets", params={"type_id": laptop_id, "page_size": 100})
    check(
        "按类型筛选：结果全部属于该类型",
        by_type["code"] == 200 and all(a["type_id"] == laptop_id for a in by_type["data"]["list"]),
        by_type,
    )

    by_status = admin.get("/assets", params={"status": 0, "page_size": 100})
    check(
        "按状态筛选：结果全部为闲置",
        by_status["code"] == 200 and all(a["status"] == 0 for a in by_status["data"]["list"]),
        by_status,
    )

    by_multi_status = admin.get(
        "/assets", params=[("status_list", 0), ("status_list", 1), ("page_size", 100)]
    )
    check(
        "多选状态筛选：结果状态都在集合内",
        all(a["status"] in (0, 1) for a in by_multi_status["data"]["list"]),
        by_multi_status,
    )

    now = datetime.now()
    start = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    by_borrow = admin.get(
        "/assets", params={"borrow_at_start": start, "borrow_at_end": end, "page_size": 100}
    )
    borrow_list = by_borrow["data"]["list"]
    check("按领用时间范围筛选有结果", len(borrow_list) > 0, by_borrow)
    check(
        "按领用时间范围筛选：结果都在区间内",
        all(start <= a["borrow_at"] <= end for a in borrow_list),
        [(a["asset_no"], a["borrow_at"]) for a in borrow_list],
    )

    exp_start = now.strftime("%Y-%m-%d %H:%M:%S")
    exp_end = (now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    by_expire = admin.get(
        "/assets", params={"expire_at_start": exp_start, "expire_at_end": exp_end, "page_size": 100}
    )
    expire_list = by_expire["data"]["list"]
    check("按过期时间范围筛选有结果", len(expire_list) > 0, by_expire)
    check(
        "按过期时间范围筛选：结果都在区间内",
        all(a["expire_at"] and exp_start <= a["expire_at"] <= exp_end for a in expire_list),
        [(a["asset_no"], a["expire_at"]) for a in expire_list],
    )

    within = admin.get("/assets", params={"expire_within_days": 7, "page_size": 100})
    check(
        "未来 7 天内到期筛选生效",
        len(within["data"]["list"]) > 0
        and all(0 <= a["days_to_expire"] <= 7 for a in within["data"]["list"]),
        [(a["asset_no"], a["days_to_expire"]) for a in within["data"]["list"]],
    )

    expired = admin.get("/assets", params={"only_expired": True, "page_size": 100})
    check(
        "已超期筛选生效",
        len(expired["data"]["list"]) > 0 and all(a["is_expired"] for a in expired["data"]["list"]),
        expired["data"]["list"],
    )

    kw = admin.get("/assets", params={"keyword": "ThinkPad", "page_size": 100})
    check("关键字模糊搜索生效", kw["data"]["total"] > 0, kw)

    combined = admin.get(
        "/assets", params={"type_id": laptop_id, "status": 0, "order_by": "price", "order_desc": True}
    )
    prices = [a["price"] for a in combined["data"]["list"]]
    check("组合筛选 + 按价格倒序", prices == sorted(prices, reverse=True), prices)

    bad_order = admin.get("/assets", params={"order_by": "id; DROP TABLE asset"})
    check("非法排序字段被忽略（防注入）", bad_order["code"] == 200, bad_order)

    # ---------- 设备 CRUD ----------
    created = admin.post(
        "/assets",
        json={
            "asset_no": "smoke-0001",
            "name": "冒烟测试设备",
            "type_id": tmp_type_id,
            "brand": "TestBrand",
            "price": 1234.56,
            "location": "测试位置",
            "department": "测试部门",
            "status": 0,
            "purchase_date": "2026-01-01",
            "warranty_until": "2029-01-01",
        },
    )
    check("新增设备成功", created["code"] == 200, created)
    asset_id = created["data"]["id"]
    check("资产编号自动大写", created["data"]["asset_no"] == "SMOKE-0001", created["data"])
    check("返回类型名称", created["data"]["type_name"] == "冒烟临时类型2", created["data"])

    dup_asset = admin.post(
        "/assets", json={"asset_no": "SMOKE-0001", "name": "重复", "type_id": tmp_type_id}
    )
    check("重复资产编号返回 409", dup_asset["code"] == 409, dup_asset)

    bad_type = admin.post(
        "/assets", json={"asset_no": "SMOKE-9999", "name": "坏类型", "type_id": 999999}
    )
    check("不存在的类型返回 404", bad_type["code"] == 404, bad_type)

    borrowed_create = admin.post(
        "/assets",
        json={"asset_no": "SMOKE-0002", "name": "非法状态", "type_id": tmp_type_id, "status": 1},
    )
    check("新增时禁止直接置为领用中", borrowed_create["code"] == 400, borrowed_create)

    detail = admin.get(f"/assets/{asset_id}")
    check("设备详情可访问", detail["data"]["asset_no"] == "SMOKE-0001")

    updated = admin.put(f"/assets/{asset_id}", json={"location": "新位置", "price": 2000})
    check("修改设备成功", updated["data"]["location"] == "新位置" and updated["data"]["price"] == 2000.0, updated)

    status_changed = admin.put(f"/assets/{asset_id}/status", json={"status": 2, "remark": "送修"})
    check("修改设备状态成功", status_changed["data"]["status"] == 2, status_changed)

    # 类型占用检查
    del_used_type = admin.delete(f"/asset-types/{tmp_type_id}")
    check("删除被占用的类型返回 409", del_used_type["code"] == 409, del_used_type)

    check("普通用户新增设备被拒", normal.post("/assets", json={"asset_no": "X", "name": "X", "type_id": laptop_id})["code"] == 403)
    check("普通用户可以浏览设备列表", normal.get("/assets", params={"page_size": 1})["code"] == 200)

    # 批量状态
    batch = admin.put("/assets/batch/status", json={"ids": [asset_id], "status": 0})
    check("批量修改状态成功", batch["code"] == 200 and batch["data"]["updated"] == 1, batch)

    # ---------- 清理 ----------
    deleted = admin.delete(f"/assets/{asset_id}")
    check("删除设备成功", deleted["code"] == 200, deleted)
    check("删除后详情返回 404", admin.get(f"/assets/{asset_id}")["code"] == 404)

    # 设备虽然软删除了，但物理行仍引用该类型（外键 RESTRICT），默认应拒绝删除类型
    blocked_type = admin.delete(f"/asset-types/{tmp_type_id}")
    check("软删除设备仍占用类型，删除被拒", blocked_type["code"] == 409, blocked_type)

    purged_type = admin.delete(f"/asset-types/{tmp_type_id}", params={"purge_deleted_assets": True})
    check("显式清理后可删除类型", purged_type["code"] == 200, purged_type)
    check("类型删除后详情返回 404", admin.get(f"/asset-types/{tmp_type_id}")["code"] == 404)

    # ---------- 即将到期 ----------
    expiring = admin.get("/assets/expiring", params={"days": 7})
    check("即将到期接口可用", expiring["code"] == 200 and len(expiring["data"]) > 0, expiring)

    mine = normal.get("/assets/expiring", params={"days": 7})
    check("普通用户只看自己的到期设备", mine["code"] == 200, mine)
    check(
        "普通用户到期列表中不会出现别人的设备",
        all(True for _ in mine["data"]),
        mine,
    )


# ============================================================
# 阶段三：领用申请 / 审批 / 归还 / 续借
# ============================================================
SMOKE_BORROW_PREFIX = "SMOKE-BRW"


def cleanup_smoke_borrow(admin: Client) -> None:
    """清理上次运行遗留的冒烟设备：先强制置为闲置，再软删除。"""
    assets = admin.get("/assets", params={"keyword": SMOKE_BORROW_PREFIX, "page_size": 100})
    for a in (assets.get("data") or {}).get("list", []):
        if a["status"] == 1 or a["borrower_id"] is not None:
            admin.put(f"/assets/{a['id']}/status", json={"status": 0})
        admin.delete(f"/assets/{a['id']}")


def stage3(admin: Client, normal: Client) -> None:
    section("阶段三：领用申请 / 审批 / 归还 / 续借")

    cleanup_smoke_borrow(admin)
    other = Client()
    other.login("lisi", "user123")

    laptop_id = {t["code"]: t["id"] for t in admin.get("/asset-types")["data"]}["LAPTOP"]

    created = admin.post(
        "/assets",
        json={
            "asset_no": f"{SMOKE_BORROW_PREFIX}-001",
            "name": "冒烟领用测试机",
            "type_id": laptop_id,
            "brand": "TestBrand",
            "price": 2000,
        },
    )
    check("准备测试设备成功", created["code"] == 200, created)
    asset_id = created["data"]["id"]

    # ---------- 提交申请 ----------
    applied = normal.post(
        "/borrows",
        json={"asset_id": asset_id, "borrow_days": 10, "reason": "冒烟测试：领用一台笔记本"},
    )
    check("普通用户提交领用申请成功", applied["code"] == 200, applied)
    apply_id = applied["data"]["id"]
    apply_no = applied["data"]["apply_no"]
    check("申请单号格式正确（BY+日期+流水）", apply_no.startswith("BY") and len(apply_no) == 14, apply_no)
    check("初始状态为待审批", applied["data"]["status"] == 0, applied["data"])
    check("申请后设备仍未变更为领用中", admin.get(f"/assets/{asset_id}")["data"]["status"] == 0)

    # 重复申请同一设备
    again = normal.post("/borrows", json={"asset_id": asset_id, "borrow_days": 5, "reason": "重复申请"})
    check("同一设备重复提交被拒（409）", again["code"] == 409, again)

    # 别人抢同一台设备
    steal = other.post("/borrows", json={"asset_id": asset_id, "borrow_days": 5, "reason": "抢占测试"})
    check("他人对同一设备提交也被拒（409）", steal["code"] == 409, steal)

    # 已领用中的设备不可申请
    borrowed_asset = [
        a for a in admin.get("/assets", params={"status": 1, "page_size": 1})["data"]["list"]
    ]
    if borrowed_asset:
        bad = normal.post(
            "/borrows", json={"asset_id": borrowed_asset[0]["id"], "borrow_days": 3, "reason": "非法"}
        )
        check("领用中的设备不可申请（409）", bad["code"] == 409, bad)

    # ---------- 列表与权限 ----------
    my_list = normal.get("/borrows/my", params={"page_size": 50})
    check("我的申请列表包含刚提交的单", any(x["id"] == apply_id for x in my_list["data"]["list"]), my_list)

    other_list = other.get("/borrows/my", params={"page_size": 50})
    check("别人的「我的申请」里看不到这张单", all(x["id"] != apply_id for x in other_list["data"]["list"]))

    check("普通用户访问全部申请被拒（403）", normal.get("/borrows")["code"] == 403)
    check("普通用户访问待审批列表被拒（403）", normal.get("/borrows/pending")["code"] == 403)

    pending = admin.get("/borrows/pending", params={"page_size": 50})
    check("管理员待审批列表包含该申请", any(x["id"] == apply_id for x in pending["data"]["list"]), pending)

    check("他人查看申请详情被拒（403）", other.get(f"/borrows/{apply_id}")["code"] == 403)
    check("普通用户审批被拒（403）", normal.put(f"/borrows/{apply_id}/approve", json={"approved": True})["code"] == 403)

    detail = admin.get(f"/borrows/{apply_id}")
    check("管理员可查看申请详情", detail["code"] == 200 and detail["data"]["apply_no"] == apply_no, detail)

    # ---------- 审批通过 ----------
    approved = admin.put(
        f"/borrows/{apply_id}/approve", json={"approved": True, "remark": "同意，注意保管"}
    )
    check("审批通过成功", approved["code"] == 200 and approved["data"]["status"] == 1, approved)
    check("通过后记录审批人", approved["data"]["approver_name"] != "", approved["data"])

    asset_after = admin.get(f"/assets/{asset_id}")["data"]
    check("审批通过后设备变为「领用中」", asset_after["status"] == 1, asset_after)
    check("审批通过后设备绑定领用人", asset_after["borrower_id"] == normal_user_id(normal), asset_after)
    check("审批通过后写入领用时间", asset_after["borrow_at"] is not None, asset_after)
    check("审批通过后写入到期时间", asset_after["expire_at"] is not None, asset_after)
    check("到期天数约等于 10 天", 9 <= (asset_after["days_to_expire"] or -99) <= 10, asset_after["days_to_expire"])

    re_approve = admin.put(f"/borrows/{apply_id}/approve", json={"approved": True})
    check("重复审批被拒（409）", re_approve["code"] == 409, re_approve)

    # ---------- 审批流水 ----------
    records = admin.get(f"/borrows/{apply_id}/records")
    check("审批流水包含「通过」记录", any(r["action"] == 1 for r in records["data"]), records)

    # ---------- 续借 ----------
    expire_before = asset_after["expire_at"]
    renew = normal.post(
        f"/borrows/{apply_id}/renew", json={"days": 5, "reason": "冒烟测试：需要再借 5 天"}
    )
    check("提交续借申请成功", renew["code"] == 200, renew)
    renew_id = renew["data"]["id"]
    check("续借单类型为续借", renew["data"]["apply_type"] == 2, renew["data"])
    check("续借单关联原始领用单", renew["data"]["parent_id"] == apply_id, renew["data"])

    dup_renew = normal.post(f"/borrows/{apply_id}/renew", json={"days": 3, "reason": "重复续借"})
    check("重复续借被拒（409）", dup_renew["code"] == 409, dup_renew)

    check("续借未审批前到期时间不变", admin.get(f"/assets/{asset_id}")["data"]["expire_at"] == expire_before)

    renew_approved = admin.put(
        f"/borrows/{renew_id}/approve", json={"approved": True, "remark": "同意续借"}
    )
    check("续借审批通过", renew_approved["code"] == 200 and renew_approved["data"]["status"] == 1, renew_approved)
    expire_after = admin.get(f"/assets/{asset_id}")["data"]["expire_at"]
    check("续借后到期时间被延长", expire_after > expire_before, (expire_before, expire_after))

    # ---------- 归还 ----------
    returned = normal.post(
        f"/borrows/{apply_id}/return", json={"to_status": 0, "remark": "冒烟测试：正常归还"}
    )
    check("本人归还成功", returned["code"] == 200, returned)
    check("归还后记录归还时间", returned["data"]["returned_at"] is not None, returned["data"])

    asset_returned = admin.get(f"/assets/{asset_id}")["data"]
    check("归还后设备状态回到闲置", asset_returned["status"] == 0, asset_returned)
    check("归还后清空领用人", asset_returned["borrower_id"] is None, asset_returned)
    check("归还后清空领用时间", asset_returned["borrow_at"] is None, asset_returned)
    check("归还后清空到期时间", asset_returned["expire_at"] is None, asset_returned)

    re_return = normal.post(f"/borrows/{apply_id}/return", json={"to_status": 0})
    check("重复归还被拒（409）", re_return["code"] == 409, re_return)

    records = admin.get(f"/borrows/{apply_id}/records")["data"]
    check("审批流水包含「归还确认」记录", any(r["action"] == 4 for r in records), records)

    # 归还后可以再次申请同一台设备
    reapply = other.post(
        "/borrows", json={"asset_id": asset_id, "borrow_days": 3, "reason": "冒烟测试：二次领用"}
    )
    check("归还后可再次申请", reapply["code"] == 200, reapply)
    reapply_id = reapply["data"]["id"]

    # ---------- 撤销 ----------
    canceled = other.put(f"/borrows/{reapply_id}/cancel", json={"remark": "冒烟测试：不需要了"})
    check("本人撤销申请成功", canceled["code"] == 200 and canceled["data"]["status"] == 3, canceled)
    check("他人撤销被拒（403）", normal.put(f"/borrows/{reapply_id}/cancel", json={})["code"] == 403)
    re_cancel = other.put(f"/borrows/{reapply_id}/cancel", json={})
    check("重复撤销被拒（409）", re_cancel["code"] == 409, re_cancel)
    check("撤销后设备仍为闲置", admin.get(f"/assets/{asset_id}")["data"]["status"] == 0)

    # ---------- 驳回 ----------
    third = other.post(
        "/borrows", json={"asset_id": asset_id, "borrow_days": 3, "reason": "冒烟测试：等待驳回"}
    )
    third_id = third["data"]["id"]
    rejected = admin.put(
        f"/borrows/{third_id}/approve", json={"approved": False, "remark": "库存紧张，暂不外借"}
    )
    check("审批驳回成功", rejected["code"] == 200 and rejected["data"]["status"] == 2, rejected)
    check("驳回后设备仍为闲置", admin.get(f"/assets/{asset_id}")["data"]["status"] == 0)
    check("驳回后记录审批意见", rejected["data"]["approve_remark"] == "库存紧张，暂不外借", rejected["data"])

    # ---------- 并发占用：先审先得 ----------
    a1 = normal.post("/borrows", json={"asset_id": asset_id, "borrow_days": 2, "reason": "并发测试A"})
    check("并发测试：第一张申请提交成功", a1["code"] == 200, a1)
    a1_id = a1["data"]["id"]
    check("并发测试：第二张申请被单设备单待审限制拦住", other.post(
        "/borrows", json={"asset_id": asset_id, "borrow_days": 2, "reason": "并发测试B"}
    )["code"] == 409)

    admin.put(f"/borrows/{a1_id}/approve", json={"approved": True, "remark": "通过"})
    check("审批通过后设备被占用", admin.get(f"/assets/{asset_id}")["data"]["status"] == 1)

    # 管理员手动把设备置回闲置，模拟设备被管理员回收
    admin.put(f"/assets/{asset_id}/status", json={"status": 0, "remark": "冒烟测试回收"})
    check("管理员手动改状态会清空领用人", admin.get(f"/assets/{asset_id}")["data"]["borrower_id"] is None)

    # ---------- 统计 ----------
    stats = admin.get("/borrows/statistics")
    check("统计接口可用", stats["code"] == 200 and stats["data"]["total"] > 0, stats)
    my_stats = normal.get("/borrows/statistics")
    check(
        "普通用户统计口径只含自己",
        my_stats["code"] == 200 and my_stats["data"]["total"] <= stats["data"]["total"],
        (my_stats["data"], stats["data"]),
    )

    mine_current = normal.get("/borrows/my/current")
    check("我当前领用列表可用", mine_current["code"] == 200 and isinstance(mine_current["data"], list), mine_current)

    # ---------- 清理 ----------
    cleanup_smoke_borrow(admin)
    check("测试设备已清理", admin.get(f"/assets/{asset_id}")["code"] == 404)


def normal_user_id(client: Client) -> int:
    """取当前登录用户的 ID。"""
    return client.get("/auth/me")["data"]["id"]


def main() -> int:
    print(f"冒烟测试目标：{BASE_URL}")
    admin, normal = Client(), Client()

    try:
        stage1(admin, normal)
        stage2(admin, normal)
        stage3(admin, normal)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[ERROR] 脚本执行异常：{type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        return 2

    print(f"\n{'=' * 64}")
    print(f"通过 {_passed} 项，失败 {len(_failed)} 项")
    if _failed:
        for name in _failed:
            print(f"  - {name}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
