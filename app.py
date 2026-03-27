import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import re
from datetime import datetime
import networkx as nx

# ==========================================
# 1. ユーティリティ・条件判定関数
# ==========================================
def parse_date(date_str):
    try:
        if '.' in date_str:
            parts = date_str.split('.')
            yy = int(parts[0])
            if yy < 100: yy += 2000
            return datetime(yy, int(parts[1]), int(parts[2]))
        elif '/' in date_str:
            parts = date_str.split('/')
            yy = int(parts[0])
            if yy < 100: yy += 2000
            return datetime(yy, int(parts[1]), int(parts[2]))
    except:
        pass
    return datetime.min

def get_ooi_layout(dist):
    try:
        d = int(dist)
        return "inner" if d in [1500, 1600, 1650] else "outer"
    except:
        return "outer"

def determine_condition(t_place, t_dist, r_place, r_dist):
    if t_place == r_place and str(t_dist) == str(r_dist):
        return 'A'
    if t_place == r_place:
        if t_place == "大井":
            if get_ooi_layout(t_dist) == get_ooi_layout(r_dist): return 'B'
            else: return 'C'
        return 'B'
    return 'C'

def get_rel_str(diff, cond, is_banei=False):
    abs_d = abs(diff)
    if is_banei:
        if abs_d >= 4.0: return ">>" if diff < 0 else "<<"
        if abs_d >= 1.5: return ">" if diff < 0 else "<"
        return "＝"
        
    if cond == 'A':
        if abs_d >= 1.1: return ">>" if diff < 0 else "<<"
        if abs_d >= 0.6: return ">" if diff < 0 else "<"
        return "＝"
    elif cond == 'B':
        if abs_d >= 1.3: return ">>" if diff < 0 else "<<"
        if abs_d >= 0.8: return ">" if diff < 0 else "<"
        return "＝"
    else: 
        if abs_d >= 1.5: return ">>" if diff < 0 else "<<"
        if abs_d >= 1.0: return ">" if diff < 0 else "<"
        return "＝"

def calculate_matchup_points(rel):
    if rel == ">>": return 3.0
    elif rel == ">": return 1.0
    elif rel == "＝": return 0.0
    elif rel == "<": return -1.0
    elif rel == "<<": return -3.0
    return 0.0

# ==========================================
# 2. Netkeiba ディープスクレイパー
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
            return float(time_str)
        except:
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
            c_match = re.search(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)', race_data02.text)
            if c_match: target_course = c_match.group(1)
        if race_data01:
            d_match = re.search(r'(\d{3,4})', race_data01.text)
            if d_match: target_distance = d_match.group(1)

        past_races_dict = {}
        umaban_dict = {}
        deep_dive_candidates = set()

        for tr in soup.find_all('tr', class_='HorseList'):
            name_tag = tr.find(class_='Horse02')
            if not name_tag: continue
            horse_name = name_tag.find('a').text.strip()

            tds = tr.find_all('td')
            if len(tds) > 1:
                umaban_dict[horse_name] = tds[1].text.strip()

            past_tds = tr.find_all('td', class_=re.compile(r'^Past'))
            # 直近3走程度をベースに取得
            for i, td in enumerate(past_tds[:4]):
                data01 = td.find('div', class_='Data01')
                data02_a = td.find('div', class_='Data02').find('a') if td.find('div', class_='Data02') else None
                data05 = td.find('div', class_='Data05')

                if data01 and data02_a and data05:
                    past_race_id = self.extract_race_id(data02_a['href'])
                    c_match = re.search(r'(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉|門別|盛岡|水沢|浦和|船橋|大井|川崎|金沢|笠松|名古屋|園田|姫路|高知|佐賀|帯広)', data01.text)
                    course = c_match.group(1) if c_match else "不明"
                    date_match = re.search(r'(\d{4})\.(\d{2})\.(\d{2})', data01.text)
                    r_date_str = f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}" if date_match else ""
                    d_match = re.search(r'(\d{3,4})', data05.text)
                    distance = d_match.group(1) if d_match else "不明"

                    if past_race_id not in past_races_dict:
                        past_races_dict[past_race_id] = {
                            'race_id': past_race_id, 'date_str': r_date_str, 'date': parse_date(r_date_str),
                            'course': course, 'distance': distance, 'horses': {}
                        }
                    deep_dive_candidates.add(past_race_id)

        for past_id in deep_dive_candidates:
            time.sleep(0.3)
            db_url = f"https://db.netkeiba.com/race/{past_id}/"
            res = requests.get(db_url, headers=self.headers)
            res.encoding = 'EUC-JP'
            db_soup = BeautifulSoup(res.text, 'html.parser')
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
                h_name = horse_link.text.strip() if horse_link else horse_cell.text.strip()

                time_str = None
                if len(tds) > 7: time_str = tds[7].text.strip()
                if not self.convert_time_to_sec(time_str):
                    for td in tds[4:]:
                        txt = td.text.strip()
                        if re.match(r'^\d{1,2}:\d{2}\.\d$', txt):
                            time_str = txt
                            break
                if not time_str: continue

                sec = self.convert_time_to_sec(time_str)
                if not sec: continue

                if winner_sec is None: winner_sec = sec
                time_behind = round(sec - winner_sec, 1)

                if past_id in past_races_dict:
                    past_races_dict[past_id]['horses'][h_name] = time_behind

        return race_title, target_course, target_distance, list(past_races_dict.values()), umaban_dict, is_banei

