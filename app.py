from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
import time
import statistics
import networkx as nx
import re

app = Flask(__name__)

# ==========================================
# 点数配分
# ==========================================
RANK_SCORES = {"S+": 35, "S": 30, "A": 20, "B": 10, "C": 7, "!": 27}

# ==========================================
# 1. Netkeiba ディープスクレイパー
# ==========================================
class NetkeibaScraper:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0'}

    def extract_race_id(self, url):
        match = re.search(r'\d{12}', url)
        return match.group(0) if match else None

    def convert_time_to_sec(self, time_str):
        try:
            if ':' in time_str:
                m, s = time_str.split(':')
                return int(m) * 60 + float(s)
            else:
                return float(time_str)
        except (ValueError, TypeError, AttributeError):
            return None

    def _is_nar_race(self, race_id):
        try:
            return int(race_id[4:6]) > 10
        except (ValueError, IndexError):
            return False

    def _fetch_water_content(self, race_id):
        try:
            db_url = f"https://db.netkeiba.com/race/{race_id}/"
            res = requests.get(db_url, headers=self.headers)
            res.encoding = 'EUC-JP'
            soup = BeautifulSoup(res.text, 'html.parser')
            data_intro = soup.find(class_='data_intro')
            if data_intro:
                m = re.search(r'水分量\s*[:：]\s*([\d.]+)', data_intro.text)
                if m:
                    return float(m.group(1))
        except (requests.RequestException, AttributeError, ValueError):
            pass
        return None

    def fetch_past5_data(self, race_id, water_mode=None):
        is_nar = self._is_nar_race(race_id)
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

        target_course, target_track, target_distance = "不明", "芝", "不明"
        if race_data02:
            c_match = re.search(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)', race_data02.text)
            if c_match: target_course = c_match.group(1)
        if is_banei:
            target_track = "ばんえい"
            if race_data01:
                d_match = re.search(r'(\d{3,4})', race_data01.text)
                if d_match: target_distance = d_match.group(1)
        elif race_data01:
            t_match = re.search(r'(芝|ダ|障)[^\d]*(\d{4})', race_data01.text)
            if t_match:
                t_val = t_match.group(1)
                target_track = "ダート" if t_val == "ダ" else "障害" if t_val == "障" else "芝"
                target_distance = t_match.group(2)

        past_races_dict = {}
        umaban_dict = {}
        deep_dive_candidates = {}

        for tr in soup.find_all('tr', class_='HorseList'):
            name_tag = tr.find(class_='Horse02')
            if not name_tag: continue
            horse_name = name_tag.find('a').text.strip()

            tds = tr.find_all('td')
            if len(tds) > 1:
                umaban_dict[horse_name] = tds[1].text.strip()

            past_tds = tr.find_all('td', class_=re.compile(r'^Past'))
            for i, td in enumerate(past_tds):
                data01 = td.find('div', class_='Data01')
                data02_a = td.find('div', class_='Data02').find('a') if td.find('div', class_='Data02') else None
                
                data03 = td.find('div', class_='Data03')
                my_past_umaban = ""
                if data03:
                    match_umaban = re.search(r'(\d+)番', data03.text)
                    if match_umaban:
                        my_past_umaban = match_umaban.group(1)

                if data01 and data02_a:
                    past_race_id = self.extract_race_id(data02_a['href'])
                    course_match = re.search(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)', data01.text)
                    course = course_match.group(1) if course_match else "不明"

                    date_match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', data01.text)
                    race_date = "不明"
                    if date_match:
                        yy = date_match.group(1)[2:]
                        mm = int(date_match.group(2))
                        race_date = f"{yy}/{mm}"

                    data05 = td.find('div', class_='Data05')
                    if data05:
                        if course == '帯広':
                            track_type = "ばんえい"
                            d_match = re.search(r'(\d{3,4})', data05.text)
                            distance = d_match.group(1) if d_match else "不明"
                        else:
                            track_match = re.search(r'(芝|ダ|障)[^\d]*(\d{4})', data05.text)
                            if track_match:
                                t_val = track_match.group(1)
                                track_type = "ダート" if t_val == "ダ" else "障害" if t_val == "障" else "芝"
                                distance = track_match.group(2)
                            else:
                                track_type, distance = "不明", "不明"
                    else:
                        track_type, distance = "不明", "不明"

                    num_span = data01.find('span', class_='Num')
                    if not num_span or not num_span.text.isdigit(): continue
                    my_rank = int(num_span.text)

                    data07 = td.find('div', class_='Data07')
                    my_time_diff = 0.0
                    ref_horse_name = None
                    if data07:
                        match = re.search(r'\(([-0-9.]+)\)', data07.text)
                        if match:
                            my_time_diff = float(match.group(1))
                        if data07.find('a'):
                            ref_horse_name = data07.find('a').text.strip()

                    if past_race_id not in past_races_dict:
                        past_races_dict[past_race_id] = {
                            'race_id': past_race_id, 'date': race_date, 'course': course,
                            'track_type': track_type, 'distance': distance,
                            'is_banei': (course == '帯広'), 'horses': {}, 'past_umaban': {}
                        }

                    if my_rank == 1 or my_time_diff < 0:
                        my_time_behind = 0.0
                        ref_time_behind = abs(my_time_diff)
                    elif my_rank == 2:
                        my_time_behind = abs(my_time_diff)
                        ref_time_behind = 0.0
                    else:
                        my_time_behind = abs(my_time_diff)
                        ref_time_behind = 0.0

                    past_races_dict[past_race_id]['horses'][horse_name] = my_time_behind
                    past_races_dict[past_race_id]['past_umaban'][horse_name] = my_past_umaban
                    
                    if ref_horse_name:
                        if my_rank <= 2 or my_time_diff < 0:
                            past_races_dict[past_race_id]['horses'][ref_horse_name] = ref_time_behind
                        elif ref_horse_name not in past_races_dict[past_race_id]['horses']:
                            past_races_dict[past_race_id]['horses'][ref_horse_name] = ref_time_behind

                    limit = 5 if is_banei else 3
                    if i < limit:
                        if past_race_id not in deep_dive_candidates or i < deep_dive_candidates[past_race_id]:
                            deep_dive_candidates[past_race_id] = i

        if is_banei and water_mode:
            filtered_out = set()
            for past_race_id in list(past_races_dict.keys()):
                time.sleep(0.3)
                wc = self._fetch_water_content(past_race_id)
                if wc is not None:
                    if water_mode == 'dry' and wc > 1.9:
                        filtered_out.add(past_race_id)
                    elif water_mode == 'wet' and wc <= 1.9:
                        filtered_out.add(past_race_id)
            for rid in filtered_out:
                del past_races_dict[rid]
                deep_dive_candidates.pop(rid, None)

        deep_dive_race_ids = set()
        for past_race_id in deep_dive_candidates:
            deep_dive_race_ids.add(past_race_id)

        for past_id in deep_dive_race_ids:
            time.sleep(0.5)
            is_nar_past = self._is_nar_race(past_id)

            db_url = f"https://db.netkeiba.com/race/{past_id}/"
            res = requests.get(db_url, headers=self.headers)
            res.encoding = 'EUC-JP'
            db_soup = BeautifulSoup(res.text, 'html.parser')

            result_table = db_soup.find('table', class_='race_table_01')

            if not result_table and is_nar_past:
                time.sleep(0.5)
                nar_url = f"https://nar.netkeiba.com/race/result.html?race_id={past_id}"
                res = requests.get(nar_url, headers=self.headers)
                res.encoding = 'EUC-JP'
                db_soup = BeautifulSoup(res.text, 'html.parser')
                result_table = db_soup.find('table', class_='RaceCommon_Table')
                if not result_table:
                    result_table = db_soup.find('table', class_='race_table_01')

            if not result_table: continue

            winner_sec = None
            for tr in result_table.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) < 7: continue

                rank_str = tds[0].text.strip()
                if not rank_str.isdigit(): continue
                
                past_umaban = tds[2].text.strip()

                horse_cell = tds[3]
                horse_link = horse_cell.find('a')
                hidden_horse_name = horse_link.text.strip() if horse_link else horse_cell.text.strip()

                time_str = None
                if len(tds) > 7:
                    time_str = tds[7].text.strip()
                    if not self.convert_time_to_sec(time_str):
                        time_str = None
                if time_str is None:
                    for td in tds[4:]:
                        txt = td.text.strip()
                        if re.match(r'^\d{1,2}:\d{2}\.\d$', txt):
                            time_str = txt
                            break
                if time_str is None: continue

                sec = self.convert_time_to_sec(time_str)
                if sec is None: continue

                if winner_sec is None:
                    winner_sec = sec

                time_behind = round(sec - winner_sec, 1)
                
                if past_id in past_races_dict:
                    past_races_dict[past_id]['horses'][hidden_horse_name] = time_behind
                    if 'past_umaban' not in past_races_dict[past_id]:
                        past_races_dict[past_id]['past_umaban'] = {}
                    
                    if not past_races_dict[past_id]['past_umaban'].get(hidden_horse_name):
                        past_races_dict[past_id]['past_umaban'][hidden_horse_name] = past_umaban

        # === 第2パス: 接戦ペア（1.0秒以内）の追加直接対決レースを深掘り ===
        close_th = 10.0 if is_banei else 1.0
        already_dived = set(deep_dive_race_ids)

        close_pairs = set()
        runners = list(umaban_dict.keys())
        for i in range(len(runners)):
            for j in range(i + 1, len(runners)):
                h1, h2 = runners[i], runners[j]
                shared_diffs = []
                for rid, rdata in past_races_dict.items():
                    if h1 in rdata['horses'] and h2 in rdata['horses']:
                        diff = abs(rdata['horses'][h1] - rdata['horses'][h2])
                        shared_diffs.append(diff)
                if shared_diffs:
                    avg_diff = sum(shared_diffs) / len(shared_diffs)
                    if avg_diff <= close_th:
                        close_pairs.add((h1, h2))

        extra_dive_ids = set()
        for h1, h2 in close_pairs:
            for rid, rdata in past_races_dict.items():
                if rid not in already_dived and h1 in rdata['horses'] and h2 in rdata['horses']:
                    extra_dive_ids.add(rid)

        for past_id in extra_dive_ids:
            time.sleep(0.5)
            is_nar_past = self._is_nar_race(past_id)

            db_url = f"https://db.netkeiba.com/race/{past_id}/"
            res = requests.get(db_url, headers=self.headers)
            res.encoding = 'EUC-JP'
            db_soup = BeautifulSoup(res.text, 'html.parser')

            result_table = db_soup.find('table', class_='race_table_01')

            if not result_table and is_nar_past:
                time.sleep(0.5)
                nar_url = f"https://nar.netkeiba.com/race/result.html?race_id={past_id}"
                res = requests.get(nar_url, headers=self.headers)
                res.encoding = 'EUC-JP'
                db_soup = BeautifulSoup(res.text, 'html.parser')
                result_table = db_soup.find('table', class_='RaceCommon_Table')
                if not result_table:
                    result_table = db_soup.find('table', class_='race_table_01')

            if not result_table: continue

            winner_sec = None
            for tr in result_table.find_all('tr'):
                tds = tr.find_all('td')
                if len(tds) < 7: continue

                rank_str = tds[0].text.strip()
                if not rank_str.isdigit(): continue

                past_umaban = tds[2].text.strip()

                horse_cell = tds[3]
                horse_link = horse_cell.find('a')
                hidden_horse_name = horse_link.text.strip() if horse_link else horse_cell.text.strip()

                time_str = None
                if len(tds) > 7:
                    time_str = tds[7].text.strip()
                    if not self.convert_time_to_sec(time_str):
                        time_str = None
                if time_str is None:
                    for td in tds[4:]:
                        txt = td.text.strip()
                        if re.match(r'^\d{1,2}:\d{2}\.\d$', txt):
                            time_str = txt
                            break
                if time_str is None: continue

                sec = self.convert_time_to_sec(time_str)
                if sec is None: continue

                if winner_sec is None:
                    winner_sec = sec

                time_behind = round(sec - winner_sec, 1)

                if past_id in past_races_dict:
                    past_races_dict[past_id]['horses'][hidden_horse_name] = time_behind
                    if 'past_umaban' not in past_races_dict[past_id]:
                        past_races_dict[past_id]['past_umaban'] = {}

                    if not past_races_dict[past_id]['past_umaban'].get(hidden_horse_name):
                        past_races_dict[past_id]['past_umaban'][hidden_horse_name] = past_umaban

        return race_title, target_course, target_track, target_distance, list(past_races_dict.values()), umaban_dict

