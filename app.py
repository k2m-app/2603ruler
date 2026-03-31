import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
import statistics
from datetime import datetime
import networkx as nx

# ==========================================
# 0. ユーティリティ・定数
# ==========================================
_CIRCLED_NUMS = '⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'

def _to_circled(n):
    try:
        n = int(n)
        return _CIRCLED_NUMS[n] if 0 <= n <= 20 else f'({n})'
    except (ValueError, TypeError):
        return ''

def parse_date(date_str):
    try:
        for sep in ('.', '/'):
            if sep in date_str:
                parts = date_str.split(sep)
                yy = int(parts[0])
                if yy < 100:
                    yy += 2000
                return datetime(yy, int(parts[1]), int(parts[2]))
    except Exception:
        pass
    return datetime.min


# ==========================================
# 1. コース形態判定（keiba_bot.py準拠の精密版）
# ==========================================
def _is_ooi_inner(dist):
    d = int(dist) if str(dist).isdigit() else 0
    return d in [1500, 1600, 1650]

def _is_ooi_outer(dist):
    d = int(dist) if str(dist).isdigit() else 0
    return d > 0 and d not in [1500, 1600, 1650]

def _is_one_turn(place, dist):
    """
    コーナー2回以下のワンターンコースを定義。
    JRA全10場 + 地方全場対応。
    """
    d = int(dist) if str(dist).isdigit() else 0

    # ===== JRA =====
    # 札幌・函館: 円形コースのため全距離4コーナー
    if place in ("札幌", "函館"): return False
    # 福島: 芝1200以下は2コーナー
    if place == "福島" and d <= 1200: return True
    # 新潟: 芝1000(直線)/芝1200(内2コーナー)/ダート1200(2コーナー)
    if place == "新潟" and d <= 1200: return True
    # 東京: 芝1400以下/ダート1300-1400は2コーナー
    if place == "東京" and d <= 1400: return True
    # 中山: 芝1200(外回り)/ダート1200は2コーナー
    if place == "中山" and d <= 1200: return True
    # 中京: 芝1200/ダート1200は2コーナー
    if place == "中京" and d <= 1200: return True
    # 京都: 芝1200/1400(内回り)/ダート1200は2コーナー
    if place == "京都" and d <= 1400: return True
    # 阪神: 芝1200/1400(内回り)/ダート1200は2コーナー
    if place == "阪神" and d <= 1400: return True
    # 小倉: 芝1200/ダート1000は2コーナー
    if place == "小倉" and d <= 1200: return True

    # ===== 南関東 =====
    if place == "川崎" and d == 900: return True
    if place == "浦和" and d == 800: return True
    if place == "船橋" and d in [1000, 1200]: return True
    if place == "大井" and d in [1000, 1200, 1400]: return True

    # ===== 地方その他 =====
    if place == "門別" and d <= 1000: return True
    if place == "盛岡" and d <= 1000: return True
    # 水沢/金沢/笠松/名古屋/園田/姫路/高知/佐賀: 全距離4コーナー小回り
    if place in ("水沢","金沢","笠松","名古屋","園田","姫路","高知","佐賀"): return False

    return False


def _get_track_layout(place, dist):
    """
    競馬場×距離 → コースレイアウト識別子を返す。
    同一競馬場でも内回り/外回り・コーナー数が異なる距離は
    別レイアウトとして区別する。

    ■ JRA (内回り/外回りあり)
      阪神: 内回り(芝1200/2000/2200/3000/3200) / 外回り(芝1600/1800/2400/2600) / ダート共通
      京都: 内回り(芝1200/1400下級/2000) / 外回り(芝1800/2200/2400/3000/3200) / ダート共通
      中山: 内回り(芝1800/2000/2200/2500) / 外回り(芝1200/1600) / ダート共通
      新潟: 内回り(芝1200/1400/2200/2400) / 外回り(芝1600/1800/2000) / 直線(芝1000) / ダート共通
    ■ JRA (単一コース): 東京/中京/札幌/函館/福島/小倉
    ■ 南関東: 大井(内/外/1ターン) / 川崎/船橋/浦和(ターン数別)
    ■ 地方その他: 距離帯で大別
    ■ ばんえい: 直線200m単一
    """
    d = int(dist) if str(dist).isdigit() else 0

    # ===== JRA 内回り/外回り =====
    if place == "阪神":
        if d <= 1400: return "inner_short"        # 芝1200/1400(内), ダート1200/1400
        if d in [1600, 1800]: return "outer_mid"   # 芝1600/1800(外), ダート1800
        if d == 2000: return "inner_mid"           # 芝2000(内), ダート2000
        if d == 2200: return "inner_long"          # 芝2200(内/宝塚記念)
        if d >= 2400: return "outer_long"          # 芝2400/2600(外), ダート2600
        return "inner_mid"

    if place == "京都":
        if d <= 1400: return "inner_short"         # 芝1200/1400(内), ダート1200/1400
        if d == 1600: return "either_mid"          # 条件で内外変動 → 同一扱い
        if d == 1800: return "outer_mid"           # 芝1800(外)
        if d == 2000: return "inner_mid"           # 芝2000(内)
        if d in [2200, 2400]: return "outer_long"  # 芝2200/2400(外)
        if d >= 2600: return "outer_very_long"     # 芝3000/3200(外)
        return "inner_mid"

    if place == "中山":
        if d <= 1200: return "outer_short"         # 芝1200(外回り)
        if d == 1600: return "outer_mid"           # 芝1600(外回り)
        if d in [1800, 2000]: return "inner_mid"   # 芝1800/2000(内回り)
        if d >= 2200: return "inner_long"          # 芝2200/2500(内回り)
        return "inner_mid"

    if place == "新潟":
        if d <= 1000: return "straight"            # 芝1000(直線コース)
        if d <= 1400: return "inner_short"         # 芝1200/1400(内回り)
        if d == 1600: return "outer_mid"           # 芝1600(外回り)
        if d in [1800, 2000]: return "outer_mid"   # 基本外回り
        if d >= 2200: return "inner_long"          # 芝2200/2400(内回り)
        return "outer_mid"

    # ===== JRA 単一コース =====
    if place == "東京":
        if d <= 1400: return "short"
        if d <= 1800: return "mid"
        if d <= 2400: return "classic"
        return "long"

    if place == "中京":
        if d <= 1200: return "short"
        if d <= 1600: return "mid"
        if d <= 2000: return "classic"
        return "long"

    if place in ("札幌", "函館"):
        if d <= 1200: return "short"
        if d <= 1800: return "mid"
        return "long"

    if place == "福島":
        if d <= 1200: return "short"
        if d <= 1800: return "mid"
        return "long"

    if place == "小倉":
        if d <= 1200: return "short"
        if d <= 1800: return "mid"
        return "long"

    # ===== 南関東 =====
    if place == "大井":
        if d <= 1400: return "outer_1turn"
        if d <= 1650: return "inner_2turn"
        return "outer_2turn"

    if place == "川崎":
        if d == 900: return "1turn"
        if d <= 1600: return "2turn"
        return "multi"

    if place == "船橋":
        if d <= 1200: return "1turn"
        if d <= 1800: return "2turn"
        return "multi"

    if place == "浦和":
        if d <= 800: return "1turn"
        if d <= 1500: return "2turn"
        return "multi"

    # ===== 地方その他 =====
    if place == "門別":
        if d <= 1000: return "short"
        if d <= 1700: return "mid"
        return "long"

    if place == "盛岡":
        if d <= 1000: return "short"
        if d <= 1600: return "mid"
        return "long"

    if place == "水沢":
        if d <= 1400: return "short"
        return "standard"

    if place in ("金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀"):
        if d <= 1200: return "short"
        if d <= 1600: return "mid"
        return "long"

    # ===== ばんえい =====
    if place == "帯広":
        return "banei"

    # フォールバック
    if d <= 1200: return "short"
    if d <= 1800: return "mid"
    return "long"