# ==========================================
# 3. 優先順位ベースのベストマッチ抽出
# ==========================================
def extract_best_matches(past_races, target_course, target_distance, umaban_dict, is_banei):
    runners = list(umaban_dict.keys())
    # 直接対決履歴の収集
    history = {u: {v: [] for v in runners} for u in runners}
    
    for race in past_races:
        cond = determine_condition(target_course, target_distance, race['course'], race['distance'])
        h_list = list(race['horses'].items())
        
        for i in range(len(h_list)):
            for j in range(i+1, len(h_list)):
                h1, t1 = h_list[i]
                h2, t2 = h_list[j]
                diff = t1 - t2 # h1基準: マイナスなら先着
                
                entry = {
                    'date': race['date'], 'date_str': race['date_str'],
                    'course': race['course'], 'distance': race['distance'],
                    'cond': cond, 'raw_diff': diff, 'rel': get_rel_str(diff, cond, is_banei)
                }
                
                if h1 in runners and h2 in runners:
                    history[h1][h2].append(entry)
                    history[h2][h1].append({**entry, 'raw_diff': -diff, 'rel': get_rel_str(-diff, cond, is_banei)})
                elif h1 in runners and h2 not in runners: # 隠れ馬用
                    if h2 not in history[h1]: history[h1][h2] = []
                    history[h1][h2].append(entry)
                elif h2 in runners and h1 not in runners:
                    if h1 not in history[h2]: history[h2][h1] = []
                    history[h2][h1].append({**entry, 'raw_diff': -diff, 'rel': get_rel_str(-diff, cond, is_banei)})

    best_matches = {u: {v: None for v in runners} for u in runners}
    
    # 優先順位でベストマッチを決定
    for u in runners:
        for v in runners:
            if u == v: continue
            
            directs = history[u].get(v, [])
            if directs:
                directs.sort(key=lambda x: x['date'], reverse=True)
                cond_a = [m for m in directs if m['cond'] == 'A']
                cond_b = [m for m in directs if m['cond'] == 'B']
                cond_c = [m for m in directs if m['cond'] == 'C']
                
                if cond_a: best_m = cond_a[0]
                elif cond_b: best_m = cond_b[0]
                else: best_m = cond_c[0]
                
                best_matches[u][v] = {**best_m, 'type': 'direct'}
                continue
            
            # 隠れ馬経由の探索
            indirects = []
            for h in history[u].keys():
                if h in runners: continue
                if h in history and v in history[h] and history[u][h] and history[h][v]:
                    # u->h と h->v の最新を使用
                    m_uh = sorted(history[u][h], key=lambda x: x['date'], reverse=True)[0]
                    m_hv = sorted(history[h][v], key=lambda x: x['date'], reverse=True)[0]
                    tot_diff = m_uh['raw_diff'] + m_hv['raw_diff']
                    rel = get_rel_str(tot_diff, 'C', is_banei)
                    
                    indirects.append({
                        'type': 'indirect', 'hidden_horse': h,
                        'date': min(m_uh['date'], m_hv['date']),
                        'raw_diff': tot_diff, 'rel': rel,
                        'cond': 'C', 'm_uh': m_uh, 'm_hv': m_hv
                    })
                    
            if indirects:
                # 隠れ馬ルートが複数あれば、日付が新しいものを優先
                indirects.sort(key=lambda x: x['date'], reverse=True)
                best_matches[u][v] = indirects[0]

    return best_matches