# ==========================================
# 2. ネットワーク構築
# ==========================================
def is_distance_in_range(race_distance, target_distance):
    try:
        rd = int(race_distance)
        td = int(target_distance)
    except (ValueError, TypeError):
        return True
    if td <= 1600:
        return abs(rd - td) <= 400
    elif td <= 2400:
        return 1500 <= rd <= 2500
    else:
        return True

def build_measuring_stick_graph(past_races, target_course, target_track, target_distance, umaban_dict, is_course_only=False):
    G = nx.DiGraph()
    for race in past_races:
        if is_course_only and race['course'] != target_course: continue
        if not is_course_only and race['track_type'] != target_track: continue
        if not is_distance_in_range(race['distance'], target_distance): continue

        is_banei = race.get('is_banei', False)
        is_nar_race = race['course'] in ('門別', '盛岡', '水沢', '浦和', '船橋', '大井', '川崎', '金沢', '笠松', '名古屋', '園田', '姫路', '高知', '佐賀')

        if is_nar_race:
            try:
                dist_diff = abs(int(race['distance']) - int(target_distance))
            except (ValueError, TypeError):
                dist_diff = 9999
            if dist_diff == 0:
                base_cost = 1 if race['course'] == target_course else 2
            elif dist_diff <= 100:
                base_cost = 5 if race['course'] == target_course else 8
            elif dist_diff <= 200:
                base_cost = 10 if race['course'] == target_course else 15
            else:
                base_cost = 20 if race['course'] == target_course else 30
        else:
            # JRA: NAR同様に距離差で細分化（同距離同競馬場を最重視）
            try:
                dist_diff = abs(int(race['distance']) - int(target_distance))
            except (ValueError, TypeError):
                dist_diff = 9999
            if dist_diff == 0:
                base_cost = 1 if race['course'] == target_course else 2
            elif dist_diff <= 100:
                base_cost = 5 if race['course'] == target_course else 8
            elif dist_diff <= 200:
                base_cost = 10 if race['course'] == target_course else 15
            else:
                base_cost = 20 if race['course'] == target_course else 30

        cap_val = 30.0 if is_banei else 10.0

        if is_banei:
            filtered_horses = list(race['horses'].items())
        else:
            filtered_horses = [(h, t) for h, t in race['horses'].items() if (h in umaban_dict) or (t < 2.0)]

        for i in range(len(filtered_horses)):
            for j in range(i + 1, len(filtered_horses)):
                h1_name, h1_time = filtered_horses[i]
                h2_name, h2_time = filtered_horses[j]

                # 少なくとも1頭は今回出走馬であること（隠れ馬同士も同レース内なら許可＝物差し馬チェーン用）
                h1_is_current = h1_name in umaban_dict
                h2_is_current = h2_name in umaban_dict
                if not h1_is_current and not h2_is_current:
                    # 同レースに今回出走馬がいる場合のみ隠れ馬同士のエッジを許可（物差し馬チェーン用）
                    has_current_in_race = any(h in umaban_dict for h, _ in filtered_horses)
                    if not has_current_in_race:
                        continue

                if h1_name > h2_name:
                    h1_name, h1_time, h2_name, h2_time = h2_name, h2_time, h1_name, h1_time

                raw_diff = h1_time - h2_time

                # 着差2.0秒以上は外れ値として除外（ばんえいは除く）
                if not is_banei and abs(raw_diff) >= 2.0:
                    continue

                if raw_diff < 0:
                    capped_diff = max(-cap_val, raw_diff)
                else:
                    capped_diff = min(cap_val, raw_diff)

                is_direct = (h1_is_current and h2_is_current)
                worse_time = max(h1_time, h2_time)
                
                if is_banei:
                    reliability_penalty = 0
                elif worse_time <= 0.5:
                    reliability_penalty = 0
                elif worse_time <= 1.0:
                    reliability_penalty = 5
                else:
                    reliability_penalty = 15

                if is_direct:
                    edge_cost = base_cost + reliability_penalty
                else:
                    edge_cost = base_cost + 100 + reliability_penalty

                h1_past_umaban = race.get('past_umaban', {}).get(h1_name, "")
                h2_past_umaban = race.get('past_umaban', {}).get(h2_name, "")

                history_item = {
                    'date': race['date'],
                    'race_id': race['race_id'],
                    'course': race['course'],
                    'distance': race['distance'],
                    'raw_diff': raw_diff,
                    'diff': capped_diff,
                    'h1_name': h1_name,
                    'h2_name': h2_name,
                    'h1_past_umaban': h1_past_umaban,
                    'h2_past_umaban': h2_past_umaban,
                    'h1_time': h1_time,
                    'h2_time': h2_time
                }

                if G.has_edge(h1_name, h2_name):
                    current_cost = G[h1_name][h2_name]['explore_cost']
                    edge_data = G[h1_name][h2_name]

                    if is_direct:
                        edge_data['diffs'].append(capped_diff)
                        edge_data['history'].append(history_item)
                        edge_data['rank_diff'] = sum(edge_data['diffs']) / len(edge_data['diffs'])
                        if edge_cost < current_cost:
                            edge_data['explore_cost'] = edge_cost
                            edge_data['race_id'] = race['race_id']
                            edge_data['course'] = race['course']
                            edge_data['distance'] = race['distance']
                            edge_data['track_type'] = race['track_type']
                            edge_data['date'] = race['date']
                            edge_data['h1_time'] = h1_time
                            edge_data['h2_time'] = h2_time
                        best_base = min(edge_cost, current_cost)
                        if len(edge_data['diffs']) >= 2:
                            var = statistics.variance(edge_data['diffs'])
                            edge_data['explore_cost'] = best_base + var * 100
                        else:
                            edge_data['explore_cost'] = best_base
                    else:
                        if edge_cost < current_cost:
                            edge_data['diffs'] = [capped_diff]
                            edge_data['history'] = [history_item]
                            edge_data['rank_diff'] = capped_diff
                            edge_data['explore_cost'] = edge_cost
                            edge_data['race_id'] = race['race_id']
                            edge_data['course'] = race['course']
                            edge_data['distance'] = race['distance']
                            edge_data['track_type'] = race['track_type']
                            edge_data['date'] = race['date']
                            edge_data['h1_time'] = h1_time
                            edge_data['h2_time'] = h2_time
                        elif edge_cost == current_cost:
                            edge_data['diffs'].append(capped_diff)
                            edge_data['history'].append(history_item)
                            edge_data['rank_diff'] = sum(edge_data['diffs']) / len(edge_data['diffs'])
                            if len(edge_data['diffs']) >= 2:
                                var = statistics.variance(edge_data['diffs'])
                                edge_data['explore_cost'] = edge_cost + var * 100
                else:
                    G.add_edge(h1_name, h2_name, weight=1, diffs=[capped_diff], history=[history_item], rank_diff=capped_diff,
                               explore_cost=edge_cost,
                               race_id=race['race_id'], course=race['course'],
                               distance=race['distance'], track_type=race['track_type'],
                               date=race['date'], h1_time=h1_time, h2_time=h2_time)
    return G

