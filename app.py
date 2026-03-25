import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
import networkx as nx
import html
from datetime import datetime

# ==========================================
# 0. ユーティリティ・コース判定・鮮度判定関数
# ==========================================
_CIRCLED_NUMS = '⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'

def _to_circled(n):
    try:
        n = int(n)
        return _CIRCLED_NUMS[n] if 0 <= n <= 20 else f'({n})'
    except (ValueError, TypeError):
        return str(n)

def _is_ooi_inner(dist):
    d_int = int(dist) if str(dist).isdigit() else 0
    return d_int in [1500, 1600, 1650]

def _is_ooi_outer(dist):
    d_int = int(dist) if str(dist).isdigit() else 0
    return d_int > 0 and d_int not in [1500, 1600, 1650]

def _is_one_turn(place, dist):
    """南関東の完全な1ターンコースと、JRAの代表的な1ターンコースを定義"""
    d = int(dist) if str(dist).isdigit() else 0
    if place == "川崎" and d == 900: return True
    if place == "浦和" and d == 800: return True
    if place == "船橋" and d in [1000, 1200]: return True
    if place == "大井" and d in [1000, 1200, 1400]: return True
    if place == "東京" and d <= 1800: return True
    if place == "新潟" and d <= 1800: return True
    if place == "中京" and d <= 1600: return True
    if place == "阪神" and d in [1200, 1400, 1600, 1800]: return True
    if place == "京都" and d in [1200, 1400, 1600, 1800]: return True
    return False

def get_track_group(place):
    """相互乗り入れする地方競馬場を「同じ場所」としてグループ化"""
    groups = {
        "姫路": "兵庫", "園田": "兵庫",
        "盛岡": "岩手", "水沢": "岩手",
        "笠松": "東海", "名古屋": "東海"
    }
    return groups.get(place, place)

def _is_same_track_layout(place, dist1, dist2):
    """競馬場と距離から、コース形態が一致するか判定"""
    d1 = int(dist1) if str(dist1).isdigit() else 0
    d2 = int(dist2) if str(dist2).isdigit() else 0
    
    if place == "大井":
        def get_ooi_layout(d):
            if d <= 1400: return "outer_1turn"
            if d <= 1650: return "inner_2turn"
            return "outer_2turn"
        return get_ooi_layout(d1) == get_ooi_layout(d2)
    elif place == "川崎":
        def get_kawasaki_layout(d):
            if d == 900: return "1turn"
            if d <= 1600: return "2turn"
            return "multi_turn"
        return get_kawasaki_layout(d1) == get_kawasaki_layout(d2)
    elif place == "船橋":
        def get_funabashi_layout(d):
            if d <= 1200: return "1turn"
            if d <= 1800: return "2turn"
            return "multi_turn"
        return get_funabashi_layout(d1) == get_funabashi_layout(d2)
    elif place == "浦和":
        def get_urawa_layout(d):
            if d <= 800: return "1turn"
            if d <= 1500: return "2turn"
            return "multi_turn"
        return get_urawa_layout(d1) == get_urawa_layout(d2)
    
    JRA_PLACES = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]
    if place in JRA_PLACES:
        return abs(d1 - d2) <= 200  # JRAは200mの差を許容
    elif place == "帯広":
        return True  # ばんえいは基本200mなので距離比較不要
    else:
        # 南関・JRA以外の地方競馬は「同距離至上主義」（完全一致のみ許容）
        return d1 == d2

def get_short_course_name(course, dist):
    if not course: return "?"
    if course == '大井':
        return f"大{'内' if _is_ooi_inner(dist) else '外'}{dist}"
    else:
        return f"{course[0:1]}{dist}"

