# -*- coding: utf-8 -*-
"""
NAR公式サイト専用 物差し能力比較 Streamlit版

目的:
- netkeiba側で親馬名を拾ってしまう問題を回避するため、出走馬名・過去走リンク・過去成績をNAR公式から取得する
- JRAモードなし。地方競馬のみ。ばんえいも対応
- ばんえいは水分量 2.0% 未満 / 2.0% 以上で過去比較対象を絞れる。矢印判定は5秒/15秒一律

起動:
    pip install streamlit requests beautifulsoup4 networkx
    streamlit run nar_official_relative_app.py

入力URL例:
    https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable?k_raceDate=2026%2f04%2f26&k_raceNo=1&k_babaCode=3
"""

from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
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
# NAR表示では「帯 広」のように空白が入ることがあるので、正規化後の文字列で探す
PLACE_RE = re.compile("(" + "|".join(map(re.escape, LOCAL_PLACES)) + ")")
TIME_RE = re.compile(r"(?<!\d)(\d{1,2}:\d{2}\.\d|\d{1,3}\.\d)(?!\d)")


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
    """馬名の比較用。NARは馬名リンクに空白が混ざることがあるので落とす。"""
    s = html.unescape(str(name or ""))
    s = s.replace("\xa0", "").replace("\u3000", "")
    s = re.sub(r"\s+", "", s)
    return s.strip()


def normalize_place_text(text: str) -> str:
    """NARの『帯 広』のような表示を『帯広』に寄せる。"""
    s = clean_text(text)
    # 地名の間にだけ入りがちな半角/全角スペースを落とす
    compact = re.sub(r"\s+", "", s)
    return compact


def extract_place(text: str) -> str:
    compact = normalize_place_text(text)
    m = PLACE_RE.search(compact)
    return m.group(1) if m else "不明"


def extract_distance(text: str) -> str:
    s = clean_text(text)
    # NARは 200ｍ / ダート 1400ｍ / 直200 のような表記
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
        try:
            return float(m.group(1))
        except Exception:
            return None
    # 出馬表の過去走欄では 26.04.20 0.8 8頭 のように水分量が日付直後に入る
    m = re.search(r"\d{2,4}[./]\d{1,2}[./]\d{1,2}\s+(\d+(?:\.\d+)?)\s+\d+頭", s)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            return None
    return None


def water_bucket(w: Optional[float]) -> Optional[str]:
    if w is None:
        return None
    return "lt2" if w < 2.0 else "ge2"


def water_bucket_label(bucket: Optional[str]) -> str:
    if bucket == "lt2":
        return "2.0%未満"
    if bucket == "ge2":
        return "2.0%以上"
    return "指定なし"


def convert_time_to_sec(time_str: str) -> Optional[float]:
    if not time_str:
        return None
    m = TIME_RE.search(str(time_str))
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


def parse_date_any(text: str) -> datetime:
    s = clean_text(text)
    # 2026年4月20日
    m = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # 2026/04/20, 2026.04.20, 26.04.20
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
    """NAR公式の相対URLを正しい /KeibaWeb/TodayRaceInfo/ 配下に解決する。

    旧版は urljoin("https://www.keiba.go.jp", "../TodayRaceInfo/...") としていたため、
    https://www.keiba.go.jp/TodayRaceInfo/... になり、/KeibaWeb が抜けて404になっていた。
    これが過去レース取得失敗 → 全頭判定不能の主因。
    """
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return urljoin(BASE, href)
    return urljoin(NAR_TODAY_BASE, href)


def href_has(href: str, key: str) -> bool:
    return key.lower() in (href or "").lower()


def qparam(url: str, key: str) -> Optional[str]:
    qs = parse_qs(urlparse(url).query)
    vals = qs.get(key)
    return vals[0] if vals else None


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

@dataclass(frozen=True)
class NarRaceKey:
    race_date: str  # YYYY/MM/DD
    race_no: int
    baba_code: str