# ==========================================
# 3. ランク階層生成 ＆ 出力処理
# ==========================================
def format_horse_name(horse, umaban_dict, race_id=None):
    if horse in umaban_dict:
        mark = f"<mark-selector race='{race_id}' horse='{horse}'></mark-selector>" if race_id else ""
        return f"{mark}[{umaban_dict[horse]}] {horse}"
    else:
        return f"[隠] {horse}"

def get_rank_tier(diff, is_banei=False):
    """絶対diff閾値によるランク判定（ストイック版）"""
    r_diff = round(diff, 1)
    if is_banei:
        if r_diff <= 5.0: return "S"
        elif r_diff <= 10.0: return "A"
        elif r_diff <= 18.0: return "B"
        else: return "C"
    else:
        if r_diff <= 0.3: return "S"
        elif r_diff <= 0.7: return "A"
        elif r_diff <= 1.2: return "B"
        else: return "C"

def assign_tiers_by_comparison(ranked_list, is_banei=False):
    if not ranked_list:
        return {}
    if len(ranked_list) == 1:
        return {ranked_list[0][0]: "S"}

    min_gap = 5.0 if is_banei else 0.3

    gaps = []
    for i in range(1, len(ranked_list)):
        gap = ranked_list[i][1] - ranked_list[i-1][1]
        gaps.append((i, gap))

    significant_gaps = [(i, g) for i, g in gaps if g >= min_gap]

    total_spread = ranked_list[-1][1] - ranked_list[0][1]
    if len(significant_gaps) < 2 and total_spread >= min_gap * 3:
        relaxed = min_gap * 0.5
        significant_gaps = [(i, g) for i, g in gaps if g >= relaxed]

    significant_gaps.sort(key=lambda x: x[1], reverse=True)
    boundaries = sorted([i for i, g in significant_gaps[:3]])

    tier_labels = ["S", "A", "B", "C"]
    result = {}
    tier_idx = 0
    for i, (horse, score) in enumerate(ranked_list):
        if tier_idx < len(boundaries) and i == boundaries[tier_idx]:
            tier_idx += 1
        result[horse] = tier_labels[min(tier_idx, 3)]

    return result

