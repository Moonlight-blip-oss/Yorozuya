# -*- coding: utf-8 -*-
"""天气 / 气候工具：weather_now · weather_forecast · weather_history · weather_climate

★ 数据源选 **Open-Meteo**：免费、**不需要 API Key**。
  这个项目有一条硬规矩 ——「能替她做就别让她配置」。要 Key 的工具等于要她先去注册账号、
  申请、再把 Key 填进来；多数人走到第二步就放弃了。Open-Meteo 不用（公开模式数据，非商业免费），
  所以装好即用、零配置。

★ 风险等级 = **exec（要审批）**：请求发到本机之外；而且**地名本身就是位置信息**，
  「有东西离开这台机器」这件事必须让人知道 —— 与 `web_fetch` 同一口径。
  一次「本会话允许该工具」之后不再反复问。

★ 输出里**写清数据来源**：历史天气来自 ERA5 **再分析**（模式回算），与当地气象站实测会有出入，
  不能当成"官方记录"引用。数字原样给，不替她抹平（这是项目里反复强调的一条）。

能力边界（都写进工具描述，别让模型乱试）：
· 预报最长 16 天；历史最早 1940 年；**一次最多 366 天逐日**。
· 常年气候按**月**聚合（这才是"气候"该有的形状）；要某几天的实况走 `weather_history`。
· 不做分钟级降水、不做空气质量/气象预警（那些是另外的数据源，不假装能做）。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

from .. import common
from .base import ToolResult, exec_tool

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

TIMEOUT = 25
UA = "YorozuyaAgent/1.0 (local workbench; weather)"

FORECAST_MAX_DAYS = 16
MAX_HISTORY_DAYS = 366
CLIMATE_YEARS_DEFAULT = 8
CLIMATE_YEARS_MAX = 12
ARCHIVE_LAG_DAYS = 7      # 存档接口有 ~5 天延迟，留点余量；更近的过去走 forecast 的 past_days

# WMO 天气码 → 中文。模型拿到 "3" 是没用的，必须翻成人看得懂的话。
WMO = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "毛毛雨（小）", 53: "毛毛雨（中）", 55: "毛毛雨（大）",
    56: "冻毛毛雨（小）", 57: "冻毛毛雨（大）",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨（小）", 67: "冻雨（大）",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨（小）", 81: "阵雨（中）", 82: "阵雨（大）",
    85: "阵雪（小）", 86: "阵雪（大）",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴大冰雹",
}
_DIRS = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
_WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
_MON = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"]

SOURCE_NOTE = "数据来源：Open-Meteo（免费 · 无需 Key）"
ARCHIVE_NOTE = "数据来源：Open-Meteo 存档（ERA5 再分析 —— 与当地气象站实测会略有出入）"

# ---------------- 进程内缓存 ----------------
# 问一次天气就发一次请求太浪费（而且她很可能连问几句）。带 TTL 的小缓存即可，
# 不落库、不跨进程 —— 进程重启就没了，不需要一致性保证。
_CACHE: dict = {}
_LOCK = threading.Lock()
_CACHE_MAX = 240


def _cache_get(key, ttl: float):
    with _LOCK:
        hit = _CACHE.get(key)
    if not hit:
        return None
    ts, val = hit
    return val if (time.time() - ts) <= ttl else None


def _cache_put(key, val) -> None:
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:            # 长命进程里别让它无限长
            for k in list(_CACHE)[: _CACHE_MAX // 2]:
                _CACHE.pop(k, None)
        _CACHE[key] = (time.time(), val)


# ---------------- 取数 ----------------

def _api_reason(body: str) -> str:
    """Open-Meteo 出错时给的是 {"error":true,"reason":"..."} —— 把 reason 原样带出来。"""
    try:
        d = json.loads(body or "")
    except Exception:
        return ""
    return str(d.get("reason") or "") if isinstance(d, dict) else ""


def _get_json(url: str) -> tuple:
    """返回 (ok, 数据, 说明)。**失败必须带原因**（这条纪律是踩过坑才定下的）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:        # noqa: S310（URL 全由本模块拼）
            raw = r.read(4 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(4000).decode("utf-8", "replace")
        except Exception:
            pass
        reason = _api_reason(body)
        return False, None, (reason or f"天气服务返回 HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        return False, None, (f"连不上天气服务（{e.reason}）。本机网络或代理不通时就是这样；"
                             f"如果只是抽风，过一会儿再试一次。")
    except Exception as e:                                            # noqa: BLE001
        return False, None, f"{type(e).__name__}: {e}"
    try:
        return True, json.loads(raw.decode("utf-8", "replace")), ""
    except Exception as e:                                            # noqa: BLE001
        return False, None, f"天气服务返回的不是 JSON（{type(e).__name__}: {e}）"


_COORD_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,，]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