def _is_same_track_layout(place, dist1, dist2):
    """
    同一競馬場における2つの距離が、同じコースレイアウト
    （内/外回り、コーナー数）を使用するかを判定する。
    JRA全10場 + 地方全場 + ばんえい対応。
    """
    return _get_track_layout(place, dist1) == _get_track_layout(place, dist2)


def determine_condition(t_place, t_dist, r_place, r_dist):
    """
    レース条件の一致度を判定 (A=同場同距離, B=同場同形態, C=異条件)
    keiba_bot.pyの精密なレイアウト判定を反映
    """
    td = int(t_dist) if str(t_dist).isdigit() else 0
    rd = int(r_dist) if str(r_dist).isdigit() else 0

    if t_place == r_place and td == rd:
        return 'A'
    if t_place == r_place:
        if _is_same_track_layout(t_place, t_dist, r_dist):
            return 'B'
        return 'C'
    return 'C'


# ==========================================
# 2. 相対評価の記号・ポイント変換
# ==========================================
def get_rel_str(diff, cond, is_banei=False):
    """
    タイム差から相対記号を返す。
    keiba_bot.pyの閾値を採用:
      同条件(A): draw=0.5, strong=1.0
      同形態(B): draw=0.7, strong=1.2
      異条件(C): draw=0.7, strong=1.2
    """
    abs_d = abs(diff)
    if is_banei:
        if abs_d >= 4.0: return ">>" if diff < 0 else "<<"
        if abs_d >= 1.5: return ">" if diff < 0 else "<"
        return "＝"

    if cond == 'A':
        draw_th, strong_th = 0.5, 1.0
    else:  # B or C
        draw_th, strong_th = 0.7, 1.2

    if abs_d >= strong_th:
        return ">>" if diff < 0 else "<<"
    if abs_d >= draw_th:
        return ">" if diff < 0 else "<"
    return "＝"


def calculate_matchup_points(rel, is_direct=True):
    """
    対戦結果をポイントに変換。
    勝ち負けは対称スコア、引き分けはゼロ。
    直接対決は weight=2.0、隠れ馬経由は weight=1.0。
    """
    weight = 2.0 if is_direct else 1.0
    if rel == ">>":   return  3.0 * weight
    elif rel == ">":  return  1.5 * weight
    elif rel == "＝": return  0.0 * weight
    elif rel == "<":  return -1.5 * weight
    elif rel == "<<": return -3.0 * weight
    return 0.0