# 【追加】複数ルート（クロスチェック）を加味してタイムを平均化する処理
def calc_path_score(G, path, target_course=None, target_distance=None):
    """パス上のタイム差を合算。同距離同競馬場のデータは重み2倍で反映。"""
    score = 0.0
    for k in range(len(path) - 1):
        u, v = path[k], path[k+1]
        if G.has_edge(u, v):
            edge = G[u][v]
            # 同距離同競馬場データの重み付け
            if target_course and target_distance and 'history' in edge:
                same_diffs = [h['diff'] for h in edge['history']
                              if h['course'] == target_course and h['distance'] == target_distance]
                other_diffs = [h['diff'] for h in edge['history']
                               if h['course'] != target_course or h['distance'] != target_distance]
                if same_diffs:
                    # 同条件diffを2倍重みで平均
                    all_weighted = same_diffs * 2 + other_diffs
                    weighted_avg = sum(all_weighted) / len(all_weighted)
                    score -= weighted_avg
                else:
                    score -= edge['rank_diff']
            else:
                score -= edge['rank_diff']
        else:
            edge = G[v][u]
            if target_course and target_distance and 'history' in edge:
                same_diffs = [h['diff'] for h in edge['history']
                              if h['course'] == target_course and h['distance'] == target_distance]
                other_diffs = [h['diff'] for h in edge['history']
                               if h['course'] != target_course or h['distance'] != target_distance]
                if same_diffs:
                    all_weighted = same_diffs * 2 + other_diffs
                    weighted_avg = sum(all_weighted) / len(all_weighted)
                    score += weighted_avg
                else:
                    score += edge['rank_diff']
            else:
                score += edge['rank_diff']
    return score

# 【追加】複数ルートでの表現を可能にするUIビルダ
def build_ability_summary(G, runner_path, runner_G, umaban_dict, race_id=None, is_banei=False):
    parts = []
    for k in range(len(runner_path) - 1):
        u, v = runner_path[k], runner_path[k+1]
        edge_paths = runner_G[u][v].get('multiple_paths', [runner_G[u][v]['full_path']])
        
        hop_scores = []
        for p in edge_paths:
            p_oriented = p if p[0] == u else p[::-1]
            hop_scores.append(calc_path_score(G, p_oriented))
        avg_score = sum(hop_scores) / len(hop_scores)
        
        gap = abs(avg_score)
        if is_banei:
            if gap <= 3.0: sep = " ＝ "
            elif gap <= 7.0: sep = " ＞ "
            elif gap <= 13.0: sep = " ＞＞ "
            else: sep = " ＞＞＞ "
        else:
            if gap <= 0.2: sep = " ＝ "
            elif gap <= 0.5: sep = " ＞ "
            elif gap <= 0.9: sep = " ＞＞ "
            else: sep = " ＞＞＞ "
        
        if k == 0:
            parts.append(format_horse_name(u, umaban_dict, race_id))
        
        # 複数ルート経由なら注釈を入れる
        if len(edge_paths) > 1:
            parts.append(f"{sep}<span style='color:#e67e22; font-size:0.85em;'>[複数ルート加味]</span>{sep}{format_horse_name(v, umaban_dict, race_id)}")
        else:
            p = edge_paths[0]
            p_oriented = p if p[0] == u else p[::-1]
            middle_nodes = p_oriented[1:-1]
            for m in middle_nodes:
                parts.append(f"{sep}{format_horse_name(m, umaban_dict, race_id)}")
            parts.append(f"{sep}{format_horse_name(v, umaban_dict, race_id)}")
            
    return "".join(parts)

# 【追加】複数ルートの詳細をリストで表示するUIビルダ
def render_path_details(G, runner_path, runner_G, umaban_dict, target_course, target_distance, race_id=None, is_banei=False):
    details = []
    for k in range(len(runner_path) - 1):
        u, v = runner_path[k], runner_path[k+1]
        edge_paths = runner_G[u][v].get('multiple_paths', [runner_G[u][v]['full_path']])
        
        if len(edge_paths) == 1:
            p_oriented = edge_paths[0] if edge_paths[0][0] == u else edge_paths[0][::-1]
            details.extend(_render_single_path_details(G, p_oriented, umaban_dict, target_course, target_distance, race_id, is_banei))
        else:
            u_disp = format_horse_name(u, umaban_dict, race_id)
            v_disp = format_horse_name(v, umaban_dict, race_id)
            details.append(f"      <li style='list-style-type:none; margin-left:-20px; color:#c0392b; font-size:0.9em; margin-top:5px;'><strong>■ {u_disp} と {v_disp} の比較（複数ルート平均）</strong></li>")
            for idx, p in enumerate(edge_paths):
                p_oriented = p if p[0] == u else p[::-1]
                details.append(f"      <li style='list-style-type:none; margin-left:-5px; color:#2980b9; font-size:0.85em; margin-top:3px;'>【ルート{idx+1}】</li>")
                details.extend(_render_single_path_details(G, p_oriented, umaban_dict, target_course, target_distance, race_id, is_banei, indent=True))
                
    return details