@dataclass
class PastLink:
    current_horse: str
    url: str
    race_date: str
    race_no: int
    baba_code: str
    # DebaTableの過去走欄（例: 26.04.20 0.8 8頭 / 帯広 直200 8番）から先読みした情報。
    # RaceMarkTable側のヘッダでも再取得するが、水分量フィルタはここでも判定できるようにする。
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
    horses: Dict[str, float] = field(default_factory=dict)  # horse_name -> seconds
    ranks: Dict[str, str] = field(default_factory=dict)
    horse_numbers: Dict[str, str] = field(default_factory=dict)  # horse_name -> 馬番（その過去レース時点）
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
        race_date = race_date.replace("-", "/")
        return NarRaceKey(race_date=race_date, race_no=int(race_no), baba_code=str(baba_code))

    def build_deba_url(self, key: NarRaceKey) -> str:
        params = urlencode({
            "k_raceDate": key.race_date,
            "k_raceNo": key.race_no,
            "k_babaCode": key.baba_code,
        })
        return f"{BASE}/KeibaWeb/TodayRaceInfo/DebaTable?{params}"

    def build_racemark_url(self, race_date: str, race_no: int, baba_code: str) -> str:
        params = urlencode({
            "k_raceDate": race_date,
            "k_raceNo": race_no,
            "k_babaCode": baba_code,
        })
        return f"{BASE}/KeibaWeb/TodayRaceInfo/RaceMarkTable?{params}"

    def parse_page_meta(self, soup: BeautifulSoup, fallback_key: Optional[NarRaceKey] = None) -> Dict[str, Any]:
        text = clean_text(soup)
        compact = normalize_place_text(text)
        course = extract_place(compact)
        distance = extract_distance(text)
        water = extract_water(text)
        dt = parse_date_any(text)

        title = ""
        # NARの出馬表は h3 にレース名が入ることが多い。ヘルプ見出し等は避ける。
        for h in soup.find_all(["h2", "h3", "h4"]):
            t = clean_text(h)
            if not t:
                continue
            if any(skip in t for skip in ["オッズ", "出馬表の見方", "地方競馬情報サイト"]):
                continue
            # 日付やサイト名ではなく、レース名らしいものを採用
            if "競走" not in t and len(t) >= 3:
                title = t
                break
        if not title:
            # RaceMarkTableではレース名が span.plus1bold02 に入る。
            sp = soup.find("span", class_="plus1bold02")
            if sp:
                title = clean_text(sp)
        if not title:
            # RaceMarkTableではhタグに無いケースがあるため、ヘッダ直後の短い行を保険で拾う
            lines = [x.strip() for x in soup.get_text("\n", strip=True).split("\n") if x.strip()]
            for idx, line in enumerate(lines):
                if "天候" in line and "馬場" in line and idx + 1 < len(lines):
                    title = lines[idx + 1]
                    break
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

    def _row_has_current_horse_link(self, row) -> bool:
        return any(href_has(a.get("href", ""), "DataRoom/HorseMarkInfo") for a in row.find_all("a", href=True))

    def _row_race_links(self, row) -> List[str]:
        urls: List[str] = []
        for a in row.find_all("a", href=True):
            href = a.get("href", "")
            if href_has(href, "TodayRaceInfo/RaceMarkTable"):
                u = abs_url(href)
                if u not in urls:
                    urls.append(u)
        return urls

    def _extract_umaban_from_row(self, row, horse_name: str, fallback_no: int) -> str:
        row_text = clean_text(row)
        prefix = row_text.split(clean_text(horse_name))[0]
        nums = re.findall(r"(?<![\d.])\d{1,2}(?![\d.])", prefix)
        if nums:
            return nums[-1]
        # cellベースの保険
        cells = [clean_text(td) for td in row.find_all(["td", "th"])]
        small_nums = [c for c in cells[:5] if re.fullmatch(r"\d{1,2}", c)]
        if small_nums:
            return small_nums[-1]
        return str(fallback_no)

    def parse_current_deba(self, url: str) -> CurrentRaceData:
        key = self.parse_key_from_url(url)
        if not key:
            raise ValueError("NAR公式URLから k_raceDate / k_raceNo / k_babaCode を抽出できませんでした。")
        canonical_url = self.build_deba_url(key)
        text = self.get_html(canonical_url)
        soup = BeautifulSoup(text, "html.parser")
        meta = self.parse_page_meta(soup, fallback_key=key)

        rows = soup.find_all("tr")
        horse_rows: List[Tuple[int, Any]] = []
        for idx, row in enumerate(rows):
            if self._row_has_current_horse_link(row):
                # 競走馬行以外の馬情報欄を避けるため、RaceMarkTableリンクまたは馬番らしき数字がある行を採用
                row_text = clean_text(row)
                if self._row_race_links(row) or re.search(r"^\s*\d+\s+\d+\s+", row_text):
                    horse_rows.append((idx, row))

        umaban_dict: Dict[str, str] = {}
        past_links: List[PastLink] = []
        parse_errors: List[str] = []
        seen_horses = set()

        for order, (idx, row) in enumerate(horse_rows, start=1):
            horse_link = None
            for a in row.find_all("a", href=True):
                if href_has(a.get("href", ""), "DataRoom/HorseMarkInfo"):
                    horse_link = a
                    break
            if not horse_link:
                continue
            horse_name = normalize_name(clean_text(horse_link))
            if not horse_name or horse_name in seen_horses:
                continue
            seen_horses.add(horse_name)

            umaban = self._extract_umaban_from_row(row, horse_name, order)
            umaban_dict[horse_name] = umaban

            # NAR公式の出馬表は、馬名行に「過去5走のraceInfo」、次行に「RaceMarkTableリンク」が並ぶ構造。
            # 例: 26.04.20 0.8 8頭 / 帯広 直200 8番 → 真ん中の0.8がばんえいの水分量。
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

            # info_nodesとurlsは通常同じ並びになる。ズレてもURL側を優先し、ヒントだけ空にする。
            past_hints: List[Dict[str, Any]] = []
            for node in info_nodes[:5]:
                itxt = clean_text(node)
                past_hints.append({
                    "date_hint": parse_date_any(itxt),
                    "water_hint": extract_water(itxt),
                    "course_hint": extract_place(itxt),
                    "distance_hint": extract_distance(itxt),
                    "raw": itxt,
                })

            if not urls:
                parse_errors.append(f"{horse_name}: 過去走リンクなし")

            for pos, u in enumerate(urls):
                race_date = qparam(u, "k_raceDate") or ""
                race_no = qparam(u, "k_raceNo") or "0"
                baba_code = qparam(u, "k_babaCode") or key.baba_code
                try:
                    rn = int(race_no)
                except Exception:
                    rn = 0
                hint = past_hints[pos] if pos < len(past_hints) else {}
                past_links.append(
                    PastLink(
                        current_horse=horse_name,
                        url=u,
                        race_date=race_date.replace("-", "/"),
                        race_no=rn,
                        baba_code=str(baba_code),
                        date_hint=hint.get("date_hint", datetime.min),
                        water_hint=hint.get("water_hint"),
                        course_hint=hint.get("course_hint", "不明"),
                        distance_hint=hint.get("distance_hint", "不明"),
                    )
                )

        # まれにtable構造が拾えない場合の最終保険。aタグ順から馬名だけは拾う。
        if not umaban_dict:
            horse_links = []
            for a in soup.find_all("a", href=True):
                if href_has(a.get("href", ""), "DataRoom/HorseMarkInfo"):
                    nm = normalize_name(clean_text(a))
                    if nm and nm not in horse_links:
                        horse_links.append(nm)
            for i, nm in enumerate(horse_links, start=1):
                umaban_dict[nm] = str(i)
            parse_errors.append("table行解析に失敗したため、HorseMarkInfoリンク順で馬名のみ取得しました。")

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
            "past_links_sample": [pl.url for pl in past_links[:10]],
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

    def parse_result_table(self, url: str, hint: Optional[PastLink] = None) -> RaceInfo:
        if url in self.result_cache:
            race = self.result_cache[url]
            # 初回取得時にRaceMarkTable側で水分量が取れなかった場合だけ、DebaTableのヒントで補完。
            if hint is not None and race.water is None:
                race.water = hint.water_hint
            return race

        key = self.parse_key_from_url(url)
        text = self.get_html(url)
        soup = BeautifulSoup(text, "html.parser")
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
            if (not race.race_date) and hint.date_hint != datetime.min:
                race.race_date = date_to_nar(hint.date_hint)

        for row in soup.find_all("tr"):
            horse_link = None
            for a in row.find_all("a", href=True):
                if href_has(a.get("href", ""), "DataRoom/HorseMarkInfo"):
                    horse_link = a
                    break
            if not horse_link:
                continue
            horse_name = normalize_name(clean_text(horse_link))
            if not horse_name:
                continue
            row_text = clean_text(row)
            sec = convert_time_to_sec(row_text)
            if sec is None:
                # 取消・中止などは比較不能
                continue

            cells = row.find_all("td")
            # RaceMarkTableは通常: 0着順 / 1枠番 / 2馬番 / 3馬名 / ... / 11タイム
            rank = clean_text(cells[0]) if len(cells) > 0 else ""
            umaban_at_race = clean_text(cells[2]) if len(cells) > 2 else ""
            if not re.fullmatch(r"\d{1,2}", rank):
                prefix = row_text.split(clean_text(horse_link))[0]
                m = re.search(r"(?<![\d.])(\d{1,2})(?![\d.])", prefix)
                rank = m.group(1) if m else ""
            if not re.fullmatch(r"\d{1,2}", umaban_at_race):
                umaban_at_race = ""

            race.horses[horse_name] = sec
            if rank:
                race.ranks[horse_name] = rank
            if umaban_at_race:
                race.horse_numbers[horse_name] = umaban_at_race

        self.result_cache[url] = race
        return race

    def fetch_current_and_past(self, deba_url: str, water_filter_bucket: Optional[str]) -> Tuple[CurrentRaceData, List[RaceInfo]]:
        current = self.parse_current_deba(deba_url)
        race_by_url: Dict[str, RaceInfo] = {}
        excluded_by_water: List[str] = []
        failed_results: List[str] = []

        # 出走馬ごとの過去リンクをfetchし、同じ過去レースは1つに統合。
        # ばんえい水分量フィルタは、まずDebaTableのraceInfoヒントで先に除外し、
        # RaceMarkTable取得後にもヘッダの馬場水分で再チェックする。
        for pl in current.past_links:
            if current.is_banei and water_filter_bucket:
                hb = water_bucket(pl.water_hint)
                if hb is not None and hb != water_filter_bucket:
                    excluded_by_water.append(
                        f"{pl.race_date} {pl.current_horse} 水分量={pl.water_hint}({water_bucket_label(hb)}) ※出馬表過去走欄で除外"
                    )
                    continue

            try:
                race = self.parse_result_table(pl.url, hint=pl)
            except Exception as e:
                failed_results.append(f"{pl.current_horse}: {pl.url} / {e}")
                continue

            # 現在馬がそのレース結果内にいない場合は、馬名表記ズレまたは行取得ミスなので使わない
            if pl.current_horse not in race.horses:
                failed_results.append(f"{pl.current_horse}: 過去結果に該当馬名なし / {race.title}")
                continue

            if current.is_banei and water_filter_bucket:
                rb = water_bucket(race.water)
                if rb != water_filter_bucket:
                    excluded_by_water.append(
                        f"{race.race_date} {race.title} 水分量={race.water}({water_bucket_label(rb)}) ※成績表ヘッダで除外"
                    )
                    continue

            if pl.url not in race_by_url:
                race_by_url[pl.url] = race
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
    """draw_th, strong_th。diff>0なら本馬優勢。

    ばんえいはユーザー指定どおり一律:
      - 5秒未満: =
      - 5秒以上15秒未満: > / <
      - 15秒以上: >> / <<
    """
    if is_banei:
        return (5.0, 15.0)
    return (0.8, 2.0) if is_strict else (1.2, 3.0)


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

    def add_edge(h1: str, h2: str, raw_diff_seconds: float, race: RaceInfo, is_direct: bool):
        if not h1 or not h2 or h1 == h2:
            return
        # 格納キーを文字列順で正規化。raw_diffは key0_time - key1_time に合わせる。
        if str(h1) > str(h2):
            h1, h2 = h2, h1
            raw_diff_seconds = -raw_diff_seconds

        cap = 30.0 if is_banei else 8.0
        capped = max(-cap, min(cap, raw_diff_seconds))
        r_dist = int(race.distance) if str(race.distance).isdigit() else 0
        is_same_place = race.course == target_course
        is_exact = is_same_place and r_dist == cur_dist
        is_same_layout = is_same_track_layout(race.course, race.distance, target_distance)
        badge = "[場×距]" if is_exact else "[場]" if is_same_place else "[距]" if r_dist == cur_dist else ""

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
        if not h_list:
            continue
        current_in_race = [(h, t) for h, t in h_list if h in current_names]
        if not current_in_race:
            continue

        # 現在出走馬同士が同じ過去レースに出ていれば直接対決
        if len(current_in_race) >= 2:
            for i in range(len(current_in_race)):
                for j in range(i + 1, len(current_in_race)):
                    h1, t1 = current_in_race[i]
                    h2, t2 = current_in_race[j]
                    add_edge(h1, h2, t1 - t2, race, True)

        # 現在出走馬 vs 隠れ馬、隠れ馬同士。後段で物差し経由に使う。
        hidden_horses = [(h, t) for h, t in h_list if h not in current_names]
        for curr_name, curr_time in current_in_race:
            for hid_name, hid_time in hidden_horses:
                add_edge(curr_name, hid_name, curr_time - hid_time, race, False)
        for i in range(len(hidden_horses)):
            for j in range(i + 1, len(hidden_horses)):
                h1, t1 = hidden_horses[i]
                h2, t2 = hidden_horses[j]
                add_edge(h1, h2, t1 - t2, race, False)

    # 同一URL重複を除去し、最新順に最大5件
    for _, _, d in G.edges(data=True):
        d["history"].sort(key=lambda x: x["date"] if isinstance(x["date"], datetime) else datetime.min, reverse=True)
        seen = set()
        deduped = []
        for hi in d["history"]:
            key = hi.get("url") or (hi.get("date_str"), hi.get("title"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(hi)
        d["history"] = deduped[:5]
        d["diffs"] = [hi["raw_diff"] for hi in d["history"]]
        d["rank_diff"] = sum(d["diffs"]) / len(d["diffs"]) if d["diffs"] else 0.0

    return G


def advantage_entries_from_edge(G: nx.DiGraph, u: str, v: str) -> List[Dict[str, Any]]:
    """uから見たvへの優劣。diff>0ならu優勢。"""
    a, b = (u, v) if str(u) < str(v) else (v, u)
    if not G.has_edge(a, b):
        return []
    out = []
    for hi in G[a][b]["history"]:
        # raw_diff = a_time - b_time。u==aなら速いほど負なので反転。
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
            direct = advantage_entries_from_edge(G, u, v)
            if direct:
                same_cond, other = [], []
                for hi in direct:
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
                        "water": hi.get("water"),
                        "date": hi.get("date", datetime.min),
                        "date_str": hi.get("date_str", ""),
                        "url": hi.get("url", ""),
                        "title": hi.get("title", ""),
                        "route": "direct",
                        "self_rank": hi.get("self_rank", ""),
                        "self_umaban": hi.get("self_umaban", ""),
                        "opp_rank": hi.get("opp_rank", ""),
                        "opp_umaban": hi.get("opp_umaban", ""),
                    }
                    if entry["is_strict"]:
                        same_cond.append(entry)
                    else:
                        other.append(entry)
                pair_net[u][v].extend(same_cond if same_cond else other)
                if pair_net[u][v]:
                    continue

            # 直接がなければ、隠れ馬1頭を介した比較
            hidden_nodes = [n for n in G.nodes() if n not in current_names]
            candidates = []
            for h in hidden_nodes:
                u_h = advantage_entries_from_edge(G, u, h)  # u > h
                h_v = advantage_entries_from_edge(G, h, v)  # h > v
                if not u_h or not h_v:
                    continue
                strict_vals = []
                loose_vals = []
                for uh in u_h:
                    for hv in h_v:
                        p1, d1 = uh.get("place", ""), uh.get("dist", "")
                        p2, d2 = hv.get("place", ""), hv.get("dist", "")
                        if p1 == "大井" and p2 == "大井":
                            if (is_ooi_inner(d1) and is_ooi_outer(d2)) or (is_ooi_outer(d1) and is_ooi_inner(d2)):
                                continue
                        if is_one_turn(target_course, cur_dist):
                            if not is_one_turn(p1, d1) or not is_one_turn(p2, d2):
                                continue
                        est = uh["diff"] + hv["diff"]
                        dt = min(
                            uh.get("date", datetime.min) if isinstance(uh.get("date"), datetime) else datetime.min,
                            hv.get("date", datetime.min) if isinstance(hv.get("date"), datetime) else datetime.min,
                        )
                        is_strict = p1 == p2 and is_same_track_layout(p1, d1, d2) and p1 == target_course and str(d1) == str(cur_dist)
                        item = (est, dt, p1, d1, h, uh, hv)
                        if is_strict:
                            strict_vals.append(item)
                        else:
                            loose_vals.append(item)
                if strict_vals:
                    raw = sum(x[0] for x in strict_vals) / len(strict_vals)
                    best = max(strict_vals, key=lambda x: x[1])
                    candidates.append({
                        "diff": raw * 0.7,
                        "is_strict": True,
                        "place": best[2],
                        "dist": best[3],
                        "water": best[5].get("water"),
                        "date": best[1],
                        "date_str": best[5].get("date_str", ""),
                        "url": best[5].get("url", ""),
                        "title": best[5].get("title", ""),
                        "route": f"hidden:{h}",
                        "via_horse": h,
                        "via_leg1": dict(best[5]),
                        "via_leg2": dict(best[6]),
                    })
                elif loose_vals:
                    raw = sum(x[0] for x in loose_vals) / len(loose_vals)
                    best = max(loose_vals, key=lambda x: x[1])
                    candidates.append({
                        "diff": raw * 0.5,
                        "is_strict": False,
                        "place": best[2],
                        "dist": best[3],
                        "water": best[5].get("water"),
                        "date": best[1],
                        "date_str": best[5].get("date_str", ""),
                        "url": best[5].get("url", ""),
                        "title": best[5].get("title", ""),
                        "route": f"hidden:{h}",
                        "via_horse": h,
                        "via_leg1": dict(best[5]),
                        "via_leg2": dict(best[6]),
                    })
            if candidates:
                candidates.sort(key=lambda x: (x["is_strict"], abs(x["diff"]), x["date"]), reverse=True)
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
            best_is_strict = any(e.get("is_strict") for e in entries)
            target_entries = [e for e in entries if bool(e.get("is_strict")) == best_is_strict]
            if not target_entries:
                continue
            target_entries.sort(key=lambda e: e.get("date") if isinstance(e.get("date"), datetime) else datetime.min, reverse=True)
            target_entries = target_entries[:3]
            draw_th, strong_th = thresholds(is_banei, best_is_strict)

            # 厳密条件で勝ち負けが混在する場合は無理に上下を付けない
            if best_is_strict and len(target_entries) >= 2:
                has_win = any(e["diff"] >= draw_th for e in target_entries)
                has_loss = any(e["diff"] <= -draw_th for e in target_entries)
                if has_win and has_loss:
                    matchup_matrix[u][v] = "="
                    matchup_matrix[v][u] = "="
                    continue

            weighted_sum = 0.0
            total_weight = 0.0
            wins = losses = 0
            for k, e in enumerate(target_entries):
                base_w = 1.0 if k == 0 else 0.85 if k == 1 else 0.65
                dt = e.get("date")
                days = (now - dt).days if isinstance(dt, datetime) and dt != datetime.min else 180
                months = max(0.0, days / 30.0)
                if e.get("is_strict"):
                    time_w = 1.0 if months <= 3 else 0.65 if months <= 6 else 0.35
                else:
                    time_w = 1.0 if months <= 2 else 0.8 if months <= 3 else 0.55 if months <= 6 else 0.3
                w = base_w * time_w
                d = e["diff"]
                if d >= draw_th:
                    wins += 1
                elif d <= -draw_th:
                    losses += 1
                weighted_sum += d * w
                total_weight += w
            avg = weighted_sum / total_weight if total_weight else 0.0

            if wins == len(target_entries) and wins > 0:
                sym = ">>" if avg >= strong_th else ">"
            elif losses == len(target_entries) and losses > 0:
                sym = "<<" if avg <= -strong_th else "<"
            elif avg >= draw_th:
                sym = ">>" if avg >= strong_th else ">"
            elif avg <= -draw_th:
                sym = "<<" if avg <= -strong_th else "<"
            else:
                sym = "="

            matchup_matrix[u][v] = sym
            matchup_matrix[v][u] = inverse_sym(sym)

    return matchup_matrix


def evaluate_and_rank(
    pair_net: Dict[str, Dict[str, List[Dict[str, Any]]]],
    matchup_matrix: Dict[str, Dict[str, str]],
    umaban_dict: Dict[str, str],
) -> Tuple[Dict[str, str], List[Tuple[str, int]], List[str]]:
    """相対比較グラフの階層でランク化する。

    重要:
    - 勝ち数・負け数の多寡では決めない。
    - 既知の優劣を「強い馬 -> 弱い馬」の有向グラフにし、
      その到達階層で S/A/B/C を決める。

    例:
        A > B, A > C, A > D, D > E
      の場合、B/C/D は互いに未比較なので同じ階層に置く。
        A=S, B/C/D=A, E=B

    直接対決は compute_pairwise_results 側で経由比較より先に採用されるため、
    直接対決がある組み合わせでは物差し馬経由より直接結果が優先される。
    """
    runners = list(umaban_dict.keys())

    def rel_is_adv(rel: str) -> bool:
        return rel in (">", ">>")

    def rel_is_disadv(rel: str) -> bool:
        return rel in ("<", "<<")

    def edge_strength(rel: str) -> int:
        return 2 if rel in (">>", "<<") else 1

    # 1) 既知の優劣だけで有向グラフを作る。勝ち負け数ではなく、上下関係そのものを保持する。
    D = nx.DiGraph()
    D.add_nodes_from(runners)

    for i, u in enumerate(runners):
        for v in runners[i + 1:]:
            rel = matchup_matrix.get(u, {}).get(v, "")
            if not rel or rel == "=":
                continue

            entries_uv = pair_net.get(u, {}).get(v, [])
            entries_vu = pair_net.get(v, {}).get(u, [])
            entries = entries_uv or entries_vu
            is_direct = any(e.get("route") == "direct" for e in entries)
            route_priority = 2 if is_direct else 1

            if rel_is_adv(rel):
                winner, loser = u, v
            elif rel_is_disadv(rel):
                winner, loser = v, u
            else:
                continue

            strength = edge_strength(rel)
            if D.has_edge(winner, loser):
                ed = D[winner][loser]
                ed["strength"] = max(ed.get("strength", 1), strength)
                ed["route_priority"] = max(ed.get("route_priority", 1), route_priority)
                ed["is_direct"] = bool(ed.get("is_direct")) or is_direct
            else:
                D.add_edge(
                    winner,
                    loser,
                    strength=strength,
                    route_priority=route_priority,
                    is_direct=is_direct,
                )

    # 2) 比較材料が全くない馬は保留。上下関係が1本でもある馬だけ階層化する。
    connected_runners = {n for n in runners if D.in_degree(n) + D.out_degree(n) > 0}
    unranked = [u for u in runners if u not in connected_runners]

    if not connected_runners:
        return {}, [], unranked

    subD = D.subgraph(connected_runners).copy()

    # 3) 三すくみなどの循環は同一階層として扱うため、強連結成分に畳み込む。
    sccs = list(nx.strongly_connected_components(subD))
    comp_index: Dict[str, int] = {}
    for idx, comp in enumerate(sccs):
        for node in comp:
            comp_index[node] = idx

    C = nx.DiGraph()
    for idx, comp in enumerate(sccs):
        C.add_node(idx, members=set(comp))

    for a, b, ed in subD.edges(data=True):
        ca, cb = comp_index[a], comp_index[b]
        if ca == cb:
            continue
        new_weight = ed.get("route_priority", 1) * 10 + ed.get("strength", 1)
        if C.has_edge(ca, cb):
            if new_weight > C[ca][cb].get("weight", 0):
                C[ca][cb].update(weight=new_weight, is_direct=ed.get("is_direct", False))
        else:
            C.add_edge(ca, cb, weight=new_weight, is_direct=ed.get("is_direct", False))

    # 4) トップ層からの距離で階層を決める。
    #    indegree 0 = 既知関係上、誰にも明確に負けていない層 = S。
    depth: Dict[int, int] = {}
    for comp in nx.topological_sort(C):
        preds = list(C.predecessors(comp))
        if not preds:
            depth[comp] = 0
        else:
            # 複数の上位馬がいる場合は、最も深い親 + 1。これにより A>D>E ならEはBになる。
            depth[comp] = max(depth[p] + 1 for p in preds)

    def depth_to_tier(d: int) -> str:
        if d <= 0:
            return "S"
        if d == 1:
            return "A"
        if d == 2:
            return "B"
        return "C"

    tier_map: Dict[str, str] = {}
    tier_base = {"S": 400000, "A": 300000, "B": 200000, "C": 100000}
    ranked: List[Tuple[str, int]] = []

    # 表示順用。ランク自体はスコアではなく depth で決まる。
    # 同じ階層内では「直接の下位数」「到達できる下位数」「馬番」で並べるだけ。
    descendants_cache: Dict[int, set] = {c: nx.descendants(C, c) for c in C.nodes()}

    for u in runners:
        if u in unranked:
            continue
        c = comp_index[u]
        tier = depth_to_tier(depth.get(c, 0))
        tier_map[u] = tier

        direct_down = sum(1 for _, _, ed in subD.out_edges(u, data=True) if ed.get("is_direct"))
        all_down_comps = descendants_cache.get(c, set())
        all_down_members = sum(len(C.nodes[x].get("members", [])) for x in all_down_comps)
        strong_down = sum(1 for _, _, ed in subD.out_edges(u, data=True) if ed.get("strength", 1) >= 2)

        try:
            uma_no = int(umaban_dict.get(u, "999"))
        except Exception:
            uma_no = 999

        sort_score = (
            tier_base.get(tier, 0)
            + direct_down * 10000
            + all_down_members * 1000
            + strong_down * 100
            + max(0, 999 - uma_no)
        )
        ranked.append((u, sort_score))

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
    parts = ["<div style='font-family:-apple-system,BlinkMacSystemFont,Meiryo,sans-serif;font-size:14px;color:#333;'>"]
    parts.append(
        f"<div style='padding:10px 12px;background:#f7f9fb;border:1px solid #e1e7ef;border-radius:8px;margin-bottom:14px;'>"
        f"対象条件：<b>{html.escape(target_course)}{html.escape(str(target_distance))}</b>{html.escape(water_txt)}{html.escape(filter_txt)} "
        f"/ 相対比較グラフ階層方式 / 直接対決を最優先 / ＞は本馬優勢、＜は劣勢</div>"
    )

    def render_entry(u: str, v: str, e: Dict[str, Any]) -> str:
        sym, c = diff_symbol_and_color(e.get("diff", 0.0), is_banei, bool(e.get("is_strict")))
        badge = "同条件" if e.get("is_strict") else f"{e.get('place','?')}{e.get('dist','?')}"
        water = e.get("water")
        wtxt = f"水分:{water:.1f}%" if isinstance(water, (int, float)) else ""
        v_uma = umaban_dict.get(v, "?")
        route = e.get("route", "direct")
        if route == "direct":
            race_link = _safe_link(e.get("url", ""), _race_link_label(e))
            self_rank = e.get("self_rank") or "?"
            self_umaban = e.get("self_umaban") or "?"
            opp_rank = e.get("opp_rank") or "?"
            opp_umaban = e.get("opp_umaban") or "?"
            route_html = (
                f"<span style='color:#0b65c2;font-weight:700;'>直接</span> "
                f"<span style='color:#777;'>当時: 本馬 {html.escape(str(self_rank))}着/{html.escape(str(self_umaban))}番 "
                f"vs 相手 {html.escape(str(opp_rank))}着/{html.escape(str(opp_umaban))}番</span> "
                f"{race_link}"
            )
        else:
            via = e.get("via_horse") or route.replace("hidden:", "")
            leg1 = e.get("via_leg1", {}) or {}
            leg2 = e.get("via_leg2", {}) or {}
            route_html = (
                f"<span style='color:#8e44ad;font-weight:700;'>経由：{html.escape(str(via))}</span> "
                f"<span style='color:#777;'>本馬↔経由馬:</span> {_safe_link(leg1.get('url',''), _race_link_label(leg1))} "
                f"<span style='color:#aaa;'>/</span> "
                f"<span style='color:#777;'>経由馬↔相手:</span> {_safe_link(leg2.get('url',''), _race_link_label(leg2))}"
            )
        return (
            f"<div style='margin-left:10px;font-size:.86em;line-height:1.7;margin-bottom:5px;'>"
            f"<span style='display:inline-block;background:#eef3f8;border-radius:999px;padding:1px 7px;margin-right:4px;'>{html.escape(badge)}</span>"
            f"<span style='color:#777;'>{html.escape(wtxt)}</span> "
            f"本馬 <span style='color:{c};font-weight:800;'>{sym}</span> [{html.escape(v_uma)}]{html.escape(v)} "
            f"<span style='color:{c};'>({e.get('diff',0.0):+.1f}秒換算)</span><br>"
            f"<span style='margin-left:14px;'>{route_html}</span>"
            f"</div>"
        )

    def render_horse(u: str) -> str:
        uma = umaban_dict.get(u, "?")
        tier = tier_map.get(u, "C")
        color = tier_colors.get(tier, "#95a5a6")
        hp = [
            f"<div style='margin:0 0 16px 0;border-left:5px solid {color};padding:10px 12px;background:#fff;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.06);'>",
            f"<div style='font-size:1.1em;font-weight:800;'>[{html.escape(uma)}] {html.escape(u)}</div>",
        ]
        wins = draws = losses = direct_wins = direct_losses = 0
        for v in runners:
            if u == v:
                continue
            rel = matchup_matrix.get(u, {}).get(v)
            entries = pair_net.get(u, {}).get(v, [])
            is_direct = any(e.get("route") == "direct" for e in entries)
            if rel in (">", ">>"):
                wins += 1
                if is_direct:
                    direct_wins += 1
            elif rel in ("<", "<<"):
                losses += 1
                if is_direct:
                    direct_losses += 1
            elif rel == "=":
                draws += 1
        if wins + draws + losses:
            hp.append(
                f"<div style='font-size:.86em;margin:4px 0 8px 0;'>"
                f"既知の優劣：<span style='color:#189a55;font-weight:700;'>{wins}優勢</span> / "
                f"<span style='color:#777;'>{draws}互角</span> / "
                f"<span style='color:#d83a3a;font-weight:700;'>{losses}劣勢</span> "
                f"<span style='color:#555;'>（直接: {direct_wins}優勢 / {direct_losses}劣勢）</span></div>"
            )
        all_lines = []
        for v in runners:
            if u == v:
                continue
            entries = pair_net.get(u, {}).get(v, [])
            if not entries:
                continue
            entries = sorted(
                entries,
                key=lambda e: (e.get("route") == "direct", bool(e.get("is_strict")), e.get("date") if isinstance(e.get("date"), datetime) else datetime.min),
                reverse=True,
            )[:3]
            for e in entries:
                all_lines.append((1 if e.get("route") == "direct" else 0, e.get("date") if isinstance(e.get("date"), datetime) else datetime.min, render_entry(u, v, e)))
        all_lines.sort(key=lambda x: (x[0], x[1]), reverse=True)
        lines = [x[2] for x in all_lines]
        hp.append("".join(lines[:16]) if lines else "<div style='margin-left:10px;font-size:.86em;color:#999;'>比較可能な直接・間接データなし</div>")
        hp.append("</div>")
        return "\n".join(hp)

    for tier in ("S", "A", "B", "C"):
        horses = [u for u, _ in ranked if tier_map.get(u) == tier]
        if not horses:
            continue
        parts.append(
            f"<h3 style='background:{tier_colors[tier]};color:#fff;padding:9px 12px;border-radius:6px;margin:18px 0 10px;'>"
            f"🏆 {tier}ランク：{tier_names[tier]}</h3>"
        )
        for u in horses:
            parts.append(render_horse(u))

    if unranked:
        parts.append("<h3 style='background:#95a5a6;color:#fff;padding:9px 12px;border-radius:6px;margin:18px 0 10px;'>❗ 保留：勝負づけ未確定・比較材料不足</h3>")
        for u in unranked:
            parts.append(
                f"<div style='margin-bottom:10px;border-left:5px solid #95a5a6;padding:10px 12px;background:#fff;border-radius:6px;'>"
                f"<b>[{html.escape(umaban_dict.get(u,'?'))}] {html.escape(u)}</b>"
                f"<div style='margin-left:10px;font-size:.84em;color:#999;'>過去5走内の比較材料が薄いため、無理にランクへ押し込まず保留にしています。</div></div>"
            )
    parts.append("</div>")
    return "\n".join(parts)


def build_group_direct_html(past_races: List[RaceInfo], umaban_dict: Dict[str, str], is_banei: bool) -> str:
    """同じ過去レースに現在出走馬が3頭以上出ている直接対決を一覧化。"""
    current_names = set(umaban_dict.keys())
    races = []
    for race in past_races:
        members = [h for h in race.horses.keys() if h in current_names]
        if len(members) < 3:
            continue
        def rank_key(h: str):
            r = race.ranks.get(h, "999")
            return int(r) if str(r).isdigit() else 999
        members.sort(key=rank_key)
        races.append((parse_date_any(race.race_date), race, members))
    races.sort(key=lambda x: x[0], reverse=True)

    if not races:
        return "<div style='padding:12px;background:#fff;border-radius:8px;border:1px solid #e5e7eb;color:#777;'>同じ過去レースに今回出走馬が3頭以上いた直接対決は見つかりませんでした。</div>"

    out = ["<div style='font-family:-apple-system,BlinkMacSystemFont,Meiryo,sans-serif;font-size:14px;color:#333;'>"]
    out.append("<p style='color:#555;'>同じ過去レースに今回出走馬が3頭以上出ていたケースです。直接対決を最重要材料として確認できます。</p>")
    for _, race, members in races:
        water = f" / 水分:{race.water:.1f}%" if isinstance(race.water, (int, float)) else ""
        title_link = _safe_link(race.url, f"{race.race_date} {race.title}")
        out.append(
            f"<div style='margin:0 0 16px;background:#fff;border:1px solid #e1e7ef;border-radius:8px;overflow:hidden;'>"
            f"<div style='background:#f7f9fb;padding:10px 12px;font-weight:800;'>🔗 {title_link} "
            f"<span style='font-weight:400;color:#666;'>({html.escape(race.course)}{html.escape(str(race.distance))}{html.escape(water)})</span></div>"
        )
        out.append("<table style='width:100%;border-collapse:collapse;font-size:13px;'>")
        out.append("<thead><tr style='background:#fbfbfb;'><th>当時着順</th><th>当時馬番</th><th>今回馬番</th><th>馬名</th><th>タイム</th><th>メモ</th></tr></thead><tbody>")
        first_time = min([race.horses[h] for h in members if race.horses.get(h) is not None], default=None)
        for h in members:
            sec = race.horses.get(h)
            diff = (sec - first_time) if first_time is not None and sec is not None else 0.0
            rank = race.ranks.get(h, "?")
            past_umaban = race.horse_numbers.get(h, "?")
            cur_umaban = umaban_dict.get(h, "?")
            time_txt = f"{int(sec//60)}:{sec%60:04.1f}" if isinstance(sec, (int, float)) and sec >= 60 else (f"{sec:.1f}" if isinstance(sec, (int, float)) else "")
            mark = "同レース内最先着" if abs(diff) < 0.01 else f"最先着から+{diff:.1f}秒"
            out.append(
                f"<tr><td style='text-align:center;font-weight:800;'>{html.escape(str(rank))}</td>"
                f"<td style='text-align:center;'>{html.escape(str(past_umaban))}</td>"
                f"<td style='text-align:center;'>{html.escape(str(cur_umaban))}</td>"
                f"<td><b>{html.escape(h)}</b></td>"
                f"<td style='text-align:center;'>{html.escape(time_txt)}</td>"
                f"<td style='color:#666;'>{html.escape(mark)}</td></tr>"
            )
        out.append("</tbody></table></div>")
    out.append("<style>td,th{border:1px solid #e5e7eb;padding:7px 8px;} th{font-weight:700;}</style></div>")
    return "\n".join(out)


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


def count_edges(G: nx.DiGraph) -> Tuple[int, int]:
    direct = hidden = 0
    for _, _, ed in G.edges(data=True):
        if any(h.get("is_direct") for h in ed.get("history", [])):
            direct += 1
        if any(not h.get("is_direct") for h in ed.get("history", [])):
            hidden += 1
    return direct, hidden


# ==========================================
# 6. 統合関数・HTML一括出力
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
        group_direct_html = build_group_direct_html(past_races, current.umaban_dict, current.is_banei)
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
        return "解析エラー", f"<div style='color:#d83a3a;font-weight:bold;'>エラー: {html.escape(str(e))}</div>", "", "", {"error": str(e), "url": deba_url}


def wrap_combined_html(results: List[Tuple[int, str, str, str, str, Dict[str, Any]]]) -> str:
    tabs, contents = "", ""
    for i, (r_num, title, body, group_direct, matrix, debug) in enumerate(results):
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab-btn {active}" onclick="openRaceTab(event, \'race_{r_num}\')">{r_num}R</button>\n'
        contents += f"""
        <div id='race_{r_num}' class='tab-content {active}'>
          <h2>📊 {html.escape(title)}</h2>
          <div class='sub-tab-buttons'>
            <button class='sub-tab-btn active' onclick="openSubTab(event, 'race_{r_num}_rank')">ランク判定</button>
            <button class='sub-tab-btn' onclick="openSubTab(event, 'race_{r_num}_direct3')">3頭以上直接対決</button>
            <button class='sub-tab-btn' onclick="openSubTab(event, 'race_{r_num}_matrix')">対戦マトリクス</button>
          </div>
          <div id='race_{r_num}_rank' class='sub-tab-content active'>{body}</div>
          <div id='race_{r_num}_direct3' class='sub-tab-content'>{group_direct}</div>
          <div id='race_{r_num}_matrix' class='sub-tab-content'><h3>対戦マトリクス</h3>{matrix}</div>
        </div>
        """
    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NAR公式 物差し能力比較</title>
<style>
body {{ font-family:-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif; background:#f7f6f2; padding:20px; }}
.container {{ background:#fff; padding:20px; border-radius:10px; max-width:1100px; margin:auto; box-shadow:0 2px 10px rgba(0,0,0,.10); }}
.tab-buttons,.sub-tab-buttons {{ display:flex; gap:5px; border-bottom:2px solid #3498db; margin-bottom:16px; flex-wrap:wrap; }}
.sub-tab-buttons {{ border-bottom-color:#9aa8b8; margin-top:8px; }}
.tab-btn,.sub-tab-btn {{ padding:10px 16px; border:none; background:#ecf0f1; cursor:pointer; font-weight:bold; border-radius:4px 4px 0 0; }}
.tab-btn.active {{ background:#3498db; color:white; }}
.sub-tab-btn.active {{ background:#596a7d; color:white; }}
.tab-content,.sub-tab-content {{ display:none; }} .tab-content.active,.sub-tab-content.active {{ display:block; }}
a {{ color:#0b65c2; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
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
st.caption("地方競馬専用 / NAR公式から取得 / 直接対決を最重要視 / 3頭以上直接対決タブ / ばんえい水分量フィルタ対応")

with st.expander("重要な変更点", expanded=False):
    st.markdown(
        """
- netkeiba馬柱では、地方・ばんえいで父馬名を拾ってしまうケースがあるため、出走馬名はNAR公式の出馬表から取得します。
- 過去5走リンクもNAR公式の出馬表から拾い、比較用の全頭結果はNAR公式の成績表（RaceMarkTable）から取得します。
- ばんえいは水分量を `2.0%未満` / `2.0%以上` に分け、チェック時は比較対象レースも同じ区分だけに絞ります。\n- 直接対決は経由比較より大きく重くランクへ反映します。勝ち抜け式ではなくスコア帯判定なので、同じ評価帯の馬は複数同ランクになります。\n- ばんえいの不等号は一律で `5秒未満=`, `5秒以上15秒未満 >/<`, `15秒以上 >>/<<` です。
        """
    )

url_input = st.text_input(
    "NAR公式の出馬表URL",
    value="https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable?k_raceDate=2026%2f04%2f26&k_raceNo=1&k_babaCode=3",
    placeholder="https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/DebaTable?k_raceDate=2026%2f04%2f26&k_raceNo=1&k_babaCode=3",
)

st.markdown("---")
use_banei_water_filter = st.checkbox(
    "ばんえい水分量フィルタを使う（2.0%未満 / 2.0%以上で比較対象を絞る）",
    value=True,
)
water_mode = st.radio(
    "フィルタ対象",
    ["現在レースと同じ区分", "2.0%未満", "2.0%以上"],
    horizontal=True,
)

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
        st.error("URLから k_raceDate / k_raceNo / k_babaCode を抽出できませんでした。NAR公式の出馬表URLを入れてください。")
        st.stop()

    if not selected_races:
        selected_races = [key.race_no]

    results: List[Tuple[int, str, str, str, str, Dict[str, Any]]] = []
    progress = st.progress(0.0)
    status = st.empty()

    for idx, rno in enumerate(selected_races):
        race_key = NarRaceKey(race_date=key.race_date, race_no=rno, baba_code=key.baba_code)
        deba_url = scraper.build_deba_url(race_key)

        # 「現在レースと同じ区分」を使う場合は、先に現在レースだけ軽く読んで水分量を決める。
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

        status.info(f"🏇 {rno}R 解析中... 水分量フィルタ={water_bucket_label(water_bucket_for_race)}")
        title, body, group_direct, matrix, debug = analyze_race(scraper, deba_url, water_bucket_for_race)
        results.append((rno, title, body, group_direct, matrix, debug))
        progress.progress((idx + 1) / len(selected_races))

    status.empty()
    st.success("✅ 分析完了")

    combined = wrap_combined_html(results)
    st.download_button(
        "📥 HTML一括ダウンロード",
        combined,
        file_name=f"NAR公式_物差し能力比較_{key.race_date.replace('/', '')}.html",
        mime="text/html",
    )

    tabs = st.tabs([f"{r[0]}R" for r in results])
    for tab, (r_num, title, body, group_direct, matrix, debug) in zip(tabs, results):
        with tab:
            st.markdown(f"### {title}")
            sub_tabs = st.tabs(["ランク判定", "3頭以上直接対決", "対戦マトリクス"])
            with sub_tabs[0]:
                st.markdown(body, unsafe_allow_html=True)
            with sub_tabs[1]:
                st.markdown(group_direct, unsafe_allow_html=True)
            with sub_tabs[2]:
                st.markdown(matrix, unsafe_allow_html=True)