# ==========================================
# 3. Netkeiba ディープスクレイパー
# ==========================================
class NetkeibaScraper:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    def extract_race_id(self, url):
        match = re.search(r'\d{12}', url)
        return match.group(0) if match else None

    def convert_time_to_sec(self, time_str):
        try:
            if ':' in time_str:
                m, s = time_str.split(':')
                return int(m) * 60 + float(s)
            return float(time_str)
        except Exception:
            return None

    def fetch_past_data(self, race_id, water_mode=None):
        is_nar = int(race_id[4:6]) > 10 if len(race_id) >= 6 else False
        url_domain = "nar.netkeiba.com" if is_nar else "race.netkeiba.com"
        url = f"https://{url_domain}/race/shutuba_past.html?race_id={race_id}"
        time.sleep(1)
        res = requests.get(url, headers=self.headers)
        res.encoding = 'EUC-JP'
        soup = BeautifulSoup(res.text, 'html.parser')

        title_tag = soup.find(class_='RaceName')
        race_title = title_tag.text.strip() if title_tag else f"レースID: {race_id}"
        race_data01 = soup.find('div', class_='RaceData01')
        race_data02 = soup.find('div', class_='RaceData02')

        is_banei = race_data02 is not None and '(ば)' in race_data02.text
        target_course, target_distance = "不明", "不明"

        if race_data02:
            c_match = re.search(
                r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|'
                r'門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)',
                race_data02.text
            )
            if c_match:
                target_course = c_match.group(1)
        if race_data01:
            d_match = re.search(r'(\d{3,4})', race_data01.text)
            if d_match:
                target_distance = d_match.group(1)

        past_races_dict = {}
        umaban_dict = {}
        deep_dive_candidates = set()

        # --- 出馬表ページから過去走情報を取得 ---
        HIDDEN_HORSE_MAX_RUNS = 3  # 隠れ馬探索は近3走のみ
        horse_valid_run_count = {}

        for tr in soup.find_all('tr', class_='HorseList'):
            name_tag = tr.find(class_='Horse02')
            if not name_tag:
                continue
            horse_name = name_tag.find('a').text.strip()

            tds = tr.find_all('td')
            if len(tds) > 1:
                umaban_dict[horse_name] = tds[1].text.strip()

            if horse_name not in horse_valid_run_count:
                horse_valid_run_count[horse_name] = 0

            past_tds = tr.find_all('td', class_=re.compile(r'^Past'))
            for i, td in enumerate(past_tds[:5]):  # 5走まで拡張
                data01 = td.find('div', class_='Data01')
                data02_a = td.find('div', class_='Data02').find('a') if td.find('div', class_='Data02') else None
                data05 = td.find('div', class_='Data05')

                if data01 and data02_a and data05:
                    past_race_id = self.extract_race_id(data02_a['href'])
                    if not past_race_id:
                        continue

                    c_match = re.search(
                        r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|'
                        r'門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)',
                        data01.text
                    )
                    course = c_match.group(1) if c_match else "不明"
                    date_match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', data01.text)
                    r_date_str = f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}" if date_match else ""
                    d_match = re.search(r'(\d{3,4})', data05.text)
                    distance = d_match.group(1) if d_match else "不明"

                    horse_valid_run_count[horse_name] += 1

                    if past_race_id not in past_races_dict:
                        past_races_dict[past_race_id] = {
                            'race_id': past_race_id, 'date_str': r_date_str,
                            'date': parse_date(r_date_str),
                            'course': course, 'distance': distance,
                            'horses': {}, 'is_direct_only': False
                        }
                    deep_dive_candidates.add(past_race_id)

                    # 4〜5走目は隠れ馬探索の対象外としてマーク
                    if horse_valid_run_count[horse_name] > HIDDEN_HORSE_MAX_RUNS:
                        past_races_dict[past_race_id]['is_direct_only'] = True

        # --- 各過去レースの全着順をスクレイピング ---
        for past_id in deep_dive_candidates:
            time.sleep(0.3)
            db_url = f"https://db.netkeiba.com/race/{past_id}/"
            try:
                res = requests.get(db_url, headers=self.headers)
                res.encoding = 'EUC-JP'
                db_soup = BeautifulSoup(res.text, 'html.parser')
                result_table = db_soup.find('table', class_='race_table_01')
                if not result_table:
                    continue

                winner_sec = None
                for tr in result_table.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) < 7:
                        continue

                    rank_str = tds[0].text.strip()
                    if not rank_str.isdigit():
                        continue

                    horse_cell = tds[3]
                    horse_link = horse_cell.find('a')
                    h_name = horse_link.text.strip() if horse_link else horse_cell.text.strip()

                    time_str = None
                    if len(tds) > 7:
                        time_str = tds[7].text.strip()
                    if not self.convert_time_to_sec(time_str):
                        for td_search in tds[4:]:
                            txt = td_search.text.strip()
                            if re.match(r'^\d{1,2}:\d{2}\.\d$', txt):
                                time_str = txt
                                break
                    if not time_str:
                        continue

                    sec = self.convert_time_to_sec(time_str)
                    if not sec:
                        continue

                    if winner_sec is None:
                        winner_sec = sec
                    time_behind = round(sec - winner_sec, 1)

                    if past_id in past_races_dict:
                        past_races_dict[past_id]['horses'][h_name] = time_behind

                # スクレイピングでコース情報を補完
                race_info = db_soup.find('diary_snap') or db_soup.find('div', class_='data_intro')
                if race_info:
                    info_text = race_info.get_text()
                    for kp in ("大井", "川崎", "船橋", "浦和", "門別", "盛岡", "水沢", "金沢", "笠松", "名古屋", "園田", "姫路", "高知", "佐賀"):
                        if kp in info_text and past_races_dict[past_id]['course'] == "不明":
                            past_races_dict[past_id]['course'] = kp
                            break

            except Exception:
                continue

        return race_title, target_course, target_distance, list(past_races_dict.values()), umaban_dict, is_banei


# ==========================================
# 4. NetworkXグラフベースの相対評価エンジン
#    (keiba_bot.pyの本番ロジックを完全移植)
# ==========================================
def build_comparison_graph(past_races, target_course, target_distance, umaban_dict, is_banei):
    """
    過去レースデータからNetworkXの有向グラフを構築し、
    全馬ペアの直接対決・隠れ馬経由の比較を行う。
    
    keiba_bot.pyからの移植ポイント:
    - 勝ち馬タイム +0.3秒補正
    - タイム差 ±1.5秒でキャップ
    - コスト関数による条件重み付け
    - 1ターンレースのフィルタリング
    - 大井内/外の混同防止
    """
    runners = list(umaban_dict.keys())
    current_names = set(runners)
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0

    G = nx.DiGraph()

    def _add_edge(h1_name, h2_name, raw_diff, r_date, r_place, r_dist_str, race_id, base_cost, is_direct):
        """グラフにエッジを追加（h1 < h2 で正規化）"""
        if h1_name > h2_name:
            h1_name, h2_name = h2_name, h1_name
            raw_diff = -raw_diff

        # keiba_bot.py: タイム差を±1.5秒でキャップ
        capped_diff = max(-1.5, min(1.5, raw_diff))

        r_dist_int = int(r_dist_str) if str(r_dist_str).isdigit() else 0
        dist_diff_val = abs(r_dist_int - cur_dist) if r_dist_int > 0 and cur_dist > 0 else 9999

        is_same_place = (r_place == target_course)
        is_exact_cond = is_same_place and (dist_diff_val == 0)

        badge = ""
        if is_exact_cond:
            badge = "[場×距]"
        elif is_same_place:
            badge = "[場]"
        elif dist_diff_val == 0:
            badge = "[距]"

        # 信頼度ペナルティ
        reliability_penalty = 0 if abs(capped_diff) <= 0.5 else (5 if abs(capped_diff) <= 1.0 else 15)
        edge_cost = base_cost + reliability_penalty + (0 if is_direct else 100)

        history_item = {
            "date": r_date, "date_str": str(r_date),
            "place": r_place, "dist": r_dist_str,
            "raw_diff": capped_diff, "badge": badge,
            "race_id": race_id,
        }

        if G.has_edge(h1_name, h2_name):
            ed = G[h1_name][h2_name]
            ed["diffs"].append(capped_diff)
            ed["history"].append(history_item)
            ed["rank_diff"] = sum(ed["diffs"]) / len(ed["diffs"])
            if edge_cost < ed["explore_cost"]:
                ed["explore_cost"] = edge_cost
        else:
            G.add_edge(
                h1_name, h2_name,
                diffs=[capped_diff],
                history=[history_item],
                rank_diff=capped_diff,
                explore_cost=edge_cost
            )

    # --- 過去レースからエッジを構築 ---
    for race in past_races:
        r_place = race['course']
        r_dist_str = race['distance']
        r_date = race['date']
        r_dist = int(r_dist_str) if str(r_dist_str).isdigit() else 0
        race_id = race['race_id']
        is_direct_only = race.get('is_direct_only', False)

        is_same_place = (r_place == target_course)
        is_exact_cond = is_same_place and (r_dist == cur_dist)
        is_same_layout = _is_same_track_layout(r_place, r_dist_str, target_distance)

        # コスト関数 (低い = 信頼度高い)
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

        h_list = list(race['horses'].items())
        if not h_list:
            continue

        min_time = min(t for _, t in h_list)

        # 今回出走馬同士の直接対決
        current_in_race = [(h, t) for h, t in h_list if h in current_names]
        if len(current_in_race) >= 2:
            for i in range(len(current_in_race)):
                for j in range(i + 1, len(current_in_race)):
                    h1, t1 = current_in_race[i]
                    h2, t2 = current_in_race[j]
                    # keiba_bot.py: 勝ち馬は+0.3秒補正
                    t1_adj = t1 + 0.3 if t1 == min_time else t1
                    t2_adj = t2 + 0.3 if t2 == min_time else t2
                    raw_diff = t1_adj - t2_adj
                    _add_edge(h1, h2, raw_diff, r_date, r_place, r_dist_str, race_id, base_cost, True)

        # 隠れ馬とのエッジ（直接対決専用レースはスキップ）
        if not is_direct_only:
            hidden_horses = [(h, t) for h, t in h_list if h not in current_names]
            if current_in_race and hidden_horses:
                for curr_name, curr_time in current_in_race:
                    for hid_name, hid_time in hidden_horses:
                        ct_adj = curr_time + 0.3 if curr_time == min_time else curr_time
                        ht_adj = hid_time + 0.3 if hid_time == min_time else hid_time
                        raw_diff = ct_adj - ht_adj
                        _add_edge(curr_name, hid_name, raw_diff, r_date, r_place, r_dist_str, race_id, base_cost, False)

                # 隠れ馬同士のエッジも追加（中継ノードとして機能するため）
                for i in range(len(hidden_horses)):
                    for j in range(i + 1, len(hidden_horses)):
                        h1_name, h1_time = hidden_horses[i]
                        h2_name, h2_time = hidden_horses[j]
                        ht1_adj = h1_time + 0.3 if h1_time == min_time else h1_time
                        ht2_adj = h2_time + 0.3 if h2_time == min_time else h2_time
                        raw_diff = ht1_adj - ht2_adj
                        _add_edge(h1_name, h2_name, raw_diff, r_date, r_place, r_dist_str, race_id, base_cost, False)

    return G