def parse_coords(place: str):
    """「纬度,经度」→ (lat, lon)；不是这个形状或超出范围就返回 None。"""
    m = _COORD_RE.match(place or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _label_of(r: dict) -> str:
    """「北京（中国 北京市）」—— 去掉重复的层级（admin2 常和 admin1 一模一样）。"""
    parts = []
    for k in ("country", "admin1", "admin2"):
        v = str(r.get(k) or "").strip()
        if v and v not in parts:
            parts.append(v)
    inner = " ".join(parts[:2])
    return f"{r.get('name')}（{inner}）" if inner else str(r.get("name") or "")


_ADM_RE = re.compile(r"(省|市|县|区|自治区|特别行政区|地区|自治州|盟|旗)$")
# 地名库里的行政级别（PPLC = 国家首都，PPLA = 一级行政区首府，PPLA2..4 依次往下，PPL = 普通居民点）
_FEATURE_RANK = {"PPLC": 0, "PPLA": 1, "PPLA2": 2, "PPLA3": 3, "PPLA4": 4, "PPL": 6, "PPLL": 7}


def _quality(r: dict) -> tuple:
    """候选的"像不像人要找的那座城"打分，**越小越好**。

    ★ 有这个函数的原因：地名接口给「长春」的**前 10 条全是同名小村**（黑龙江、陕西、
      福建…），真正的吉林省会长春在库里叫「**长春市**」—— 光看接口顺序会把省会判成村子，
      然后天气数字看着还挺正常，没有任何报错。所以必须自己排序：
      先看有没有人口（有 = 是真城市），再看行政级别，最后按人口多的优先。
    """
    pop = r.get("population")
    has_pop = 0 if (isinstance(pop, (int, float)) and pop > 0) else 1
    rank = _FEATURE_RANK.get(str(r.get("feature_code") or ""), 5)
    return (has_pop, rank, -(pop if isinstance(pop, (int, float)) else 0))


def _query_variants(q: str) -> list:
    """地名写法很杂，按优先级给一串候选逐个试。

    ★ 实测踩到两件事（别再犯）：
      ① 接口**近乎精确匹配**：「吉林 长春」「吉林省长春市」直接 0 条 ——
         而"省 + 市"恰恰是人最自然的写法（我自己的报错提示里还推荐过它，属于自打脸）。
      ② 加了「市」才命中：真·长春在库里叫「长春市」，「长春」只匹配同名小村。
    """
    out = []

    def add(s):
        s = str(s or "").strip().strip("，,")
        if s and s not in out:
            out.append(s)

    def expand(s):
        s = str(s or "").strip().strip("，,")
        if not s:
            return
        add(s)
        core = _ADM_RE.sub("", s)
        add(core)
        add(core + "市")            # ★ 见上：不加这个后缀，省会会被认成同名小村
        add(core + "省")

    expand(q)
    toks = [t for t in re.split(r"[\s,，]+", q) if t]
    if len(toks) > 1:
        expand(toks[-1])            # 最常见：省在前、市在后
        expand(toks[0])
    no_prov = re.sub(r"^.{2,12}?(省|自治区|特别行政区)", "", q)   # 「吉林省长春市」连写
    if no_prov != q:
        expand(no_prov)
    return out


def _pick(got: list) -> dict:
    """从一次查询的候选里挑最像目标城市的那一个（保持接口给出的相对顺序做平手判定）。"""
    best, best_key = None, None
    for i, r in enumerate(got):
        k = (_quality(r), i)
        if best_key is None or k < best_key:
            best, best_key = r, k
    return best


def resolve(place: str) -> tuple:
    """地名 → 地点坐标。返回 (ok, 地点 dict, 说明)。

    ★ 名字相近的地方很多（搜「北京」能出 3 个：北京市、重庆的一个村…），所以
      **必须把「我选的是哪一个」明说出来**，并把其它候选一并带上供纠正 ——
      闷头给一个坐标，她按错的天气穿衣服就没人负责了。
    """
    q = (place or "").strip()
    if not q:
        return False, None, "place 不能为空（写城市名，或「纬度,经度」如 39.9075,116.397）"
    c = parse_coords(q)
    if c:
        lat, lon = c
        return True, {"name": f"{_n(lat, 4)},{_n(lon, 4)}", "lat": lat, "lon": lon,
                      "given": q, "matched_by": q,
                      "label": f"{_n(lat, 4)}, {_n(lon, 4)}（按坐标查）", "candidates": []}, ""
    key = ("geo", q.lower())
    hit = _cache_get(key, 86400)
    if hit is not None:
        return hit

    best, best_key, best_got, best_v, err = None, None, [], q, ""
    for vi, v in enumerate(_query_variants(q)):
        url = f"{GEO_URL}?name={urllib.parse.quote(v)}&count=6&language=zh&format=json"
        ok, data, why = _get_json(url)
        if not ok:
            err = why
            break
        got = (data or {}).get("results") or []
        cand = _pick(got)
        if cand is not None:
            # 变体顺序也是优先级的一部分（越贴近她原话的写法越该赢）
            k = (_quality(cand), vi)
            if best_key is None or k < best_key:
                best, best_key, best_got, best_v = cand, k, got, v
            if best_key[0][0] == 0:      # 已经拿到"有户口"的真城市，不必再试更远的写法
                break
    if best is None:
        out = (False, None,
               err or (f"没找到「{q}」这个地方。换成城市名本身试试（如「长春」而不是「吉林 长春」），"
                       f"或者直接给「纬度,经度」（如 39.9075,116.397）。"))
        _cache_put(key, out)
        return out

    # 候选列表：按"像不像目标"排序，把最可能的排前面（她/模型一眼能看出选错没有）
    ordered = sorted(best_got, key=_quality)
    cands, seen = [], {_label_of(best)}
    for r in ordered:
        lb = _label_of(r)
        if lb not in seen:
            seen.add(lb)
            cands.append({"label": lb, "lat": r.get("latitude"), "lon": r.get("longitude")})
        if len(cands) >= 3:
            break
    out = (True,
           {"name": best.get("name"), "lat": best.get("latitude"), "lon": best.get("longitude"),
            "label": _label_of(best), "elevation": best.get("elevation"),
            "tz": best.get("timezone") or "",
            "given": q, "matched_by": best_v, "candidates": cands}, "")
    _cache_put(key, out)
    return out


def _fetch_daily(loc: dict, kind: str, d0, d1, past_days: int = 0) -> tuple:
    """逐日数据。kind = 'archive'（历史存档）| 'forecast'（含最近几天）。返回 (ok, rows, 说明)。"""
    base = (f"latitude={loc['lat']}&longitude={loc['lon']}&timezone=auto")
    if kind == "archive":
        # 存档接口只有实测量（没有"降水概率""紫外线指数"这类预报量），别一起要 —— 会 400。
        vars_ = ("weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_sum,wind_speed_10m_max")
        url = f"{ARCHIVE_URL}?{base}&start_date={d0}&end_date={d1}&daily={vars_}"
    else:
        vars_ = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset,uv_index_max")
        url = (f"{FORECAST_URL}?{base}&daily={vars_}"
               f"&past_days={max(0, int(past_days))}&forecast_days=1")
    ok, data, why = _get_json(url)
    if not ok:
        return False, [], why
    d = (data or {}).get("daily") or {}
    times = d.get("time") or []
    lo, hi = str(d0), str(d1)
    rows = []
    for i, t in enumerate(times):
        t = str(t)
        if not (lo <= t <= hi):
            continue
        row = {"date": t}
        for k, arr in d.items():
            if k == "time" or not isinstance(arr, list) or i >= len(arr):
                continue
            row[k] = arr[i]
        rows.append(row)
    return True, rows, ""


# ---------------- 格式化小工具 ----------------

def wmo_text(code) -> str:
    try:
        return WMO.get(int(code), f"未知天气码 {code}")
    except Exception:
        return f"未知天气码 {code}"


def wind_dir(deg) -> str:
    try:
        d = float(deg) % 360
    except Exception:
        return ""
    return _DIRS[int((d + 22.5) // 45) % 8]


def _n(v, nd: int = 1) -> str:
    """数字原样（只去掉小数末尾无意义的零），缺值给「—」而不是 0。

    ⚠️ 尾零只能在**有小数点时**才剥：`"40".rstrip("0")` 会得到 `"4"` —— 湿度 40% 会印成
       "4%"，概率 0 会印成空字符串。整数位上的 0 是有效数字，不是"多余的零"。
    """
    if v is None:
        return "—"
    try:
        f = float(v)
    except Exception:
        return str(v)
    s = f"{f:.{nd}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _weekday(iso: str) -> str:
    try:
        return _WD[date.fromisoformat(iso).weekday()]
    except Exception:
        return ""


def _dw(s: str) -> int:
    """显示宽度：CJK / 全角标点算 2 列（用它才能把逐日表对齐 —— 中文按 1 列算会歪）。"""
    return sum(2 if (ord(c) > 0x2E7F or c in "（）·") else 1 for c in str(s))


def _pad(s: str, width: int) -> str:
    s = str(s)
    return s + " " * max(0, width - _dw(s))


def _hhmm(iso) -> str:
    s = str(iso or "")
    return s[11:16] if len(s) >= 16 else s


def _avg(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return (sum(vals) / len(vals)) if vals else None


def _loc_head(loc: dict, title: str) -> str:
    lines = [f"{loc['label']}  {title}"]
    given, used = str(loc.get("given") or ""), str(loc.get("matched_by") or "")
    if given and used and given != used:
        # 退了候选写法才查到 —— 说出来，免得她以为自己写的地名被完整识别了
        lines.append(f"（你说的「{given}」是按「{used}」查的）")
    if loc.get("elevation") is not None:
        lines.append(f"位置：{_n(loc['lat'], 4)}, {_n(loc['lon'], 4)}（海拔 {_n(loc['elevation'], 0)} m）")
    cands = [c["label"] for c in (loc.get("candidates") or []) if c.get("label")]
    if cands:
        lines.append("同名 / 相近的还有：" + "、".join(cands)
                     + " —— 如果不是这个，把地名写更具体（加省或国家）")
    return "\n".join(lines)


def build_weather_tools(ws, output_limit: int):
    """装配天气工具。ws 目前用不到，但保持与其它 build_*_tools 同样的签名。"""

    def weather_now(args: dict) -> ToolResult:
        ok, loc, why = resolve(str(args.get("place") or ""))
        if not ok:
            return ToolResult(False, error=why)
        key = ("now", round(loc["lat"], 4), round(loc["lon"], 4))
        data = _cache_get(key, 600)
        if data is None:
            url = (f"{FORECAST_URL}?latitude={loc['lat']}&longitude={loc['lon']}"
                   "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
                   "precipitation,weather_code,cloud_cover,pressure_msl,"
                   "wind_speed_10m,wind_direction_10m,wind_gusts_10m"
                   "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,"
                   "precipitation_probability_max,uv_index_max"
                   "&timezone=auto&forecast_days=1")
            ok2, data, why2 = _get_json(url)
            if not ok2:
                return ToolResult(False, error=why2)
            _cache_put(key, data)
        cur = (data or {}).get("current") or {}
        dl = (data or {}).get("daily") or {}
        today = str((dl.get("time") or ["?"])[0])

        L = [_loc_head(loc, "实况")]
        _lt = str(cur.get("time") or "—").replace("T", " ")
        L.append(f"当地时间：{_lt}（{'白天' if cur.get('is_day') else '夜间'}）")
        L.append(f"气温 {_n(cur.get('temperature_2m'))}°C"
                 f"（体感 {_n(cur.get('apparent_temperature'))}°C）｜{wmo_text(cur.get('weather_code'))}")
        L.append(f"湿度 {_n(cur.get('relative_humidity_2m'), 0)}% ｜ "
                 f"云量 {_n(cur.get('cloud_cover'), 0)}% ｜ 气压 {_n(cur.get('pressure_msl'), 0)} hPa")
        wind = f"风：{wind_dir(cur.get('wind_direction_10m'))} {_n(cur.get('wind_speed_10m'))} km/h"
        if cur.get("wind_gusts_10m") is not None:
            wind += f"（阵风 {_n(cur.get('wind_gusts_10m'))} km/h）"
        L.append(wind)
        L.append(f"当前降水 {_n(cur.get('precipitation'))} mm")
        L.append(f"今天（{today} {_weekday(today)}）："
                 f"{_n((dl.get('temperature_2m_min') or [None])[0])} ~ "
                 f"{_n((dl.get('temperature_2m_max') or [None])[0])}°C ｜ "
                 f"降水概率 {_n((dl.get('precipitation_probability_max') or [None])[0], 0)}% ｜ "
                 f"紫外线最强 {_n((dl.get('uv_index_max') or [None])[0], 0)}")
        L.append(f"日出 {_hhmm((dl.get('sunrise') or [''])[0])} / 日落 {_hhmm((dl.get('sunset') or [''])[0])}")
        L.append(SOURCE_NOTE)

        body, truncated = common.clip("\n".join(L), output_limit)
        return ToolResult(True,
                          summary=f"{loc['label']} {_n(cur.get('temperature_2m'))}°C "
                                  f"{wmo_text(cur.get('weather_code'))}",
                          output=body, truncated=truncated,
                          data={"place": loc, "time": cur.get("time"),
                                "temperature_c": cur.get("temperature_2m"),
                                "apparent_c": cur.get("apparent_temperature"),
                                "humidity_pct": cur.get("relative_humidity_2m"),
                                "weather_code": cur.get("weather_code"),
                                "weather": wmo_text(cur.get("weather_code")),
                                "wind_kmh": cur.get("wind_speed_10m"),
                                "wind_dir_deg": cur.get("wind_direction_10m"),
                                "precipitation_mm": cur.get("precipitation"),
                                "today_min_c": (dl.get("temperature_2m_min") or [None])[0],
                                "today_max_c": (dl.get("temperature_2m_max") or [None])[0],
                                "source": "open-meteo"})

    def weather_forecast(args: dict) -> ToolResult:
        ok, loc, why = resolve(str(args.get("place") or ""))
        if not ok:
            return ToolResult(False, error=why)
        try:
            days = int(args.get("days") or 5)
        except Exception:
            return ToolResult(False, error="days 要是个整数（1–16）")
        clipped = days != max(1, min(days, FORECAST_MAX_DAYS))
        days = max(1, min(days, FORECAST_MAX_DAYS))

        key = ("fc", round(loc["lat"], 4), round(loc["lon"], 4), days)
        data = _cache_get(key, 1800)
        if data is None:
            url = (f"{FORECAST_URL}?latitude={loc['lat']}&longitude={loc['lon']}"
                   "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                   "precipitation_probability_max,wind_speed_10m_max,sunrise,sunset,uv_index_max"
                   f"&timezone=auto&forecast_days={days}")
            ok2, data, why2 = _get_json(url)
            if not ok2:
                return ToolResult(False, error=why2)
            _cache_put(key, data)
        dl = (data or {}).get("daily") or {}
        times = dl.get("time") or []
        if not times:
            return ToolResult(False, error="天气服务没返回预报数据（可能是坐标在海面上或数据缺失）")

        def col(name, i):
            arr = dl.get(name) or []
            return arr[i] if i < len(arr) else None

        L = [_loc_head(loc, f"未来 {len(times)} 天预报")]
        for i, t in enumerate(times):
            t = str(t)
            rain = col("precipitation_sum", i)
            prob = col("precipitation_probability_max", i)
            L.append(f"{t} {_weekday(t)}  {_pad(wmo_text(col('weather_code', i)), 14)}"
                     f" {_n(col('temperature_2m_min', i))}~{_n(col('temperature_2m_max', i))}°C"
                     f" ｜ 降水 {_n(rain)} mm"
                     + (f"（概率 {_n(prob, 0)}%）" if prob is not None else "")
                     + f" ｜ 风 ≤ {_n(col('wind_speed_10m_max', i))} km/h")
        L.append(SOURCE_NOTE)
        if clipped:
            L.append(f"（days 超出 1–{FORECAST_MAX_DAYS}，已按 {days} 天取）")

        body, truncated = common.clip("\n".join(L), output_limit)
        hi = [col("temperature_2m_max", i) for i in range(len(times))]
        lo = [col("temperature_2m_min", i) for i in range(len(times))]
        return ToolResult(True,
                          summary=f"{loc['label']} 未来 {len(times)} 天"
                                  f"（{_n(_avg(lo))} ~ {_n(_avg(hi))}°C）",
                          output=body, truncated=truncated,
                          data={"place": loc, "days": len(times), "dates": [str(t) for t in times],
                                "tmax_c": hi, "tmin_c": lo,
                                "source": "open-meteo"})

    def weather_history(args: dict) -> ToolResult:
        ok, loc, why = resolve(str(args.get("place") or ""))
        if not ok:
            return ToolResult(False, error=why)
        try:
            d0 = date.fromisoformat(str(args.get("start_date") or "").strip())
            d1 = date.fromisoformat(str(args.get("end_date") or "").strip())
        except Exception:
            return ToolResult(False, error="日期要写成 2026-08-01 这种格式（start_date / end_date）")
        today = date.today()
        if d1 < d0:
            return ToolResult(False, error="end_date 不能早于 start_date")
        # 「未来日期」比「跨度超限」更根本，先报它 —— 否则写个 2099 年，
        # 她看到的是"一次最多查 366 天"，而真正的问题是"那是未来"。
        if d1 > today:
            return ToolResult(False,
                              error=f"end_date 不能是未来（今天是 {today.isoformat()}）—— "
                                    f"未来的天气用 weather_forecast。")
        span = (d1 - d0).days + 1
        if span > MAX_HISTORY_DAYS:
            return ToolResult(False,
                              error=f"一次最多查 {MAX_HISTORY_DAYS} 天（这次 {span} 天）。"
                                    f"要看长期气候规律请用 weather_climate（按月的常年值）。")

        # ★ 两个数据源拼接：存档接口只到 ~7 天前，更近的过去要走预报接口的 past_days。
        #   不拼的话，最常见的「最近两周」会**静默少掉最后几天**（最难发现的那种错）。
        merged: dict = {}
        used = []
        archive_end = min(d1, today - timedelta(days=ARCHIVE_LAG_DAYS))
        if d0 <= archive_end:
            got, rows, why2 = _fetch_daily(loc, "archive", d0, archive_end)
            if not got:
                return ToolResult(False, error=why2)
            for r in rows:
                merged[r["date"]] = r
            used.append(f"存档 {d0}~{archive_end}")
        recent_start = max(d0, today - timedelta(days=ARCHIVE_LAG_DAYS - 1))
        if recent_start <= d1:
            past = (today - recent_start).days + 1
            got, rows, why2 = _fetch_daily(loc, "forecast", recent_start, d1, past_days=past)
            if not got:
                return ToolResult(False, error=why2)
            for r in rows:
                merged[r["date"]] = r
            used.append(f"近况 {recent_start}~{d1}")
        if not merged:
            return ToolResult(False, error="这段时间没有取到数据（试试换个日期范围）")

        rows = [merged[k] for k in sorted(merged)]
        hi = [r.get("temperature_2m_max") for r in rows]
        lo = [r.get("temperature_2m_min") for r in rows]
        pr = [r.get("precipitation_sum") for r in rows]
        wet = [p for p in pr if isinstance(p, (int, float)) and p >= 1]

        L = [_loc_head(loc, f"历史天气 {rows[0]['date']} ~ {rows[-1]['date']}（{len(rows)} 天）")]
        for r in rows:
            rain = r.get("precipitation_sum")
            note = "雨" if (isinstance(rain, (int, float)) and rain >= 1) else ""
            L.append(f"{r['date']} {_weekday(r['date'])}  {_pad(wmo_text(r.get('weather_code')), 14)}"
                     f" {_n(r.get('temperature_2m_min'))}~{_n(r.get('temperature_2m_max'))}°C"
                     f" ｜ 降水 {_n(rain)} mm {note}".rstrip())
        L.append("—")
        L.append(f"这段整体：平均 {_n(_avg(lo))} ~ {_n(_avg(hi))}°C ｜ "
                 f"降水合计 {_n(sum(p for p in pr if isinstance(p, (int, float))), 1)} mm ｜ "
                 f"有雨（≥1mm）{len(wet)} / {len(rows)} 天")
        if hi:
            mx = max((v for v in hi if isinstance(v, (int, float))), default=None)
            mn = min((v for v in lo if isinstance(v, (int, float))), default=None)
            if mx is not None and mn is not None:
                L.append(f"区间极值：最高 {_n(mx)}°C ｜ 最低 {_n(mn)}°C")
        L.append(ARCHIVE_NOTE + ("；最近几天来自预报接口的实际观测" if len(used) > 1 else ""))
        body, truncated = common.clip("\n".join(L), output_limit)
        return ToolResult(True,
                          summary=f"{loc['label']} {rows[0]['date']}~{rows[-1]['date']}"
                                  f"（{_n(_avg(lo))}~{_n(_avg(hi))}°C）",
                          output=body, truncated=truncated,
                          data={"place": loc, "start": rows[0]["date"], "end": rows[-1]["date"],
                                "days": len(rows), "avg_tmax_c": _avg(hi), "avg_tmin_c": _avg(lo),
                                "precip_total_mm": sum(p for p in pr if isinstance(p, (int, float))),
                                "wet_days": len(wet), "sources": used,
                                "source": "open-meteo"})

    def weather_climate(args: dict) -> ToolResult:
        ok, loc, why = resolve(str(args.get("place") or ""))
        if not ok:
            return ToolResult(False, error=why)
        raw = args.get("month")
        m = None
        if raw is not None:
            m = _parse_month(raw)
            if m is None:
                return ToolResult(False, error="month 要是 1–12 的整数（比如 8 表示 8 月）")
        try:
            years = int(args.get("years") or CLIMATE_YEARS_DEFAULT)
        except Exception:
            return ToolResult(False, error="years 要是个整数")
        years = max(1, min(years, CLIMATE_YEARS_MAX))
        today = date.today()
        end_year = today.year - 1                    # 当年还没过完，不掺进来
        start_year = end_year - years + 1
        months = [m] if m else list(range(1, 13))

        key = ("clim", round(loc["lat"], 4), round(loc["lon"], 4), start_year, end_year)
        data = _cache_get(key, 86400)
        if data is None:
            url = (f"{ARCHIVE_URL}?latitude={loc['lat']}&longitude={loc['lon']}"
                   f"&start_date={start_year}-01-01&end_date={end_year}-12-31"
                   "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum"
                   "&timezone=auto")
            ok2, data, why2 = _get_json(url)
            if not ok2:
                return ToolResult(False, error=why2)
            _cache_put(key, data)
        dl = (data or {}).get("daily") or {}
        times = dl.get("time") or []
        if not times:
            return ToolResult(False, error="气象档案没返回数据（换个地点或年份范围试试）")

        # 按「年-月」分桶，一次网络调用就能算任意月的常年值（比一年一次少 8 倍请求）
        buckets: dict = {}
        for i, t in enumerate(times):
            t = str(t)
            keym = t[:7]
            if int(t[5:7]) not in months:
                continue
            buckets.setdefault(keym, []).append({
                "date": t,
                "hi": (dl.get("temperature_2m_max") or [None] * len(times))[i],
                "lo": (dl.get("temperature_2m_min") or [None] * len(times))[i],
                "pr": (dl.get("precipitation_sum") or [None] * len(times))[i]})
        if not buckets:
            return ToolResult(False, error="这段年份里没取到要查的月份")

        def stat(rows, field):
            return [r[field] for r in rows if isinstance(r[field], (int, float))]

        allrows = [r for v in buckets.values() for r in v]
        hi, lo, pr = stat(allrows, "hi"), stat(allrows, "lo"), stat(allrows, "pr")
        if not hi or not lo:
            return ToolResult(False, error="气温数据缺失，算不出常年值")
        hottest = max(allrows, key=lambda r: r["hi"] if isinstance(r["hi"], (int, float)) else -999)
        coldest = min(allrows, key=lambda r: r["lo"] if isinstance(r["lo"], (int, float)) else 999)
        # 月总降水：按每个"年-月"分别求和，再对年数取平均（不能用日均值 × 30 去估）
        month_totals = [sum(stat(v, "pr")) for v in buckets.values()]

        title = ((f"{m} 月常年气候" if m else "各月常年气候")
                 + f"（{start_year}–{end_year}，取 {len(buckets)} 个年份档）")
        L = [_loc_head(loc, title)]
        if m:
            L.append(f"平均 {_n(_avg(lo))} ~ {_n(_avg(hi))}°C"
                     f"（日均高低温的平均）｜月降水 {_n(_avg(month_totals), 1)} mm")
            L.append(f"极端：最高 {_n(hottest['hi'])}°C（{hottest['date']}）"
                     f"｜最低 {_n(coldest['lo'])}°C（{coldest['date']}）")
            wet = [r for r in allrows if isinstance(r["pr"], (int, float)) and r["pr"] >= 1]
            L.append(f"有雨（≥1mm）的日子平均每月 {_n(len(wet) / max(1, len(buckets)), 1)} 天")
            by_year = {}
            for keym, v in buckets.items():
                by_year[int(keym[:4])] = _avg(stat(v, "hi"))
            L.append("逐年平均高温：" + " ｜ ".join(f"{y} {_n(by_year[y])}°C" for y in sorted(by_year)))
            L.append("（当年不在统计里 —— 一年还没过完，掺进来会把常年值拉偏）")
        else:
            for mo in months:
                rows = [r for keym, v in buckets.items() if int(keym[5:7]) == mo for r in v]
                if not rows:
                    continue
                mh, ml, _ = stat(rows, "hi"), stat(rows, "lo"), stat(rows, "pr")
                tot = [sum(stat(v, "pr")) for keym, v in buckets.items() if int(keym[5:7]) == mo]
                L.append(f"{mo:>2} 月  {_n(_avg(ml))}~{_n(_avg(mh))}°C ｜ 月降水 {_n(_avg(tot), 1)} mm"
                         f" ｜ 极端 {_n(min(ml))}~{_n(max(mh))}°C")
        L.append(ARCHIVE_NOTE)

        body, truncated = common.clip("\n".join(L), output_limit)
        return ToolResult(True,
                          summary=f"{loc['label']} "
                                  + (f"{m} 月常年 {_n(_avg(lo))}~{_n(_avg(hi))}°C" if m
                                     else f"各月常年气候（{start_year}–{end_year}）"),
                          output=body, truncated=truncated,
                          data={"place": loc, "month": m, "years": [start_year, end_year],
                                "months_counted": len(buckets),
                                "avg_tmax_c": _avg(hi), "avg_tmin_c": _avg(lo),
                                "avg_month_precip_mm": _avg(month_totals),
                                "record_high_c": hottest["hi"], "record_high_date": hottest["date"],
                                "record_low_c": coldest["lo"], "record_low_date": coldest["date"],
                                "source": "open-meteo-archive-era5"})

    return [
        exec_tool("weather_now",
                  "查某个地方**现在的**天气：气温、体感、天气现象、湿度、风、气压、今天的最高最低与日出日落。"
                  "place 可以是城市名（中文即可，如「北京」「上海 浦东」），也可以是「纬度,经度」。"
                  "要未来的看 weather_forecast，要过去的看 weather_history。",
                  {"type": "object", "required": ["place"], "properties": {
                      "place": {"type": "string", "description": "城市名（如 北京 / 吉林 长春）或「纬度,经度」"}}},
                  weather_now, preview_keys=("place",)),

        exec_tool("weather_forecast",
                  f"查某个地方未来几天的**逐日预报**（最高/最低温、天气现象、降水量与概率、风力、日出日落、紫外线）。"
                  f"days 取 1–{FORECAST_MAX_DAYS}，默认 5。想看现在用 weather_now，想看过去用 weather_history。",
                  {"type": "object", "required": ["place"], "properties": {
                      "place": {"type": "string", "description": "城市名或「纬度,经度」"},
                      "days": {"type": "integer", "minimum": 1, "maximum": FORECAST_MAX_DAYS,
                               "description": f"预报天数（1–{FORECAST_MAX_DAYS}，默认 5）"}}},
                  weather_forecast, preview_keys=("place", "days")),

        exec_tool("weather_history",
                  f"查某个地方**过去某段日期**的逐日天气（含区间平均值与极值）。"
                  f"日期用 YYYY-MM-DD；一次最多 {MAX_HISTORY_DAYS} 天；不能查未来日期。"
                  f"要「某个月常年多少度」这类气候规律请用 weather_climate。",
                  {"type": "object", "required": ["place", "start_date", "end_date"], "properties": {
                      "place": {"type": "string", "description": "城市名或「纬度,经度」"},
                      "start_date": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                      "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD（不能是未来）"}}},
                  weather_history, preview_keys=("place", "start_date", "end_date")),

        exec_tool("weather_climate",
                  "查某个地方的**常年气候**（按月聚合过去若干年）：平均高温/低温、月降水量、"
                  "历史极端最高最低及出现日期、有雨天数。不给 month 就返回 12 个月一整张表。"
                  "适合回答「这里 8 月一般多热」「梅雨季在几月」这类问题；查某一天的天气请用另外两个工具。",
                  {"type": "object", "required": ["place"], "properties": {
                      "place": {"type": "string", "description": "城市名或「纬度,经度」"},
                      "month": {"type": "integer", "minimum": 1, "maximum": 12,
                                "description": "月份 1–12；不给则返回 12 个月的常年值"},
                      "years": {"type": "integer", "minimum": 1, "maximum": CLIMATE_YEARS_MAX,
                                "description": f"用过去多少年的数据（1–{CLIMATE_YEARS_MAX}，默认 {CLIMATE_YEARS_DEFAULT}）"}}},
                  weather_climate, preview_keys=("place", "month", "years")),
    ]


def _parse_month(raw):
    """宽容一点：8 / "8" / "8月" / "八月" 都收。收不了返回 None（让调用方给出明确报错）。"""
    if isinstance(raw, int):
        return raw if 1 <= raw <= 12 else None
    s = str(raw or "").strip().rstrip("月").strip()
    if not s:
        return None
    if s.isdigit():
        v = int(s)
        return v if 1 <= v <= 12 else None
    if s in _MON:
        return _MON.index(s) + 1
    return None