def calculate_time_decay(race_date_str):
    """鮮度の評価：古いレースほど価値（ポイント）を割り引く"""
    # 日付が取得できなかった場合は、不当に評価を下げないよう1.0倍を返す
    if not race_date_str or race_date_str == "不明": return 1.0 
    try:
        parts = race_date_str.split('/')
        if len(parts) == 3:
            yy = int(parts[0])
            yy = yy + 2000 if yy < 100 else yy
            r_date = datetime(yy, int(parts[1]), int(parts[2]))
            days = (datetime.now() - r_date).days
            if days <= 60: return 1.0       
            elif days <= 120: return 0.8    
            elif days <= 180: return 0.6    
            else: return 0.4                
    except:
        pass
    return 1.0

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
                if m: return float(m.group(1))
        except:
            pass
        return None

    def fetch_past_data(self, race_id, water_mode=None):
        is_nar = self._is_nar_race(race_id)
        url_domain = "nar.netkeiba.com" if is_nar else "race.netkeiba.com"
        url = f"https://{url_domain}/race/shutuba_past.html?race_id={race_id}"
        time.sleep(0.5)
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
                
                if data01 and data02_a:
                    past_race_id = self.extract_race_id(data02_a['href'])
                    course_match = re.search(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)', data01.text)
                    course = course_match.group(1) if course_match else "不明"

                    # 地方の2桁年や / 表記にも対応
                    date_match = re.search(r'(\d{2,4})[./年](\d{1,2})[./月](\d{1,2})', data01.text)
                    race_date = "不明"
                    if date_match:
                        yy = int(date_match.group(1))
                        if yy < 100: yy += 2000
                        mm = int(date_match.group(2))
                        dd = int(date_match.group(3))
                        race_date = f"{yy}/{mm:02d}/{dd:02d}"

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
                            'url': f"https://db.netkeiba.com/race/{past_race_id}/",
                            'date': race_date, 'place': course,
                            'track_type': track_type, 'dist': distance,
                            'is_banei': (course == '帯広'), 'horses': {}
                        }

                    my_time_behind = abs(my_time_diff) if my_rank > 1 and my_time_diff >= 0 else 0.0
                    ref_time_behind = 0.0 if my_rank > 1 and my_time_diff >= 0 else abs(my_time_diff)

                    past_races_dict[past_race_id]['horses'][horse_name] = my_time_behind
                    
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
                time.sleep(0.2)
                wc = self._fetch_water_content(past_race_id)
                if wc is not None:
                    if water_mode == 'dry' and wc > 1.9: filtered_out.add(past_race_id)
                    elif water_mode == 'wet' and wc <= 1.9: filtered_out.add(past_race_id)
            for rid in filtered_out:
                del past_races_dict[rid]
                deep_dive_candidates.pop(rid, None)

        for past_id in deep_dive_candidates.keys():
            time.sleep(0.3)
            db_url = f"https://db.netkeiba.com/race/{past_id}/"
            res = requests.get(db_url, headers=self.headers)
            res.encoding = 'EUC-JP'
            db_soup = BeautifulSoup(res.text, 'html.parser')
            result_table = db_soup.find('table', class_='race_table_01')

            if not result_table and self._is_nar_race(past_id):
                time.sleep(0.3)
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

                horse_cell = tds[3]
                horse_link = horse_cell.find('a')
                hidden_horse_name = horse_link.text.strip() if horse_link else horse_cell.text.strip()

                time_str = None
                if len(tds) > 7:
                    time_str = tds[7].text.strip()
                    if not self.convert_time_to_sec(time_str): time_str = None
                if time_str is None:
                    for td in tds[4:]:
                        txt = td.text.strip()
                        if re.match(r'^\d{1,2}:\d{2}\.\d$', txt):
                            time_str = txt
                            break
                if time_str is None: continue
                sec = self.convert_time_to_sec(time_str)
                if sec is None: continue

                if winner_sec is None: winner_sec = sec
                time_behind = round(sec - winner_sec, 1)

                if past_id in past_races_dict:
                    past_races_dict[past_id]['horses'][hidden_horse_name] = time_behind

        return race_title, target_course, target_track, target_distance, past_races_dict, umaban_dict