def compute_pairwise_results(G, runners, target_course, target_distance, is_banei):
    """
    グラフから全ペアの対戦結果を計算する。
    直接対決優先、隠れ馬経由はフォールバック。
    keiba_bot.pyの以下の仕様を完全反映:
    - 直接対決がある場合は隠れ馬経由をスキップ
    - 同条件データ優先、異条件データは0.4倍で補助参照
    - 1ターンコースフィルタリング
    - 大井内/外の混同防止
    - 隠れ馬経由は strict=0.7, loose=0.5 で割引
    """
    current_names = set(runners)
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0

    pair_net = {u: {v: [] for v in runners} for u in runners}

    for u in runners:
        for v in runners:
            if u == v:
                continue

            # --- 直接対決データの収集 ---
            direct_entries = []
            h_a, h_b = (u, v) if u < v else (v, u)

            if G.has_edge(h_a, h_b):
                for hi in G[h_a][h_b]["history"]:
                    # u基準でのタイム差を計算
                    diff = -hi["raw_diff"] if u == h_a else hi["raw_diff"]
                    direct_entries.append((diff, hi.get("date", datetime.min), hi.get("place", ""), hi.get("dist", "")))

            if direct_entries:
                same_cond = []
                other_cond = []
                for diff, dt, place, dist in direct_entries:
                    # 1ターンコースフィルタ
                    if _is_one_turn(target_course, cur_dist) and not _is_one_turn(place, dist):
                        continue

                    dist_int = int(dist) if str(dist).isdigit() else 0
                    if place == target_course and dist_int == cur_dist:
                        same_cond.append((diff, dt, place, dist))
                    else:
                        other_cond.append((diff, place, dist))

                if same_cond:
                    for d, dt, p, dist in same_cond:
                        pair_net[u][v].append((d, True, p, dist))

                    # 異条件で大勝ちしている場合は0.4倍で補助参照
                    if other_cond:
                        max_other_item = max(other_cond, key=lambda x: x[0])
                        max_other = max_other_item[0]
                        if max_other > 1.0:
                            pair_net[u][v].append((max_other * 0.4, False, max_other_item[1], max_other_item[2]))
                else:
                    for oc in other_cond:
                        pair_net[u][v].append((oc[0], False, oc[1], oc[2]))

                # 直接対決データがあれば隠れ馬経由はスキップ
                if pair_net[u][v]:
                    continue

            # --- 隠れ馬経由の比較 ---
            hidden_nodes = [n for n in G.nodes() if n not in current_names]
            for h in hidden_nodes:
                u_h_hist = []
                h_a_uh = (u, h) if u < h else (h, u)
                if G.has_edge(h_a_uh[0], h_a_uh[1]):
                    for hi in G[h_a_uh[0]][h_a_uh[1]]["history"]:
                        diff = -hi["raw_diff"] if u == h_a_uh[0] else hi["raw_diff"]
                        u_h_hist.append((diff, hi["place"], hi["dist"]))

                h_v_hist = []
                h_a_hv = (h, v) if h < v else (v, h)
                if G.has_edge(h_a_hv[0], h_a_hv[1]):
                    for hi in G[h_a_hv[0]][h_a_hv[1]]["history"]:
                        diff = -hi["raw_diff"] if h == h_a_hv[0] else hi["raw_diff"]
                        h_v_hist.append((diff, hi["place"], hi["dist"]))

                strict_diffs = []
                loose_diffs = []
                for diff_uh, p_uh, d_uh in u_h_hist:
                    for diff_hv, p_hv, d_hv in h_v_hist:
                        # 大井内/外の混同防止
                        if p_uh == "大井" and p_hv == "大井":
                            if (_is_ooi_inner(d_uh) and _is_ooi_outer(d_hv)) or \
                               (_is_ooi_outer(d_uh) and _is_ooi_inner(d_hv)):
                                continue

                        # 1ターンコースフィルタ
                        if _is_one_turn(target_course, cur_dist):
                            if not _is_one_turn(p_uh, d_uh) or not _is_one_turn(p_hv, d_hv):
                                continue

                        if p_uh == p_hv and _is_same_track_layout(p_uh, d_uh, d_hv):
                            if p_uh == target_course and str(d_uh) == str(cur_dist):
                                strict_diffs.append((diff_uh + diff_hv, p_uh, d_uh))
                            else:
                                loose_diffs.append((diff_uh + diff_hv, p_uh, d_uh))
                        else:
                            loose_diffs.append((diff_uh + diff_hv, p_uh, d_uh))

                if strict_diffs:
                    raw_hidden = sum(x[0] for x in strict_diffs) / len(strict_diffs)
                    discounted = raw_hidden * 0.7
                    pair_net[u][v].append((discounted, True, strict_diffs[0][1], strict_diffs[0][2]))
                elif loose_diffs:
                    raw_hidden = sum(x[0] for x in loose_diffs) / len(loose_diffs)
                    discounted = raw_hidden * 0.5
                    pair_net[u][v].append((discounted, False, loose_diffs[0][1], loose_diffs[0][2]))

    return pair_net


