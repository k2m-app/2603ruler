# -*- coding: utf-8 -*-
"""
NAR公式サイト専用 物差し能力比較 Streamlit 完全版

データ元:
    出馬表  : https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable
    成績表  : https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable

重要:
    - netkeiba / 南関東公式 / keibabook からは相対比較データを取得しない。
    - 成績表のタイムは「タイム」列から読む。行全体から 54.0 などを拾わない。
      これにより、斤量を着順・タイムと誤認する事故を避ける。

起動:
    pip install streamlit requests beautifulsoup4 networkx
    streamlit run nar_official_relative_app_complete.py
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import networkx as nx
import requests
import streamlit as st
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ==========================================
# 0. 定数・ユーティリティ
# ==========================================

BASE = "https://www.keiba.go.jp"
NAR_TODAY_BASE = BASE + "/KeibaWeb/TodayRaceInfo/"
REQUEST_INTERVAL_SEC = 0.22

LOCAL_PLACES = [
    "帯広", "門別", "盛岡", "水沢", "浦和", "船橋", "大井", "川崎",
    "金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀",
]
PLACE_RE = re.compile("(" + "|".join(map(re.escape, LOCAL_PLACES)) + ")")
TIME_TOKEN_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}\.\d|\d{1,3}\.\d)(?!\d)")
RANK_SPECIALS = ("中止", "取消", "除外", "失格", "降着")


def clean_text(x: Any) -> str:
    if x is None:
        return ""
    if hasattr(x, "get_text"):
        s = x.get_text(" ", strip=True)
    else:
        s = str(x)
    s = html.unescape(s)
    s = s.replace("\xa0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", s).strip()


def normalize_name(name: str) -> str:
    s = html.unescape(str(name or ""))
    s = s.replace("\xa0", "").replace("\u3000", "")
    s = re.sub(r"\s+", "", s)
    return s.strip()


def normalize_place_text(text: str) -> str:
    return re.sub(r"\s+", "", clean_text(text))


def extract_place(text: str) -> str:
    m = PLACE_RE.search(normalize_place_text(text))
    return m.group(1) if m else "不明"


def extract_distance(text: str) -> str:
    s = clean_text(text)
    m = re.search(r"(\d{3,4})\s*[mｍ]", s)
    if m:
        return m.group(1)
    m = re.search(r"(?:直|ダ|芝)\s*(\d{3,4})", s)
    if m:
        return m.group(1)
    return "不明"


def extract_water(text: str) -> Optional[float]:
    s = clean_text(text)
    m = re.search(r"馬場\s*[:：]\s*(\d+(?:\.\d+)?)", s)
    if m:
        return _to_float(m.group(1))
    # ばんえいの過去走欄: 26.04.20 0.8 8頭
    m = re.search(r"\d{2,4}[./]\d{1,2}[./]\d{1,2}\s+(\d+(?:\.\d+)?)\s+\d+頭", s)
    if m:
        return _to_float(m.group(1))
    return None


def water_bucket(w: Optional[float]) -> Optional[str]:
    if w is None:
        return None
    return "lt2" if w < 2.0 else "ge2"


def water_bucket_label(bucket: Optional[str]) -> str:
    return {"lt2": "2.0%未満", "ge2": "2.0%以上"}.get(bucket, "指定なし")


def _to_float(x: Any) -> Optional[float]:
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return None


def parse_time_token(token: str) -> Optional[float]:
    token = clean_text(token)
    m = TIME_TOKEN_RE.search(token)
    if not m:
        return None
    t = m.group(1)
    try:
        if ":" in t:
            mm, ss = t.split(":", 1)
            return int(mm) * 60 + float(ss)
        sec = float(t)
        # ばんえいを含め、短距離でも40秒未満は競走タイムとして扱わない。
        return sec if 40.0 <= sec <= 400.0 else None
    except Exception:
        return None


def parse_date_any(text: str) -> datetime:
    s = clean_text(text)
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{2,4})[./-](\d{1,2})[./-](\d{1,2})", s)
    if m:
        yy = int(m.group(1))
        if yy < 100:
            yy += 2000
        return datetime(yy, int(m.group(2)), int(m.group(3)))
    return datetime.min


def date_to_nar(date: datetime) -> str:
    return f"{date.year:04d}/{date.month:02d}/{date.day:02d}"


def decode_response(res: requests.Response) -> str:
    raw = res.content or b""
    candidates: List[str] = []
    for enc in [res.encoding, res.apparent_encoding, "EUC-JP", "cp932", "shift_jis", "utf-8"]:
        if enc and enc not in candidates:
            candidates.append(enc)
    for enc in candidates:
        try:
            return raw.decode(enc, errors="strict")
        except Exception:
            pass
    return raw.decode("cp932", errors="replace")


def abs_url(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("/"):
        return urljoin(BASE, href)
    return urljoin(NAR_TODAY_BASE, href)


def href_has(href: str, key: str) -> bool:
    return key.lower() in (href or "").lower()


def qparam(url: str, key: str) -> Optional[str]:
    vals = parse_qs(urlparse(url).query).get(key)
    return vals[0] if vals else None


def _norm_header(s: str) -> str:
    s = normalize_place_text(s)
    return s.replace(" ", "")


def _rank_from_text(text: str) -> str:
    s = clean_text(text)
    for kw in RANK_SPECIALS:
        if kw in s:
            return kw
    m = re.search(r"(?<![\d.])(\d{1,2})(?:着)?(?![\d.])", s)
    return m.group(1) if m else ""


def _numeric_rank(rank: str) -> Optional[int]:
    return int(rank) if str(rank).isdigit() else None


# ==========================================
# 1. コース形態判定
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
    if place in ("門別", "盛岡", "水沢", "金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀"):
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

@dataclass(frozen=True)
class NarRaceKey:
    race_date: str
    race_no: int
    baba_code: str


@dataclass
class PastLink:
    current_horse: str
    url: str
    race_date: str
    race_no: int
    baba_code: str
    date_hint: datetime = datetime.min
    water_hint: Optional[float] = None
    course_hint: str = "不明"
    distance_hint: str = "不明"


@dataclass
class RaceInfo:
    url: str
    race_date: str = ""
    race_no: int = 0
    title: str = ""
    course: str = "不明"
    distance: str = "不明"
    water: Optional[float] = None
    horses: Dict[str, float] = field(default_factory=dict)
    ranks: Dict[str, str] = field(default_factory=dict)
    horse_numbers: Dict[str, str] = field(default_factory=dict)
    source_current_horses: List[str] = field(default_factory=list)
    fetched: bool = False


@dataclass
class CurrentRaceData:
    key: NarRaceKey
    title: str
    target_course: str
    target_distance: str
    target_water: Optional[float]
    is_banei: bool
    umaban_dict: Dict[str, str]
    past_links: List[PastLink]
    debug: Dict[str, Any]


# ==========================================
# 3. NAR公式スクレイパー
# ==========================================

class NarOfficialScraper:
    def __init__(self):
        self.session = requests.Session()
        retries = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=0.6,
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
            "Referer": "https://www.keiba.go.jp/",
        })
        self.result_cache: Dict[str, RaceInfo] = {}

    def get_html(self, url: str, timeout: int = 15) -> str:
        time.sleep(REQUEST_INTERVAL_SEC)
        res = self.session.get(url, timeout=timeout)
        res.raise_for_status()
        return decode_response(res)

    def parse_key_from_url(self, url: str) -> Optional[NarRaceKey]:
        qs = parse_qs(urlparse(url).query)
        race_date = (qs.get("k_raceDate") or [None])[0]
        race_no = (qs.get("k_raceNo") or [None])[0]
        baba_code = (qs.get("k_babaCode") or [None])[0]
        if not (race_date and race_no and baba_code):
            return None
        return NarRaceKey(race_date=race_date.replace("-", "/"), race_no=int(race_no), baba_code=str(baba_code))

    def build_deba_url(self, key: NarRaceKey) -> str:
        params = urlencode({"k_raceDate": key.race_date, "k_raceNo": key.race_no, "k_babaCode": key.baba_code})
        return f"{BASE}/KeibaWeb/TodayRaceInfo/DebaTable?{params}"

    def build_racemark_url(self, race_date: str, race_no: int, baba_code: str) -> str:
        params = urlencode({"k_raceDate": race_date, "k_raceNo": race_no, "k_babaCode": baba_code})
        return f"{BASE}/KeibaWeb/TodayRaceInfo/RaceMarkTable?{params}"

    def parse_page_meta(self, soup: BeautifulSoup, fallback_key: Optional[NarRaceKey] = None) -> Dict[str, Any]:
        text = clean_text(soup)
        course = extract_place(text)
        distance = extract_distance(text)
        water = extract_water(text)
        dt = parse_date_any(text)

        title = ""
        for h in soup.find_all(["h2", "h3", "h4"]):
            t = clean_text(h)
            if not t or any(skip in t for skip in ["オッズ", "出馬表の見方", "地方競馬情報サイト"]):
                continue
            if "競走" not in t and len(t) >= 3:
                title = t
                break
        if not title:
            sp = soup.find("span", class_="plus1bold02")
            if sp:
                title = clean_text(sp)
        if not title:
            title = "レース名不明"

        if fallback_key:
            date_str = fallback_key.race_date
            race_no = fallback_key.race_no
            baba_code = fallback_key.baba_code
        else:
            date_str = date_to_nar(dt) if dt != datetime.min else ""
            race_no = 0
            baba_code = ""

        return {
            "title": title,
            "course": course,
            "distance": distance,
            "water": water,
            "date": dt,
            "date_str": date_to_nar(dt) if dt != datetime.min else date_str,
            "race_no": race_no,
            "baba_code": baba_code,
        }

    def _row_has_current_horse_link(self, row: Any) -> bool:
        return row is not None and row.find("a", class_="horseName") is not None

    def _row_race_links(self, row: Any) -> List[str]:
        urls: List[str] = []
        for a in row.find_all("a", href=True):
            href = a.get("href", "")
            if href_has(href, "TodayRaceInfo/RaceMarkTable"):
                u = abs_url(href)
                if u not in urls:
                    urls.append(u)
        return urls

    def _extract_umaban_from_row(self, row: Any, horse_name: str, fallback_no: int) -> str:
        horse_num_cell = row.find("td", class_=lambda c: c and "horseNum" in str(c))
        if horse_num_cell:
            m = re.search(r"\d{1,2}", clean_text(horse_num_cell))
            if m:
                return m.group(0)

        row_text = clean_text(row)
        prefix = row_text.split(horse_name)[0] if horse_name in row_text else row_text[:30]
        nums = re.findall(r"(?<![\d.])\d{1,2}(?![\d.])", prefix)
        if nums:
            return nums[-1]

        cells = [clean_text(td) for td in row.find_all(["td", "th"])]
        small_nums = [c for c in cells[:5] if re.fullmatch(r"\d{1,2}", c)]
        return small_nums[-1] if small_nums else str(fallback_no)

    def parse_current_deba(self, url: str) -> CurrentRaceData:
        key = self.parse_key_from_url(url)
        if not key:
            raise ValueError("NAR公式URLから k_raceDate / k_raceNo / k_babaCode を抽出できませんでした。")

        canonical_url = self.build_deba_url(key)
        soup = BeautifulSoup(self.get_html(canonical_url), "html.parser")
        meta = self.parse_page_meta(soup, fallback_key=key)

        rows = soup.find_all("tr")
        horse_rows = [(idx, row) for idx, row in enumerate(rows) if self._row_has_current_horse_link(row)]
        umaban_dict: Dict[str, str] = {}
        past_links: List[PastLink] = []
        parse_errors: List[str] = []
        seen_horses = set()

        for order, (idx, row) in enumerate(horse_rows, start=1):
            horse_link = row.find("a", class_="horseName")
            if not horse_link:
                continue
            horse_name = normalize_name(clean_text(horse_link))
            if not horse_name or horse_name in seen_horses:
                continue
            seen_horses.add(horse_name)
            umaban_dict[horse_name] = self._extract_umaban_from_row(row, horse_name, order)

            block_rows = [row]
            j = idx + 1
            while j < len(rows) and not self._row_has_current_horse_link(rows[j]):
                block_rows.append(rows[j])
                j += 1

            info_nodes = []
            urls: List[str] = []
            for br in block_rows:
                info_nodes.extend(br.find_all("div", class_="raceInfo"))
                urls.extend(self._row_race_links(br))
            urls = list(dict.fromkeys(urls))[:5]

            hints: List[Dict[str, Any]] = []
            for node in info_nodes[:5]:
                itxt = clean_text(node)
                hints.append({
                    "date_hint": parse_date_any(itxt),
                    "water_hint": extract_water(itxt),
                    "course_hint": extract_place(itxt),
                    "distance_hint": extract_distance(itxt),
                    "raw": itxt,
                })

            if not urls:
                parse_errors.append(f"{horse_name}: 過去走リンクなし")

            for pos, u in enumerate(urls):
                hint = hints[pos] if pos < len(hints) else {}
                rn = qparam(u, "k_raceNo") or "0"
                past_links.append(PastLink(
                    current_horse=horse_name,
                    url=u,
                    race_date=(qparam(u, "k_raceDate") or "").replace("-", "/"),
                    race_no=int(rn) if rn.isdigit() else 0,
                    baba_code=str(qparam(u, "k_babaCode") or key.baba_code),
                    date_hint=hint.get("date_hint", datetime.min),
                    water_hint=hint.get("water_hint"),
                    course_hint=hint.get("course_hint", "不明"),
                    distance_hint=hint.get("distance_hint", "不明"),
                ))

        is_banei = meta["course"] == "帯広" or key.baba_code == "3" or "ばんえい" in clean_text(soup)
        debug = {
            "deba_url": canonical_url,
            "title": meta["title"],
            "target_course": meta["course"],
            "target_distance": meta["distance"],
            "target_water": meta["water"],
            "target_water_bucket": water_bucket_label(water_bucket(meta["water"])),
            "is_banei": is_banei,
            "horse_rows": len(horse_rows),
            "runners": len(umaban_dict),
            "past_links": len(past_links),
            "runner_names": list(umaban_dict.keys()),
            "parse_errors": parse_errors[:20],
        }
        return CurrentRaceData(
            key=key,
            title=meta["title"],
            target_course=meta["course"],
            target_distance=meta["distance"],
            target_water=meta["water"],
            is_banei=is_banei,
            umaban_dict=umaban_dict,
            past_links=past_links,
            debug=debug,
        )

    def _result_table_and_headers(self, soup: BeautifulSoup) -> Tuple[Optional[Any], Dict[str, int]]:
        best_table = None
        best_score = -1
        for table in soup.find_all("table"):
            txt = clean_text(table)
            has_horse = table.find("a", class_="horseName") or table.find("a", href=re.compile(r"DataRoom/HorseMarkInfo"))
            score = 0
            if has_horse:
                score += 5
            for kw in ("着順", "馬番", "馬名", "タイム", "負担", "斤量"):
                if kw in txt:
                    score += 1
            if score > best_score:
                best_score = score
                best_table = table

        if not best_table:
            return None, {}

        header_cells: List[str] = []
        for tr in best_table.find_all("tr")[:5]:
            ths = tr.find_all("th")
            if len(ths) >= 3:
                header_cells = [_norm_header(th) for th in ths]
        header_map: Dict[str, int] = {}
        for idx, h in enumerate(header_cells):
            if "着順" in h or h == "着":
                header_map.setdefault("rank", idx)
            elif h == "馬番" or "馬番" in h:
                header_map.setdefault("horse_no", idx)
            elif "馬名" in h:
                header_map.setdefault("horse_name", idx)
            elif "タイム" in h and "差" not in h:
                header_map.setdefault("time", idx)
            elif "コーナー" in h or "通過" in h:
                header_map.setdefault("passage", idx)
        return best_table, header_map

    def _cells_for_result_row(self, row: Any) -> List[Any]:
        return row.find_all(["td", "th"], recursive=False)

    def _parse_result_row(self, row: Any, header_map: Dict[str, int]) -> Optional[Tuple[str, float, str, str]]:
        horse_link = row.find("a", class_="horseName")
        if horse_link is None:
            horse_link = row.find("a", href=re.compile(r"DataRoom/HorseMarkInfo"))
        if not horse_link:
            return None

        horse_name = normalize_name(clean_text(horse_link))
        if not horse_name:
            return None

        cells = self._cells_for_result_row(row)
        if not cells:
            return None

        def cell_text(key: str, fallback_idx: Optional[int] = None) -> str:
            idx = header_map.get(key)
            if idx is not None and idx < len(cells):
                return clean_text(cells[idx])
            if fallback_idx is not None and fallback_idx < len(cells):
                return clean_text(cells[fallback_idx])
            return ""

        # NAR成績表の一般形:
        # 着順 / 枠番 / 馬番 / 馬名 / 性齢 / 負担重量 / 騎手 / タイム / ...
        rank = _rank_from_text(cell_text("rank", 0))
        umaban = cell_text("horse_no", 2)
        if not re.fullmatch(r"\d{1,2}", umaban):
            umaban = ""

        time_text = cell_text("time")
        sec = parse_time_token(time_text)

        # ヘッダを取れなかった場合だけ、馬名セルより後ろの「m:ss」優先で保険。
        # 小数単独は斤量と衝突しやすいので、ヘッダなしでは最後の手段に留める。
        if sec is None:
            name_idx = None
            for i, c in enumerate(cells):
                if c.find("a", class_="horseName") or c.find("a", href=re.compile(r"DataRoom/HorseMarkInfo")):
                    name_idx = i
                    break
            scan_cells = cells[(name_idx + 1) if name_idx is not None else 0:]
            for c in scan_cells:
                candidate_text = clean_text(c)
                if re.fullmatch(r"\d{1,2}:\d{2}\.\d", candidate_text):
                    sec = parse_time_token(candidate_text)
                    break
            if sec is None and header_map:
                for c in scan_cells:
                    candidate_text = clean_text(c)
                    if re.fullmatch(r"\d{2,3}\.\d", candidate_text):
                        sec = parse_time_token(candidate_text)
                        if sec is not None:
                            break

        if sec is None:
            return None

        return horse_name, sec, rank, umaban

    def parse_result_table(self, url: str, hint: Optional[PastLink] = None) -> RaceInfo:
        if url in self.result_cache:
            race = self.result_cache[url]
            if hint is not None and race.water is None:
                race.water = hint.water_hint
            return race

        key = self.parse_key_from_url(url)
        soup = BeautifulSoup(self.get_html(url), "html.parser")
        meta = self.parse_page_meta(soup, fallback_key=key)

        race = RaceInfo(
            url=url,
            race_date=meta["date_str"],
            race_no=meta["race_no"],
            title=meta["title"],
            course=meta["course"],
            distance=meta["distance"],
            water=meta["water"],
            fetched=True,
        )
        if hint is not None:
            if race.water is None:
                race.water = hint.water_hint
            if race.course == "不明" and hint.course_hint != "不明":
                race.course = hint.course_hint
            if race.distance == "不明" and hint.distance_hint != "不明":
                race.distance = hint.distance_hint
            if not race.race_date and hint.date_hint != datetime.min:
                race.race_date = date_to_nar(hint.date_hint)

        table, header_map = self._result_table_and_headers(soup)
        rows = table.find_all("tr") if table else soup.find_all("tr")
        for row in rows:
            parsed = self._parse_result_row(row, header_map)
            if not parsed:
                continue
            horse_name, sec, rank, umaban = parsed
            race.horses[horse_name] = sec
            if rank:
                race.ranks[horse_name] = rank
            if umaban:
                race.horse_numbers[horse_name] = umaban

        self.result_cache[url] = race
        return race

    def fetch_current_and_past(self, deba_url: str, water_filter_bucket: Optional[str]) -> Tuple[CurrentRaceData, List[RaceInfo]]:
        current = self.parse_current_deba(deba_url)
        race_by_url: Dict[str, RaceInfo] = {}
        excluded_by_water: List[str] = []
        failed_results: List[str] = []

        for pl in current.past_links:
            if current.is_banei and water_filter_bucket:
                hb = water_bucket(pl.water_hint)
                if hb is not None and hb != water_filter_bucket:
                    excluded_by_water.append(
                        f"{pl.race_date} {pl.current_horse} 水分量={pl.water_hint}({water_bucket_label(hb)})"
                    )
                    continue

            try:
                race = self.parse_result_table(pl.url, hint=pl)
            except Exception as e:
                failed_results.append(f"{pl.current_horse}: {pl.url} / {e}")
                continue

            if pl.current_horse not in race.horses:
                failed_results.append(f"{pl.current_horse}: 過去結果に該当馬名なし / {race.title}")
                continue

            if current.is_banei and water_filter_bucket:
                rb = water_bucket(race.water)
                if rb != water_filter_bucket:
                    excluded_by_water.append(
                        f"{race.race_date} {race.title} 水分量={race.water}({water_bucket_label(rb)})"
                    )
                    continue

            race_by_url.setdefault(pl.url, race)
            if pl.current_horse not in race_by_url[pl.url].source_current_horses:
                race_by_url[pl.url].source_current_horses.append(pl.current_horse)

        current.debug.update({
            "past_result_races_used": len(race_by_url),
            "past_result_fetch_failed": len(failed_results),
            "failed_results_sample": failed_results[:20],
            "water_filter": water_bucket_label(water_filter_bucket),
            "excluded_by_water": len(excluded_by_water),
            "excluded_by_water_sample": excluded_by_water[:20],
        })
        return current, list(race_by_url.values())


# ==========================================
# 4. 比較グラフ
# ==========================================

def thresholds(is_banei: bool, is_strict: bool) -> Tuple[float, float]:
    if is_banei:
        return 5.0, 15.0
    return (0.55, 1.05) if is_strict else (0.75, 1.25)


def _safe_rank(rank: Any) -> int:
    try:
        return int(rank)
    except (TypeError, ValueError):
        return 99


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

    def add_edge(h1: str, h2: str, raw_diff_seconds: float, race: RaceInfo, is_direct: bool) -> None:
        if not h1 or not h2 or h1 == h2:
            return
        if str(h1) > str(h2):
            h1, h2 = h2, h1
            raw_diff_seconds = -raw_diff_seconds

        if is_banei:
            capped = max(-30.0, min(30.0, raw_diff_seconds))
        else:
            # keiba_bot.py の相対比較と同じく、極端な大敗差で序列が壊れないよう非対称に丸める。
            capped = max(-1.0, min(1.5, raw_diff_seconds))
        r_dist = int(race.distance) if str(race.distance).isdigit() else 0
        is_same_place = race.course == target_course
        is_exact = is_same_place and r_dist == cur_dist
        is_same_layout = is_same_track_layout(race.course, race.distance, target_distance)
        badge = "[同場同距]" if is_exact else "[同場]" if is_same_place else "[同距]" if r_dist == cur_dist else ""

        history = {
            "date": parse_date_any(race.race_date),
            "date_str": race.race_date,
            "place": race.course,
            "dist": race.distance,
            "water": race.water,
            "raw_diff": capped,
            "badge": badge,
            "url": race.url,
            "title": race.title,
            "is_direct": is_direct,
            "is_exact": is_exact,
            "is_same_layout": is_same_layout,
            "rank_a": race.ranks.get(h1, ""),
            "rank_b": race.ranks.get(h2, ""),
            "umaban_a": race.horse_numbers.get(h1, ""),
            "umaban_b": race.horse_numbers.get(h2, ""),
        }
        if G.has_edge(h1, h2):
            ed = G[h1][h2]
            ed["history"].append(history)
            ed["diffs"].append(capped)
            ed["rank_diff"] = sum(ed["diffs"]) / len(ed["diffs"])
        else:
            G.add_edge(h1, h2, history=[history], diffs=[capped], rank_diff=capped)

    for race in past_races:
        h_list = [(h, t) for h, t in race.horses.items() if t is not None]
        current_in_race = [(h, t) for h, t in h_list if h in current_names]
        if not current_in_race:
            continue

        if len(current_in_race) >= 2:
            for i in range(len(current_in_race)):
                for j in range(i + 1, len(current_in_race)):
                    h1, t1 = current_in_race[i]
                    h2, t2 = current_in_race[j]
                    add_edge(h1, h2, t1 - t2, race, True)

        hidden_horses = [(h, t) for h, t in h_list if h not in current_names]
        for curr_name, curr_time in current_in_race:
            for hid_name, hid_time in hidden_horses:
                add_edge(curr_name, hid_name, curr_time - hid_time, race, False)
        for i in range(len(hidden_horses)):
            for j in range(i + 1, len(hidden_horses)):
                h1, t1 = hidden_horses[i]
                h2, t2 = hidden_horses[j]
                add_edge(h1, h2, t1 - t2, race, False)

    for _, _, d in G.edges(data=True):
        def hist_rank(hi: Dict[str, Any]) -> int:
            ranks = [_safe_rank(hi.get("rank_a")), _safe_rank(hi.get("rank_b"))]
            return min(ranks) if ranks else 99

        def hist_date(hi: Dict[str, Any]) -> datetime:
            dt = hi.get("date")
            return dt if isinstance(dt, datetime) else datetime.min

        d["history"].sort(key=lambda x: (hist_rank(x), -hist_date(x).timestamp() if hist_date(x) != datetime.min else 0))
        seen = set()
        deduped = []
        for hi in d["history"]:
            key = (hi.get("place", ""), hi.get("dist", ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hi)
        d["history"] = deduped[:5]
        d["diffs"] = [hi["raw_diff"] for hi in d["history"]]
        d["rank_diff"] = sum(d["diffs"]) / len(d["diffs"]) if d["diffs"] else 0.0

    return G


def advantage_entries_from_edge(G: nx.DiGraph, u: str, v: str) -> List[Dict[str, Any]]:
    a, b = (u, v) if str(u) < str(v) else (v, u)
    if not G.has_edge(a, b):
        return []
    out = []
    for hi in G[a][b]["history"]:
        diff = -hi["raw_diff"] if u == a else hi["raw_diff"]
        item = dict(hi)
        item["diff"] = diff
        if u == a:
            item["self_rank"] = hi.get("rank_a", "")
            item["self_umaban"] = hi.get("umaban_a", "")
            item["opp_rank"] = hi.get("rank_b", "")
            item["opp_umaban"] = hi.get("umaban_b", "")
        else:
            item["self_rank"] = hi.get("rank_b", "")
            item["self_umaban"] = hi.get("umaban_b", "")
            item["opp_rank"] = hi.get("rank_a", "")
            item["opp_umaban"] = hi.get("umaban_a", "")
        out.append(item)
    return out


def _entry_sort_date(e: Dict[str, Any]) -> datetime:
    dt = e.get("date")
    if isinstance(dt, datetime):
        return dt
    if isinstance(dt, str):
        return parse_date_any(dt)
    return datetime.min


def _rank_sort_key(rank: Any, dt: Any) -> Tuple[int, float]:
    dt_obj = dt if isinstance(dt, datetime) else parse_date_any(str(dt or ""))
    return (_safe_rank(rank), -dt_obj.timestamp() if dt_obj != datetime.min else 0.0)


def _ooi_track_side(dist: Any) -> str:
    if is_ooi_inner(dist):
        return "inner"
    if is_ooi_outer(dist):
        return "outer"
    return ""


def _direct_race_priority(place: str, dist: Any, target_course: str, target_distance: Any) -> int:
    d = int(dist) if str(dist).isdigit() else 0
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0
    if not place or d <= 0 or cur_dist <= 0:
        return 0
    if place == target_course:
        if d == cur_dist:
            return 3
        if place == "大井" and _ooi_track_side(d) != _ooi_track_side(cur_dist):
            return 0
        return 2
    if d == cur_dist:
        return 1
    return 0


def _hidden_bridge_priority(place1: str, dist1: Any, place2: str, dist2: Any) -> int:
    d1 = int(dist1) if str(dist1).isdigit() else 0
    d2 = int(dist2) if str(dist2).isdigit() else 0
    if not place1 or not place2 or d1 <= 0 or d2 <= 0:
        return 0
    if place1 == place2:
        if d1 == d2:
            return 3
        if place1 == "大井" and _ooi_track_side(d1) != _ooi_track_side(d2):
            return 0
        return 2
    if d1 == d2:
        return 1
    return 0


def _entry_rank_priority(entry: Dict[str, Any]) -> int:
    """順位化用の信頼度。小さいほど強い条件。"""
    bridge_priority = int(entry.get("bridge_priority") or 0)
    if bridge_priority:
        return 4 - bridge_priority
    direct_priority = int(entry.get("direct_priority") or 0)
    if direct_priority:
        return 4 - direct_priority
    if entry.get("is_strict"):
        return 1
    return 4


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
    hidden_nodes = [n for n in G.nodes() if n not in current_names]

    for u in runners:
        for v in runners:
            if u == v:
                continue

            direct = advantage_entries_from_edge(G, u, v)
            if direct:
                priority_entries = []
                for hi in direct:
                    place = hi.get("place", "")
                    dist = hi.get("dist", "")
                    priority = _direct_race_priority(place, dist, target_course, target_distance)
                    if not priority:
                        continue
                    dist_int = int(dist) if str(dist).isdigit() else 0
                    rank = min(_safe_rank(hi.get("self_rank")), _safe_rank(hi.get("opp_rank")))
                    priority_entries.append({
                        "diff": hi["diff"],
                        "is_strict": priority == 3,
                        "place": place,
                        "dist": dist,
                        "water": hi.get("water"),
                        "date": hi.get("date", datetime.min),
                        "date_str": hi.get("date_str", ""),
                        "url": hi.get("url", ""),
                        "title": hi.get("title", ""),
                        "route": "direct",
                        "rank": rank,
                        "direct_priority": priority,
                        "self_rank": hi.get("self_rank", ""),
                        "self_umaban": hi.get("self_umaban", ""),
                        "opp_rank": hi.get("opp_rank", ""),
                        "opp_umaban": hi.get("opp_umaban", ""),
                    })

                if priority_entries:
                    best_priority = max(e["direct_priority"] for e in priority_entries)
                    target_direct = [e for e in priority_entries if e["direct_priority"] == best_priority]
                    target_direct.sort(key=lambda e: _rank_sort_key(e.get("rank"), e.get("date")))
                    pair_net[u][v].append(target_direct[0])
                if pair_net[u][v]:
                    continue

            candidates: List[Dict[str, Any]] = []
            for h in hidden_nodes:
                u_h = advantage_entries_from_edge(G, u, h)
                h_v = advantage_entries_from_edge(G, h, v)
                if not u_h or not h_v:
                    continue
                bridge_diffs = []
                for uh in u_h:
                    for hv in h_v:
                        p1, d1 = uh.get("place", ""), uh.get("dist", "")
                        p2, d2 = hv.get("place", ""), hv.get("dist", "")
                        bridge_priority = _hidden_bridge_priority(p1, d1, p2, d2)
                        if not bridge_priority:
                            continue

                        est = uh["diff"] + hv["diff"]
                        dt = min(_entry_sort_date(uh), _entry_sort_date(hv))
                        is_current_same = (
                            p1 == target_course
                            and p2 == target_course
                            and str(d1) == str(cur_dist)
                            and str(d2) == str(cur_dist)
                        )
                        bridge_rank = min(_safe_rank(uh.get("self_rank")), _safe_rank(hv.get("opp_rank")))
                        bridge_diffs.append((bridge_priority, est, p1, d1, dt, is_current_same, bridge_rank, uh, hv))

                if not bridge_diffs:
                    continue
                best_priority = max(x[0] for x in bridge_diffs)
                target_diffs = [x for x in bridge_diffs if x[0] == best_priority]
                target_diffs.sort(key=lambda x: _rank_sort_key(x[6], x[4]))
                best = target_diffs[0]
                discount = 0.7 if best[5] else (0.35 if best_priority == 1 else 0.5)
                candidates.append({
                    "diff": best[1] * discount,
                    "is_strict": best[5],
                    "place": best[2],
                    "dist": best[3],
                    "water": best[7].get("water"),
                    "date": best[4],
                    "date_str": best[7].get("date_str", ""),
                    "url": best[7].get("url", ""),
                    "title": best[7].get("title", ""),
                    "route": f"hidden:{h}",
                    "via_horse": h,
                    "via_leg1": dict(best[7]),
                    "via_leg2": dict(best[8]),
                    "bridge_priority": best_priority,
                    "rank": best[6],
                })

            if candidates:
                candidates.sort(
                    key=lambda x: (
                        int(x.get("bridge_priority", 0)),
                        -_safe_rank(x.get("rank")),
                        _entry_sort_date(x),
                        abs(x.get("diff", 0.0)),
                    ),
                    reverse=True,
                )
                pair_net[u][v].append(candidates[0])

    return pair_net


def inverse_sym(s: str) -> str:
    return {">>": "<<", ">": "<", "=": "=", "<": ">", "<<": ">>"}.get(s, "=")


def compute_matchup_matrix(
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]],
    runners: List[str],
    target_course: str,
    target_distance: str,
    is_banei: bool,
) -> Dict[str, Dict[str, str]]:
    matchup_matrix: Dict[str, Dict[str, str]] = {u: {} for u in runners}
    now = datetime.now()

    for i, u in enumerate(runners):
        for j, v in enumerate(runners):
            if i >= j:
                continue
            entries = pair_net.get(u, {}).get(v, [])
            if not entries:
                continue

            best_bridge_priority = max((int(e.get("bridge_priority") or 0) for e in entries), default=0)
            if best_bridge_priority:
                entries = [e for e in entries if int(e.get("bridge_priority") or 0) == best_bridge_priority]

            best_is_strict = any(e.get("is_strict") for e in entries)
            target_entries = [e for e in entries if bool(e.get("is_strict")) == best_is_strict]
            target_entries.sort(key=lambda e: _rank_sort_key(e.get("rank"), e.get("date")))
            target_entries = target_entries[:1] if best_bridge_priority else target_entries[:3]
            draw_th, strong_th = thresholds(is_banei, best_is_strict)

            is_forgiven_u = False
            is_forgiven_v = False
            cur_dist = int(target_distance) if str(target_distance).isdigit() else 0
            if target_course == "大井" and is_ooi_outer(cur_dist):
                for e in target_entries:
                    if e.get("place") == "大井" and is_ooi_inner(e.get("dist")):
                        if e.get("diff", 0.0) < 0:
                            is_forgiven_u = True
                        if -e.get("diff", 0.0) < 0:
                            is_forgiven_v = True

            if best_is_strict and len(target_entries) >= 2:
                has_win = any(e["diff"] >= draw_th for e in target_entries)
                has_loss = any(e["diff"] <= -draw_th for e in target_entries)
                if has_win and has_loss:
                    matchup_matrix[u][v] = matchup_matrix[v][u] = "="
                    rank_priority = min(_entry_rank_priority(e) for e in target_entries)
                    for e in target_entries:
                        e["matchup_rank_priority"] = rank_priority
                    continue

            def get_sym(entries_for_calc: List[Dict[str, Any]], sign: float = 1.0) -> str:
                if not entries_for_calc:
                    return "="
                weighted_sum = 0.0
                total_weight = 0.0
                wins = losses = 0
                for k, e in enumerate(entries_for_calc):
                    base_w = 1.0 if k == 0 else 0.9 if k == 1 else 0.7
                    dt = _entry_sort_date(e)
                    days = (now - dt).days if dt != datetime.min else 180
                    months = max(0.0, days / 30.0)
                    if best_is_strict:
                        time_w = 1.0 if months <= 3 else 0.6 if months <= 6 else 0.3
                    else:
                        time_w = 1.0 if months <= 2 else 0.8 if months <= 3 else 0.6 if months <= 6 else 0.3
                    w = base_w * time_w
                    d = e["diff"] * sign
                    if d >= draw_th:
                        wins += 1
                    elif d <= -draw_th:
                        losses += 1
                    weighted_sum += d * w
                    total_weight += w

                avg = weighted_sum / total_weight if total_weight else 0.0
                if wins == len(entries_for_calc) and wins > 0:
                    return ">>" if avg >= strong_th else ">"
                if losses == len(entries_for_calc) and losses > 0:
                    return "<<" if avg <= -strong_th else "<"
                if avg >= draw_th:
                    return ">>" if avg >= strong_th else ">"
                if avg <= -draw_th:
                    return "<<" if avg <= -strong_th else "<"
                return "="

            sym_all_u = get_sym(target_entries, sign=1.0)
            sorted_for_u = sorted(target_entries, key=lambda e: e["diff"], reverse=True)
            sym_best2_u = get_sym(sorted_for_u[:2], sign=1.0)
            sorted_for_v = sorted(target_entries, key=lambda e: e["diff"])
            sym_best2_v = get_sym(sorted_for_v[:2], sign=-1.0)

            rescue_u = sym_all_u in ("<", "<<") and sym_best2_u in (">", ">>")
            sym_all_v = inverse_sym(sym_all_u)
            rescue_v = sym_all_v in ("<", "<<") and sym_best2_v in (">", ">>")

            if rescue_u:
                sym = sym_best2_u
            elif rescue_v:
                sym = inverse_sym(sym_best2_v)
            else:
                sym = sym_all_u

            if is_forgiven_u and sym in ("<", "<<"):
                sym = "="
            if is_forgiven_v and sym in (">", ">>"):
                sym = "="

            rank_priority = min(_entry_rank_priority(e) for e in target_entries)
            for e in target_entries:
                e["matchup_rank_priority"] = rank_priority

            matchup_matrix[u][v] = sym
            matchup_matrix[v][u] = inverse_sym(sym)

    return matchup_matrix


def evaluate_and_rank(
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]],
    matchup_matrix: Dict[str, Dict[str, str]],
    umaban_dict: Dict[str, str],
) -> Tuple[Dict[str, str], List[Tuple[str, int]], List[str]]:
    runners = list(umaban_dict.keys())
    comparable_horses = set()
    for u in runners:
        for v in runners:
            if u != v and pair_net.get(u, {}).get(v):
                comparable_horses.add(u)
                comparable_horses.add(v)

    unranked = [u for u in runners if u not in comparable_horses]
    if not comparable_horses:
        return {}, [], unranked

    pool = sorted(comparable_horses, key=lambda h: int(umaban_dict.get(h, 99)) if str(umaban_dict.get(h, "")).isdigit() else 99)
    D = nx.DiGraph()
    D.add_nodes_from(pool)

    def matchup_priority(a: str, b: str) -> int:
        candidates = pair_net.get(a, {}).get(b, []) + pair_net.get(b, {}).get(a, [])
        priorities = [int(e.get("matchup_rank_priority", _entry_rank_priority(e))) for e in candidates]
        return min(priorities) if priorities else 99

    for i, u in enumerate(runners):
        for v in runners[i + 1:]:
            rel = matchup_matrix.get(u, {}).get(v, "")
            if rel in ("", "="):
                continue
            winner, loser = (u, v) if rel in (">", ">>") else (v, u)
            D.add_edge(winner, loser, rank_priority=matchup_priority(u, v))

    def prioritize_strong_conditions_in_cycles(graph: nx.DiGraph) -> nx.DiGraph:
        adjusted = graph.copy()
        changed = True
        while changed:
            changed = False
            for horses in list(nx.strongly_connected_components(adjusted)):
                if len(horses) < 3:
                    continue
                internal_edges = [
                    (a, b, adjusted[a][b])
                    for a, b in adjusted.edges()
                    if a in horses and b in horses
                ]
                if not internal_edges:
                    continue
                best_priority = min(data.get("rank_priority", 99) for _, _, data in internal_edges)
                for a, b, data in internal_edges:
                    if data.get("rank_priority", 99) > best_priority:
                        adjusted.remove_edge(a, b)
                        changed = True
        return adjusted

    D = prioritize_strong_conditions_in_cycles(D)
    sccs = list(nx.strongly_connected_components(D))
    comp_index = {h: idx for idx, comp in enumerate(sccs) for h in comp}
    C = nx.DiGraph()
    for idx in range(len(sccs)):
        C.add_node(idx)
    for winner, loser in D.edges():
        wi, li = comp_index[winner], comp_index[loser]
        if wi != li:
            C.add_edge(wi, li)

    level_by_comp = {idx: 0 for idx in C.nodes()}
    for idx in nx.topological_sort(C):
        for loser_idx in C.successors(idx):
            level_by_comp[loser_idx] = max(level_by_comp[loser_idx], level_by_comp[idx] + 1)

    tier_by_level = {0: "S", 1: "A", 2: "B"}

    tier_base = {"S": 400000, "A": 300000, "B": 200000, "C": 100000}
    tier_map: Dict[str, str] = {}
    ranked: List[Tuple[str, int]] = []

    for u in pool:
        if u in unranked:
            continue
        comp = comp_index[u]
        tier = tier_by_level.get(level_by_comp.get(comp, 0), "C")
        tier_map[u] = tier
        direct_down = 0
        strong_down = 0
        for _, loser in D.out_edges(u):
            entries = pair_net.get(u, {}).get(loser, []) + pair_net.get(loser, {}).get(u, [])
            if any(e.get("route") == "direct" for e in entries):
                direct_down += 1
            rel = matchup_matrix.get(u, {}).get(loser)
            if rel in (">>", "<<"):
                strong_down += 1
        downstream_comps = nx.descendants(C, comp)
        all_down = sum(len(sccs[c]) for c in downstream_comps)
        try:
            uma_no = int(umaban_dict.get(u, "999"))
        except Exception:
            uma_no = 999
        score = tier_base.get(tier, 0) + direct_down * 10000 + all_down * 1000 + strong_down * 100 + max(0, 999 - uma_no)
        ranked.append((u, score))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return tier_map, ranked, unranked


# ==========================================
# 5. HTML出力
# ==========================================

def diff_symbol_and_color(adv: float, is_banei: bool, is_strict: bool) -> Tuple[str, str]:
    draw_th, strong_th = thresholds(is_banei, is_strict)
    if abs(adv) < draw_th:
        return "＝", "#777"
    if adv > 0:
        return ("≫" if adv >= strong_th else "＞"), "#189a55"
    return ("≪" if adv <= -strong_th else "＜"), "#d83a3a"


def _safe_link(url: str, label: str) -> str:
    label = html.escape(label or "レース")
    if not url:
        return label
    return f"<a href='{html.escape(url)}' target='_blank' rel='noopener noreferrer'>{label}</a>"


def _race_link_label(e: Dict[str, Any]) -> str:
    title = e.get("title") or "レース"
    date = e.get("date_str") or ""
    place = e.get("place") or ""
    dist = e.get("dist") or ""
    return f"{date} {place}{dist} {title}".strip()


def build_html_output(
    tier_map: Dict[str, str],
    ranked: List[Tuple[str, int]],
    unranked: List[str],
    umaban_dict: Dict[str, str],
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]],
    matchup_matrix: Dict[str, Dict[str, str]],
    target_course: str,
    target_distance: str,
    target_water: Optional[float],
    water_filter_bucket: Optional[str],
    is_banei: bool,
) -> str:
    runners = list(umaban_dict.keys())
    tier_colors = {"S": "#e74c3c", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    tier_names = {"S": "最上位", "A": "上位", "B": "中位", "C": "下位"}
    water_txt = f" / 水分量:{target_water:.1f}%" if target_water is not None else ""
    filter_txt = f" / 水分量フィルタ:{water_bucket_label(water_filter_bucket)}" if is_banei else ""
    parts = ["<div class='relative-root'>"]
    parts.append(
        f"<div class='condition-box'>対象条件：<b>{html.escape(target_course)}{html.escape(str(target_distance))}</b>"
        f"{html.escape(water_txt)}{html.escape(filter_txt)} / 直接対決優先 / 物差し馬経由は割引</div>"
    )

    def render_entry(u: str, v: str, e: Dict[str, Any]) -> str:
        sym, c = diff_symbol_and_color(e.get("diff", 0.0), is_banei, bool(e.get("is_strict")))
        badge = "同条件" if e.get("is_strict") else f"{e.get('place','?')}{e.get('dist','?')}"
        water = e.get("water")
        wtxt = f"水分:{water:.1f}%" if isinstance(water, (int, float)) else ""
        v_uma = umaban_dict.get(v, "?")
        if e.get("route") == "direct":
            race_link = _safe_link(e.get("url", ""), _race_link_label(e))
            self_rank = e.get("self_rank") or "?"
            self_umaban = e.get("self_umaban") or "?"
            opp_rank = e.get("opp_rank") or "?"
            opp_umaban = e.get("opp_umaban") or "?"
            route_html = (
                f"<span class='direct-label'>直接</span> "
                f"<span class='muted'>当時: 本馬 {html.escape(str(self_rank))}着/{html.escape(str(self_umaban))}番 "
                f"vs 相手 {html.escape(str(opp_rank))}着/{html.escape(str(opp_umaban))}番</span> {race_link}"
            )
        else:
            via = e.get("via_horse") or str(e.get("route", "")).replace("hidden:", "")
            leg1 = e.get("via_leg1", {}) or {}
            leg2 = e.get("via_leg2", {}) or {}
            route_html = (
                f"<span class='via-label'>経由：{html.escape(str(via))}</span> "
                f"<span class='muted'>本馬↔経由馬:</span> {_safe_link(leg1.get('url',''), _race_link_label(leg1))} "
                f"<span class='muted'>/ 経由馬↔相手:</span> {_safe_link(leg2.get('url',''), _race_link_label(leg2))}"
            )
        return (
            f"<div class='comparison-line'>"
            f"<span class='badge'>{html.escape(badge)}</span> <span class='muted'>{html.escape(wtxt)}</span> "
            f"本馬 <span style='color:{c};font-weight:800;'>{sym}</span> [{html.escape(v_uma)}]{html.escape(v)} "
            f"<span style='color:{c};'>({e.get('diff',0.0):+.1f}秒換算)</span><br>"
            f"<span class='route-line'>{route_html}</span></div>"
        )

    def render_horse(u: str) -> str:
        uma = umaban_dict.get(u, "?")
        tier = tier_map.get(u, "C")
        color = tier_colors.get(tier, "#95a5a6")
        hp = [f"<div class='horse-card' style='border-left-color:{color};'>", f"<div class='horse-title'>[{html.escape(uma)}] {html.escape(u)}</div>"]
        wins = draws = losses = direct_wins = direct_losses = 0
        for v in runners:
            if u == v:
                continue
            rel = matchup_matrix.get(u, {}).get(v)
            entries = pair_net.get(u, {}).get(v, [])
            is_direct = any(e.get("route") == "direct" for e in entries)
            if rel in (">", ">>"):
                wins += 1
                direct_wins += 1 if is_direct else 0
            elif rel in ("<", "<<"):
                losses += 1
                direct_losses += 1 if is_direct else 0
            elif rel == "=":
                draws += 1
        if wins + draws + losses:
            hp.append(
                f"<div class='summary-line'>既知の優劣：<span class='green'>{wins}優勢</span> / "
                f"<span class='muted'>{draws}互角</span> / <span class='red'>{losses}劣勢</span> "
                f"<span class='muted'>（直接: {direct_wins}優勢 / {direct_losses}劣勢）</span></div>"
            )

        lines = []
        for v in runners:
            if u == v:
                continue
            entries = pair_net.get(u, {}).get(v, [])
            entries = sorted(entries, key=lambda e: (e.get("route") == "direct", bool(e.get("is_strict")), _entry_sort_date(e)), reverse=True)[:3]
            for e in entries:
                lines.append((1 if e.get("route") == "direct" else 0, _entry_sort_date(e), render_entry(u, v, e)))
        lines.sort(key=lambda x: (x[0], x[1]), reverse=True)
        hp.append("".join(x[2] for x in lines[:16]) if lines else "<div class='no-data'>比較可能な直接・間接データなし</div>")
        hp.append("</div>")
        return "\n".join(hp)

    for tier in ("S", "A", "B", "C"):
        horses = [u for u, _ in ranked if tier_map.get(u) == tier]
        if not horses:
            continue
        parts.append(f"<h3 class='tier-heading' style='background:{tier_colors[tier]};'>{tier}ランク：{tier_names[tier]}</h3>")
        for u in horses:
            parts.append(render_horse(u))

    if unranked:
        parts.append("<h3 class='tier-heading' style='background:#95a5a6;'>保留：比較材料不足</h3>")
        for u in unranked:
            parts.append(
                f"<div class='horse-card pending'><div class='horse-title'>[{html.escape(umaban_dict.get(u,'?'))}] {html.escape(u)}</div>"
                f"<div class='no-data'>過去5走内の比較材料が薄いため、無理にランクへ押し込まず保留。</div></div>"
            )

    parts.append("</div>")
    return "\n".join(parts)


def build_group_direct_html(past_races: List[RaceInfo], umaban_dict: Dict[str, str]) -> str:
    current_names = set(umaban_dict.keys())
    races = []
    for race in past_races:
        members = [h for h in race.horses if h in current_names]
        if len(members) < 3:
            continue
        members.sort(key=lambda h: _numeric_rank(race.ranks.get(h, "999")) or 999)
        races.append((parse_date_any(race.race_date), race, members))
    races.sort(key=lambda x: x[0], reverse=True)

    if not races:
        return "<div class='empty-box'>同じ過去レースに今回出走馬が3頭以上いた直接対決は見つかりませんでした。</div>"

    out = ["<div class='relative-root'>"]
    for _, race, members in races:
        water = f" / 水分:{race.water:.1f}%" if isinstance(race.water, (int, float)) else ""
        title_link = _safe_link(race.url, f"{race.race_date} {race.title}")
        out.append(f"<div class='direct-race'><div class='direct-race-head'>{title_link} <span class='muted'>({html.escape(race.course)}{html.escape(str(race.distance))}{html.escape(water)})</span></div>")
        out.append("<table class='direct-table'><thead><tr><th>当時着順</th><th>当時馬番</th><th>今回馬番</th><th>馬名</th><th>タイム</th><th>メモ</th></tr></thead><tbody>")
        first_time = min([race.horses[h] for h in members if race.horses.get(h) is not None], default=None)
        for h in members:
            sec = race.horses.get(h)
            diff = (sec - first_time) if first_time is not None and sec is not None else 0.0
            rank = race.ranks.get(h, "?")
            past_umaban = race.horse_numbers.get(h, "?")
            cur_umaban = umaban_dict.get(h, "?")
            time_txt = f"{int(sec // 60)}:{sec % 60:04.1f}" if isinstance(sec, (int, float)) and sec >= 60 else (f"{sec:.1f}" if isinstance(sec, (int, float)) else "")
            mark = "同レース内最先着" if abs(diff) < 0.01 else f"最先着から+{diff:.1f}秒"
            out.append(
                f"<tr><td>{html.escape(str(rank))}</td><td>{html.escape(str(past_umaban))}</td><td>{html.escape(str(cur_umaban))}</td>"
                f"<td><b>{html.escape(h)}</b></td><td>{html.escape(time_txt)}</td><td class='muted'>{html.escape(mark)}</td></tr>"
            )
        out.append("</tbody></table></div>")
    out.append("</div>")
    return "\n".join(out)


def build_matrix_html(matchup_matrix: Dict[str, Dict[str, str]], umaban_dict: Dict[str, str]) -> str:
    runners = list(umaban_dict.keys())
    ths = "".join(f"<th>[{html.escape(umaban_dict.get(h,'?'))}]<br>{html.escape(h)}</th>" for h in runners)
    rows = []
    for u in runners:
        tds = [f"<th>[{html.escape(umaban_dict.get(u,'?'))}]<br>{html.escape(u)}</th>"]
        for v in runners:
            if u == v:
                tds.append("<td class='self-cell'>-</td>")
            else:
                rel = matchup_matrix.get(u, {}).get(v, "")
                cls = "green" if rel in (">", ">>") else "red" if rel in ("<", "<<") else "muted"
                tds.append(f"<td class='{cls}' style='text-align:center;font-weight:800;'>{html.escape(rel or ' ')}</td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f"<div class='matrix-wrap'><table class='matrix-table'><thead><tr><th></th>{ths}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"


def count_edges(G: nx.DiGraph) -> Tuple[int, int]:
    direct = hidden = 0
    for _, _, ed in G.edges(data=True):
        if any(h.get("is_direct") for h in ed.get("history", [])):
            direct += 1
        if any(not h.get("is_direct") for h in ed.get("history", [])):
            hidden += 1
    return direct, hidden


# ==========================================
# 6. 統合
# ==========================================

def analyze_race(scraper: NarOfficialScraper, deba_url: str, water_filter_bucket: Optional[str]) -> Tuple[str, str, str, str, Dict[str, Any]]:
    try:
        current, past_races = scraper.fetch_current_and_past(deba_url, water_filter_bucket)
        if not current.umaban_dict:
            return current.title, "データなし", "", "", current.debug

        G = build_comparison_graph(past_races, current.target_course, current.target_distance, current.umaban_dict, current.is_banei)
        runners = list(current.umaban_dict.keys())
        pair_net = compute_pairwise_results(G, runners, current.target_course, current.target_distance, current.is_banei)
        matchup_matrix = compute_matchup_matrix(pair_net, runners, current.target_course, current.target_distance, current.is_banei)
        tier_map, ranked, unranked = evaluate_and_rank(pair_net, matchup_matrix, current.umaban_dict)
        html_out = build_html_output(
            tier_map,
            ranked,
            unranked,
            current.umaban_dict,
            pair_net,
            matchup_matrix,
            current.target_course,
            current.target_distance,
            current.target_water,
            water_filter_bucket,
            current.is_banei,
        )
        group_direct_html = build_group_direct_html(past_races, current.umaban_dict)
        matrix_html = build_matrix_html(matchup_matrix, current.umaban_dict)
        direct_edges, hidden_edges = count_edges(G)
        current.debug.update({
            "past_races_after_filter": len(past_races),
            "graph_nodes": G.number_of_nodes(),
            "graph_edges": G.number_of_edges(),
            "direct_edges": direct_edges,
            "hidden_edges": hidden_edges,
            "ranked": len(ranked),
            "unranked": len(unranked),
        })
        return current.title, html_out, group_direct_html, matrix_html, current.debug
    except Exception as e:
        return "解析エラー", f"<div class='error-box'>エラー: {html.escape(str(e))}</div>", "", "", {"error": str(e), "url": deba_url}


APP_CSS = """
<style>
body { font-family:-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif; }
.relative-root { font-size:14px; line-height:1.6; color:#263238; }
.condition-box,.empty-box,.error-box { padding:10px 12px; background:#f7f9fb; border:1px solid #e1e7ef; border-radius:8px; margin-bottom:14px; }
.error-box { color:#d83a3a; font-weight:700; background:#fff5f5; border-color:#ffd2d2; }
.tier-heading { color:#fff; padding:8px 12px; border-radius:6px; margin:18px 0 10px; font-size:1.05rem; }
.horse-card { margin:0 0 16px 0; border-left:5px solid #95a5a6; padding:10px 12px; background:#fff; border-radius:6px; box-shadow:0 1px 3px rgba(0,0,0,.06); }
.horse-card.pending { border-left-color:#95a5a6; }
.horse-title { font-size:1.08em; font-weight:800; color:#17202a; }
.summary-line { font-size:.86em; margin:4px 0 8px 0; }
.comparison-line { margin-left:10px; font-size:.86em; line-height:1.75; margin-bottom:5px; }
.route-line { margin-left:14px; }
.badge { display:inline-block; background:#eef3f8; border-radius:999px; padding:1px 7px; margin-right:4px; }
.direct-label { color:#0b65c2; font-weight:800; }
.via-label { color:#8e44ad; font-weight:800; }
.muted { color:#67727e; }
.green { color:#189a55; font-weight:700; }
.red { color:#d83a3a; font-weight:700; }
.no-data { margin-left:10px; font-size:.86em; color:#8a949e; }
.direct-race { margin:0 0 16px; background:#fff; border:1px solid #e1e7ef; border-radius:8px; overflow:hidden; }
.direct-race-head { background:#f7f9fb; padding:10px 12px; font-weight:800; }
.direct-table,.matrix-table { width:100%; border-collapse:collapse; font-size:13px; background:#fff; }
.direct-table th,.direct-table td,.matrix-table th,.matrix-table td { border:1px solid #e5e7eb; padding:7px 8px; }
.direct-table th,.matrix-table th { background:#f8fafc; font-weight:700; }
.direct-table td { text-align:center; }
.direct-table td:nth-child(4) { text-align:left; }
.matrix-wrap { overflow:auto; }
.matrix-table th { min-width:68px; }
.self-cell { background:#f0f0f0; color:#777; text-align:center; }
a { color:#0b65c2; text-decoration:none; }
a:hover { text-decoration:underline; }
</style>
"""


def wrap_combined_html(results: List[Tuple[int, str, str, str, str, Dict[str, Any]]]) -> str:
    tabs, contents = "", ""
    for i, (r_num, title, body, group_direct, matrix, debug) in enumerate(results):
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab-btn {active}" onclick="openRaceTab(event, \'race_{r_num}\')">{r_num}R</button>\n'
        contents += f"""
        <div id='race_{r_num}' class='tab-content {active}'>
          <h2>{html.escape(title)}</h2>
          <div class='sub-tab-buttons'>
            <button class='sub-tab-btn active' onclick="openSubTab(event, 'race_{r_num}_rank')">ランク判定</button>
            <button class='sub-tab-btn' onclick="openSubTab(event, 'race_{r_num}_direct3')">3頭以上直接対決</button>
            <button class='sub-tab-btn' onclick="openSubTab(event, 'race_{r_num}_matrix')">対戦マトリクス</button>
          </div>
          <div id='race_{r_num}_rank' class='sub-tab-content active'>{body}</div>
          <div id='race_{r_num}_direct3' class='sub-tab-content'>{group_direct}</div>
          <div id='race_{r_num}_matrix' class='sub-tab-content'>{matrix}</div>
        </div>
        """
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NAR公式 物差し能力比較</title>{APP_CSS}
<style>
body {{ background:#f7f6f2; padding:20px; }}
.container {{ background:#fff; padding:20px; border-radius:10px; max-width:1120px; margin:auto; box-shadow:0 2px 10px rgba(0,0,0,.10); }}
.tab-buttons,.sub-tab-buttons {{ display:flex; gap:5px; border-bottom:2px solid #3498db; margin-bottom:16px; flex-wrap:wrap; }}
.sub-tab-buttons {{ border-bottom-color:#9aa8b8; margin-top:8px; }}
.tab-btn,.sub-tab-btn {{ padding:10px 16px; border:none; background:#ecf0f1; cursor:pointer; font-weight:bold; border-radius:4px 4px 0 0; }}
.tab-btn.active {{ background:#3498db; color:white; }}
.sub-tab-btn.active {{ background:#596a7d; color:white; }}
.tab-content,.sub-tab-content {{ display:none; }}
.tab-content.active,.sub-tab-content.active {{ display:block; }}
</style></head><body><div class="container"><div class="tab-buttons">{tabs}</div>{contents}</div>
<script>
function openRaceTab(evt, id) {{
  document.querySelectorAll('.tab-content, .tab-btn').forEach(e => e.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  evt.currentTarget.classList.add('active');
}}
function openSubTab(evt, id) {{
  const parent = evt.currentTarget.closest('.tab-content');
  parent.querySelectorAll('.sub-tab-content, .sub-tab-btn').forEach(e => e.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  evt.currentTarget.classList.add('active');
}}
</script></body></html>"""


# ==========================================
# 7. Streamlit UI
# ==========================================

st.set_page_config(page_title="NAR公式 物差し能力比較", page_icon="🏇", layout="wide")
st.title("🏇 NAR公式 物差し能力比較")
st.caption("地方競馬専用 / NAR公式から取得 / 直接対決優先 / 斤量を着順・タイムとして読まない安全パーサ")

with st.expander("この版の修正点", expanded=False):
    st.markdown(
        """
- データ元は引き続き NAR公式の `DebaTable` と `RaceMarkTable` だけです。
- 成績表はヘッダから `着順`・`馬番`・`タイム` 列を特定して読みます。行全体から `54.0` のような数字を拾わないため、斤量誤認を避けます。
- ランク付けは添付 `keiba_bot.py` の「相対比較」ロジックに合わせ、比較条件の優先度と循環整理から S/A/B/C を決めます。
- 直接対決を最優先し、同場同距離 → 同場 → 同距離の順で最も強い材料を採用します。
- 物差し馬経由は直接対決がない場合のみ採用し、同条件経由は0.7倍、同場経由は0.5倍、同距離経由は0.35倍に割引します。
- 比較が三すくみになった場合は、より信頼度の高い条件の矢印を残して弱い条件の矢印を外してからランク化します。
- ばんえいは水分量 `2.0%未満` / `2.0%以上` で比較対象を絞れます。不等号は `5秒 / 15秒` 閾値です。
        """
    )

url_input = st.text_input(
    "NAR公式の出馬表URL",
    value="https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable?k_raceDate=2026%2f04%2f26&k_raceNo=1&k_babaCode=3",
    placeholder="https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable?k_raceDate=YYYY%2fMM%2fDD&k_raceNo=1&k_babaCode=3",
)

st.markdown("---")
use_banei_water_filter = st.checkbox("ばんえい水分量フィルタを使う", value=True)
water_mode = st.radio("フィルタ対象", ["現在レースと同じ区分", "2.0%未満", "2.0%以上"], horizontal=True)

st.markdown("---")
st.write("分析するレース番号。未選択ならURLのレースだけ分析します。")
cols = st.columns(12)
selected_races: List[int] = []
for i in range(12):
    with cols[i]:
        if st.checkbox(f"{i + 1}R", key=f"race_{i + 1}"):
            selected_races.append(i + 1)

submitted = st.button("🚀 分析を開始", type="primary")

if submitted:
    if not url_input.strip():
        st.error("NAR公式の出馬表URLを入力してください。")
        st.stop()

    scraper = NarOfficialScraper()
    key = scraper.parse_key_from_url(url_input.strip())
    if not key:
        st.error("URLから k_raceDate / k_raceNo / k_babaCode を抽出できませんでした。")
        st.stop()

    if not selected_races:
        selected_races = [key.race_no]

    results: List[Tuple[int, str, str, str, str, Dict[str, Any]]] = []
    progress = st.progress(0.0)
    status = st.empty()

    for idx, rno in enumerate(selected_races):
        race_key = NarRaceKey(race_date=key.race_date, race_no=rno, baba_code=key.baba_code)
        deba_url = scraper.build_deba_url(race_key)
        water_bucket_for_race: Optional[str] = None
        if use_banei_water_filter:
            if water_mode == "2.0%未満":
                water_bucket_for_race = "lt2"
            elif water_mode == "2.0%以上":
                water_bucket_for_race = "ge2"
            else:
                try:
                    cur = scraper.parse_current_deba(deba_url)
                    if cur.is_banei:
                        water_bucket_for_race = water_bucket(cur.target_water)
                except Exception:
                    water_bucket_for_race = None

        status.info(f"{rno}R 解析中... 水分量フィルタ={water_bucket_label(water_bucket_for_race)}")
        title, body, group_direct, matrix, debug = analyze_race(scraper, deba_url, water_bucket_for_race)
        results.append((rno, title, body, group_direct, matrix, debug))
        progress.progress((idx + 1) / len(selected_races))

    status.empty()
    st.success("分析完了")

    combined = wrap_combined_html(results)
    st.download_button(
        "HTML一括ダウンロード",
        combined,
        file_name=f"NAR公式_物差し能力比較_{key.race_date.replace('/', '')}.html",
        mime="text/html",
    )

    tabs = st.tabs([f"{r[0]}R" for r in results])
    for tab, (r_num, title, body, group_direct, matrix, debug) in zip(tabs, results):
        with tab:
            st.markdown(APP_CSS, unsafe_allow_html=True)
            st.markdown(f"### {title}")
            sub_tabs = st.tabs(["ランク判定", "3頭以上直接対決", "対戦マトリクス", "デバッグ"])
            with sub_tabs[0]:
                st.markdown(body, unsafe_allow_html=True)
            with sub_tabs[1]:
                st.markdown(group_direct, unsafe_allow_html=True)
            with sub_tabs[2]:
                st.markdown(matrix, unsafe_allow_html=True)
            with sub_tabs[3]:
                st.json(debug)