# ==========================================
# 2. 完全版 理論に基づく相対評価・ランク付け
# ==========================================
def calculate_relative_scores_advanced(past_races_dict, current_course, current_track, current_dist, umaban_dict, is_banei):
    current_names = list(umaban_dict.keys())
    G = nx.DiGraph()

    cur_dist = int(current_dist) if str(current_dist).isdigit() else 0
    cap_val = 30.0 if is_banei else 1.5

    for race_id, race in past_races_dict.items():
        if race.get('track_type') != current_track: continue
        
        r_place = race.get('place', '')
        r_dist_str = str(race.get('dist', ''))
        r_date = race.get('date', '')
        r_url = race.get('url', '')
        
        horses_in_race = race.get('horses', {})
        names = list(horses_in_race.keys())
        
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                h1, h2 = names[i], names[j]
                h1_is_current = h1 in current_names
                h2_is_current = h2 in current_names
                
                if not h1_is_current and not h2_is_current: continue
                
                raw_diff = horses_in_race[h1] - horses_in_race[h2]
                if h1 > h2:
                    h1, h2 = h2, h1
                    raw_diff = -raw_diff

                capped_diff = raw_diff
                if capped_diff > cap_val: capped_diff = cap_val
                elif capped_diff < -cap_val: capped_diff = -cap_val

                history_item = {
                    "date": r_date, "place": r_place, "dist": r_dist_str,
                    "raw_diff": capped_diff, "url": r_url,
                }

                if G.has_edge(h1, h2):
                    G[h1][h2]["history"].append(history_item)
                else:
                    G.add_edge(h1, h2, history=[history_item])

    pair_net = {u: {v: [] for v in current_names} for u in current_names}

    for u in current_names:
        for v in current_names:
            if u == v: continue

            direct_entries = []
            if G.has_edge(u, v):
                for hi in G[u][v]["history"]:
                    direct_entries.append((-hi["raw_diff"], hi.get("date", ""), hi.get("place", ""), hi.get("dist", "")))
            if G.has_edge(v, u):
                for hi in G[v][u]["history"]:
                    direct_entries.append((hi["raw_diff"], hi.get("date", ""), hi.get("place", ""), hi.get("dist", "")))

            if direct_entries:
                same_cond = []
                other_cond = []
                for diff, dt, place, dist in direct_entries:
                    if _is_one_turn(current_course, cur_dist) and not _is_one_turn(place, dist): continue
                    
                    dist_int = int(dist) if str(dist).isdigit() else 0
                    
                    # グループ判定（姫路と園田などは同じ場所として扱う）
                    if get_track_group(place) == get_track_group(current_course) and dist_int == cur_dist:
                        same_cond.append((diff, dt, place, dist))
                    else:
                        other_cond.append((diff, place, dist))

                if same_cond:
                    if len(same_cond) == 1:
                        pair_net[u][v].append((same_cond[0][0], True, same_cond[0][2], same_cond[0][3], True, same_cond[0][1]))
                    else:
                        same_cond.sort(key=lambda x: x[1], reverse=True)
                        decay = 0.3
                        weights = [decay ** i for i in range(len(same_cond))]
                        w_sum = sum(weights)
                        weighted = sum(d * w for (d, _, _, _), w in zip(same_cond, weights)) / w_sum
                        pair_net[u][v].append((weighted, True, same_cond[0][2], same_cond[0][3], True, same_cond[0][1]))
                else:
                    if other_cond:
                        max_other_item = max(other_cond, key=lambda x: x[0])
                        max_other = max_other_item[0]
                        if max_other > 1.0:
                            pair_net[u][v].append((max_other * 0.4, False, max_other_item[1], max_other_item[2], True, "不明"))
                        for oc in other_cond:
                            pair_net[u][v].append((oc[0], False, oc[1], oc[2], True, "不明"))

            hidden_nodes = [n for n in G.nodes() if n not in current_names]
            for h in hidden_nodes:
                u_h_hist = []
                if G.has_edge(u, h): u_h_hist.extend([(-hi["raw_diff"], hi["place"], hi["dist"], hi["date"]) for hi in G[u][h]["history"]])
                if G.has_edge(h, u): u_h_hist.extend([(hi["raw_diff"], hi["place"], hi["dist"], hi["date"]) for hi in G[h][u]["history"]])

                h_v_hist = []
                if G.has_edge(h, v): h_v_hist.extend([(-hi["raw_diff"], hi["place"], hi["dist"], hi["date"]) for hi in G[h][v]["history"]])
                if G.has_edge(v, h): h_v_hist.extend([(hi["raw_diff"], hi["place"], hi["dist"], hi["date"]) for hi in G[v][h]["history"]])

                strict_diffs = []
                loose_diffs = []
                for diff_uh, p_uh, d_uh, dt_uh in u_h_hist:
                    for diff_hv, p_hv, d_hv, dt_hv in h_v_hist:
                        if p_uh == "大井" and p_hv == "大井":
                            if (_is_ooi_inner(d_uh) and _is_ooi_outer(d_hv)) or (_is_ooi_outer(d_uh) and _is_ooi_inner(d_hv)): continue
                        
                        if _is_one_turn(current_course, cur_dist):
                            if not _is_one_turn(p_uh, d_uh) or not _is_one_turn(p_hv, d_hv): continue

                        date_to_use = dt_uh if dt_uh > dt_hv else dt_hv
                        
                        # グループ判定（姫路と園田などは同じ場所として扱う）
                        if get_track_group(p_uh) == get_track_group(p_hv) and _is_same_track_layout(p_uh, d_uh, d_hv):
                            strict_diffs.append((diff_uh + diff_hv, p_uh, d_uh, date_to_use))
                        else:
                            loose_diffs.append((diff_uh + diff_hv, p_uh, d_uh, date_to_use))

                if strict_diffs:
                    raw_hidden_diff = sum(x[0] for x in strict_diffs) / len(strict_diffs)
                    discounted_diff = raw_hidden_diff * 0.8  
                    pair_net[u][v].append((discounted_diff, True, strict_diffs[0][1], strict_diffs[0][2], False, strict_diffs[0][3]))
                elif loose_diffs:
                    raw_hidden_diff = sum(x[0] for x in loose_diffs) / len(loose_diffs)
                    discounted_diff = raw_hidden_diff * 0.5  
                    pair_net[u][v].append((discounted_diff, False, loose_diffs[0][1], loose_diffs[0][2], False, loose_diffs[0][3]))

    matchup_matrix = {u: {} for u in current_names}
    is_direct_matrix = {u: {} for u in current_names}
    date_matrix = {u: {} for u in current_names}
    
    comparable_horses = set()
    mult = 10.0 if is_banei else 1.0

    for u in current_names:
        for v in current_names:
            if u == v or not pair_net[u][v]: continue
            
            comparable_horses.add(u)
            comparable_horses.add(v)
            
            best_diff = -999
            best_is_strict = True
            is_forgiven = False
            best_is_direct = False
            best_date = "不明"
            
            for diff, is_strict, p_place, p_dist, is_dir, r_date in pair_net[u][v]:
                if diff > best_diff:
                    best_diff = diff
                    best_is_strict = is_strict
                    best_is_direct = is_dir
                    best_date = r_date
                    
                    if current_course == "大井" and _is_ooi_outer(cur_dist):
                        if p_place == "大井" and _is_ooi_inner(p_dist) and diff < 0: is_forgiven = True
                        else: is_forgiven = False
                    else:
                        is_forgiven = False
                        
            is_direct_matrix[u][v] = best_is_direct
            date_matrix[u][v] = best_date
            
            draw_th = (0.5 if best_is_strict else 0.7) * mult
            strong_th = (1.0 if best_is_strict else 1.2) * mult
                
            if best_diff >= strong_th: matchup_matrix[u][v] = ">>"
            elif best_diff > draw_th: matchup_matrix[u][v] = ">"
            elif best_diff >= -draw_th: matchup_matrix[u][v] = "="
            elif best_diff > -strong_th: matchup_matrix[u][v] = "=" if is_forgiven else "<"
            else: matchup_matrix[u][v] = "=" if is_forgiven else "<<"

    pool = list(comparable_horses)
    all_tiers = {u: None for u in current_names}
    
    if pool:
        horse_points = {}
        for u in pool:
            pts = 0.0
            count = 0
            for v in pool:
                if u == v: continue
                rel = matchup_matrix[u].get(v)
                is_dir = is_direct_matrix[u].get(v, False)
                r_date = date_matrix[u].get(v, "不明")
                
                if rel:
                    count += 1
                    pts_add = 0.0
                    if rel == ">>": pts_add = 3.0
                    elif rel == ">": pts_add = 1.5
                    elif rel == "=": pts_add = 0.0
                    elif rel == "<": pts_add = -1.5
                    elif rel == "<<": pts_add = -3.0
                    
                    if not is_dir: pts_add *= 0.8
                    
                    decay = calculate_time_decay(r_date)
                    pts_add *= decay
                    pts += pts_add
            
            avg_pts = (pts / count) + (count * 0.1) if count > 0 else 0
            horse_points[u] = avg_pts

        ranked_pool = sorted(horse_points.items(), key=lambda x: x[1], reverse=True)
        top_score = ranked_pool[0][1]
        
        # 【修正】絶対スコア基準によるランク分け（インフレ防止）
        for h, score in ranked_pool:
            diff_from_top = top_score - score
            
            if score >= 1.0 and diff_from_top <= 0.8: all_tiers[h] = "S"
            elif score >= 0.0: all_tiers[h] = "A"
            elif score >= -1.0: all_tiers[h] = "B"
            else: all_tiers[h] = "C"
                
        # 【修正】下剋上防止ロジック（降格型：矛盾を見つけたら負けた方を下げる）
        tier_val = {"S": 4, "A": 3, "B": 2, "C": 1}
        val_tier = {4: "S", 3: "A", 2: "B", 1: "C"}
        
        for _ in range(3): 
            changed = False
            for u in pool:
                for v in pool:
                    if u == v: continue
                    rel = matchup_matrix[u].get(v)
                    if rel in [">>", ">"]:
                        t_u = tier_val[all_tiers[u]]
                        t_v = tier_val[all_tiers[v]]
                        if t_u <= t_v:
                            rel_reverse = matchup_matrix[v].get(u)
                            if rel_reverse not in [">>", ">"]:
                                # 基本は負けた方を1ランク下げる。すでにCランクなら勝った方を上げる
                                if t_v > 1:
                                    all_tiers[v] = val_tier[t_v - 1]
                                    changed = True
                                elif t_u < 4:
                                    all_tiers[u] = val_tier[t_u + 1]
                                    changed = True
            if not changed: break

    return all_tiers, pair_net, G