def compute_matchup_matrix(pair_net, runners, G, target_course, target_distance):
    """
    pair_netから勝敗記号 (>>, >, =, <, <<) の対戦マトリクスを構築。
    大井外→内の赦免ルール適用。
    """
    current_names = set(runners)
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0

    matchup_matrix = {u: {} for u in runners}

    for u in runners:
        for v in runners:
            if u == v or not pair_net[u][v]:
                continue

            best_is_strict = any(is_strict for _, is_strict, _, _ in pair_net[u][v])
            target_diffs = [diff for diff, is_strict, _, _ in pair_net[u][v] if is_strict == best_is_strict]

            # 大井外→内の赦免ルール
            is_forgiven = False
            if target_course == "大井" and _is_ooi_outer(cur_dist):
                for diff, is_strict, p_place, p_dist in pair_net[u][v]:
                    if p_place == "大井" and _is_ooi_inner(p_dist) and diff < 0:
                        is_forgiven = True

            if best_is_strict:
                draw_th, strong_th = 0.5, 1.0
            else:
                draw_th, strong_th = 0.7, 1.2

            wins = sum(1 for d in target_diffs if d >= draw_th)
            losses = sum(1 for d in target_diffs if d <= -draw_th)
            avg_diff = sum(target_diffs) / len(target_diffs) if target_diffs else 0

            if is_forgiven:
                matchup_matrix[u][v] = "="
            elif wins == len(target_diffs) and wins > 0:
                matchup_matrix[u][v] = ">>" if avg_diff >= strong_th else ">"
            elif losses == len(target_diffs) and losses > 0:
                matchup_matrix[u][v] = "<<" if avg_diff <= -strong_th else "<"
            elif wins > losses:
                matchup_matrix[u][v] = ">"
            elif losses > wins:
                matchup_matrix[u][v] = "<"
            else:
                matchup_matrix[u][v] = "="

    return matchup_matrix


