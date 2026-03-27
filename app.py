import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
import statistics
import networkx as nx

# ==========================================
# 1. Netkeiba ディープスクレイパー (app.pyより統合)
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

        deep_dive_race_ids = set(deep_dive_candidates.keys())

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

        # === 接戦ペアの追加直接対決レースを深掘り ===
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
# 2. ネットワーク構築 (app.pyより統合)
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

        cap_val = 30.0 if is_banei else 1.5

        if is_banei:
            filtered_horses = list(race['horses'].items())
        else:
            filtered_horses = [(h, t) for h, t in race['horses'].items() if (h in umaban_dict) or (t < 2.0)]

        for i in range(len(filtered_horses)):
            for j in range(i + 1, len(filtered_horses)):
                h1_name, h1_time = filtered_horses[i]
                h2_name, h2_time = filtered_horses[j]

                h1_is_current = h1_name in umaban_dict
                h2_is_current = h2_name in umaban_dict
                if not h1_is_current and not h2_is_current:
                    has_current_in_race = any(h in umaban_dict for h, _ in filtered_horses)
                    if not has_current_in_race:
                        continue

                if h1_name > h2_name:
                    h1_name, h1_time, h2_name, h2_time = h2_name, h2_time, h1_name, h1_time

                raw_diff = h1_time - h2_time

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
# 3. ランク階層生成＆出力処理 (app.pyより統合)
# ==========================================
def format_horse_name(horse, umaban_dict, race_id=None):
    if horse in umaban_dict:
        return f"[{umaban_dict[horse]}] {horse}"
    else:
        return f"[隠] {horse}"

def get_rank_tier(diff, is_banei=False):
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

def calc_path_score(G, path, target_course=None, target_distance=None):
    score = 0.0
    for k in range(len(path) - 1):
        u, v = path[k], path[k+1]
        if G.has_edge(u, v):
            edge = G[u][v]
            if target_course and target_distance and 'history' in edge:
                same_entries = sorted(
                    [h for h in edge['history'] if h['course'] == target_course and h['distance'] == target_distance],
                    key=lambda x: x.get('date', ''), reverse=True)
                other_diffs = [h['diff'] for h in edge['history']
                               if h['course'] != target_course or h['distance'] != target_distance]
                if same_entries:
                    decay_weights = [1.0, 0.8] + [0.6] * max(0, len(same_entries) - 2)
                    w_sum = sum(decay_weights[:len(same_entries)])
                    same_weighted = sum(e['diff'] * w for e, w in zip(same_entries, decay_weights)) / w_sum
                    if other_diffs:
                        max_other = max(other_diffs, key=abs)
                        if abs(max_other) > 1.0:
                            weighted_avg = (same_weighted * 2 + max_other * 0.4) / (2 + 0.4)
                        else:
                            weighted_avg = same_weighted
                    else:
                        weighted_avg = same_weighted
                    score -= weighted_avg
                else:
                    score -= edge['rank_diff']
            else:
                score -= edge['rank_diff']
        else:
            edge = G[v][u]
            if target_course and target_distance and 'history' in edge:
                same_entries = sorted(
                    [h for h in edge['history'] if h['course'] == target_course and h['distance'] == target_distance],
                    key=lambda x: x.get('date', ''), reverse=True)
                other_diffs = [h['diff'] for h in edge['history']
                               if h['course'] != target_course or h['distance'] != target_distance]
                if same_entries:
                    decay_weights = [1.0, 0.8] + [0.6] * max(0, len(same_entries) - 2)
                    w_sum = sum(decay_weights[:len(same_entries)])
                    same_weighted = sum(e['diff'] * w for e, w in zip(same_entries, decay_weights)) / w_sum
                    if other_diffs:
                        max_other = max(other_diffs, key=abs)
                        if abs(max_other) > 1.0:
                            weighted_avg = (same_weighted * 2 + max_other * 0.4) / (2 + 0.4)
                        else:
                            weighted_avg = same_weighted
                    else:
                        weighted_avg = same_weighted
                    score += weighted_avg
                else:
                    score += edge['rank_diff']
            else:
                score += edge['rank_diff']
                
    if len(path) > 2:
        is_strict_path = True
        if target_course:
            for k in range(len(path) - 1):
                u_node, v_node = path[k], path[k+1]
                e = G[u_node][v_node] if G.has_edge(u_node, v_node) else G[v_node][u_node]
                if 'history' in e:
                    if not any(h.get('course') == target_course for h in e['history']):
                        is_strict_path = False
                        break
        score *= 0.7 if is_strict_path else 0.5

    return score