# ==========================================
# 3. 個別HTML出力ジェネレータ
# ==========================================
def build_html_output(all_tiers, pair_net, umaban_dict, current_course, current_dist, is_banei, G):
    tier_colors = {"S": "#e74c3c", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    
    html_parts = []
    html_parts.append("<div style='font-family: sans-serif; font-size:14px; line-height:1.6; color:#333;'>")
    
    no_compare_horses = [(u, n) for n, u in umaban_dict.items() if all_tiers.get(n) is None]
    current_names = list(umaban_dict.keys())
    mult = 10.0 if is_banei else 1.0

    def _diff_symbol_and_color(adv, is_same_condition=True, is_strict=True):
        draw_limit = (0.5 if is_same_condition and is_strict else 0.7) * mult
        strong_limit = (1.0 if is_same_condition and is_strict else 1.2) * mult
        if adv >= strong_limit: return "≫", "#27ae60"
        elif adv > draw_limit: return "＞", "#27ae60"
        elif -draw_limit <= adv <= draw_limit: return "＝", "#888"
        elif adv > -strong_limit: return "＜", "#e74c3c"
        else: return "≪", "#e74c3c"

    def fmt_opps_by_tier(opps_list, is_same_condition=True, is_strict=True):
        draw_limit = (0.5 if is_same_condition and is_strict else 0.7) * mult
        strong_limit = (1.0 if is_same_condition and is_strict else 1.2) * mult
            
        dominant  = [(u, a) for u, a in opps_list if a >= strong_limit]
        advantage = [(u, a) for u, a in opps_list if draw_limit < a < strong_limit]
        even      = [(u, a) for u, a in opps_list if -draw_limit <= a <= draw_limit]
        behind    = [(u, a) for u, a in opps_list if -strong_limit < a < -draw_limit]
        far_behind= [(u, a) for u, a in opps_list if a <= -strong_limit]

        parts = []
        def _fmt(lst, color):
            return "".join([f"<span style='color:{color};'>[{u}]({a:+.1f})</span>" for u, a in sorted(lst, key=lambda x: abs(x[1]), reverse=True)])

        if dominant: parts.append(f"本馬 ≫ {_fmt(dominant, '#27ae60')}")
        if advantage: parts.append(f"本馬 ＞ {_fmt(advantage, '#27ae60')}")
        if even: parts.append(f"本馬 ＝ {_fmt(even, '#888')}")
        if behind: parts.append(f"本馬 ＜ {_fmt(behind, '#e74c3c')}")
        if far_behind: parts.append(f"本馬 ≪ {_fmt(far_behind, '#e74c3c')}")
        return " / ".join(parts)

    def _render_horse_block(hname, umaban):
        parts = []
        parts.append(f"<div style='font-size:1.1em; font-weight:bold; color:#2c3e50; border-left:4px solid #3498db; padding-left:8px; margin: 15px 0 5px;'>[{umaban}] {hname}</div>")

        race_groups = {}
        for opp_n in current_names:
            if hname == opp_n: continue
            if not pair_net[hname][opp_n]: continue
            
            for diff, is_strict, p_place, p_dist, is_dir, r_date in pair_net[hname][opp_n]:
                if is_dir:
                    r_key = (r_date, p_place, p_dist)
                    if r_key not in race_groups: race_groups[r_key] = []
                    opp_u = umaban_dict.get(opp_n, "?")
                    race_groups[r_key].append((opp_u, diff))
                    break 

        wins, draws, losses = 0, 0, 0
        for r_key, opps in race_groups.items():
            for _, a in opps:
                if a > 0.5 * mult: wins += 1
                elif a < -0.5 * mult: losses += 1
                else: draws += 1
        
        if wins + draws + losses > 0:
            sp = []
            if wins: sp.append(f"<span style='color:#27ae60; font-weight:bold;'>{wins}勝</span>")
            if draws: sp.append(f"<span style='color:#888;'>{draws}分</span>")
            if losses: sp.append(f"<span style='color:#e74c3c; font-weight:bold;'>{losses}敗</span>")
            parts.append(f"<div style='margin-left:15px; font-size:0.9em;'>直接対決: {' '.join(sp)}</div>")

        for (r_date, r_place, r_dist), opps in sorted(race_groups.items(), key=lambda x: x[0][0], reverse=True):
            if not opps: continue
            is_match = (get_track_group(r_place) == get_track_group(current_course) and str(r_dist) == str(current_dist))
            style = "background:#fff9c4; border-left:3px solid #fbc02d; padding-left:5px;" if is_match else ""
            badge = " <span style='color:#fbc02d; font-weight:bold;'>[同条件]</span>" if is_match else ""
            
            race_label = f"🔍{r_date}の{r_place}{r_dist}"
            rel_str = fmt_opps_by_tier(opps, is_same_condition=is_match, is_strict=True)

            parts.append(f"<div style='margin-left:15px; font-size:0.85em; {style}'>{race_label}{badge}</div>")
            parts.append(f"<div style='margin-left:30px; font-size:0.85em; {style}'>└ {rel_str}</div>")

        hidden_comparisons = []
        for opp_n in current_names:
            if hname == opp_n: continue
            if not pair_net[hname][opp_n]: continue
            
            for diff, is_strict, p_place, p_dist, is_dir, r_date in pair_net[hname][opp_n]:
                if not is_dir:
                    opp_u = umaban_dict.get(opp_n, "?")
                    is_same_cond = (get_track_group(p_place) == get_track_group(current_course) and str(p_dist) == str(current_dist))
                    c_label = f"{p_place}{p_dist}"
                    if not is_strict: c_label = f"条件違({c_label})"
                    
                    hidden_comparisons.append((opp_u, opp_n, diff, is_same_cond, c_label, is_strict))
                    break

        if hidden_comparisons:
            hidden_comparisons.sort(key=lambda x: (x[3], x[5], x[2]), reverse=True)
            parts.append(f"<div style='margin-left:15px; font-size:0.85em; color:#8e44ad; margin-top:5px; font-weight:bold;'>🔗 隠れ馬経由の比較:</div>")
            for opp_u, opp_n, est, is_same_cond, place_dist, is_strict in hidden_comparisons:
                sym, color = _diff_symbol_and_color(est, is_same_condition=is_same_cond, is_strict=is_strict)
                parts.append(
                    f"<div style='margin-left:30px; font-size:0.85em;'>"
                    f"<span style='color:{color};'>本馬{sym}[{opp_u}]{opp_n}({est:+.1f})</span>"
                    f"<span style='color:#888;'> ※{place_dist}</span></div>"
                )

        return "\n".join(parts)

    for tier in ["S", "！", "A", "B", "C"]:
        if tier == "！":
            if not no_compare_horses: continue
            html_parts.append("<h4 style='background:#95a5a6; color:#fff; padding:6px; border-radius:4px; margin-top:20px;'>！：比較不可（直接・隠れ馬なし）</h4>")
            for umaban, hname in sorted(no_compare_horses, key=lambda x: int(x[0])):
                html_parts.append(f"<div style='font-size:1.1em; font-weight:bold; color:#7f8c8d; border-left:4px solid #bdc3c7; padding-left:8px; margin: 10px 0 5px;'>[{umaban}] {hname}</div>")
        else:
            horses = [(umaban_dict.get(n, "?"), n) for n, t in all_tiers.items() if t == tier]
            if not horses: continue
            bg_color = tier_colors[tier]
            html_parts.append(f"<h4 style='background:{bg_color}; color:#fff; padding:6px; border-radius:4px; margin-top:20px;'>{tier}ランク</h4>")
            for umaban, hname in sorted(horses, key=lambda x: int(x[0]) if x[0].isdigit() else 99):
                html_parts.append(_render_horse_block(hname, umaban))

    html_parts.append("</div>")
    return "\n".join(html_parts)


# ==========================================
# 4. 一括HTML出力用ラップ関数 (ダウンロード用)
# ==========================================
def wrap_combined_html(results_list):
    """
    複数のレースのHTMLを、1つのタブ切り替え可能なHTMLファイルにまとめる
    """
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

st.title("🏇 競馬AI 相対評価ツール (JRA/地方対応)")
st.caption("NetkeibaのレースURLから、最新の「1ターン縛り」「時間減衰」「試合数ボーナス」「同距離至上主義（地方競馬）」等を用いた相対序列を出力します。")

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
    
    st.info(f"全 {len(race_ids_to_process)} レースのデータを収集・AI分析しています...")
    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, rid in enumerate(race_ids_to_process):
        r_num = int(rid[-2:])
        status_text.text(f"処理中: {r_num}R ... ({idx+1}/{len(race_ids_to_process)})")
        
        try:
            race_title, target_course, target_track, target_distance, past_races_dict, umaban_dict = scraper.fetch_past_data(rid, water_mode=wmode)
            is_banei = (target_track == "ばんえい")

            if not umaban_dict:
                results_list.append((r_num, f"{r_num}R (出走馬なし)", "<p>データが取得できませんでした。</p>"))
                continue

            all_tiers, pair_net, G = calculate_relative_scores_advanced(past_races_dict, target_course, target_track, target_distance, umaban_dict, is_banei)
            content_html = build_html_output(all_tiers, pair_net, umaban_dict, target_course, target_distance, is_banei, G)
            
            results_list.append((r_num, race_title, content_html))
            
        except Exception as e:
            results_list.append((r_num, f"{r_num}R (エラー)", f"<p style='color:red;'>エラー発生: {e}</p>"))
            
        progress_bar.progress((idx + 1) / len(race_ids_to_process))

    status_text.success("✅ すべての分析が完了しました！")

    combined_html_doc = wrap_combined_html(results_list)
    
    st.download_button(
        label="📥 分析結果をHTMLファイルとして一括保存（ダウンロード）",
        data=combined_html_doc,
        file_name=f"競馬AI分析結果_{base_prefix}.html",
        mime="text/html",
        type="primary"
    )
    st.markdown("---")

    tabs = st.tabs([f"{r[0]}R" for r in results_list])
    for tab, (r_num, r_title, r_html) in zip(tabs, results_list):
        with tab:
            st.subheader(f"📊 {r_title}")
            st.markdown(r_html, unsafe_allow_html=True)