# ==========================================
# 5. ティア判定（keiba_bot.pyの完全版）
#    カバレッジ閾値 + ポイントベース + 矛盾解決
# ==========================================
def evaluate_and_rank(pair_net, matchup_matrix, G, umaban_dict, is_banei):
    """
    keiba_bot.pyの評価アルゴリズムを完全反映:
    1. 比較可能馬の特定（カバレッジ閾値25%）
    2. 重み付きポイント集計（直接対決2倍）
    3. トップからの相対差分でS/A/B/C割り当て
    4. 直接対決結果との整合性補正（最大2回イテレーション）
    """
    runners = list(umaban_dict.keys())
    current_names = set(runners)
    total_opponents = len(runners) - 1

    # --- 比較可能馬の特定 ---
    comparable_horses = set()
    for u in runners:
        for v in runners:
            if u != v and pair_net[u][v]:
                comparable_horses.add(u)
                comparable_horses.add(v)

    all_tiers = {}
    for u in runners:
        if u not in comparable_horses:
            all_tiers[u] = None

    # カバレッジ閾値チェック
    COVERAGE_THRESHOLD = 0.25
    for u in list(comparable_horses):
        compared_count = sum(1 for v in runners if u != v and len(pair_net[u][v]) > 0)
        coverage = compared_count / total_opponents if total_opponents > 0 else 0
        if coverage < COVERAGE_THRESHOLD:
            has_strong_win = any(matchup_matrix[u].get(v) == ">>" for v in runners)
            if not has_strong_win:
                all_tiers[u] = None
                comparable_horses.discard(u)

    pool = list(comparable_horses)

    if pool:
        # --- ポイント集計 ---
        horse_points = {}
        for u in pool:
            pts = 0.0
            count = 0
            for v in pool:
                if u == v:
                    continue
                rel = matchup_matrix[u].get(v)
                if rel:
                    # 直接対決かどうかを判定
                    h_a, h_b = (u, v) if u < v else (v, u)
                    is_direct = G.has_edge(h_a, h_b)
                    weight = 2.0 if is_direct else 1.0

                    count += weight
                    if rel == ">>":   pts += 3.0 * weight
                    elif rel == ">":  pts += 1.5 * weight
                    elif rel == "=":  pts += 0.0 * weight   # 引き分けはゼロ（旧版の+1.0が全馬S化の元凶）
                    elif rel == "<":  pts -= 1.5 * weight   # 負けは勝ちと対称に
                    elif rel == "<<": pts -= 3.0 * weight   # 圧敗も勝ちと対称に

            avg_pts = pts / count if count > 0 else 0
            horse_points[u] = avg_pts

        # --- 勝率・圧勝率を加味した総合スコアでティア割り当て ---
        # ポイント平均だけでなく、勝率と圧勝(>>)回数も反映する
        horse_stats = {}
        for u in pool:
            wins = 0
            losses = 0
            dominations = 0  # >> の回数
            total = 0
            for v in pool:
                if u == v:
                    continue
                rel = matchup_matrix[u].get(v)
                if not rel:
                    continue
                total += 1
                if rel in (">", ">>"):
                    wins += 1
                if rel == ">>":
                    dominations += 1
                if rel in ("<", "<<"):
                    losses += 1

            win_rate = wins / total if total > 0 else 0.0
            loss_rate = losses / total if total > 0 else 0.0
            dom_rate = dominations / total if total > 0 else 0.0

            # 総合スコア = ポイント平均(50%) + 勝率ベース(30%) + 圧勝ボーナス(20%)
            pts_component = horse_points.get(u, 0)
            winrate_component = (win_rate - loss_rate) * 3.0  # -3.0 ~ +3.0 のレンジ
            dom_component = dom_rate * 3.0  # 0.0 ~ 3.0 のレンジ

            combined = pts_component * 0.5 + winrate_component * 0.3 + dom_component * 0.2

            horse_stats[u] = {
                "combined": combined,
                "wins": wins, "losses": losses, "dominations": dominations,
                "total": total, "win_rate": win_rate, "dom_rate": dom_rate
            }

        ranked_pool = sorted(horse_stats.items(), key=lambda x: x[1]["combined"], reverse=True)
        top_score = ranked_pool[0][1]["combined"] if ranked_pool else 0
        bottom_score = ranked_pool[-1][1]["combined"] if ranked_pool else 0
        spread = top_score - bottom_score

        if spread < 0.3:
            for h, stats in ranked_pool:
                all_tiers[h] = "B"
        else:
            step = spread / 4.0
            for h, stats in ranked_pool:
                diff_from_top = top_score - stats["combined"]
                if diff_from_top <= step:
                    all_tiers[h] = "S"
                elif diff_from_top <= step * 2:
                    all_tiers[h] = "A"
                elif diff_from_top <= step * 3:
                    all_tiers[h] = "B"
                else:
                    all_tiers[h] = "C"

        # --- 圧勝(>>)が3回以上ある馬のティア引き上げ補正 ---
        # 弱い相手ばかりでも圧勝3回以上は実力の証拠
        for h in pool:
            stats = horse_stats.get(h, {})
            doms = stats.get("dominations", 0)
            current_tier = all_tiers.get(h)
            if doms >= 3 and current_tier in ("B", "C"):
                all_tiers[h] = "A"  # 最低でもAまで引き上げ
            elif doms >= 2 and current_tier == "C":
                all_tiers[h] = "B"  # 圧勝2回ならBまで引き上げ

        # --- 直接対決結果との整合性補正 ---
        # 勝敗グラフのトポロジカル深度からティアを強制する。
        # 三すくみ（循環）は検出してスキップし、同ランクを許容する。

        tier_list = ["S", "A", "B", "C"]
        rank_order = {"S": 4, "A": 3, "B": 2, "C": 1}

        # 1. 非循環の勝敗ペアを収集
        win_edges = {}  # winner -> [losers]
        for u in pool:
            win_edges[u] = []
            for v in pool:
                if u == v:
                    continue
                rel = matchup_matrix[u].get(v)
                if rel in (">", ">>"):
                    rev = matchup_matrix[v].get(u)
                    if rev in (">", ">>"):
                        continue  # 三すくみはスキップ
                    win_edges[u].append(v)

        # 2. 各馬の「最長敗北チェーン深度」を計算
        depth_cache = {}
        def get_win_depth(horse, visited=None):
            if horse in depth_cache:
                return depth_cache[horse]
            if visited is None:
                visited = set()
            if horse in visited:
                return 0
            visited.add(horse)
            if not win_edges.get(horse):
                depth_cache[horse] = 0
                return 0
            max_d = 0
            for loser in win_edges[horse]:
                d = get_win_depth(loser, visited.copy()) + 1
                if d > max_d:
                    max_d = d
            depth_cache[horse] = max_d
            return max_d

        for h in pool:
            get_win_depth(h)

        # 3. 深度に基づいてティアを補正（底突き救済 + 上位固定）
        #    深度3以上=S, 深度2=A, 深度1=B, 深度0=C
        #    5段以上のチェーンは下の方が全てCに圧縮される
        if depth_cache:
            max_depth = max(depth_cache.values())

            if max_depth > 0:
                depth_to_tier = {0: "C"}
                if max_depth >= 1:
                    depth_to_tier[1] = "B"
                if max_depth >= 2:
                    depth_to_tier[2] = "A"

                for h in pool:
                    depth = depth_cache.get(h, 0)
                    if depth >= 3:
                        forced_tier = "S"
                    else:
                        forced_tier = depth_to_tier.get(depth, "C")

                    current_rank = rank_order.get(all_tiers[h], 0)
                    forced_rank = rank_order.get(forced_tier, 0)

                    # 深度ベースで上方修正（底突き救済）のみ適用
                    if forced_rank > current_rank:
                        all_tiers[h] = forced_tier

                # 4. 最終検証: 勝者が敗者と同ランク以下なら敗者を押し下げる
                for _ in range(len(pool) * 2):
                    changed = False
                    for winner in pool:
                        for loser in win_edges.get(winner, []):
                            w_rank = rank_order.get(all_tiers[winner], 0)
                            l_rank = rank_order.get(all_tiers[loser], 0)
                            if w_rank <= l_rank:
                                l_idx = tier_list.index(all_tiers[loser])
                                if l_idx < len(tier_list) - 1:
                                    all_tiers[loser] = tier_list[l_idx + 1]
                                    changed = True
                    if not changed:
                        break

    # --- ランキングリスト構築 ---
    tier_map = {}
    ranked = []
    unranked = []

    for u in runners:
        tier = all_tiers.get(u)
        if tier is None:
            unranked.append(u)
        else:
            tier_map[u] = tier
            # スコアはティアの数値化
            score_val = {"S": 4, "A": 3, "B": 2, "C": 1}.get(tier, 0)
            ranked.append((u, score_val))

    ranked.sort(key=lambda x: x[1], reverse=True)

    return tier_map, ranked, unranked


