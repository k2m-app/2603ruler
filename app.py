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
RANK_SCORES = {"S+": 35, "S": 30, "!": 27, "A": 20, "B": 10, "C": 7}

# ==========================================
# 大井競馬場 内回り/外回り 判定関数
# ==========================================
def get_ooi_track(dist_str):
    try:
        d = int(dist_str)
        return "内" if d in [1500, 1600] else "外"
    except:
        return "不明"

def get_short_course_name(course, dist):
    """短いコース名（例：大外1200、船1400）を生成"""
    if not course: return "?"
    if course == '大井':
        return f"大{get_ooi_track(dist)}{dist}"
    else:
        return f"{course[0:1]}{dist}"

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
            t_match = re.search(r'(芝|ダ|障)[^\d]*(\d{3,4})', race_data01.text)
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
                        dd = int(date_match.group(3))
                        race_date = f"{yy}/{mm}/{dd}"

                    data05 = td.find('div', class_='Data05')
                    if data05:
                        if course == '帯広':
                            track_type = "ばんえい"
                            d_match = re.search(r'(\d{3,4})', data05.text)
                            distance = d_match.group(1) if d_match else "不明"
                        else:
                            track_match = re.search(r'(芝|ダ|障)[^\d]*(\d{3,4})', data05.text)
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
# 優先順位ソート（大井の内/外判定込み）
# ==========================================
def sort_histories(histories, target_course, target_distance):
    def score_hist(h):
        c_match = (h.get('course') == target_course)
        d_match = (str(h.get('distance')) == str(target_distance))
        
        p = 0
        if c_match and d_match:
            p = 10
        elif c_match:
            if target_course == '大井':
                t_track = get_ooi_track(target_distance)
                h_track = get_ooi_track(h.get('distance'))
                if t_track == h_track:
                    p = 8 
                else:
                    p = 4 
            else:
                p = 6 
        elif d_match:
            p = 5 
        else:
            p = 0
        return (p, h.get('date', ''))
    return sorted(histories, key=score_hist, reverse=True)

# ==========================================
# 新規: PageRankを用いた能力ネットワークスコア計算
# ==========================================
def calculate_pagerank(G):
    """
    全レースの勝敗ネットワークからPageRank（相対的強さ）を計算。
    負けた馬（弱い）から勝った馬（強い）へリンクを張ることで、
    強い馬と戦って僅差だった馬のスコアも底上げされる。
    """
    PR_G = nx.DiGraph()
    # 孤立ノードもスコア0として扱うために全追加
    for node in G.nodes():
        PR_G.add_node(node)
        
    for u, v, data in G.edges(data=True):
        for hist in data.get('history', []):
            diff = hist['raw_diff'] # h1_time(u) - h2_time(v)
            # diff < -0.1 なら u の勝ち。v から u へ投票リンクを張る
            if diff <= -0.1:
                if PR_G.has_edge(v, u):
                    PR_G[v][u]['weight'] += 1
                else:
                    PR_G.add_edge(v, u, weight=1)
            elif diff >= 0.1:
                if PR_G.has_edge(u, v):
                    PR_G[u][v]['weight'] += 1
                else:
                    PR_G.add_edge(u, v, weight=1)
                    
    try:
        pr = nx.pagerank(PR_G, alpha=0.85, weight='weight')
        max_pr = max(pr.values()) if pr else 1.0
        # 100点満点に正規化
        return {k: round((v / max_pr) * 100, 1) for k, v in pr.items()}
    except:
        return {k: 0.0 for k in G.nodes()}

# ==========================================
# 2. ネットワーク構築（統合版）
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