def _render_single_path_details(G, path, umaban_dict, target_course, target_distance, race_id=None, is_banei=False, indent=False):
    close_th = 3.0 if is_banei else 0.2
    near_th = 7.0 if is_banei else 0.5
    far_th = 13.0 if is_banei else 0.9
    draw_th = 2.0 if is_banei else 0.1

    details = []
    li_style = " style='margin-left: 15px; font-size: 0.9em; color: #555; list-style-type: circle;'" if indent else ""

    for k in range(len(path) - 1):
        u, v = path[k], path[k+1]

        if G.has_edge(u, v):
            edge = G[u][v]
            h1, h2 = u, v
        else:
            edge = G[v][u]
            h1, h2 = v, u

        if edge['rank_diff'] < 0:
            winner, loser = h1, h2
            winner_time = edge['h1_time']
            loser_time = edge['h2_time']
            wins = sum(1 for h in edge['history'] if h['raw_diff'] < -draw_th)
            losses = sum(1 for h in edge['history'] if h['raw_diff'] > draw_th)
        else:
            winner, loser = h2, h1
            winner_time = edge['h2_time']
            loser_time = edge['h1_time']
            wins = sum(1 for h in edge['history'] if h['raw_diff'] > draw_th)
            losses = sum(1 for h in edge['history'] if h['raw_diff'] < -draw_th)

        num_matches = len(edge['history'])
        draws = num_matches - wins - losses

        record_str = f" <span style='color:#8e44ad; font-weight:bold;'>[{num_matches}戦：{wins}勝{losses}敗{draws}分]</span>" if num_matches > 1 else ""

        margin = abs(edge['rank_diff'])
        w_disp = format_horse_name(winner, umaban_dict, race_id)
        l_disp = format_horse_name(loser, umaban_dict, race_id)

        w_disp_time = f"{w_disp}(+{winner_time:.1f})" if winner_time > 0 else f"{w_disp}(±0.0)"
        l_disp_time = f"{l_disp}(+{loser_time:.1f})" if loser_time > 0 else f"{l_disp}(±0.0)"

        race_str = f"{edge['date']}の{edge['course']} {edge['track_type']}{edge['distance']}m"
        url = f"https://db.netkeiba.com/race/{edge['race_id']}"

        is_close_race = (margin <= close_th)
        is_same_course = (edge['course'] == target_course)
        is_same_dist = (edge['distance'] == target_distance)
        star_mark = " <span title='同条件での接戦です。レース内容を要確認！' style='font-size: 1em;'>⭐️</span>" if is_close_race and (is_same_course or is_same_dist) else ""

        match_badge = ""
        if is_same_course and is_same_dist:
            match_badge = " <span style='color:#e74c3c;font-weight:bold;'>[場×距]</span>"
        elif is_same_course:
            match_badge = " <span style='color:#e67e22;font-weight:bold;'>[場同]</span>"
        elif is_same_dist:
            match_badge = " <span style='color:#27ae60;font-weight:bold;'>[距同]</span>"
        else:
            match_badge = " <span style='color:#95a5a6;font-size:0.85em;'>[別条件]</span>"

        match_badge += star_mark

        link = f"<a href='{url}' target='_blank' class='race-link' style='color: #3498db; text-decoration: none; font-weight: bold;'>{race_str}</a>{match_badge}"

        if num_matches == 1:
            if margin <= close_th:
                details.append(f"      <li{li_style}>{link} で {w_disp_time} ＝ {l_disp_time}</li>")
            elif margin <= near_th:
                details.append(f"      <li{li_style}>{link} で <strong>{w_disp_time}</strong> ＞ {l_disp_time}</li>")
            elif margin <= far_th:
                details.append(f"      <li{li_style}>{link} で <strong>{w_disp_time}</strong> ＞＞ {l_disp_time}</li>")
            else:
                details.append(f"      <li{li_style}>{link} で <strong>{w_disp_time}</strong> ＞＞＞ {l_disp_time}</li>")
        else:
            if margin <= close_th:
                details.append(f"      <li{li_style}>{link} 等で {w_disp} ＝ {l_disp} {record_str}</li>")
            elif margin <= near_th:
                details.append(f"      <li{li_style}>{link} 等で <strong>{w_disp}</strong> ＞ {l_disp} {record_str}</li>")
            elif margin <= far_th:
                details.append(f"      <li{li_style}>{link} 等で <strong>{w_disp}</strong> ＞＞ {l_disp} {record_str}</li>")
            else:
                details.append(f"      <li{li_style}>{link} 等で <strong>{w_disp}</strong> ＞＞＞ {l_disp} {record_str}</li>")
    return details


def build_runner_graph(undirected_G, umaban_dict, is_banei=False):
    runner_G = nx.Graph()
    current_runners = [h for h in umaban_dict.keys() if h in undirected_G.nodes()]
    decisive_th = 10.0 if is_banei else 1.1  # これ以上の差は勝負付け済み

    for h in current_runners:
        runner_G.add_node(h)

    for i in range(len(current_runners)):
        for j in range(i + 1, len(current_runners)):
            u, v = current_runners[i], current_runners[j]

            if undirected_G.has_edge(u, v):
                edge_data = undirected_G[u][v]
                cost = edge_data.get('explore_cost', 10)
                margin = abs(edge_data.get('rank_diff', 0))

                if margin >= decisive_th:
                    # 勝負付け済み：直接対決のみで確定、物差し馬探索しない
                    runner_G.add_edge(u, v, explore_cost=cost, full_path=[u, v], multiple_paths=[[u, v]])
                else:
                    # 接戦（1.0秒以内）：直接対決を使いつつ、他にも直接対決データがあれば全て活用
                    # （複数レースのデータは既にグラフのdiffs[]に蓄積済み）
                    runner_G.add_edge(u, v, explore_cost=cost, full_path=[u, v], multiple_paths=[[u, v]])
            else:
                # 直接対決なし：物差し馬（隠れ馬）経由で最大3ルートまで探索
                try:
                    paths = list(nx.all_simple_paths(undirected_G, source=u, target=v, cutoff=3))
                    valid_paths = []
                    for path in paths:
                        # 間に別の出走馬を挟むループは除外
                        middle_nodes = path[1:-1]
                        if any(node in umaban_dict for node in middle_nodes):
                            continue
                        cost = sum(undirected_G[path[k]][path[k+1]]['explore_cost'] for k in range(len(path)-1))
                        valid_paths.append((path, cost))

                    if valid_paths:
                        valid_paths.sort(key=lambda x: x[1])
                        top_paths = [p[0] for p in valid_paths[:3]] # 上位3ルートを採用
                        best_cost = valid_paths[0][1]
                        runner_G.add_edge(u, v, explore_cost=best_cost, full_path=top_paths[0], multiple_paths=top_paths)
                except nx.NetworkXNoPath:
                    pass

    return runner_G