def build_ability_summary(G, runner_path, runner_G, umaban_dict, race_id=None, is_banei=False):
    parts = []
    is_hidden_comparison = len(runner_path) > 2
    
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
            
        discount_badge = "<span style='color:#9b59b6; font-size:0.8em; font-weight:bold;'>[隠れ馬割引適用]</span> " if is_hidden_comparison and k == len(runner_path) - 2 else ""
        
        if len(edge_paths) > 1:
            parts.append(f"{sep}<span style='color:#e67e22; font-size:0.85em;'>[複数ルート加味]</span>{sep}{discount_badge}{format_horse_name(v, umaban_dict, race_id)}")
        else:
            p = edge_paths[0]
            p_oriented = p if p[0] == u else p[::-1]
            middle_nodes = p_oriented[1:-1]
            for m in middle_nodes:
                parts.append(f"{sep}{format_horse_name(m, umaban_dict, race_id)}")
            parts.append(f"{sep}{discount_badge}{format_horse_name(v, umaban_dict, race_id)}")
            
    return "".join(parts)

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
                
    if len(path) > 2:
        details.append(f"      <li{li_style}><span style='color:#9b59b6; font-size:0.85em; font-weight:bold;'>※上記合計タイム差に対して、隠れ馬ノイズ割引（×0.7等）が適用されています。</span></li>")
                
    return details