def build_unified_graph(past_races, target_course, target_track, target_distance, umaban_dict):
    G = nx.DiGraph()
    for race in past_races:
        if race.get('track_type') != target_track: continue
        if not is_distance_in_range(race.get('distance'), target_distance): continue

        is_banei = race.get('is_banei', False)
        is_same_course = (race.get('course') == target_course)
        
        try:
            dist_diff = abs(int(race.get('distance', 0)) - int(target_distance))
        except (ValueError, TypeError):
            dist_diff = 9999
            
        if is_same_course and dist_diff == 0:
            base_cost = 1   
        elif is_same_course:
            if target_course == '大井':
                if get_ooi_track(target_distance) == get_ooi_track(race.get('distance')):
                    base_cost = 3 if dist_diff <= 200 else 5
                else:
                    base_cost = 15
            else:
                base_cost = 4 if dist_diff <= 200 else 6
        elif not is_same_course and dist_diff == 0:
            base_cost = 8   
        else:
            base_cost = 20  

        cap_val = 30.0 if is_banei else 10.0

        if is_banei:
            filtered_horses = list(race.get('horses', {}).items())
        else:
            filtered_horses = [(h, t) for h, t in race.get('horses', {}).items() if (h in umaban_dict) or (t < 2.0)]

        for i in range(len(filtered_horses)):
            for j in range(i + 1, len(filtered_horses)):
                h1_name, h1_time = filtered_horses[i]
                h2_name, h2_time = filtered_horses[j]

                h1_is_current = h1_name in umaban_dict
                h2_is_current = h2_name in umaban_dict
                is_direct = (h1_is_current and h2_is_current)

                if not is_direct:
                    has_current_in_race = any(h in umaban_dict for h, _ in filtered_horses)
                    if not has_current_in_race:
                        continue
                        
                if h1_name > h2_name:
                    h1_name, h1_time, h2_name, h2_time = h2_name, h2_time, h1_name, h1_time

                raw_diff = h1_time - h2_time

                if not is_direct:
                    if is_banei and abs(raw_diff) >= 30.0: continue
                    elif not is_banei and abs(raw_diff) >= 2.0: continue

                capped_diff = max(-cap_val, raw_diff) if raw_diff < 0 else min(cap_val, raw_diff)

                worse_time = max(h1_time, h2_time)
                if is_banei: reliability_penalty = 0
                elif worse_time <= 0.5: reliability_penalty = 0
                elif worse_time <= 1.0: reliability_penalty = 5
                else: reliability_penalty = 15

                edge_cost = (base_cost + reliability_penalty) if is_direct else (base_cost + 100 + reliability_penalty)

                history_item = {
                    'date': race.get('date'), 'race_id': race.get('race_id'), 'course': race.get('course'),
                    'distance': race.get('distance'), 'track_type': race.get('track_type', '不明'), 
                    'raw_diff': raw_diff, 'diff': capped_diff,
                    'h1_name': h1_name, 'h2_name': h2_name,
                    'h1_past_umaban': race.get('past_umaban', {}).get(h1_name, ""),
                    'h2_past_umaban': race.get('past_umaban', {}).get(h2_name, ""),
                    'h1_time': h1_time, 'h2_time': h2_time
                }

                if G.has_edge(h1_name, h2_name):
                    current_cost = G[h1_name][h2_name]['explore_cost']
                    edge_data = G[h1_name][h2_name]
                    edge_data['diffs'].append(capped_diff)
                    edge_data['history'].append(history_item)
                    edge_data['rank_diff'] = sum(edge_data['diffs']) / len(edge_data['diffs'])
                    
                    if edge_cost < current_cost:
                        edge_data['explore_cost'] = edge_cost
                        for k in ['race_id', 'course', 'distance', 'track_type', 'date', 'h1_time', 'h2_time']:
                            edge_data[k] = race.get(k) if k in race else locals().get(k)
                    
                    if len(edge_data['diffs']) >= 2:
                        var = statistics.variance(edge_data['diffs'])
                        edge_data['explore_cost'] = min(edge_cost, current_cost) + var * 100
                else:
                    G.add_edge(h1_name, h2_name, weight=1, diffs=[capped_diff], history=[history_item], rank_diff=capped_diff,
                               explore_cost=edge_cost, race_id=race.get('race_id'), course=race.get('course'),
                               distance=race.get('distance'), track_type=race.get('track_type', '不明'),
                               date=race.get('date'), h1_time=h1_time, h2_time=h2_time)
    return G

# ==========================================
# 3. 新UI向け出力フォーマット関数群
# ==========================================
def format_horse_name(horse, umaban_dict):
    return f"[{umaban_dict.get(horse, '?')}]{horse}"

def get_separator(gap, is_banei):
    """着差ごとの不等号をチューニング (0.5秒差までは同列)"""
    gap = abs(gap)
    if is_banei:
        if gap <= 5.0: return "＝"
        elif gap <= 10.0: return "＞"
        elif gap <= 15.0: return "＞＞"
        else: return "＞＞＞"
    else:
        if gap <= 0.5: return "＝"
        elif gap <= 1.0: return "＞"
        elif gap <= 1.5: return "＞＞"
        else: return "＞＞＞"