def _rank_component(G, runner_G, component, umaban_dict, target_course, target_distance, is_banei=False):
    current_runners = list(component)

    pair_scores = {}
    for u in current_runners:
        pair_scores[u] = {}
        for v in current_runners:
            if u == v:
                pair_scores[u][v] = 0.0
                continue
            try:
                runner_path = nx.shortest_path(runner_G, source=u, target=v, weight='explore_cost')
                total_score = 0.0
                for k in range(len(runner_path) - 1):
                    hop_u, hop_v = runner_path[k], runner_path[k+1]
                    edge_paths = runner_G[hop_u][hop_v].get('multiple_paths', [runner_G[hop_u][hop_v]['full_path']])
                    hop_scores = []
                    for p in edge_paths:
                        p_oriented = p if p[0] == hop_u else p[::-1]
                        hop_scores.append(calc_path_score(G, p_oriented, target_course, target_distance))
                    # 複数ルートのスコアを平均化する！（同距離同競馬場データは重み2倍で反映済み）
                    total_score += sum(hop_scores) / len(hop_scores)
                pair_scores[u][v] = total_score
            except nx.NetworkXNoPath:
                pair_scores[u][v] = float('inf')

    best_anchor = None
    best_neg_sum = -float('inf')
    
    for candidate in current_runners:
        scores = pair_scores[candidate]
        finite = [s for s in scores.values() if s != float('inf')]
        if not finite: continue
        neg_sum = sum(s for s in finite if s < 0)
        if neg_sum > best_neg_sum:
            best_neg_sum = neg_sum
            best_anchor = candidate
            
    fastest = best_anchor
    final_scores = pair_scores[fastest].copy()
    
    min_score = min(s for s in final_scores.values() if s != float('inf'))
    if min_score < -0.01:
        true_fastest = min([h for h in final_scores if final_scores[h] != float('inf')], key=final_scores.get)
        fastest = true_fastest
        final_scores = pair_scores[fastest].copy()
        
        min_score = min(s for s in final_scores.values() if s != float('inf'))
        if min_score < -0.01:
            for h in final_scores:
                if final_scores[h] != float('inf'):
                    final_scores[h] -= min_score

    ranked_list = sorted([(h, s) for h, s in final_scores.items() if s != float('inf')], key=lambda x: (x[1], 0 if x[0] == fastest else 1))

    # === ストイック合議制ティア判定 ===
    # 方式A: 絶対diff閾値によるランク判定
    abs_tiers = {}
    for h, s in ranked_list:
        abs_tiers[h] = get_rank_tier(max(0.0, s), is_banei=is_banei)

    # 方式B: ギャップベースのティア割り当て
    gap_tiers = assign_tiers_by_comparison(ranked_list, is_banei=is_banei)

    # 方式C: ペアワイズ勝敗判定（ストイック閾値）
    beat_th = 5.0 if is_banei else 0.3
    beats_set = {h: set() for h in current_runners}
    for u in current_runners:
        for v in current_runners:
            if u == v:
                continue
            s_uv = pair_scores[u].get(v, float('inf'))
            s_vu = pair_scores[v].get(u, float('inf'))
            if s_uv != float('inf') and s_vu != float('inf'):
                if s_uv < -beat_th:  # u は v より速い
                    beats_set[u].add(v)

    beaten_by = {h: set() for h in current_runners}
    for u in current_runners:
        for v in beats_set[u]:
            beaten_by[v].add(u)

    pw_tiers = {}
    remaining = set(current_runners)
    s_h = {h for h in remaining if not beaten_by[h].intersection(remaining)}
    for h in s_h:
        pw_tiers[h] = "S"
    remaining -= s_h
    a_h = {h for h in remaining if beaten_by[h].intersection(remaining).issubset(s_h)}
    for h in a_h:
        pw_tiers[h] = "A"
    remaining -= a_h
    b_h = {h for h in remaining if beaten_by[h].intersection(remaining | s_h | a_h).issubset(s_h | a_h)}
    for h in b_h:
        pw_tiers[h] = "B"
    remaining -= b_h
    for h in remaining:
        pw_tiers[h] = "C"

    # 3方式の最も厳しいランクを採用
    _tier_rank = {"S": 0, "A": 1, "B": 2, "C": 3}
    _rank_tier = {0: "S", 1: "A", 2: "B", 3: "C"}
    tier_map = {}
    for h, _ in ranked_list:
        t_abs = _tier_rank.get(abs_tiers.get(h, "C"), 3)
        t_gap = _tier_rank.get(gap_tiers.get(h, "C"), 3)
        t_pw  = _tier_rank.get(pw_tiers.get(h, "C"), 3)
        tier_map[h] = _rank_tier[max(t_abs, t_gap, t_pw)]

    # 同距離同競馬場で直接勝っている馬が下位ランクにならないよう補正
    for u in current_runners:
        for v in current_runners:
            if u == v:
                continue
            if G.has_edge(u, v) or G.has_edge(v, u):
                a, b = (u, v) if u < v else (v, u)
                if G.has_edge(a, b):
                    edge = G[a][b]
                    same_cond_diffs = [h['raw_diff'] for h in edge['history']
                                       if h['course'] == target_course and h['distance'] == target_distance]
                    if same_cond_diffs:
                        avg_sc = sum(same_cond_diffs) / len(same_cond_diffs)
                        # avg_sc < 0 means a is faster, > 0 means b is faster
                        winner, loser = (a, b) if avg_sc < 0 else (b, a)
                        th = 5.0 if is_banei else 0.5
                        if abs(avg_sc) >= th:
                            w_t = _tier_rank.get(tier_map.get(winner, "C"), 3)
                            l_t = _tier_rank.get(tier_map.get(loser, "C"), 3)
                            if w_t > l_t:
                                tier_map[winner] = tier_map[loser]

    return fastest, final_scores, ranked_list, tier_map