# ==========================================
# 4. リーグ戦評価と強制補正
# ==========================================
def evaluate_and_rank(best_matches, umaban_dict):
    runners = list(umaban_dict.keys())
    scores = {u: 0.0 for u in runners}
    counts = {u: 0 for u in runners}
    
    # スコア集計
    for u in runners:
        for v in runners:
            m = best_matches[u][v]
            if not m: continue
            
            pts = calculate_matchup_points(m['rel'])
            if m['type'] == 'indirect': pts *= 0.6 # 隠れ馬は影響を抑える
            
            scores[u] += pts
            counts[u] += 1
            
    final_scores = {u: (scores[u] / counts[u] if counts[u] > 0 else -999) for u in runners}
    ranked = sorted([(u, s) for u, s in final_scores.items() if s != -999], key=lambda x: x[1], reverse=True)
    
    tier_map = {u: "C" for u in runners}
    if ranked:
        top_s = ranked[0][1]
        for u, s in ranked:
            if s >= 1.5 and (top_s - s) <= 1.0: tier_map[u] = "S"
            elif s >= 0.5: tier_map[u] = "A"
            elif s >= -1.0: tier_map[u] = "B"
            else: tier_map[u] = "C"

    # 👑 強制補正ルール適用
    tier_val = {"S": 4, "A": 3, "B": 2, "C": 1}
    val_tier = {4: "S", 3: "A", 2: "B", 1: "C"}
    
    changed = True
    while changed:
        changed = False
        for u in runners:
            for v in runners:
                m = best_matches[u][v]
                if not m: continue
                
                t_u = tier_val[tier_map[u]]
                t_v = tier_val[tier_map[v]]
                
                # 絶対ルール1: 同競馬場同距離で「＝」なら同ランク
                if m['type'] == 'direct' and m['cond'] == 'A' and m['rel'] == '＝':
                    if t_u < t_v:
                        tier_map[u] = val_tier[t_v]
                        changed = True
                    elif t_v < t_u:
                        tier_map[v] = val_tier[t_u]
                        changed = True
                        
                # 絶対ルール2: ベストマッチで相手に勝っているなら、相手より下にならない
                if m['rel'] in ['>>', '>']:
                    if t_u < t_v:
                        tier_map[u] = val_tier[t_v]
                        changed = True

    unranked = [u for u in runners if final_scores[u] == -999]
    return tier_map, ranked, unranked