def build_runner_graph(undirected_G, umaban_dict, is_banei=False):
    runner_G = nx.Graph()
    current_runners = [h for h in umaban_dict.keys() if h in undirected_G.nodes()]
    decisive_th = 10.0 if is_banei else 1.1

    for h in current_runners:
        runner_G.add_node(h)

    for i in range(len(current_runners)):
        for j in range(i + 1, len(current_runners)):
            u, v = current_runners[i], current_runners[j]

            if undirected_G.has_edge(u, v):
                edge_data = undirected_G[u][v]
                cost = edge_data.get('explore_cost', 10)
                margin = abs(edge_data.get('rank_diff', 0))

                runner_G.add_edge(u, v, explore_cost=cost, full_path=[u, v], multiple_paths=[[u, v]])
            else:
                try:
                    paths = list(nx.all_simple_paths(undirected_G, source=u, target=v, cutoff=3))
                    valid_paths = []
                    for path in paths:
                        middle_nodes = path[1:-1]
                        if any(node in umaban_dict for node in middle_nodes):
                            continue
                        cost = sum(undirected_G[path[k]][path[k+1]]['explore_cost'] for k in range(len(path)-1))
                        valid_paths.append((path, cost))

                    if valid_paths:
                        valid_paths.sort(key=lambda x: x[1])
                        top_paths = [p[0] for p in valid_paths[:3]]
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

    matchup_matrix = {u: {} for u in current_runners}
    near_th = 7.0 if is_banei else 0.5
    far_th = 13.0 if is_banei else 0.9

    for u in current_runners:
        for v in current_runners:
            if u == v: continue
            s_uv = pair_scores[u].get(v, float('inf'))
            if s_uv == float('inf'):
                matchup_matrix[u][v] = "="
                continue
            adv = -s_uv
            if adv >= far_th: matchup_matrix[u][v] = ">>"
            elif adv >= near_th: matchup_matrix[u][v] = ">"
            elif adv <= -far_th: matchup_matrix[u][v] = "<<"
            elif adv <= -near_th: matchup_matrix[u][v] = "<"
            else: matchup_matrix[u][v] = "="

    comparable_horses = set()
    total_opponents = len(current_runners) - 1
    coverage_threshold = 0.25

    for u in current_runners:
        compared_count = sum(1 for v in current_runners
                             if u != v and pair_scores[u].get(v, float('inf')) != float('inf'))
        coverage = compared_count / total_opponents if total_opponents > 0 else 0
        if coverage >= coverage_threshold or any(matchup_matrix[u].get(v) == ">>" for v in current_runners):
            comparable_horses.add(u)

    tier_map = {}
    pool = list(comparable_horses)

    if pool:
        horse_points = {}
        for u in pool:
            pts = 0.0
            count = 0
            for v in pool:
                if u == v: continue
                rel = matchup_matrix[u].get(v)
                if rel:
                    count += 1
                    if rel == ">>": pts += 3.0
                    elif rel == ">": pts += 1.5
                    elif rel == "=": pts += 0.0
                    elif rel == "<": pts -= 1.5
                    elif rel == "<<": pts -= 3.0
            horse_points[u] = pts / count if count > 0 else 0

        ranked_pool = sorted(horse_points.items(), key=lambda x: x[1], reverse=True)
        top_score = ranked_pool[0][1]

        for h, score in ranked_pool:
            diff_from_top = top_score - score
            if is_banei:
                if score >= 1.0 and diff_from_top <= 1.5: tier_map[h] = "S"
                elif score >= 0.0: tier_map[h] = "A"
                elif score >= -1.0: tier_map[h] = "B"
                else: tier_map[h] = "C"
            else:
                if score >= 1.0 and diff_from_top <= 0.8: tier_map[h] = "S"
                elif score >= 0.0: tier_map[h] = "A"
                elif score >= -1.0: tier_map[h] = "B"
                else: tier_map[h] = "C"

        tier_val = {"S": 4, "A": 3, "B": 2, "C": 1}
        val_tier = {4: "S", 3: "A", 2: "B", 1: "C"}
        for _ in range(3):
            changed = False
            for u in pool:
                for v in pool:
                    if u == v: continue
                    rel = matchup_matrix[u].get(v)
                    if rel in [">>", ">"]:
                        t_u = tier_val.get(tier_map.get(u, "C"), 1)
                        t_v = tier_val.get(tier_map.get(v, "C"), 1)
                        if t_u <= t_v:
                            if matchup_matrix[v].get(u) not in [">>", ">"]:
                                if t_v > 1:
                                    tier_map[v] = val_tier[t_v - 1]
                                    changed = True
                                elif t_u < 4:
                                    tier_map[u] = val_tier[t_u + 1]
                                    changed = True
            if not changed: break

    for h in current_runners:
        if h not in tier_map: tier_map[h] = "C"

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

            if comp_idx == 0: true_fastest = fastest

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
# 4. 一括HTML出力用ラップ関数 (ダウンロード用)
# ==========================================
def wrap_combined_html(results_list):
    tabs_html = ""
    contents_html = ""
    
    for i, (r_num, r_title, content) in enumerate(results_list):
        active_class = "active" if i == 0 else ""
        tabs_html += f'<button class="tab-btn {active_class}" onclick="openTab(event, \'race_{r_num}\')">{r_num}R</button>\n'
        
        contents_html += f'''
        <div id="race_{r_num}" class="tab-content {active_class}">
            <h2 class="race-title">📊 {r_title}</h2>
            {content}
        </div>
        '''

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>競馬AI 相対評価 一括分析結果</title>
  <style>
    body {{ font-family: 'Helvetica Neue', Arial, sans-serif; background: #f7f6f2; margin: 0; padding: 20px; color: #333; }}
    .container {{ background: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 900px; margin: auto; }}
    .tab-buttons {{ display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 20px; border-bottom: 2px solid #3498db; padding-bottom: 5px; position: sticky; top: 0; background: #fff; z-index: 10; }}
    .tab-btn {{ padding: 10px 16px; border: none; background: #ecf0f1; border-radius: 4px 4px 0 0; cursor: pointer; font-weight: bold; color: #7f8c8d; font-size: 14px; transition: 0.3s; }}
    .tab-btn:hover {{ background: #bdc3c7; }}
    .tab-btn.active {{ background: #3498db; color: white; }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
    h2.race-title {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; margin-top: 0; font-size: 1.4em; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="tab-buttons">
       {tabs_html}
    </div>
    {contents_html}
  </div>
  <script>
    function openTab(evt, tabId) {{
        var contents = document.querySelectorAll('.tab-content');
        contents.forEach(c => c.classList.remove('active'));
        var btns = document.querySelectorAll('.tab-btn');
        btns.forEach(b => b.classList.remove('active'));
        
        document.getElementById(tabId).classList.add('active');
        evt.currentTarget.classList.add('active');
    }}
  </script>
</body>
</html>"""

# ==========================================
# 5. Streamlit UI
# ==========================================
st.set_page_config(page_title="競馬AI 相対評価ツール", page_icon="🏇", layout="centered")

if "chk_initialized" not in st.session_state:
    st.session_state.chk_initialized = True
    for i in range(1, 13):
        st.session_state[f"chk_{i}"] = False

def select_all_races():
    for i in range(1, 13): st.session_state[f"chk_{i}"] = True

def deselect_all_races():
    for i in range(1, 13): st.session_state[f"chk_{i}"] = False

st.title("🏇 競馬AI 相対評価ツール (高度ネットワーク版)")
st.caption("NetkeibaのレースURLから、複数ルートや隠れ馬経由でのタイム差分を網羅的に計算し、相対的な能力序列を出力します。")

url_input = st.text_input("netkeibaのレースURL (基準となるURLを1つ入力)", placeholder="https://race.netkeiba.com/race/result.html?race_id=202405020111")
water_mode = st.selectbox("水分量フィルタ（ばんえい専用）", options=["なし", "軽馬場（dry）", "重馬場（wet）"])

st.markdown("---")
st.write("🎯 **一括分析するレースを選択**")

c1, c2, _ = st.columns([1, 1, 3])
c1.button("✅ 全選択", on_click=select_all_races)
c2.button("⬜ 全解除", on_click=deselect_all_races)

cols = st.columns(4)
for i in range(1, 13):
    cols[(i-1)%4].checkbox(f"{i}R", key=f"chk_{i}")

st.markdown("<br>", unsafe_allow_html=True)
submitted = st.button("🚀 分析開始", type="primary", use_container_width=True)

if submitted:
    scraper = NetkeibaScraper()
    base_race_id = scraper.extract_race_id(url_input)

    if not base_race_id:
        st.error("正しいnetkeibaのURLを入力してください（例：https://race.netkeiba.com/race/result.html?race_id=...）")
        st.stop()

    selected_races = [i for i in range(1, 13) if st.session_state[f"chk_{i}"]]
    
    if not selected_races:
        try:
            selected_races = [int(base_race_id[-2:])]
        except:
            st.error("対象レースが選択されていません。")
            st.stop()

    base_prefix = base_race_id[:10]
    race_ids_to_process = [f"{base_prefix}{r:02d}" for r in selected_races]
    
    wmode = "dry" if water_mode == "軽馬場（dry）" else "wet" if water_mode == "重馬場（wet）" else None
    
    results_list = []
    
    st.info(f"全 {len(race_ids_to_process)} レースのデータを収集・高度AI分析しています。処理には少し時間がかかります...")
    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, rid in enumerate(race_ids_to_process):
        r_num = int(rid[-2:])
        status_text.text(f"処理中: {r_num}R ... ({idx+1}/{len(race_ids_to_process)})")
        
        try:
            race_title, target_course, target_track, target_distance, past_races, umaban_dict = scraper.fetch_past5_data(rid, water_mode=wmode)
            is_banei = (target_track == "ばんえい")

            if not umaban_dict:
                results_list.append((r_num, f"{r_num}R (出走馬なし)", "<p>データが取得できませんでした。</p>"))
                continue

            # app.py由来の評価ロジック実行（同コース）
            G_course = build_measuring_stick_graph(past_races, target_course, target_track, target_distance, umaban_dict, is_course_only=True)
            result_course_html, _, _, _, _ = analyze_all_horses_html(G_course, umaban_dict, target_course, target_distance, rid, is_banei=is_banei)

            water_note = ""
            if is_banei and wmode:
                label = "1.9%以下（軽馬場）" if wmode == 'dry' else "2.0%以上（重馬場）"
                water_note = f"<p style='text-align:center; color:#2980b9; font-size:13px; margin-bottom:15px;'>💧 水分量フィルタ: <strong>{label}</strong> のレースのみ使用</p>"

            if is_banei:
                final_html = (
                    f"{water_note}"
                    f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 10px 0 8px 0;'>帯広ばんえい {target_distance}m での比較</h2>"
                    f"{result_course_html}"
                )
            else:
                # app.py由来の評価ロジック実行（全国）
                G_track = build_measuring_stick_graph(past_races, target_course, target_track, target_distance, umaban_dict, is_course_only=False)
                result_track_html, _, _, _, _ = analyze_all_horses_html(G_track, umaban_dict, target_course, target_distance, rid, is_banei=is_banei)

                final_html = (
                    f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 10px 0 8px 0;'>【第1部】同コース（{target_course}）での比較</h2>"
                    f"{result_course_html}"
                    f"<h2 class='section-title' style='background-color: #7b8d7a; color: white; padding: 8px 10px; border-radius: 6px; font-size: 0.95em; margin: 20px 0 8px 0;'>【第2部】全国の競馬場（{target_track}）での比較<br><span style='font-size:0.75em; font-weight:normal;'>※適性(コース/距離)が近い過去レースを優先して算出</span></h2>"
                    f"{result_track_html}"
                )
            
            results_list.append((r_num, race_title, final_html))
            
        except Exception as e:
            results_list.append((r_num, f"{r_num}R (エラー)", f"<p style='color:red;'>エラー発生: {e}</p>"))
            
        progress_bar.progress((idx + 1) / len(race_ids_to_process))

    status_text.success("✅ すべての高度AI分析が完了しました！")

    combined_html_doc = wrap_combined_html(results_list)
    
    st.download_button(
        label="📥 分析結果をHTMLファイルとして一括保存（ダウンロード）",
        data=combined_html_doc,
        file_name=f"競馬AI分析結果_高度版_{base_prefix}.html",
        mime="text/html",
        type="primary"
    )
    st.markdown("---")

    tabs = st.tabs([f"{r[0]}R" for r in results_list])
    for tab, (r_num, r_title, r_html) in zip(tabs, results_list):
        with tab:
            st.subheader(f"📊 {r_title}")
            st.markdown(r_html, unsafe_allow_html=True)
