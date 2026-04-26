# -*- coding: utf-8 -*-
"""
地方競馬 netkeiba 物差し能力比較 Streamlit版
- JRA非対応。nar.netkeiba.com の地方競馬URLだけを対象
- ばんえい（帯広）対応
- shutuba_past.html のHTML構造変更に対応：出走馬名は Horse01 から取得
- 過去レース結果ページが取れない場合でも、馬柱ページ内の直接対決だけで判定可能

起動:
    pip install streamlit requests beautifulsoup4 networkx
    streamlit run nar_netkeiba_relative_app.py
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Iterable

import requests
import streamlit as st
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import networkx as nx

# ==========================================
# 0. 定数・ユーティリティ
# ==========================================

LOCAL_PLACES = [
    "帯広", "門別", "盛岡", "水沢", "浦和", "船橋", "大井", "川崎",
    "金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀",
]
PLACE_RE = re.compile("(" + "|".join(map(re.escape, LOCAL_PLACES)) + ")")
TIME_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}\.\d|\d{1,3}\.\d)(?!\d)")
RACE_ID_RE = re.compile(r"\d{12}")
CIRCLED_NUMS = "⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"

REQUEST_INTERVAL_SEC = 0.28
HIDDEN_HORSE_MAX_RUNS = 3  # 各出走馬の直近3走までは隠れ馬も読む。4走目以降は直接対決だけ。


def clean_text(x: Any) -> str:
    if x is None:
        return ""
    return re.sub(r"\s+", " ", x.get_text(" ", strip=True) if hasattr(x, "get_text") else str(x)).strip()


def normalize_name(name: str) -> str:
    """表記揺れ対策。空白と一部記号だけ落とす。馬名自体は変えすぎない。"""
    name = html.unescape(str(name or "")).strip()
    name = re.sub(r"\s+", "", name)
    name = name.replace("\u3000", "")
    return name


def parse_date(date_str: str) -> datetime:
    try:
        for sep in (".", "/", "-"):
            if sep in date_str:
                parts = date_str.split(sep)
                yy = int(parts[0])
                if yy < 100:
                    yy += 2000
                return datetime(yy, int(parts[1]), int(parts[2]))
    except Exception:
        pass
    return datetime.min


def to_circled(n: Any) -> str:
    try:
        i = int(n)
        return CIRCLED_NUMS[i] if 0 <= i <= 20 else f"({i})"
    except Exception:
        return ""


def sec_to_time(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}:{s:04.1f}" if m else f"{s:.1f}"


def convert_time_to_sec(time_str: str) -> Optional[float]:
    if not time_str:
        return None
    text = str(time_str).strip()
    m = TIME_RE.search(text)
    if not m:
        return None
    token = m.group(1)
    try:
        if ":" in token:
            mm, ss = token.split(":", 1)
            return int(mm) * 60 + float(ss)
        return float(token)
    except Exception:
        return None


def extract_distance(text: str) -> str:
    """'直200m', 'ダ1400', '200 1:40.5' などから距離を拾う。"""
    if not text:
        return "不明"
    # まず m 付き表記を優先
    m = re.search(r"(?:直|ダ|芝)?\s*(\d{3,4})\s*m", text)
    if m:
        return m.group(1)
    # 馬柱セル Data05 は '200 1:40.5' のような形なので先頭数字を拾う
    m = re.search(r"^\s*(\d{3,4})(?=\s|$)", text)
    if m:
        return m.group(1)
    # 最後の保険。タイムの一部を拾わないよう、3-4桁のみ。
    m = re.search(r"(?<!\d)(\d{3,4})(?![\d\.])", text)
    return m.group(1) if m else "不明"


def extract_place(text: str) -> str:
    m = PLACE_RE.search(text or "")
    return m.group(1) if m else "不明"


def extract_race_id(text: str) -> Optional[str]:
    m = RACE_ID_RE.search(text or "")
    return m.group(0) if m else None


def decode_response(res: requests.Response) -> str:
    """netkeibaはEUC-JP/CP932が混じるので、文字化けしにくい順に試す。"""
    raw = res.content or b""
    candidates = []
    for enc in [res.encoding, res.apparent_encoding, "EUC-JP", "cp932", "utf-8"]:
        if enc and enc not in candidates:
            candidates.append(enc)
    for enc in candidates:
        try:
            return raw.decode(enc, errors="strict")
        except Exception:
            continue
    return raw.decode("EUC-JP", errors="replace")


# ==========================================
# 1. 地方コース形態判定
# ==========================================

def is_ooi_inner(dist: Any) -> bool:
    d = int(dist) if str(dist).isdigit() else 0
    return d in (1500, 1600, 1650)


def is_ooi_outer(dist: Any) -> bool:
    d = int(dist) if str(dist).isdigit() else 0
    return d > 0 and d not in (1500, 1600, 1650)


def is_one_turn(place: str, dist: Any) -> bool:
    d = int(dist) if str(dist).isdigit() else 0
    if place == "川崎" and d == 900:
        return True
    if place == "浦和" and d == 800:
        return True
    if place == "船橋" and d in (1000, 1200):
        return True
    if place == "大井" and d in (1000, 1200, 1400):
        return True
    if place == "門別" and d <= 1000:
        return True
    if place == "盛岡" and d <= 1000:
        return True
    return False


def get_track_layout(place: str, dist: Any) -> str:
    d = int(dist) if str(dist).isdigit() else 0

    if place == "帯広":
        return "banei"
    if place == "大井":
        if d <= 1400:
            return "outer_1turn"
        if d <= 1650:
            return "inner_2turn"
        return "outer_2turn"
    if place == "川崎":
        if d == 900:
            return "1turn"
        if d <= 1600:
            return "2turn"
        return "multi"
    if place == "船橋":
        if d <= 1200:
            return "1turn"
        if d <= 1800:
            return "2turn"
        return "multi"
    if place == "浦和":
        if d <= 800:
            return "1turn"
        if d <= 1500:
            return "2turn"
        return "multi"
    if place == "門別":
        if d <= 1000:
            return "short"
        if d <= 1700:
            return "mid"
        return "long"
    if place == "盛岡":
        if d <= 1000:
            return "short"
        if d <= 1600:
            return "mid"
        return "long"
    if place == "水沢":
        if d <= 1400:
            return "short"
        return "standard"
    if place in ("金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀"):
        if d <= 1200:
            return "short"
        if d <= 1600:
            return "mid"
        return "long"

    if d <= 1200:
        return "short"
    if d <= 1800:
        return "mid"
    return "long"


def is_same_track_layout(place: str, dist1: Any, dist2: Any) -> bool:
    return get_track_layout(place, dist1) == get_track_layout(place, dist2)


# ==========================================
# 2. データ構造
# ==========================================

@dataclass
class RaceInfo:
    race_id: str
    date_str: str = ""
    date: datetime = datetime.min
    course: str = "不明"
    distance: str = "不明"
    horses: Dict[str, float] = field(default_factory=dict)  # horse_name -> seconds
    ranks: Dict[str, str] = field(default_factory=dict)     # horse_name -> rank str
    include_hidden: bool = False
    fetched_result: bool = False
    source: str = "馬柱"


@dataclass
class FetchResult:
    race_title: str
    target_course: str
    target_distance: str
    past_races: List[RaceInfo]
    umaban_dict: Dict[str, str]
    is_banei: bool
    debug: Dict[str, Any]


# ==========================================
# 3. Netkeiba 地方専用スクレイパー
# ==========================================

class NarNetkeibaScraper:
    def __init__(self):
        self.session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": "https://nar.netkeiba.com/",
        })

    def get_html(self, url: str, timeout: int = 15) -> str:
        time.sleep(REQUEST_INTERVAL_SEC)
        res = self.session.get(url, timeout=timeout)
        res.raise_for_status()
        return decode_response(res)

    def extract_race_id(self, url_or_text: str) -> Optional[str]:
        return extract_race_id(url_or_text)

    def build_shutuba_past_url(self, race_id: str) -> str:
        return f"https://nar.netkeiba.com/race/shutuba_past.html?race_id={race_id}"

    def parse_current_runner(self, tr) -> Tuple[Optional[str], Optional[str], Dict[str, str]]:
        """
        ここが今回の最重要修正点。
        地方の shutuba_past では、出走馬名は .Horse01。
        .Horse02 は父馬名として使われるケースがあるため、馬名取得に使わない。
        """
        info = tr.find("td", class_=lambda c: c and "Horse_Info" in c)
        if not info:
            return None, None, {}

        # 正：出走馬名
        horse_tag = info.select_one("dt.Horse01")
        horse_name = normalize_name(clean_text(horse_tag))

        # 保険：別構造になった場合だけ horse link から拾う。ただし父馬リンクを拾わないよう Horse01 優先。
        if not horse_name:
            horse_link = info.select_one("a[href*='/horse/']")
            horse_name = normalize_name(clean_text(horse_link))

        if not horse_name:
            return None, None, {}

        umaban = None
        umaban_td = tr.find("td", class_=lambda c: c and "Waku" in c, attrs={"data-sort-value": True})
        if umaban_td:
            umaban = clean_text(umaban_td) or umaban_td.get("data-sort-value")
        if not umaban:
            tds = tr.find_all("td")
            if len(tds) > 1:
                umaban = clean_text(tds[1])

        meta = {}
        jockey_td = tr.find("td", class_=lambda c: c and "Jockey" in c)
        if jockey_td:
            barei_tag = jockey_td.find(class_="Barei")
            meta["barei"] = clean_text(barei_tag)
            # spanやBareiを除いた騎手名の雑抽出
            jtxt = clean_text(jockey_td)
            if meta["barei"]:
                jtxt = jtxt.replace(meta["barei"], "", 1).strip()
            jtxt = re.sub(r"\d+(?:\.\d+)?", "", jtxt).strip()
            meta["jockey"] = jtxt

        odds_area = info.select_one("dt.Horse07")
        if odds_area:
            meta["weight_odds"] = clean_text(odds_area)

        return horse_name, str(umaban or "?"), meta

    def parse_past_cell(self, td, current_horse: str) -> Optional[Tuple[str, RaceInfo, float, str]]:
        data01 = td.find("div", class_="Data01")
        data02 = td.find("div", class_="Data02")
        data02_a = data02.find("a") if data02 else None
        data05 = td.find("div", class_="Data05")
        if not (data01 and data02_a and data05):
            return None

        href = data02_a.get("href", "")
        past_race_id = extract_race_id(href)
        if not past_race_id:
            return None

        data01_text = clean_text(data01)
        data05_text = clean_text(data05)
        course = extract_place(data01_text)
        date_match = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", data01_text)
        date_str = f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}" if date_match else ""
        distance = extract_distance(data05_text)
        sec = convert_time_to_sec(data05_text)
        if sec is None:
            return None

        num_tag = data01.find(class_="Num")
        rank_str = clean_text(num_tag) if num_tag else ""
        if rank_str and not rank_str.isdigit():
            # 取・除・中などは比較不能
            return None

        race = RaceInfo(
            race_id=past_race_id,
            date_str=date_str,
            date=parse_date(date_str),
            course=course,
            distance=distance,
            horses={current_horse: sec},
            ranks={current_horse: rank_str},
            include_hidden=False,
            fetched_result=False,
            source="馬柱直接",
        )
        return past_race_id, race, sec, rank_str

    def parse_result_table_generic(self, soup: BeautifulSoup) -> Dict[str, Tuple[float, str]]:
        """db.netkeiba / nar result の表構造差異を吸収して、馬名 -> (秒, 着順) を返す。"""
        out: Dict[str, Tuple[float, str]] = {}

        # 優先: db.netkeiba の結果表
        candidate_tables = []
        t = soup.find("table", class_=lambda c: c and "race_table_01" in c)
        if t:
            candidate_tables.append(t)
        candidate_tables.extend([x for x in soup.find_all("table") if x not in candidate_tables])

        for table in candidate_tables:
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 4:
                    continue

                rank = clean_text(tds[0])
                rank = re.sub(r"[^0-9]", "", rank)
                if not rank:
                    continue

                # 馬名リンク。結果表内の最初の /horse/ リンクを基本馬名とする。
                horse_link = None
                for a in tr.find_all("a", href=True):
                    if "/horse/" in a["href"]:
                        horse_link = a
                        break
                if not horse_link:
                    # db標準なら4列目が馬名
                    if len(tds) > 3:
                        horse_name_raw = clean_text(tds[3])
                    else:
                        continue
                else:
                    horse_name_raw = clean_text(horse_link)

                horse_name = normalize_name(horse_name_raw)
                if not horse_name:
                    continue

                sec = None
                for cell in tds:
                    txt = clean_text(cell)
                    sec = convert_time_to_sec(txt)
                    if sec is not None:
                        break
                if sec is None:
                    continue

                out[horse_name] = (sec, rank)

            if out:
                return out
        return out

    def fetch_result_horses(self, race_id: str) -> Dict[str, Tuple[float, str]]:
        """過去レースの全頭結果を取得。失敗時は空で返す。"""
        urls = [
            f"https://db.netkeiba.com/race/{race_id}/",
            f"https://nar.netkeiba.com/race/result.html?race_id={race_id}",
        ]
        for url in urls:
            try:
                text = self.get_html(url)
                soup = BeautifulSoup(text, "html.parser")
                horses = self.parse_result_table_generic(soup)
                if horses:
                    return horses
            except Exception:
                continue
        return {}

    def fetch_past_data(self, race_id: str) -> FetchResult:
        url = self.build_shutuba_past_url(race_id)
        text = self.get_html(url)
        soup = BeautifulSoup(text, "html.parser")

        title_tag = soup.find(class_="RaceName")
        race_title = clean_text(title_tag) if title_tag else f"レースID: {race_id}"
        race_data01 = soup.find("div", class_="RaceData01")
        race_data02 = soup.find("div", class_="RaceData02")
        race_data_text = " ".join([clean_text(race_data01), clean_text(race_data02)])

        is_banei = "帯広" in race_data_text or "(ば)" in race_data_text
        target_course = extract_place(race_data_text)
        target_distance = extract_distance(race_data_text)

        if target_course == "不明":
            # title / OGP 等からの保険
            target_course = extract_place(clean_text(soup))
        if target_distance == "不明":
            m = re.search(r"(\d{3,4})m", clean_text(soup))
            target_distance = m.group(1) if m else "不明"

        past_races: Dict[str, RaceInfo] = {}
        umaban_dict: Dict[str, str] = {}
        horse_valid_run_count: Dict[str, int] = {}
        current_meta: Dict[str, Dict[str, str]] = {}
        parse_errors: List[str] = []

        horse_rows = soup.find_all("tr", class_=lambda c: c and "HorseList" in c)
        for row_idx, tr in enumerate(horse_rows, start=1):
            horse_name, umaban, meta = self.parse_current_runner(tr)
            if not horse_name:
                parse_errors.append(f"{row_idx}行目: 馬名取得失敗")
                continue
            umaban_dict[horse_name] = umaban or "?"
            current_meta[horse_name] = meta
            horse_valid_run_count.setdefault(horse_name, 0)

            past_tds = tr.find_all("td", class_=lambda c: c and str(c).startswith("Past"))
            # class_ の lambda だと 'Past Ranking_2' が分解される環境があるため保険
            if not past_tds:
                past_tds = [td for td in tr.find_all("td") if any(str(cls).startswith("Past") for cls in (td.get("class") or []))]

            for td in past_tds[:5]:
                parsed = self.parse_past_cell(td, horse_name)
                if not parsed:
                    continue
                past_id, race_obj, sec, rank = parsed

                horse_valid_run_count[horse_name] += 1
                include_hidden = horse_valid_run_count[horse_name] <= HIDDEN_HORSE_MAX_RUNS

                if past_id not in past_races:
                    past_races[past_id] = race_obj
                else:
                    # 既存レースに現在馬の馬柱情報を追加
                    existing = past_races[past_id]
                    existing.horses[horse_name] = sec
                    if rank:
                        existing.ranks[horse_name] = rank
                    # より確かな情報で埋める
                    if existing.course == "不明" and race_obj.course != "不明":
                        existing.course = race_obj.course
                    if existing.distance == "不明" and race_obj.distance != "不明":
                        existing.distance = race_obj.distance
                    if existing.date == datetime.min and race_obj.date != datetime.min:
                        existing.date = race_obj.date
                        existing.date_str = race_obj.date_str

                if include_hidden:
                    past_races[past_id].include_hidden = True

        # 過去レースページを読んで隠れ馬を補完。読めなくても直接対決は残る。
        fetched_count = 0
        for past_id, race in list(past_races.items()):
            if not race.include_hidden:
                continue
            result_horses = self.fetch_result_horses(past_id)
            if not result_horses:
                continue
            fetched_count += 1
            race.fetched_result = True
            race.source = "結果表+馬柱"
            for h_name, (sec, rank) in result_horses.items():
                race.horses.setdefault(h_name, sec)
                if rank:
                    race.ranks.setdefault(h_name, rank)

        debug = {
            "url": url,
            "race_id": race_id,
            "race_title": race_title,
            "target_course": target_course,
            "target_distance": target_distance,
            "is_banei": is_banei,
            "horse_rows": len(horse_rows),
            "runners": len(umaban_dict),
            "past_races": len(past_races),
            "past_races_with_direct_current": sum(1 for r in past_races.values() if len([h for h in r.horses if h in umaban_dict]) >= 1),
            "past_races_with_result_table": fetched_count,
            "parse_errors": parse_errors[:10],
            "current_meta": current_meta,
        }

        return FetchResult(
            race_title=race_title,
            target_course=target_course,
            target_distance=target_distance,
            past_races=list(past_races.values()),
            umaban_dict=umaban_dict,
            is_banei=is_banei,
            debug=debug,
        )


# ==========================================
# 4. 比較グラフ
# ==========================================

def build_comparison_graph(
    past_races: List[RaceInfo],
    target_course: str,
    target_distance: str,
    umaban_dict: Dict[str, str],
    is_banei: bool,
) -> nx.DiGraph:
    runners = list(umaban_dict.keys())
    current_names = set(runners)
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0
    G = nx.DiGraph()

    def add_edge(h1: str, h2: str, raw_diff_seconds: float, race: RaceInfo, base_cost: float, is_direct: bool):
        """
        raw_diff_seconds は h1_time - h2_time。
        グラフには文字列順の正規化キーで格納する。
        history.raw_diff は key0_time - key1_time。
        """
        if not h1 or not h2 or h1 == h2:
            return

        h1_key, h2_key = str(h1), str(h2)
        if h1_key > h2_key:
            h1, h2 = h2, h1
            raw_diff_seconds = -raw_diff_seconds

        # 同一レースの秒差は能力差として強く出す。ただし極端値は丸める。
        if is_banei:
            cap_minus, cap_plus = -8.0, 8.0
        else:
            cap_minus, cap_plus = -5.0, 5.0
        capped_diff = max(cap_minus, min(cap_plus, raw_diff_seconds))

        r_dist_int = int(race.distance) if str(race.distance).isdigit() else 0
        dist_diff_val = abs(r_dist_int - cur_dist) if r_dist_int > 0 and cur_dist > 0 else 9999
        is_same_place = race.course == target_course
        is_exact_cond = is_same_place and dist_diff_val == 0

        badge = ""
        if is_exact_cond:
            badge = "[場×距]"
        elif is_same_place:
            badge = "[場]"
        elif dist_diff_val == 0:
            badge = "[距]"

        # 秒差が大きすぎる比較は信頼コストを少し上げる
        abs_d = abs(capped_diff)
        if is_banei:
            reliability_penalty = 0 if abs_d <= 2.0 else (5 if abs_d <= 5.0 else 10)
        else:
            reliability_penalty = 0 if abs_d <= 0.8 else (5 if abs_d <= 2.0 else 10)
        edge_cost = base_cost + reliability_penalty + (0 if is_direct else 100)

        history_item = {
            "date": race.date,
            "date_str": race.date.strftime("%Y/%m/%d") if race.date != datetime.min else race.date_str,
            "place": race.course,
            "dist": race.distance,
            "raw_diff": capped_diff,
            "badge": badge,
            "race_id": race.race_id,
            "is_direct": is_direct,
        }

        if G.has_edge(h1, h2):
            ed = G[h1][h2]
            ed["diffs"].append(capped_diff)
            ed["history"].append(history_item)
            ed["rank_diff"] = sum(ed["diffs"]) / len(ed["diffs"])
            ed["explore_cost"] = min(ed["explore_cost"], edge_cost)
        else:
            G.add_edge(
                h1,
                h2,
                diffs=[capped_diff],
                history=[history_item],
                rank_diff=capped_diff,
                explore_cost=edge_cost,
            )

    for race in past_races:
        r_dist = int(race.distance) if str(race.distance).isdigit() else 0
        is_same_place = race.course == target_course
        is_exact_cond = is_same_place and r_dist == cur_dist
        is_same_layout = is_same_track_layout(race.course, race.distance, target_distance)

        if is_exact_cond:
            base_cost = 0.5
        elif is_same_place and is_same_layout:
            base_cost = 2
        elif is_same_place:
            base_cost = 5
        elif is_same_layout:
            base_cost = 8
        else:
            base_cost = 15

        h_list = [(h, t) for h, t in race.horses.items() if t is not None]
        if not h_list:
            continue

        current_in_race = [(h, t) for h, t in h_list if h in current_names]
        if len(current_in_race) >= 2:
            for i in range(len(current_in_race)):
                for j in range(i + 1, len(current_in_race)):
                    h1, t1 = current_in_race[i]
                    h2, t2 = current_in_race[j]
                    add_edge(h1, h2, t1 - t2, race, base_cost, True)

        # 隠れ馬経由。結果表が取れたレースだけ対象。
        if race.include_hidden and race.fetched_result:
            hidden_horses = [(h, t) for h, t in h_list if h not in current_names]
            if current_in_race and hidden_horses:
                for curr_name, curr_time in current_in_race:
                    for hid_name, hid_time in hidden_horses:
                        add_edge(curr_name, hid_name, curr_time - hid_time, race, base_cost, False)

                # 隠れ馬同士も保持すると、複数経路のつながりが増える
                for i in range(len(hidden_horses)):
                    for j in range(i + 1, len(hidden_horses)):
                        h1, t1 = hidden_horses[i]
                        h2, t2 = hidden_horses[j]
                        add_edge(h1, h2, t1 - t2, race, base_cost, False)

    # 履歴整理：同一レースID重複を除去し、最新順に最大5件
    for _, _, d in G.edges(data=True):
        d["history"].sort(key=lambda x: x["date"] if isinstance(x["date"], datetime) else datetime.min, reverse=True)
        seen_races = set()
        deduped = []
        for hi in d["history"]:
            key = hi.get("race_id") or (hi.get("date_str"), hi.get("place"), hi.get("dist"))
            if key in seen_races:
                continue
            seen_races.add(key)
            deduped.append(hi)
        d["history"] = deduped[:5]
        d["diffs"] = [hi["raw_diff"] for hi in d["history"]]
        d["rank_diff"] = sum(d["diffs"]) / len(d["diffs"]) if d["diffs"] else 0

    return G


def advantage_entries_from_edge(G: nx.DiGraph, u: str, v: str) -> List[Dict[str, Any]]:
    """uから見たvへの優劣。diff > 0 なら u優勢。"""
    a, b = (u, v) if str(u) < str(v) else (v, u)
    if not G.has_edge(a, b):
        return []
    out = []
    for hi in G[a][b]["history"]:
        # hi.raw_diff = a_time - b_time。aがuなら、uが速いほど raw_diff は負なので反転。
        diff = -hi["raw_diff"] if u == a else hi["raw_diff"]
        item = dict(hi)
        item["diff"] = diff
        out.append(item)
    return out


# ==========================================
# 5. ペアワイズ・マトリクス・ランク
# ==========================================

def compute_pairwise_results(
    G: nx.DiGraph,
    runners: List[str],
    target_course: str,
    target_distance: str,
    is_banei: bool,
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    current_names = set(runners)
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]] = {u: {v: [] for v in runners} for u in runners}

    for u in runners:
        for v in runners:
            if u == v:
                continue

            direct_entries = advantage_entries_from_edge(G, u, v)
            if direct_entries:
                same_cond, other_cond = [], []
                for hi in direct_entries:
                    place = hi.get("place", "")
                    dist = hi.get("dist", "")
                    if is_one_turn(target_course, cur_dist) and not is_one_turn(place, dist):
                        continue
                    dist_int = int(dist) if str(dist).isdigit() else 0
                    entry = {
                        "diff": hi["diff"],
                        "is_strict": place == target_course and dist_int == cur_dist,
                        "place": place,
                        "dist": dist,
                        "date": hi.get("date", datetime.min),
                        "date_str": hi.get("date_str", ""),
                        "race_id": hi.get("race_id", ""),
                        "route": "direct",
                    }
                    if entry["is_strict"]:
                        same_cond.append(entry)
                    else:
                        other_cond.append(entry)

                # 同条件を最優先。なければ他条件も使う。
                pair_net[u][v].extend(same_cond if same_cond else other_cond)
                if pair_net[u][v]:
                    continue

            hidden_nodes = [n for n in G.nodes() if n not in current_names]
            hidden_candidates = []

            for h in hidden_nodes:
                u_h_hist = advantage_entries_from_edge(G, u, h)  # diff > 0: u > h
                h_v_hist = advantage_entries_from_edge(G, h, v)  # diff > 0: h > v
                if not u_h_hist or not h_v_hist:
                    continue

                strict_diffs = []
                loose_diffs = []
                for uh in u_h_hist:
                    for hv in h_v_hist:
                        p_uh, d_uh = uh.get("place", ""), uh.get("dist", "")
                        p_hv, d_hv = hv.get("place", ""), hv.get("dist", "")

                        if p_uh == "大井" and p_hv == "大井":
                            if (is_ooi_inner(d_uh) and is_ooi_outer(d_hv)) or (is_ooi_outer(d_uh) and is_ooi_inner(d_hv)):
                                continue

                        if is_one_turn(target_course, cur_dist):
                            if not is_one_turn(p_uh, d_uh) or not is_one_turn(p_hv, d_hv):
                                continue

                        est = uh["diff"] + hv["diff"]
                        dt_combined = min(
                            uh.get("date", datetime.min) if isinstance(uh.get("date"), datetime) else datetime.min,
                            hv.get("date", datetime.min) if isinstance(hv.get("date"), datetime) else datetime.min,
                        )
                        is_strict = (
                            p_uh == p_hv
                            and is_same_track_layout(p_uh, d_uh, d_hv)
                            and p_uh == target_course
                            and str(d_uh) == str(cur_dist)
                        )
                        rec = (est, p_uh, d_uh, dt_combined, h)
                        if is_strict:
                            strict_diffs.append(rec)
                        else:
                            loose_diffs.append(rec)

                if strict_diffs:
                    raw = sum(x[0] for x in strict_diffs) / len(strict_diffs)
                    hidden_candidates.append({
                        "diff": raw * 0.7,
                        "is_strict": True,
                        "place": strict_diffs[0][1],
                        "dist": strict_diffs[0][2],
                        "date": max(x[3] for x in strict_diffs),
                        "date_str": "",
                        "race_id": "",
                        "route": f"hidden:{h}",
                    })
                elif loose_diffs:
                    raw = sum(x[0] for x in loose_diffs) / len(loose_diffs)
                    hidden_candidates.append({
                        "diff": raw * 0.5,
                        "is_strict": False,
                        "place": loose_diffs[0][1],
                        "dist": loose_diffs[0][2],
                        "date": max(x[3] for x in loose_diffs),
                        "date_str": "",
                        "race_id": "",
                        "route": f"hidden:{h}",
                    })

            if hidden_candidates:
                # 厳密条件優先、次に絶対値が大きく新しいもの
                hidden_candidates.sort(
                    key=lambda x: (x["is_strict"], abs(x["diff"]), x["date"] if isinstance(x["date"], datetime) else datetime.min),
                    reverse=True,
                )
                pair_net[u][v].append(hidden_candidates[0])

    return pair_net


def thresholds(is_banei: bool, is_strict: bool) -> Tuple[float, float]:
    """draw_th, strong_th。diffが正なら優勢。"""
    if is_banei:
        return (2.0, 5.0) if is_strict else (3.0, 7.0)
    return (0.8, 2.0) if is_strict else (1.2, 3.0)


def inverse_sym(s: str) -> str:
    return {">>": "<<", ">": "<", "=": "=", "<": ">", "<<": ">>"}.get(s, "=")


def compute_matchup_matrix(
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]],
    runners: List[str],
    target_course: str,
    target_distance: str,
    is_banei: bool,
) -> Dict[str, Dict[str, str]]:
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0
    matchup_matrix: Dict[str, Dict[str, str]] = {u: {} for u in runners}
    now = datetime.now()

    for i, u in enumerate(runners):
        for j, v in enumerate(runners):
            if i >= j:
                continue
            entries = pair_net.get(u, {}).get(v, [])
            if not entries:
                continue

            best_is_strict = any(e.get("is_strict") for e in entries)
            target_entries = [e for e in entries if bool(e.get("is_strict")) == best_is_strict]
            if not target_entries:
                continue

            draw_th, strong_th = thresholds(is_banei, best_is_strict)
            target_entries.sort(key=lambda x: x.get("date") if isinstance(x.get("date"), datetime) else datetime.min, reverse=True)
            target_entries = target_entries[:3]

            # 厳密条件で勝ち負けが混在するなら無理に上下をつけない
            if best_is_strict and len(target_entries) >= 2:
                has_win = any(e["diff"] >= draw_th for e in target_entries)
                has_loss = any(e["diff"] <= -draw_th for e in target_entries)
                if has_win and has_loss:
                    matchup_matrix[u][v] = "="
                    matchup_matrix[v][u] = "="
                    continue

            for k, e in enumerate(target_entries):
                e["weight"] = 1.0 if k == 0 else 0.85 if k == 1 else 0.65

            def get_sym(items: List[Dict[str, Any]], sign: float = 1.0) -> str:
                if not items:
                    return "="
                weighted_sum = 0.0
                total_weight = 0.0
                wins = losses = 0
                for e in items:
                    dt = e.get("date")
                    days_ago = (now - dt).days if isinstance(dt, datetime) and dt != datetime.min else 180
                    months_ago = max(0.0, days_ago / 30.0)
                    if e.get("is_strict"):
                        time_w = 1.0 if months_ago <= 3 else 0.65 if months_ago <= 6 else 0.35
                    else:
                        time_w = 1.0 if months_ago <= 2 else 0.8 if months_ago <= 3 else 0.55 if months_ago <= 6 else 0.3
                    w = e.get("weight", 1.0) * time_w
                    d = e["diff"] * sign
                    if d >= draw_th:
                        wins += 1
                    elif d <= -draw_th:
                        losses += 1
                    weighted_sum += d * w
                    total_weight += w
                avg = weighted_sum / total_weight if total_weight else 0.0

                if wins == len(items) and wins > 0:
                    return ">>" if avg >= strong_th else ">"
                if losses == len(items) and losses > 0:
                    return "<<" if avg <= -strong_th else "<"
                if avg >= draw_th:
                    return ">>" if avg >= strong_th else ">"
                if avg <= -draw_th:
                    return "<<" if avg <= -strong_th else "<"
                return "="

            sym_u = get_sym(target_entries, 1.0)

            # 大井外回り予定で内回り敗戦だけなら少し許す
            if target_course == "大井" and is_ooi_outer(cur_dist):
                for e in target_entries:
                    if e.get("place") == "大井" and is_ooi_inner(e.get("dist")) and sym_u in ("<", "<<"):
                        sym_u = "="
                        break

            matchup_matrix[u][v] = sym_u
            matchup_matrix[v][u] = inverse_sym(sym_u)

    return matchup_matrix


def evaluate_and_rank(
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]],
    matchup_matrix: Dict[str, Dict[str, str]],
    umaban_dict: Dict[str, str],
) -> Tuple[Dict[str, str], List[Tuple[str, int]], List[str]]:
    runners = list(umaban_dict.keys())
    comparable = set()
    for u in runners:
        for v in runners:
            if u != v and pair_net.get(u, {}).get(v):
                comparable.add(u)
                comparable.add(v)

    all_tiers: Dict[str, Optional[str]] = {u: None for u in runners}
    pool = set(comparable)
    for tier in ("S", "A", "B", "C"):
        if not pool:
            break
        if tier == "C":
            for h in pool:
                all_tiers[h] = "C"
            break

        loss_counts = {}
        win_counts = {}
        for u in pool:
            losses = wins = 0
            for v in pool:
                if u == v:
                    continue
                rel = matchup_matrix.get(u, {}).get(v)
                if rel in ("<", "<<"):
                    losses += 1
                elif rel in (">", ">>"):
                    wins += 1
            loss_counts[u] = losses
            win_counts[u] = wins

        min_losses = min(loss_counts.values())
        candidates = [u for u in pool if loss_counts[u] == min_losses]
        # 同じ敗数なら勝ち数が多い馬を同Tierにする
        max_wins = max(win_counts[u] for u in candidates)
        candidates = [u for u in candidates if win_counts[u] == max_wins]
        for h in candidates:
            all_tiers[h] = tier
        pool -= set(candidates)

    tier_map: Dict[str, str] = {}
    ranked: List[Tuple[str, int]] = []
    unranked: List[str] = []
    score_by_tier = {"S": 4, "A": 3, "B": 2, "C": 1}
    for u in runners:
        tier = all_tiers.get(u)
        if tier is None:
            unranked.append(u)
        else:
            tier_map[u] = tier
            ranked.append((u, score_by_tier.get(tier, 0)))

    ranked.sort(key=lambda x: (x[1], -int(umaban_dict.get(x[0], "999") if str(umaban_dict.get(x[0], "")).isdigit() else 999)), reverse=True)
    return tier_map, ranked, unranked


# ==========================================
# 6. HTML出力
# ==========================================

def diff_symbol_and_color(adv: float, is_banei: bool, is_strict: bool) -> Tuple[str, str]:
    draw_th, strong_th = thresholds(is_banei, is_strict)
    if abs(adv) < draw_th:
        return "＝", "#777"
    if adv > 0:
        return ("≫" if adv >= strong_th else "＞"), "#189a55"
    return ("≪" if adv <= -strong_th else "＜"), "#d83a3a"


def build_html_output(
    tier_map: Dict[str, str],
    ranked: List[Tuple[str, int]],
    unranked: List[str],
    umaban_dict: Dict[str, str],
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]],
    matchup_matrix: Dict[str, Dict[str, str]],
    G: nx.DiGraph,
    target_course: str,
    target_distance: str,
    is_banei: bool,
) -> str:
    runners = list(umaban_dict.keys())
    tier_colors = {"S": "#e74c3c", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    tier_names = {"S": "最上位", "A": "上位", "B": "中位", "C": "下位"}

    html_parts = ["<div style='font-family:-apple-system,BlinkMacSystemFont,Meiryo,sans-serif;font-size:14px;color:#333;'>"]
    html_parts.append(
        f"<div style='padding:10px 12px;background:#f7f9fb;border:1px solid #e1e7ef;border-radius:8px;margin-bottom:14px;'>"
        f"対象条件：<b>{html.escape(target_course)}{html.escape(str(target_distance))}</b> "
        f" / 判定基準：{'ばんえい秒差' if is_banei else '同一レース秒差'} / ＞は本馬優勢、＜は劣勢"
        f"</div>"
    )

    def render_horse(u: str) -> str:
        uma = umaban_dict.get(u, "?")
        tier = tier_map.get(u, "C")
        color = tier_colors.get(tier, "#95a5a6")
        parts = [
            f"<div style='margin:0 0 16px 0;border-left:5px solid {color};padding:10px 12px;background:#fff;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.06);'>",
            f"<div style='font-size:1.1em;font-weight:800;'>[{html.escape(uma)}] {html.escape(u)}</div>",
        ]

        wins = draws = losses = 0
        for v in runners:
            if u == v:
                continue
            rel = matchup_matrix.get(u, {}).get(v)
            if rel in (">", ">>"):
                wins += 1
            elif rel in ("<", "<<"):
                losses += 1
            elif rel == "=":
                draws += 1
        if wins + draws + losses:
            parts.append(
                f"<div style='font-size:.86em;margin:4px 0 8px 0;'>"
                f"総合対戦：<span style='color:#189a55;font-weight:700;'>{wins}優勢</span> / "
                f"<span style='color:#777;'>{draws}互角</span> / "
                f"<span style='color:#d83a3a;font-weight:700;'>{losses}劣勢</span></div>"
            )

        # 直接対決・隠れ馬比較を相手別に表示
        lines = []
        for v in runners:
            if u == v:
                continue
            entries = pair_net.get(u, {}).get(v, [])
            if not entries:
                continue
            # 厳密 > その他、最新順
            entries = sorted(
                entries,
                key=lambda e: (bool(e.get("is_strict")), e.get("date") if isinstance(e.get("date"), datetime) else datetime.min),
                reverse=True,
            )[:3]
            v_uma = umaban_dict.get(v, "?")
            for e in entries:
                sym, c = diff_symbol_and_color(e.get("diff", 0.0), is_banei, bool(e.get("is_strict")))
                badge = "同条件" if e.get("is_strict") else f"{e.get('place','?')}{e.get('dist','?')}"
                route = e.get("route", "direct")
                route_label = "直接" if route == "direct" else f"経由 {html.escape(route.replace('hidden:', ''))}"
                date_str = e.get("date_str") or (e.get("date").strftime("%Y/%m/%d") if isinstance(e.get("date"), datetime) and e.get("date") != datetime.min else "")
                lines.append(
                    f"<div style='margin-left:10px;font-size:.86em;line-height:1.55;'>"
                    f"{html.escape(date_str)} <span style='display:inline-block;background:#eef3f8;border-radius:999px;padding:1px 7px;margin-right:4px;'>{html.escape(badge)}</span>"
                    f"<span style='color:#777;'>[{html.escape(route_label)}]</span> "
                    f"本馬 <span style='color:{c};font-weight:800;'>{sym}</span> [{html.escape(v_uma)}]{html.escape(v)}"
                    f" <span style='color:{c};'>({e.get('diff',0.0):+.1f}秒換算)</span>"
                    f"</div>"
                )
        if lines:
            parts.append("".join(lines[:10]))
        else:
            parts.append("<div style='margin-left:10px;font-size:.86em;color:#999;'>比較可能な直接・間接データなし</div>")

        parts.append("</div>")
        return "\n".join(parts)

    for tier in ("S", "A", "B", "C"):
        horses = [u for u, _ in ranked if tier_map.get(u) == tier]
        if not horses:
            continue
        html_parts.append(
            f"<h3 style='background:{tier_colors[tier]};color:#fff;padding:9px 12px;border-radius:6px;margin:18px 0 10px;'>"
            f"🏆 {tier}ランク：{tier_names[tier]}</h3>"
        )
        for u in horses:
            html_parts.append(render_horse(u))

    if unranked:
        html_parts.append(
            "<h3 style='background:#95a5a6;color:#fff;padding:9px 12px;border-radius:6px;margin:18px 0 10px;'>"
            "❗ 測定不能：別路線・比較データ不足</h3>"
        )
        for u in unranked:
            uma = umaban_dict.get(u, "?")
            html_parts.append(
                f"<div style='margin-bottom:10px;border-left:5px solid #95a5a6;padding:10px 12px;background:#fff;border-radius:6px;'>"
                f"<b>[{html.escape(uma)}] {html.escape(u)}</b>"
                f"<div style='margin-left:10px;font-size:.84em;color:#999;'>過去5走内で、他出走馬または隠れ馬経由の比較線が作れませんでした。</div>"
                f"</div>"
            )

    html_parts.append("</div>")
    return "\n".join(html_parts)


def build_matrix_html(matchup_matrix: Dict[str, Dict[str, str]], umaban_dict: Dict[str, str]) -> str:
    runners = list(umaban_dict.keys())
    ths = "".join(f"<th>[{html.escape(umaban_dict.get(h,'?'))}]<br>{html.escape(h)}</th>" for h in runners)
    rows = []
    for u in runners:
        tds = [f"<th>[{html.escape(umaban_dict.get(u,'?'))}]<br>{html.escape(u)}</th>"]
        for v in runners:
            if u == v:
                tds.append("<td style='background:#f0f0f0;'>-</td>")
            else:
                rel = matchup_matrix.get(u, {}).get(v, "")
                color = "#189a55" if rel in (">", ">>") else "#d83a3a" if rel in ("<", "<<") else "#777"
                tds.append(f"<td style='text-align:center;font-weight:800;color:{color};'>{html.escape(rel or ' ')}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f"""
    <div style='overflow:auto;'>
    <table style='border-collapse:collapse;font-size:12px;background:#fff;'>
      <thead><tr><th></th>{ths}</tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    </div>
    <style>
      table th, table td {{ border:1px solid #ddd; padding:6px; min-width:58px; }}
      table th {{ background:#f8fafc; position:sticky; left:0; z-index:1; }}
      table thead th {{ position:sticky; top:0; z-index:2; }}
    </style>
    """


# ==========================================
# 7. 統合関数
# ==========================================

def analyze_race(scraper: NarNetkeibaScraper, race_id: str) -> Tuple[str, str, str, Dict[str, Any]]:
    try:
        fetched = scraper.fetch_past_data(race_id)
        if not fetched.umaban_dict:
            return fetched.race_title, "データなし", "", fetched.debug

        G = build_comparison_graph(
            fetched.past_races,
            fetched.target_course,
            fetched.target_distance,
            fetched.umaban_dict,
            fetched.is_banei,
        )
        runners = list(fetched.umaban_dict.keys())
        pair_net = compute_pairwise_results(G, runners, fetched.target_course, fetched.target_distance, fetched.is_banei)
        matchup_matrix = compute_matchup_matrix(pair_net, runners, fetched.target_course, fetched.target_distance, fetched.is_banei)
        tier_map, ranked, unranked = evaluate_and_rank(pair_net, matchup_matrix, fetched.umaban_dict)
        html_out = build_html_output(
            tier_map,
            ranked,
            unranked,
            fetched.umaban_dict,
            pair_net,
            matchup_matrix,
            G,
            fetched.target_course,
            fetched.target_distance,
            fetched.is_banei,
        )
        matrix_html = build_matrix_html(matchup_matrix, fetched.umaban_dict)

        direct_edges = 0
        hidden_edges = 0
        for _, _, ed in G.edges(data=True):
            if any(h.get("is_direct") for h in ed.get("history", [])):
                direct_edges += 1
            if any(not h.get("is_direct") for h in ed.get("history", [])):
                hidden_edges += 1
        fetched.debug.update({
            "graph_nodes": G.number_of_nodes(),
            "graph_edges": G.number_of_edges(),
            "direct_edges": direct_edges,
            "hidden_edges": hidden_edges,
            "ranked": len(ranked),
            "unranked": len(unranked),
        })

        return fetched.race_title, html_out, matrix_html, fetched.debug
    except Exception as e:
        return f"レースID: {race_id}", f"<div style='color:#d83a3a;font-weight:bold;'>エラー: {html.escape(str(e))}</div>", "", {"error": str(e)}


def wrap_combined_html(results_list: List[Tuple[int, str, str, str, Dict[str, Any]]]) -> str:
    tabs, contents = "", ""
    for i, (r_num, r_title, content, matrix_html, debug) in enumerate(results_list):
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab-btn {active}" onclick="openTab(event, \'race_{r_num}\')">{r_num}R</button>\n'
        debug_html = "<pre>" + html.escape("\n".join(f"{k}: {v}" for k, v in debug.items() if k != "current_meta")) + "</pre>"
        contents += (
            f'<div id="race_{r_num}" class="tab-content {active}">'
            f'<h2 class="race-title">📊 {html.escape(r_title)}</h2>{content}'
            f'<h3>対戦マトリクス</h3>{matrix_html}'
            f'<details><summary>デバッグ情報</summary>{debug_html}</details>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>地方競馬 物差し能力比較</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", Meiryo, sans-serif; background:#f7f6f2; padding:20px; }}
.container {{ background:#fff; padding:20px; border-radius:10px; max-width:1100px; margin:auto; box-shadow:0 2px 10px rgba(0,0,0,.10); }}
.tab-buttons {{ display:flex; gap:5px; border-bottom:2px solid #3498db; margin-bottom:20px; flex-wrap:wrap; }}
.tab-btn {{ padding:10px 16px; border:none; background:#ecf0f1; cursor:pointer; font-weight:bold; border-radius:4px 4px 0 0; }}
.tab-btn.active {{ background:#3498db; color:white; }}
.tab-content {{ display:none; }}
.tab-content.active {{ display:block; }}
.race-title {{ font-size:1.15em; color:#2c3e50; }}
pre {{ white-space:pre-wrap; background:#f5f5f5; padding:10px; border-radius:6px; font-size:12px; }}
</style></head><body>
<div class="container"><div class="tab-buttons">{tabs}</div>{contents}</div>
<script>
function openTab(evt, id) {{
  document.querySelectorAll('.tab-content, .tab-btn').forEach(e => e.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  evt.currentTarget.classList.add('active');
}}
</script></body></html>"""


# ==========================================
# 8. Streamlit UI
# ==========================================

st.set_page_config(page_title="地方競馬 物差し能力比較", page_icon="🏇", layout="wide")

st.title("🏇 地方競馬 物差し能力比較")
st.caption("地方netkeiba専用 / ばんえい対応 / JRAモードなし / Horse01構造対応版")

with st.expander("今回の修正ポイント", expanded=False):
    st.markdown(
        """
- 地方の馬柱ページでは、出走馬名は `dt.Horse01` に入ります。旧コードの `Horse02` は父馬名を拾うため、過去結果表の馬名と一致せず全頭測定不能になります。
- 過去レース結果ページを読めなかった場合でも、現在の馬柱ページ内にある各馬の過去走タイムを先に登録するため、直接対決は判定できます。
- JRA用の分岐を削除し、URLは `nar.netkeiba.com` の `shutuba_past.html?race_id=...` に統一します。
        """
    )

url_input = st.text_input(
    "地方netkeibaのレースURL",
    value="https://nar.netkeiba.com/race/shutuba_past.html?race_id=202665042605",
    placeholder="https://nar.netkeiba.com/race/shutuba_past.html?race_id=202665042605",
)

st.markdown("---")
cols = st.columns(12)
selected_races = []
for i in range(12):
    with cols[i]:
        if st.checkbox(f"{i + 1}R", key=f"chk_{i + 1}"):
            selected_races.append(i + 1)

submitted = st.button("🚀 分析を開始", type="primary")

if submitted:
    if not url_input.strip():
        st.error("URLを入力してください。")
        st.stop()

    scraper = NarNetkeibaScraper()
    base_id = scraper.extract_race_id(url_input)
    if not base_id:
        st.error("URLから12桁のrace_idを抽出できませんでした。")
        st.stop()

    if not selected_races:
        selected_races = [int(base_id[-2:])]

    results: List[Tuple[int, str, str, str, Dict[str, Any]]] = []
    progress = st.progress(0.0)
    status = st.empty()

    for idx, r in enumerate(selected_races):
        rid = f"{base_id[:10]}{r:02d}"
        status.info(f"🏇 {r}R 解析中... race_id={rid}")
        r_title, html_out, matrix_html, debug = analyze_race(scraper, rid)
        results.append((r, r_title, html_out, matrix_html, debug))
        progress.progress((idx + 1) / len(selected_races))

    status.empty()
    st.success("✅ 分析完了")

    combined = wrap_combined_html(results)
    st.download_button(
        "📥 HTML一括ダウンロード",
        combined,
        file_name=f"地方競馬_物差し能力比較_{base_id[:10]}.html",
        mime="text/html",
    )

    tabs = st.tabs([f"{r[0]}R" for r in results])
    for tab, (r_num, r_title, r_html, matrix_html, debug) in zip(tabs, results):
        with tab:
            st.markdown(f"### {r_title}")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("出走馬", debug.get("runners", 0))
            c2.metric("過去レース", debug.get("past_races", 0))
            c3.metric("結果表取得", debug.get("past_races_with_result_table", 0))
            c4.metric("直接エッジ", debug.get("direct_edges", 0))
            c5.metric("隠れ馬エッジ", debug.get("hidden_edges", 0))

            st.markdown(r_html, unsafe_allow_html=True)
            with st.expander("対戦マトリクス"):
                st.markdown(matrix_html, unsafe_allow_html=True)
            with st.expander("デバッグ情報"):
                st.json(debug)