def format_direct_chain(base_horse, current_runners, race_info, is_banei, umaban_dict):
    horses_in_race = []
    base_time = race_info.get('horses', {}).get(base_horse)
    if base_time is None: return ""
    
    for h in current_runners:
        if h in race_info.get('horses', {}):
            horses_in_race.append((h, race_info['horses'][h]))
            
    horses_in_race.sort(key=lambda x: x[1])
    
    parts = []
    for i, (h, t) in enumerate(horses_in_race):
        diff_to_base = t - base_time
        if h == base_horse:
            parts.append("本馬")
        else:
            sign = "+" if diff_to_base > 0 else "-" if diff_to_base < 0 else "±"
            u_str = umaban_dict.get(h, "?")
            parts.append(f"[{u_str}]{h}({sign}{abs(diff_to_base):.1f})")
        
        if i < len(horses_in_race) - 1:
            next_t = horses_in_race[i+1][1]
            gap = next_t - t
            parts.append(get_separator(gap, is_banei))
            
    return "".join(parts)

def calc_path_score(G, path):
    score = 0.0
    for k in range(len(path) - 1):
        u, v = path[k], path[k+1]
        
        if G.has_edge(u, v):
            val = G[u][v].get('rank_diff', 0.0)
            sign = 1
        elif G.has_edge(v, u):
            val = G[v][u].get('rank_diff', 0.0)
            sign = -1
        else:
            continue
        score += val * sign
                
    if len(path) > 2:
        score *= 0.7
    return score

def build_runner_graph(undirected_G, umaban_dict, is_banei=False):
    runner_G = nx.Graph()
    current_runners = [h for h in umaban_dict.keys() if h in undirected_G.nodes()]

    for h in current_runners: runner_G.add_node(h)

    for i in range(len(current_runners)):
        for j in range(i + 1, len(current_runners)):
            u, v = current_runners[i], current_runners[j]
            if undirected_G.has_edge(u, v):
                edge_data = undirected_G[u][v]
                cost = edge_data.get('explore_cost', 10)
                runner_G.add_edge(u, v, explore_cost=cost, full_path=[u, v], multiple_paths=[[u, v]])
            else:
                try:
                    paths = list(nx.all_simple_paths(undirected_G, source=u, target=v, cutoff=3))
                    valid_paths = []
                    for path in paths:
                        if any(node in umaban_dict for node in path[1:-1]): continue
                        cost = sum(undirected_G[path[k]][path[k+1]].get('explore_cost', 10) for k in range(len(path)-1))
                        valid_paths.append((path, cost))
                    if valid_paths:
                        valid_paths.sort(key=lambda x: x[1])
                        top_paths = [p[0] for p in valid_paths[:3]]
                        runner_G.add_edge(u, v, explore_cost=valid_paths[0][1], full_path=top_paths[0], multiple_paths=top_paths)
                except nx.NetworkXNoPath:
                    pass
    return runner_G