# ==========================================
# 6. HTML出力（対戦詳細付き）
# ==========================================
def build_html_output(tier_map, ranked, unranked, umaban_dict, pair_net, matchup_matrix, G, target_course, target_distance):
    """プロ仕様HTMLレンダリング"""
    html_parts = ["<div style='font-family: sans-serif; font-size:14px; color:#333;'>"]
    tier_colors = {"S": "#e74c3c", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    runners = list(umaban_dict.keys())
    current_names = set(runners)
    cur_dist = int(target_distance) if str(target_distance).isdigit() else 0

    def _diff_symbol_and_color(adv, is_same_cond=True):
        aa = abs(adv)
        if is_same_cond:
            draw_limit, strong_limit = 0.5, 1.0
        else:
            draw_limit, strong_limit = 0.7, 1.2

        if aa <= draw_limit:
            return "＝", "#888"
        elif aa <= strong_limit:
            return ("＞" if adv > 0 else "＜"), ("#27ae60" if adv > 0 else "#e74c3c")
        else:
            return ("≫" if adv > 0 else "≪"), ("#27ae60" if adv > 0 else "#e74c3c")

    def _render_horse(u):
        uma = umaban_dict.get(u, "?")
        parts = []
        parts.append(f"<div style='margin-bottom: 15px; border-left: 4px solid {tier_colors.get(tier_map.get(u, 'C'), '#3498db')}; padding-left: 10px;'>")
        parts.append(f"  <strong style='font-size:1.1em;'>[{uma}] {u}</strong>")

        # --- 直接対決の表示（レース別グループ化）---
        race_groups = {}
        for v in runners:
            if u == v:
                continue
            h_a, h_b = (u, v) if u < v else (v, u)
            if G.has_edge(h_a, h_b):
                for hi in G[h_a][h_b]["history"]:
                    adv = -hi['raw_diff'] if u == h_a else hi['raw_diff']
                    r_key = (hi.get('date_str', ''), hi['place'], hi['dist'], hi.get('race_id', ''))
                    if r_key not in race_groups:
                        race_groups[r_key] = []
                    race_groups[r_key].append((v, adv))

        # 戦績サマリー
        total_wins = sum(1 for opps in race_groups.values() for _, a in opps if a >= 0.5)
        total_draws = sum(1 for opps in race_groups.values() for _, a in opps if abs(a) < 0.5)
        total_losses = sum(1 for opps in race_groups.values() for _, a in opps if a <= -0.5)
        if total_wins + total_draws + total_losses > 0:
            sp = []
            if total_wins:
                sp.append(f"<span style='color:#27ae60; font-weight:bold;'>{total_wins}勝</span>")
            if total_draws:
                sp.append(f"<span style='color:#888;'>{total_draws}分</span>")
            if total_losses:
                sp.append(f"<span style='color:#e74c3c; font-weight:bold;'>{total_losses}敗</span>")
            parts.append(f"<div style='margin-left:10px; font-size:0.85em;'>直接対決: {' '.join(sp)}</div>")

        # レース別の詳細
        def _race_sort_key(item):
            (r_date, r_place, r_dist, r_id), _ = item
            is_same = (r_place == target_course and str(r_dist) == str(target_distance))
            return (1 if is_same else 0, r_date)

        for (r_date, r_place, r_dist, r_id), opps in sorted(race_groups.items(), key=_race_sort_key, reverse=True):
            if _is_one_turn(target_course, target_distance) and not _is_one_turn(r_place, r_dist):
                continue
            opps = [(v, a) for v, a in opps if abs(a) < 2.0]
            if not opps:
                continue

            is_match = (r_place == target_course and str(r_dist) == str(target_distance))
            style = "background:#fff9c4; border-left:3px solid #fbc02d; padding-left:5px;" if is_match else ""
            badge = " <span style='color:#fbc02d; font-weight:bold;'>[同条件]</span>" if is_match else ""

            parts.append(f"<div style='margin-left:10px; font-size:0.85em; {style}'>🔍 {r_date} {r_place}{r_dist}{badge}</div>")

            for v, adv in sorted(opps, key=lambda x: x[1], reverse=True):
                v_uma = umaban_dict.get(v, "?")
                sym, color = _diff_symbol_and_color(adv, is_same_cond=is_match)
                parts.append(f"<div style='margin-left:20px; font-size:0.85em;'>└ 本馬 <span style='color:{color}; font-weight:bold;'>{sym}</span> [{v_uma}]{v}({adv:+.1f})</div>")

        # --- 隠れ馬経由の比較 ---
        direct_opps = set()
        for v in runners:
            if u == v:
                continue
            h_a, h_b = (u, v) if u < v else (v, u)
            if G.has_edge(h_a, h_b):
                direct_opps.add(v)

        hidden_comparisons = []
        hidden_nodes = [n for n in G.nodes() if n not in current_names]

        for v in runners:
            if u == v or v in direct_opps:
                continue

            candidates = []
            for h in hidden_nodes:
                h_a_uh = (u, h) if u < h else (h, u)
                h_a_hv = (h, v) if h < v else (v, h)
                u_h_hist = G[h_a_uh[0]][h_a_uh[1]]["history"] if G.has_edge(h_a_uh[0], h_a_uh[1]) else []
                h_v_hist = G[h_a_hv[0]][h_a_hv[1]]["history"] if G.has_edge(h_a_hv[0], h_a_hv[1]) else []

                if not u_h_hist or not h_v_hist:
                    continue

                valid_pairs = []
                for hi_uh in u_h_hist:
                    for hi_hv in h_v_hist:
                        p_uh, d_uh = hi_uh["place"], hi_uh["dist"]
                        p_hv, d_hv = hi_hv["place"], hi_hv["dist"]

                        if p_uh == "大井" and p_hv == "大井":
                            if (_is_ooi_inner(d_uh) and _is_ooi_outer(d_hv)) or \
                               (_is_ooi_outer(d_uh) and _is_ooi_inner(d_hv)):
                                continue
                        if _is_one_turn(target_course, cur_dist):
                            if not _is_one_turn(p_uh, d_uh) or not _is_one_turn(p_hv, d_hv):
                                continue

                        d_uh_val = -hi_uh["raw_diff"] if u == h_a_uh[0] else hi_uh["raw_diff"]
                        d_hv_val = -hi_hv["raw_diff"] if h == h_a_hv[0] else hi_hv["raw_diff"]
                        est_diff = d_uh_val + d_hv_val

                        is_strict = (p_uh == p_hv and _is_same_track_layout(p_uh, d_uh, d_hv)
                                     and p_uh == target_course and str(d_uh) == str(cur_dist))

                        valid_pairs.append({
                            "est": est_diff, "is_strict": is_strict,
                            "uh_place": p_uh, "uh_dist": d_uh,
                            "hv_place": p_hv, "hv_dist": d_hv
                        })

                if not valid_pairs:
                    continue

                strict_pairs = [vp for vp in valid_pairs if vp["is_strict"]]
                target_pairs = strict_pairs if strict_pairs else valid_pairs
                discount = 0.7 if strict_pairs else 0.5

                raw_est = sum(vp["est"] for vp in target_pairs) / len(target_pairs)
                avg_est = raw_est * discount

                if abs(avg_est) < 2.0:
                    best = target_pairs[0]
                    uh_label = f"{best['uh_place'][0]}{best['uh_dist']}"
                    hv_label = f"{best['hv_place'][0]}{best['hv_dist']}"
                    place_dist = uh_label if uh_label == hv_label else f"{uh_label}/{hv_label}"
                    candidates.append((h, avg_est, bool(strict_pairs), place_dist))

            if candidates:
                candidates.sort(key=lambda c: (c[2], c[1]), reverse=True)
                h, est, is_strict, place_dist = candidates[0]
                hidden_comparisons.append((v, h, est, is_strict, place_dist))

        if hidden_comparisons:
            hidden_comparisons.sort(key=lambda x: (x[2], x[3]), reverse=True)
            parts.append(f"<div style='margin-left:10px; margin-top:6px; font-size:0.85em; font-weight:bold; color:#8e44ad;'>🔗 隠れ馬経由の比較</div>")
            for v, h_name, est, is_strict, place_dist in hidden_comparisons:
                sym, color = _diff_symbol_and_color(est, is_same_cond=is_strict)
                v_uma = umaban_dict.get(v, "?")
                parts.append(
                    f"<div style='margin-left:20px; font-size:0.85em;'>"
                    f"<span style='color:#9b59b6;'>[{h_name}]</span> "
                    f"本馬 <span style='color:{color}; font-weight:bold;'>{sym}</span> [{v_uma}]{v}({est:+.1f})"
                    f" <span style='color:#888;'>※{place_dist}</span></div>"
                )

        parts.append("</div>")
        return "\n".join(parts)

    # --- ティア別に表示 ---
    for tier in ["S", "UNRANKED", "A", "B", "C"]:
        if tier == "UNRANKED":
            if unranked:
                html_parts.append(
                    "<h3 style='background-color:#95a5a6; color:white; padding:8px; border-radius:4px;'>"
                    "❗ 測定不能（別路線・データ不足）</h3>"
                )
                for u in unranked:
                    uma = umaban_dict.get(u, '?')
                    html_parts.append(
                        f"<div style='margin-bottom: 10px; border-left: 4px solid #95a5a6; padding-left: 10px;'>"
                        f"  <strong style='font-size:1.1em;'>[{uma}] {u}</strong>"
                        f"  <div style='margin-left:10px; font-size:0.82em; color:#999;'>直近走で他出走馬との比較データ不足</div>"
                        f"</div>"
                    )
            continue

        horses = [u for u, s in ranked if tier_map.get(u) == tier]
        if not horses:
            continue

        html_parts.append(
            f"<h3 style='background-color:{tier_colors[tier]}; color:white; padding:8px; border-radius:4px;'>"
            f"🏆 {tier}ランク</h3>"
        )
        for u in horses:
            html_parts.append(_render_horse(u))

    html_parts.append("</div>")
    return "\n".join(html_parts)


# ==========================================
# 7. 一括HTML出力（タブ付き）
# ==========================================
def wrap_combined_html(results_list):
    tabs, contents = "", ""
    for i, (r_num, r_title, content) in enumerate(results_list):
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab-btn {active}" onclick="openTab(event, \'race_{r_num}\')">{r_num}R</button>\n'
        contents += f'<div id="race_{r_num}" class="tab-content {active}"><h2 class="race-title">📊 {r_title}</h2>{content}</div>'

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
    body {{ font-family: "Hiragino Kaku Gothic ProN", "Meiryo", sans-serif; background: #f7f6f2; padding: 20px; }}
    .container {{ background: #fff; padding: 20px; border-radius: 8px; max-width: 900px; margin: auto; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    .tab-buttons {{ display: flex; gap: 5px; border-bottom: 2px solid #3498db; margin-bottom: 20px; flex-wrap: wrap; }}
    .tab-btn {{ padding: 10px 16px; border: none; background: #ecf0f1; cursor: pointer; font-weight: bold; border-radius: 4px 4px 0 0; }}
    .tab-btn.active {{ background: #3498db; color: white; }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
    .race-title {{ font-size: 1.1em; margin-bottom: 15px; color: #2c3e50; }}
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
# 8. メインパイプライン統合関数
# ==========================================
def analyze_race(scraper, race_id, water_mode=None):
    """
    1つのレースIDに対して相対評価の全パイプラインを実行。
    Returns: (race_title, html_output) or (race_title, error_string)
    """
    try:
        r_title, t_course, t_dist, past_races, uma_dict, is_banei = scraper.fetch_past_data(race_id, water_mode)

        if not uma_dict:
            return r_title, "データなし"

        # 1. グラフ構築
        G = build_comparison_graph(past_races, t_course, t_dist, uma_dict, is_banei)

        # 2. ペアワイズ比較
        runners = list(uma_dict.keys())
        pair_net = compute_pairwise_results(G, runners, t_course, t_dist, is_banei)

        # 3. 対戦マトリクス
        matchup_matrix = compute_matchup_matrix(pair_net, runners, G, t_course, t_dist)

        # 4. ティア判定
        tier_map, ranked, unranked = evaluate_and_rank(pair_net, matchup_matrix, G, uma_dict, is_banei)

        # 5. HTML出力
        html_out = build_html_output(tier_map, ranked, unranked, uma_dict, pair_net, matchup_matrix, G, t_course, t_dist)

        return r_title, html_out

    except Exception as e:
        return f"レースID: {race_id}", f"エラー: {str(e)}"


# ==========================================
# 9. Streamlit UI
# ==========================================
st.set_page_config(page_title="競馬AI 究極相対評価", page_icon="🏇", layout="wide")

st.title("🏇 競馬AI 究極相対評価 (完全チューニング版)")
st.caption(
    "keiba_bot.pyの本番ロジックを完全統合: "
    "NetworkXグラフ探索 / 勝ち馬+0.3秒補正 / タイム差±1.5秒キャップ / "
    "JRA全10場(阪神・京都・中山・新潟の内外回り対応) + 地方全場 + ばんえい対応 / "
    "1ターンフィルタ / 大井内外混同防止 / "
    "カバレッジ閾値25% / 直接対決2倍ウェイト / 穴馬救済スコアリング / 矛盾解決イテレーション"
)

url_input = st.text_input(
    "netkeibaのレースURL",
    placeholder="https://race.netkeiba.com/race/result.html?race_id=202405020111"
)
water_mode = st.selectbox("水分量フィルタ（ばんえい専用）", ["なし", "軽馬場（dry）", "重馬場（wet）"])

st.markdown("---")
cols = st.columns(12)
selected_races = []
for i in range(12):
    with cols[i]:
        if st.checkbox(f"{i+1}R", key=f"chk_{i+1}"):
            selected_races.append(i + 1)

submitted = st.button("🚀 分析を開始", type="primary")

if submitted and url_input:
    scraper = NetkeibaScraper()
    base_id = scraper.extract_race_id(url_input)
    if not base_id:
        st.error("URLからレースIDを抽出できませんでした。")
        st.stop()
    if not selected_races:
        selected_races = [int(base_id[-2:])]

    wmode = "dry" if "軽" in water_mode else "wet" if "重" in water_mode else None
    results = []

    progress = st.progress(0)
    status = st.empty()

    for idx, r in enumerate(selected_races):
        rid = f"{base_id[:10]}{r:02d}"
        status.info(f"🏇 {r}R データ解析中...")

        r_title, html_out = analyze_race(scraper, rid, wmode)
        results.append((r, r_title, html_out))

        progress.progress((idx + 1) / len(selected_races))

    status.empty()
    st.success("✅ 分析完了！")

    st.download_button(
        "📥 HTML一括ダウンロード",
        wrap_combined_html(results),
        file_name=f"究極評価_{base_id[:10]}.html",
        mime="text/html"
    )

    tabs = st.tabs([f"{r[0]}R" for r in results])
    for tab, (r_num, r_title, r_html) in zip(tabs, results):
        with tab:
            st.markdown(f"### {r_title}")
            st.markdown(r_html, unsafe_allow_html=True)