def analyze_all_horses_html(G, umaban_dict, target_course, target_distance, race_id=None, is_banei=False):
    undirected_G = G.to_undirected()
    runner_G = build_runner_graph(undirected_G, umaban_dict, is_banei=is_banei)

    components = list(nx.connected_components(runner_G))
    valid_components = sorted([c for c in components if len(c) >= 2], key=len, reverse=True)

    output = []
    all_ranked_scores = {}
    all_tier_map = {}
    all_ranked_horses = set()
    true_fastest = None

    diff_zero_th = 0.5 if is_banei else 0.05
    unit_label = "秒差換算"
    tier_colors = {"S": "#e74c3c", "!": "#9b59b6", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    tier_labels_map = {"S": "🏆 Sランク", "!": "❗ 測定不能", "A": "🏆 Aランク", "B": "🏆 Bランク", "C": "🏆 Cランク"}

    if not valid_components:
        output.append("<p>比較可能な対戦データがありませんでした。</p>")
    else:
        for comp_idx, component in enumerate(valid_components):
            fastest, final_scores, ranked_list, tier_map = _rank_component(
                G, runner_G, component, umaban_dict, target_course, target_distance, is_banei=is_banei
            )

            if comp_idx == 0:
                true_fastest = fastest

            all_ranked_scores.update(final_scores)
            all_tier_map.update(tier_map)
            all_ranked_horses.update(component)

            if comp_idx > 0:
                sub_names = [format_horse_name(h, umaban_dict, race_id) for h in component]
                output.append(f"<h3 style='color: white; background-color: #7f8c8d; padding: 6px 12px; border-radius: 5px; margin-top: 20px; margin-bottom: 8px; font-size: 0.85em;'>📊 別グループ（{', '.join(sub_names)}）</h3>")

            rank_groups = {"S": [], "A": [], "B": [], "C": []}
            for horse, diff in ranked_list:
                tier = tier_map.get(horse, "C")
                rank_groups[tier].append((horse, diff))

            output.append("<div class='ranking-list'>")

            for tier in ["S", "A", "B", "C"]:
                horses_in_tier = rank_groups[tier]
                if not horses_in_tier: continue

                color = tier_colors.get(tier, "#333")
                label = tier_labels_map.get(tier, f"🏆 {tier}ランク")
                output.append(f"<h3 style='color: white; background-color: {color}; padding: 6px 12px; border-radius: 5px; margin-top: 15px; margin-bottom: 8px; font-size: 0.9em;'>{label}</h3>")

                for horse, diff in horses_in_tier:
                    diff = max(0.0, diff)
                    diff_str = "±0.0" if diff < diff_zero_th else f"+{diff:.1f}"
                    horse_disp = format_horse_name(horse, umaban_dict, race_id)

                    output.append(f"<div class='horse-rank' style='margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px dashed #ccc;'>")
                    output.append(f"  <h4 class='rank-title' style='margin: 0 0 5px 0; font-size: 0.95em; color: #2c3e50;'>{horse_disp} <span class='time-diff' style='color: #e74c3c; font-size: 0.85em;'>[{diff_str}{unit_label}]</span></h4>")
                    output.append(f"  <div class='theory-box' style='background-color: #f8f9fa; border-left: 4px solid {color}; padding: 8px 12px; font-size: 0.85em;'>")

                    if horse == fastest:
                        output.append("    <p class='theory-text' style='margin:0;'>✨ <strong>この集団の最先着基準馬です。</strong></p>")

                        runners_except_1st = [h for h in component if h != fastest]
                        if runners_except_1st:
                            second_horse = min(runners_except_1st, key=lambda x: final_scores[x])
                            second_disp = format_horse_name(second_horse, umaban_dict, race_id)

                            output.append(f"    <p class='theory-text' style='margin:5px 0 3px 0; font-size:0.85em; color:#c0392b;'><strong>※次点評価の {second_disp} を上回る根拠：</strong></p>")
                            runner_path = nx.shortest_path(runner_G, source=fastest, target=second_horse, weight='explore_cost')

                            ability_summary = build_ability_summary(G, runner_path, runner_G, umaban_dict, race_id, is_banei=is_banei)
                            output.append(f"    <p class='theory-text' style='margin:0 0 5px 0;'>🔍 <strong>能力比較：</strong> {ability_summary}</p>")
                            output.append("    <ul class='theory-details' style='margin: 0; padding-left: 20px; color: #555;'>")
                            output.extend(render_path_details(G, runner_path, runner_G, umaban_dict, target_course, target_distance, race_id, is_banei))
                            output.append("    </ul>")
                    else:
                        runner_path = nx.shortest_path(runner_G, source=fastest, target=horse, weight='explore_cost')

                        if len(runner_path) > 2:
                            display_runner_path = runner_path[-2:]
                            ref_horse = display_runner_path[0]
                            ref_disp = format_horse_name(ref_horse, umaban_dict, race_id)
                            ref_diff = max(0.0, final_scores.get(ref_horse, 0))
                            ref_tier = all_tier_map.get(ref_horse, get_rank_tier(ref_diff, is_banei=is_banei))
                            ref_diff_str = "±0.0" if ref_diff < diff_zero_th else f"+{ref_diff:.1f}"
                            output.append(f"    <p class='theory-text' style='margin:0 0 3px 0; font-size:0.85em; color:#888;'>※ {ref_disp} ({ref_tier}ランク / {ref_diff_str}秒差) との比較：</p>")
                        else:
                            display_runner_path = runner_path

                        ability_summary = build_ability_summary(G, display_runner_path, runner_G, umaban_dict, race_id, is_banei=is_banei)
                        output.append(f"    <p class='theory-text' style='margin:0 0 5px 0;'>🔍 <strong>能力比較：</strong> {ability_summary}</p>")
                        output.append("    <ul class='theory-details' style='margin: 0; padding-left: 20px; color: #555;'>")
                        output.extend(render_path_details(G, display_runner_path, runner_G, umaban_dict, target_course, target_distance, race_id, is_banei))
                        output.append("    </ul>")

                    output.append("  </div></div>")

                if tier == "S":
                    s_horses = [h for h, d in rank_groups["S"]]
                    target_horses = s_horses if len(s_horses) >= 2 else s_horses + [h for h, d in rank_groups["A"]]
                    
                    all_race_info = {}
                    for u, v, data in undirected_G.edges(data=True):
                        for hist in data.get('history', []):
                            rid = hist['race_id']
                            if rid not in all_race_info:
                                all_race_info[rid] = {
                                    'date': hist['date'],
                                    'course': hist['course'],
                                    'distance': hist['distance'],
                                    'horses': {}
                                }
                            h1_n = hist.get('h1_name')
                            h2_n = hist.get('h2_name')
                            if h1_n and h1_n in umaban_dict:
                                all_race_info[rid]['horses'][h1_n] = {'time': hist['h1_time'], 'umaban': hist['h1_past_umaban']}
                            if h2_n and h2_n in umaban_dict:
                                all_race_info[rid]['horses'][h2_n] = {'time': hist['h2_time'], 'umaban': hist['h2_past_umaban']}
                    
                    attention_races = {}
                    for i in range(len(target_horses)):
                        for j in range(i+1, len(target_horses)):
                            h1 = target_horses[i]
                            h2 = target_horses[j]
                            if undirected_G.has_edge(h1, h2):
                                for hist in undirected_G[h1][h2]['history']:
                                    if abs(hist['raw_diff']) <= (10.0 if is_banei else 0.8):
                                        rid = hist['race_id']
                                        if rid not in attention_races:
                                            attention_races[rid] = all_race_info[rid]
                    
                    if attention_races:
                        output.append("<div class='attention-races' style='background-color: #fff9e6; border: 1px solid #f1c40f; padding: 12px; margin-top: 15px; margin-bottom: 25px; border-radius: 6px;'>")
                        output.append("<h4 style='margin: 0 0 8px 0; color: #d35400; font-size: 0.95em;'>🔍 上位馬の直接対決（タイム僅差の注目レース）</h4>")
                        
                        sorted_races = sorted(attention_races.items(), key=lambda x: x[1]['date'], reverse=True)
                        for rid, rdata in sorted_races:
                            url = f"https://db.netkeiba.com/race/{rid}"
                            race_name = f"{rdata['date']} {rdata['course']} {rdata['distance']}m"
                            output.append(f"<div style='margin-bottom: 12px; font-size: 0.9em; background: #fff; padding: 8px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);'>")
                            output.append(f"  <div style='margin-bottom: 5px;'><a href='{url}' target='_blank' style='color: #2980b9; font-weight: bold; text-decoration: underline;'>▶ {race_name} の結果・映像へ</a></div>")
                            output.append("  <ul style='margin: 0; padding-left: 20px; color: #333;'>")
                            
                            sorted_h = sorted(rdata['horses'].items(), key=lambda x: x[1]['time'])
                            best_time = sorted_h[0][1]['time']
                            for h, hdata in sorted_h:
                                curr_umaban = umaban_dict.get(h, "?")
                                past_uma = hdata['umaban']
                                if not past_uma: past_uma = "?"
                                diff_to_best = hdata['time'] - best_time
                                diff_str = f"+{diff_to_best:.1f}秒" if diff_to_best > 0 else "最先着"
                                output.append(f"<li style='margin-bottom: 2px;'>[今回: <strong>{curr_umaban}</strong>番] {h} <span style='color: #7f8c8d; font-size: 0.85em;'>(当時の馬番: <strong style='color: #c0392b;'>{past_uma}</strong>番)</span> <span style='color: #e74c3c; font-weight: bold; margin-left: 5px;'>{diff_str}</span></li>")
                            output.append("  </ul>")
                            output.append("</div>")
                        output.append("</div>")

            output.append("</div>")

    unranked = [h for h in umaban_dict.keys() if h not in all_ranked_horses]
    if unranked:
        color = tier_colors["!"]
        output.append(f"<h3 style='color: white; background-color: {color}; padding: 6px 12px; border-radius: 5px; margin-top: 15px; margin-bottom: 8px; font-size: 0.9em;'>{tier_labels_map['!']}</h3>")
        unranked_disp = [format_horse_name(h, umaban_dict, race_id) for h in unranked]
        output.append(f"<div style='background-color: #f5f0fa; border-left: 4px solid {color}; padding: 8px 12px; font-size: 0.85em; margin-bottom: 10px;'>")
        output.append(f"  <p style='margin: 0; color: #555;'>対戦繋がりがない別路線組</p>")
        output.append(f"  <p style='margin: 8px 0 0 0; font-size: 0.9em;'>{'、'.join(unranked_disp)}</p>")
        output.append("</div>")

    return "".join(output), all_ranked_scores, unranked, true_fastest, all_tier_map

# ==========================================
# 4. メインAPI: rank_horses()
# ==========================================
def rank_horses(race_id, mark_race_id=None):
    html_race_id = mark_race_id or race_id
    scraper = NetkeibaScraper()
    race_title, target_course, target_track, target_distance, past_races, umaban_dict = scraper.fetch_past5_data(race_id)

    is_banei = (target_track == "ばんえい")

    G_course = build_measuring_stick_graph(past_races, target_course, target_track, target_distance, umaban_dict, is_course_only=True)
    result_course_html, _, _, _, _ = analyze_all_horses_html(G_course, umaban_dict, target_course, target_distance, html_race_id, is_banei=is_banei)

    G_track = build_measuring_stick_graph(past_races, target_course, target_track, target_distance, umaban_dict, is_course_only=False)
    result_track_html, track_scores, track_unranked, track_fastest, track_tier_map = analyze_all_horses_html(G_track, umaban_dict, target_course, target_distance, html_race_id, is_banei=is_banei)

    ruler_ranks = {}
    for horse_name, diff in track_scores.items():
        diff = max(0.0, diff)
        tier = track_tier_map.get(horse_name, get_rank_tier(diff, is_banei=is_banei))
        if tier == "S" and horse_name == track_fastest:
            score = RANK_SCORES["S+"]
        else:
            score = RANK_SCORES[tier]
        ruler_ranks[horse_name] = {
            "rank": tier,
            "score": score,
            "diff": round(diff, 2)
        }

    for horse_name in track_unranked:
        ruler_ranks[horse_name] = {
            "rank": "!",
            "score": RANK_SCORES["!"],
            "diff": -1
        }

    ruler_html = (
        f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 10px 0 8px 0;'>【第1部】同コース（{target_course}）での比較</h2>"
        f"{result_course_html}"
        f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 20px 0 8px 0;'>【第2部】全国の競馬場（{target_track}）での比較<br><span style='font-size:0.75em; font-weight:normal;'>※適性(コース/距離)が近い過去レースを優先して算出。この結果が点数に反映されます。</span></h2>"
        f"{result_track_html}"
    )

    return ruler_ranks, ruler_html

# ==========================================
# 5. Webアプリの動き
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    input_url = request.form['url']
    selected_races = request.form.getlist('races')
    water_mode = request.form.get('water_mode', '')

    scraper = NetkeibaScraper()
    base_race_id = scraper.extract_race_id(input_url)
    if not base_race_id:
        return "<h3>エラー：正しいURLを入力してください</h3>"

    race_ids = [base_race_id]
    if selected_races:
        base_prefix = base_race_id[:10]
        for r in selected_races:
            rid = base_prefix + str(int(r)).zfill(2)
            if rid != base_race_id:
                race_ids.append(rid)
        race_ids = sorted(set(race_ids), key=lambda x: int(x[-2:]))

    race_list = [{'race_id': rid, 'race_num': int(rid[-2:])} for rid in race_ids]
    return render_template('result.html', race_list=race_list, water_mode=water_mode)

@app.route('/analyze_single', methods=['POST'])
def analyze_single():
    data = request.get_json()
    race_id = data.get('race_id', '')
    water_mode = data.get('water_mode', '') or None
    if not race_id or not re.match(r'^\d{12}$', race_id):
        return jsonify({'error': '不正なrace_id'}), 400

    try:
        scraper = NetkeibaScraper()
        race_title, target_course, target_track, target_distance, past_races, umaban_dict = scraper.fetch_past5_data(race_id, water_mode=water_mode)

        is_banei = (target_track == "ばんえい")

        G_course = build_measuring_stick_graph(past_races, target_course, target_track, target_distance, umaban_dict, is_course_only=True)
        result_course, _, _, _, _ = analyze_all_horses_html(G_course, umaban_dict, target_course, target_distance, is_banei=is_banei)

        water_note = ""
        if is_banei and water_mode:
            label = "1.9%以下（軽馬場）" if water_mode == 'dry' else "2.0%以上（重馬場）"
            water_note = f"<p style='text-align:center; color:#2980b9; font-size:13px; margin-bottom:15px;'>💧 水分量フィルタ: <strong>{label}</strong> のレースのみ使用</p>"

        if is_banei:
            result_html = (
                f"{water_note}"
                f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 10px 0 8px 0;'>帯広ばんえい {target_distance}m での比較</h2>"
                f"{result_course}"
            )
        else:
            G_track = build_measuring_stick_graph(past_races, target_course, target_track, target_distance, umaban_dict, is_course_only=False)
            result_track, _, _, _, _ = analyze_all_horses_html(G_track, umaban_dict, target_course, target_distance, is_banei=False)

            result_html = (
                f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 10px 0 8px 0;'>【第1部】同コース（{target_course}）での比較</h2>"
                f"{result_course}"
                f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 20px 0 8px 0;'>【第2部】全国の競馬場（{target_track}）での比較<br><span style='font-size:0.75em; font-weight:normal;'>※適性(コース/距離)が近い過去レースを優先して算出</span></h2>"
                f"{result_track}"
            )
        return jsonify({'title': race_title, 'html': result_html})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