def _rank_component(G, runner_G, component, umaban_dict, target_course, target_distance, pr_scores, is_banei=False):
    current_runners = list(component)
    tie_th = 5.0 if is_banei else 0.5 # 0.5秒差までは「＝（同列）」

    adv_matrix = {u: {v: 0.0 for v in current_runners} for u in current_runners}

    for u in current_runners:
        for v in current_runners:
            if u == v: continue
            try:
                path = nx.shortest_path(runner_G, source=u, target=v, weight='explore_cost')
                score = 0.0
                has_target_cond = False

                if len(path) == 2 and (G.has_edge(u, v) or G.has_edge(v, u)):
                    edge = G[u][v] if G.has_edge(u, v) else G[v][u]
                    histories = edge.get('history', [])
                    if histories:
                        best_hist = sort_histories(histories, target_course, target_distance)[0]
                        score = best_hist.get('raw_diff', 0.0) if G.has_edge(u, v) else -best_hist.get('raw_diff', 0.0)
                        has_target_cond = True

                if not has_target_cond:
                    total_score = 0.0
                    for k in range(len(path) - 1):
                        hop_u, hop_v = path[k], path[k+1]
                        edge_paths = runner_G[hop_u][hop_v].get('multiple_paths', [runner_G[hop_u][hop_v].get('full_path')])
                        hop_scores = [calc_path_score(G, p if p[0] == hop_u else p[::-1]) for p in edge_paths]
                        total_score += sum(hop_scores) / len(hop_scores) if hop_scores else 0.0
                    score = total_score

                adv_matrix[u][v] = score 
            except nx.NetworkXNoPath:
                adv_matrix[u][v] = float('inf')

    pool = set(current_runners)
    tier_map = {}
    
    for tier in ["S", "A", "B"]:
        if not pool: break
        
        # 👑 PageRankを利用した勝ち残りトーナメント
        unbeaten_candidates = []
        for h in pool:
            has_clear_loss = False
            for opp in pool:
                if h == opp: continue
                # 誰かに明確に負けていたら（tie_th秒以上遅い）、エース候補から脱落
                if adv_matrix[h][opp] != float('inf') and adv_matrix[h][opp] > tie_th:
                    has_clear_loss = True
                    break
            if not has_clear_loss:
                unbeaten_candidates.append(h)
                
        if unbeaten_candidates:
            # 無敗馬が複数いる場合は、PageRank（ネットワーク上の強さ）が一番高い馬を真のエースとする
            best_anchor = max(unbeaten_candidates, key=lambda h: pr_scores.get(h, 0.0))
        else:
            # 全員が三すくみで負け合っている場合は、PageRank最強馬を強制的にエースとする
            best_anchor = max(pool, key=lambda h: pr_scores.get(h, 0.0))

        tier_map[best_anchor] = tier
        pool.remove(best_anchor)
        
        tied_horses = []
        for h in pool:
            # エース馬と互角（0.5秒差以内）なら、同じランクに引き上げる
            if adv_matrix[h][best_anchor] != float('inf') and adv_matrix[h][best_anchor] <= tie_th:
                tied_horses.append(h)
                
        for h in tied_horses:
            tier_map[h] = tier
            pool.remove(h)
            
    for h in pool:
        tier_map[h] = "C"

    s_tier_horses = [h for h, t in tier_map.items() if t == "S"]
    if not s_tier_horses: s_tier_horses = current_runners
    fastest = min(s_tier_horses, key=lambda h: sum(adv_matrix[h][o] for o in current_runners if adv_matrix[h][o] != float('inf')))
    
    final_scores = {h: adv_matrix[h][fastest] if adv_matrix[h][fastest] != float('inf') else float('inf') for h in current_runners}
    ranked_list = sorted([(h, s) for h, s in final_scores.items() if s != float('inf')], key=lambda x: (x[1], 0 if x[0] == fastest else 1))

    return fastest, final_scores, ranked_list, tier_map, adv_matrix