# ==========================================
# 5. プロ仕様HTMLレンダリング
# ==========================================
def build_html_output(tier_map, ranked, unranked, umaban_dict, best_matches):
    html = ["<div style='font-family: sans-serif; font-size:14px; color:#333;'>"]
    tier_colors = {"S": "#e74c3c", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    runners = list(umaban_dict.keys())
    
    for tier in ["S", "UNRANKED", "A", "B", "C"]:
        if tier == "UNRANKED":
            if unranked:
                html.append("<h3 style='background-color:#95a5a6; color:white; padding:8px; border-radius:4px;'>❗ 測定不能（別路線）</h3>")
                for u in unranked:
                    html.append(f"<div style='margin-bottom: 10px; border-left: 4px solid #95a5a6; padding-left: 10px;'>")
                    html.append(f"  <strong style='font-size:1.1em;'>[{umaban_dict.get(u, '?')}] {u}</strong>")
                    html.append("</div>")
            continue
            
        horses = [u for u, s in ranked if tier_map.get(u) == tier]
        if not horses: continue
        
        html.append(f"<h3 style='background-color:{tier_colors[tier]}; color:white; padding:8px; border-radius:4px;'>🏆 {tier}ランク</h3>")
        
        for u in horses:
            uma = umaban_dict.get(u, "?")
            html.append(f"<div style='margin-bottom: 15px; border-left: 4px solid {tier_colors[tier]}; padding-left: 10px;'>")
            html.append(f"  <strong style='font-size:1.1em;'>[{uma}] {u}</strong>")
            
            directs = []
            indirects = []
            
            for v in runners:
                m = best_matches[u][v]
                if not m: continue
                if m['type'] == 'direct':
                    directs.append((v, m))
                else:
                    indirects.append((v, m))
                    
            if directs:
                html.append(f"<div style='margin-top:5px; font-size:0.9em; font-weight:bold;'>直接対決 (ベストマッチ)</div>")
                # レースごとにグループ化
                race_groups = {}
                for v, m in directs:
                    k = (m['date_str'], m['course'], m['distance'], m['cond'])
                    if k not in race_groups: race_groups[k] = []
                    race_groups[k].append((v, m['rel'], m['raw_diff']))
                
                for (d_str, crs, dst, cond), items in sorted(race_groups.items(), key=lambda x: x[0][0], reverse=True):
                    badge = " <span style='color:#e67e22;'>[同条件]</span>" if cond == 'A' else " <span style='color:#2980b9;'>[同形態]</span>" if cond == 'B' else ""
                    html.append(f"<div style='margin-left:10px; font-size:0.85em; color:#555; margin-top:3px;'>🔍{d_str}の{crs}{dst}{badge}</div>")
                    
                    for v, rel, diff in items:
                        v_uma = umaban_dict.get(v, "?")
                        color = "#27ae60" if ">" in rel else "#888" if rel == "＝" else "#e74c3c"
                        html.append(f"<div style='margin-left:20px; font-size:0.85em;'>└ 本馬 <span style='color:{color}; font-weight:bold;'>{rel}</span> [{v_uma}]{v}({diff:+.1f})</div>")

            if indirects:
                html.append(f"<div style='margin-left:10px; margin-top:6px; font-size:0.85em; font-weight:bold; color:#8e44ad;'>🔗 隠れ馬経由の比較</div>")
                for v, m in indirects:
                    rel = m['rel']
                    color = "#27ae60" if ">" in rel else "#888" if rel == "＝" else "#e74c3c"
                    v_uma = umaban_dict.get(v, "?")
                    h = m['hidden_horse']
                    c1 = f"{m['m_uh']['course'][0]}{m['m_uh']['distance']}"
                    c2 = f"{m['m_hv']['course'][0]}{m['m_hv']['distance']}"
                    
                    html.append(f"<div style='margin-left:20px; font-size:0.85em;'>[{h}] 本馬 <span style='color:{color}; font-weight:bold;'>{rel}</span> [{v_uma}]{v}({m['raw_diff']:+.1f}) ※{c1}/{c2}</div>")
            
            html.append("</div>")

    html.append("</div>")
    return "".join(html)

def wrap_combined_html(results_list):
    tabs, contents = "", ""
    for i, (r_num, r_title, content) in enumerate(results_list):
        active = "active" if i == 0 else ""
        tabs += f'<button class="tab-btn {active}" onclick="openTab(event, \'race_{r_num}\')">{r_num}R</button>\n'
        contents += f'<div id="race_{r_num}" class="tab-content {active}"><h2 class="race-title">📊 {r_title}</h2>{content}</div>'

    return f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><style>
    body {{ font-family: sans-serif; background: #f7f6f2; padding: 20px; }}
    .container {{ background: #fff; padding: 20px; border-radius: 8px; max-width: 900px; margin: auto; }}
    .tab-buttons {{ display: flex; gap: 5px; border-bottom: 2px solid #3498db; margin-bottom: 20px; }}
    .tab-btn {{ padding: 10px 16px; border: none; background: #ecf0f1; cursor: pointer; }}
    .tab-btn.active {{ background: #3498db; color: white; }}
    .tab-content {{ display: none; }}
    .tab-content.active {{ display: block; }}
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
# 6. Streamlit UI
# ==========================================
st.set_page_config(page_title="競馬AI 究極相対評価", page_icon="🏇")

st.title("🏇 競馬AI 究極相対評価 (ベストマッチ＆隠れ馬版)")
st.caption("優先順位（同競馬場同距離が最優先）に基づいて各馬の「決定的な1戦」を抽出し、完璧な序列を組み上げます。")

url_input = st.text_input("netkeibaのレースURL", placeholder="https://race.netkeiba.com/race/result.html?race_id=202405020111")
water_mode = st.selectbox("水分量フィルタ（ばんえい専用）", ["なし", "軽馬場（dry）", "重馬場（wet）"])

st.markdown("---")
selected_races = [i for i in range(1, 13) if st.checkbox(f"{i}R", key=f"chk_{i}")]
submitted = st.button("🚀 分析を開始", type="primary")

if submitted and url_input:
    scraper = NetkeibaScraper()
    base_id = scraper.extract_race_id(url_input)
    if not base_id: st.stop()
    if not selected_races: selected_races = [int(base_id[-2:])]

    wmode = "dry" if "軽" in water_mode else "wet" if "重" in water_mode else None
    results = []
    
    progress = st.progress(0)
    for idx, r in enumerate(selected_races):
        rid = f"{base_id[:10]}{r:02d}"
        
        try:
            r_title, t_course, t_dist, past_races, uma_dict, is_banei = scraper.fetch_past_data(rid, wmode)
            if not uma_dict:
                results.append((r, f"{r}R (出走なし)", "データなし"))
                continue
                
            best_matches = extract_best_matches(past_races, t_course, t_dist, uma_dict, is_banei)
            tier_map, ranked, unranked = evaluate_and_rank(best_matches, uma_dict)
            html_out = build_html_output(tier_map, ranked, unranked, uma_dict, best_matches)
            
            results.append((r, r_title, html_out))
        except Exception as e:
            results.append((r, f"{r}R (エラー)", str(e)))
            
        progress.progress((idx + 1) / len(selected_races))

    st.success("✅ 分析完了！")
    st.download_button("📥 HTML一括ダウンロード", wrap_combined_html(results), file_name=f"究極評価_{base_id[:10]}.html", mime="text/html")
    
    tabs = st.tabs([f"{r[0]}R" for r in results])
    for tab, (r_num, r_title, r_html) in zip(tabs, results):
        with tab:
            st.markdown(r_html, unsafe_allow_html=True)