def analyze_all_horses_html(G, past_races, umaban_dict, target_course, target_distance, pr_scores, race_id=None, is_banei=False):
    undirected_G = G.to_undirected()
    runner_G = build_runner_graph(undirected_G, umaban_dict, is_banei=is_banei)

    components = list(nx.connected_components(runner_G))
    valid_components = sorted([c for c in components if len(c) >= 2], key=len, reverse=True)

    output = []
    all_ranked_scores = {}
    all_tier_map = {}
    all_ranked_horses = set()
    true_fastest = None

    tier_colors = {"S": "#e74c3c", "!": "#9b59b6", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    tier_labels_map = {"S": "🏆 Sランク", "!": "❗ ！ランク（別路線・測定不能）", "A": "🏆 Aランク", "B": "🏆 Bランク", "C": "🏆 Cランク"}

    if not valid_components:
        output.append("<p>比較可能な対戦データがありませんでした。</p>")
    else:
        for comp_idx, component in enumerate(valid_components):
            fastest, final_scores, ranked_list, tier_map, adv_matrix = _rank_component(
                G, runner_G, component, umaban_dict, target_course, target_distance, pr_scores, is_banei=is_banei
            )

            if comp_idx == 0: true_fastest = fastest
            all_ranked_scores.update(final_scores)
            all_tier_map.update(tier_map)
            all_ranked_horses.update(component)

            if comp_idx > 0:
                output.append(f"<h3 style='color: white; background-color: #7f8c8d; padding: 6px 12px; border-radius: 5px; margin-top: 20px; margin-bottom: 8px; font-size: 0.85em;'>📊 別グループ</h3>")

            rank_groups = {"S": [], "A": [], "B": [], "C": []}
            for horse, diff in ranked_list:
                tier = tier_map.get(horse, "C")
                rank_groups[tier].append((horse, diff))

            output.append("<div class='ranking-list'>")
            display_order = ["S", "!", "A", "B", "C"]
            unranked_horses = [h for h in umaban_dict.keys() if h not in all_ranked_horses]

            for tier in display_order:
                if tier == "!":
                    if comp_idx == 0 and unranked_horses:
                        color = tier_colors["!"]
                        output.append(f"<h3 style='color: white; background-color: {color}; padding: 6px 12px; border-radius: 5px; margin-top: 15px; margin-bottom: 8px; font-size: 0.9em;'>{tier_labels_map['!']}</h3>")
                        for h in unranked_horses:
                            pr_val = pr_scores.get(h, 0.0)
                            output.append(f"<div style='margin-bottom: 10px; font-weight: bold; color: #2c3e50;'>{format_horse_name(h, umaban_dict)} <span style='font-size:0.8em; color:#8e44ad; font-weight:normal;'>[PR: {pr_val}]</span></div>")
                    continue

                horses_in_tier = rank_groups.get(tier, [])
                if not horses_in_tier: continue

                color = tier_colors.get(tier, "#333")
                output.append(f"<h3 style='color: white; background-color: {color}; padding: 6px 12px; border-radius: 5px; margin-top: 15px; margin-bottom: 8px; font-size: 0.9em;'>{tier_labels_map[tier]}</h3>")

                for horse, diff in horses_in_tier:
                    is_star = (horse == fastest)
                    star_prefix = "<span style='color:#f1c40f; font-size:1.2em; margin-right:4px;'>⭐</span>" if is_star else ""
                    pr_val = pr_scores.get(horse, 0.0)
                    horse_disp = f"{star_prefix}{format_horse_name(horse, umaban_dict)} <span style='font-size:0.8em; color:#8e44ad; font-weight:normal;'>[PR: {pr_val}]</span>"

                    output.append(f"<div style='margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #eee;'>")
                    output.append(f"  <div style='font-size: 1.05em; font-weight: bold; color: #2c3e50; margin-bottom: 4px;'>{horse_disp}</div>")

                    direct_html = []
                    hidden_html = []
                    
                    direct_race_ids = set()
                    for opp in list(component):
                        if horse == opp: continue
                        if G.has_edge(horse, opp) or G.has_edge(opp, horse):
                            edge = G[horse][opp] if G.has_edge(horse, opp) else G[opp][horse]
                            histories = edge.get('history', [])
                            if histories:
                                best_hist = sort_histories(histories, target_course, target_distance)[0]
                                direct_race_ids.add(best_hist.get('race_id'))
                            
                    if direct_race_ids:
                        direct_html.append("<div style='font-size:0.85em; font-weight:bold; color:#2980b9; margin-top:5px;'>🔍直接対決</div>")
                        race_infos = [next((r for r in past_races if r.get('race_id') == rid), None) for rid in direct_race_ids]
                        race_infos = [r for r in race_infos if r is not None]
                        race_infos.sort(key=lambda x: x.get('date', ''), reverse=True) 
                        
                        for rinfo in race_infos:
                            c_short = get_short_course_name(rinfo.get('course'), rinfo.get('distance'))
                            link = f"<a href='https://db.netkeiba.com/race/{rinfo.get('race_id')}' target='_blank' style='color:#3498db; text-decoration:none;'>{rinfo.get('date')} {c_short}</a>"
                            chain_str = format_direct_chain(horse, list(component), rinfo, is_banei, umaban_dict)
                            direct_html.append(f"<div style='font-size:0.85em; margin-left:8px; margin-bottom:3px;'>{link}<br>{chain_str}</div>")

                    for opp in list(component):
                        if horse == opp: continue
                        if not (G.has_edge(horse, opp) or G.has_edge(opp, horse)) and runner_G.has_edge(horse, opp):
                            try:
                                path = nx.shortest_path(runner_G, source=horse, target=opp, weight='explore_cost')
                                diff_val = adv_matrix[opp][horse] 
                                hidden_nodes = path[1:-1]
                                hidden_names = ",".join([f"[{umaban_dict.get(h,'?')}] {h}" for h in hidden_nodes])
                                
                                conds = []
                                for k in range(len(path) - 1):
                                    n1, n2 = path[k], path[k+1]
                                    edge = G[n1][n2] if G.has_edge(n1, n2) else G[n2][n1]
                                    best_hist = sort_histories(edge.get('history', []), target_course, target_distance)[0]
                                    conds.append(get_short_course_name(best_hist.get('course'), best_hist.get('distance')))
                                
                                unique_conds = []
                                for c in conds:
                                    if not unique_conds or unique_conds[-1] != c:
                                        unique_conds.append(c)
                                cond_str = "※" + "/".join(unique_conds)
                                
                                abs_diff = abs(diff_val)
                                sep = get_separator(abs_diff, is_banei)
                                opp_str = f"[{umaban_dict.get(opp, '?')}]{opp}"
                                
                                if diff_val >= 0:
                                    hidden_html.append(f"<div style='font-size:0.85em; margin-left:8px;'>[{hidden_names}] 本馬{sep}{opp_str}(+{abs_diff:.1f}) <span style='color:#7f8c8d;'>{cond_str}</span></div>")
                                else:
                                    hidden_html.append(f"<div style='font-size:0.85em; margin-left:8px;'>[{hidden_names}] {opp_str}(-{abs_diff:.1f}){sep}本馬 <span style='color:#7f8c8d;'>{cond_str}</span></div>")
                            except:
                                pass
                                
                    if hidden_html:
                        hidden_html.insert(0, "<div style='font-size:0.85em; font-weight:bold; color:#8e44ad; margin-top:5px;'>👻隠れ馬経由</div>")

                    output.extend(direct_html)
                    output.extend(hidden_html)
                    output.append("</div>")

            output.append("</div>")

    unranked = [h for h in umaban_dict.keys() if h not in all_ranked_horses]
    return "".join(output), all_ranked_scores, unranked, true_fastest, all_tier_map

# ==========================================
# 4. メインAPI: rank_horses()
# ==========================================
def rank_horses(race_id, mark_race_id=None):
    html_race_id = mark_race_id or race_id
    scraper = NetkeibaScraper()
    race_title, target_course, target_track, target_distance, past_races, umaban_dict = scraper.fetch_past5_data(race_id)

    is_banei = (target_track == "ばんえい")

    G_unified = build_unified_graph(past_races, target_course, target_track, target_distance, umaban_dict)
    pr_scores = calculate_pagerank(G_unified)
    result_html_content, track_scores, track_unranked, track_fastest, track_tier_map = analyze_all_horses_html(G_unified, past_races, umaban_dict, target_course, target_distance, pr_scores, html_race_id, is_banei=is_banei)

    ruler_ranks = {}
    for horse_name, diff in track_scores.items():
        diff = max(0.0, diff)
        tier = track_tier_map.get(horse_name, "C")
        score = RANK_SCORES["S+"] if tier == "S" and horse_name == track_fastest else RANK_SCORES.get(tier, 7)
        ruler_ranks[horse_name] = {"rank": tier, "score": score, "diff": round(diff, 2)}

    for horse_name in track_unranked:
        ruler_ranks[horse_name] = {"rank": "!", "score": RANK_SCORES["!"], "diff": -1}

    t_track_disp = f"大井({get_ooi_track(target_distance)}回り)" if target_course == '大井' else target_course
    ruler_html = (
        f"<h2 class='section-title' style='background-color: #2c3e50; color: white; padding: 10px 12px; border-radius: 6px; font-size: 1.05em; margin: 15px 0 10px 0;'>"
        f"📊 {t_track_disp} {target_distance}m 基準：能力序列<br>"
        f"<span style='font-size:0.75em; font-weight:normal; color:#bdc3c7;'>※PageRankによるトーナメント抽出。同列は0.5秒差未満に設定</span></h2>"
        f"{result_html_content}"
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
            if rid != base_race_id: race_ids.append(rid)
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

        G_unified = build_unified_graph(past_races, target_course, target_track, target_distance, umaban_dict)
        pr_scores = calculate_pagerank(G_unified)
        result_html_content, _, _, _, _ = analyze_all_horses_html(G_unified, past_races, umaban_dict, target_course, target_distance, pr_scores, is_banei=is_banei)

        water_note = ""
        if is_banei and water_mode:
            label = "1.9%以下（軽馬場）" if water_mode == 'dry' else "2.0%以上（重馬場）"
            water_note = f"<p style='text-align:center; color:#2980b9; font-size:13px; margin-bottom:15px;'>💧 水分量フィルタ: <strong>{label}</strong> のレースのみ使用</p>"

        t_track_disp = f"大井({get_ooi_track(target_distance)}回り)" if target_course == '大井' else target_course
        result_html = (
            f"{water_note}"
            f"<h2 class='section-title' style='background-color: #2c3e50; color: white; padding: 10px 12px; border-radius: 6px; font-size: 1.05em; margin: 15px 0 10px 0;'>"
            f"📊 {t_track_disp} {target_distance}m 基準：能力序列<br>"
            f"<span style='font-size:0.75em; font-weight:normal; color:#bdc3c7;'>※PageRankによるトーナメント抽出。同列は0.5秒差未満に設定</span></h2>"
            f"{result_html_content}"
        )
        return jsonify({'title': race_title, 'html': result_html})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
