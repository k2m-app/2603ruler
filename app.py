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
    return None

def is_older_than_1_year(r_date):
    if not r_date: return False
    now = datetime.now()
    return (now - r_date).days > 365

def get_ooi_layout(dist):
    try:
        d = int(dist)
        return "inner" if d in [1500, 1600, 1650] else "outer"
    except:
        return "outer"

def determine_condition(t_place, t_dist, r_place, r_dist):
    """ 条件A(同条件), 条件B(同競馬場同形態・異距離), 条件C(他競馬場または異形態) を判定 """
    if t_place == r_place and str(t_dist) == str(r_dist):
        return 'A'
    
    if t_place == r_place:
        if t_place == "大井":
            if get_ooi_layout(t_dist) == get_ooi_layout(r_dist):
                return 'B'
            else:
                return 'C' # 大井の内・外違いは条件C
        return 'B'
    
    return 'C'

def get_rel_str(diff, cond, is_banei=False):
    """ 着差から関係性文字列(>>, >, ＝, <, <<)を取得 """
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
    else: # cond == 'C'
        if abs_d >= 1.5: return ">>" if diff < 0 else "<<"
        if abs_d >= 1.0: return ">" if diff < 0 else "<"
        return "＝"

def calculate_matchup_points(diff, cond, is_banei=False):
    """ 着差と条件から基礎ポイントを算出 (負けの場合はマイナス) """
    rel = get_rel_str(diff, cond, is_banei)
    if rel == ">>": return 3.0
    elif rel == ">": return 1.0
    elif rel == "<<": return -3.0
    elif rel == "<": return -1.0
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

    def fetch_past5_data(self, race_id, water_mode=None):
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
            for i, td in enumerate(past_tds):
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

                    # 同条件なら無制限、異条件なら過去3走まで深掘り対象
                    if course == target_course and str(distance) == str(target_distance):
                        deep_dive_candidates.add(past_race_id)
                    elif i < 3:
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
# 3. 勝負づけ判定ロジック（ポイント制グラフ）
# ==========================================
def build_matchup_graph(past_races, target_course, target_distance, umaban_dict, is_banei):
    G = nx.DiGraph()
    runners = list(umaban_dict.keys())
    
    for r in runners:
        G.add_node(r)

    match_history = {u: {v: [] for v in runners} for u in runners}

    # 直接対決の収集
    for race in past_races:
        r_course = race['course']
        r_dist = race['distance']
        r_date = race['date']
        r_date_str = race['date_str']
        
        cond = determine_condition(target_course, target_distance, r_course, r_dist)
        horses_in_race = [h for h in race['horses'].items() if h[0] in umaban_dict or h[1] < 2.0]
        
        for i in range(len(horses_in_race)):
            for j in range(i + 1, len(horses_in_race)):
                h1, t1 = horses_in_race[i]
                h2, t2 = horses_in_race[j]
                
                diff = t1 - t2 # H1が早い場合 diff < 0
                pts_h1 = calculate_matchup_points(diff, cond, is_banei)
                
                hist_entry = {
                    'date': r_date, 'date_str': r_date_str, 'course': r_course, 'distance': r_dist,
                    'cond': cond, 'raw_diff': diff, 'pts_h1': pts_h1
                }
                
                if h1 in runners and h2 in runners:
                    match_history[h1][h2].append(hist_entry)
                    match_history[h2][h1].append({**hist_entry, 'raw_diff': -diff, 'pts_h1': -pts_h1})
                elif h1 in runners and h2 not in runners:
                    if not G.has_edge(h1, h2): G.add_edge(h1, h2, history=[])
                    G[h1][h2]['history'].append(hist_entry)
                elif h2 in runners and h1 not in runners:
                    if not G.has_edge(h2, h1): G.add_edge(h2, h1, history=[])
                    G[h2][h1]['history'].append({**hist_entry, 'raw_diff': -diff, 'pts_h1': -pts_h1})

    # 対戦スコアの集計
    for u in runners:
        for v in runners:
            if u == v or not match_history[u][v]: continue
            
            history = sorted(match_history[u][v], key=lambda x: x['date'] or datetime.min, reverse=True)
            total_score = 0.0
            
            for idx, h in enumerate(history):
                mult = 1.0
                if idx == 0: mult *= 1.5 # 前走（直近）ボーナス
                if is_older_than_1_year(h['date']): mult *= 0.7 # 1年以上前減衰
                
                total_score += h['pts_h1'] * mult
                
            G.add_edge(u, v, score=total_score, history=history, is_direct=True)

    return G, match_history

# ==========================================
# 4. リーグ戦評価 ＆ 👑ランク強制補正
# ==========================================
def evaluate_league(G, match_history, umaban_dict):
    runners = list(umaban_dict.keys())
    
    league_scores = {u: 0.0 for u in runners}
    opponents_count = {u: 0 for u in runners}

    # 1. リーグ戦ポイントの計算
    for u in runners:
        for v in runners:
            if u == v or not G.has_edge(u, v): continue
            
            opponents_count[u] += 1
            score = G[u][v]['score']
            
            if score >= 4.0: pts = 3.0
            elif score >= 1.0: pts = 1.0
            elif score > -1.0: pts = 0.2
            elif score <= -4.0: pts = -3.0
            else: pts = -1.5
            
            league_scores[u] += pts

    # 2. 初期ティア割り当て
    final_scores = {}
    for u in runners:
        if opponents_count[u] == 0:
            final_scores[u] = -999.0
            continue
        final_scores[u] = league_scores[u] / opponents_count[u]

    ranked = sorted([(u, s) for u, s in final_scores.items() if s != -999.0], key=lambda x: x[1], reverse=True)
    tier_map = {u: "C" for u in runners}
    
    if ranked:
        top_s = ranked[0][1]
        for u, s in ranked:
            if s >= 1.5 and (top_s - s) <= 1.0: tier_map[u] = "S"
            elif s >= 0.5: tier_map[u] = "A"
            elif s >= -1.0: tier_map[u] = "B"
            else: tier_map[u] = "C"

    # 3. 👑 負けていない馬の確実なランク引き上げ（下克上防止）
    tier_val = {"S": 4, "A": 3, "B": 2, "C": 1}
    val_tier = {4: "S", 3: "A", 2: "B", 1: "C"}
    
    changed = True
    while changed:
        changed = False
        for u in runners:
            for v in runners:
                if u == v or not match_history[u][v]: continue
                
                # uがvに一度も負けていないかチェック（pts_h1 < 0が一度もない）
                has_loss = any(h['pts_h1'] < 0 for h in match_history[u][v])
                if not has_loss:
                    t_u = tier_val[tier_map[u]]
                    t_v = tier_val[tier_map[v]]
                    
                    if t_u < t_v: # 負けていないのに相手よりランクが下なら引き上げる
                        tier_map[u] = val_tier[t_v]
                        changed = True

    unranked = [u for u in runners if final_scores[u] == -999.0]
    return tier_map, ranked, unranked

# ==========================================
# 5. プロ仕様HTMLレンダリング
# ==========================================
def build_html_output(tier_map, ranked, unranked, umaban_dict, match_history, G, is_banei):
    html = ["<div style='font-family: sans-serif; font-size:14px; color:#333;'>"]
    tier_colors = {"S": "#e74c3c", "A": "#e67e22", "B": "#f1c40f", "C": "#3498db"}
    runners = list(umaban_dict.keys())
    
    # S, 測定不能, A, B, C の順で出力
    display_order = ["S", "UNRANKED", "A", "B", "C"]
    
    for tier in display_order:
        if tier == "UNRANKED":
            if unranked:
                html.append("<h3 style='background-color:#95a5a6; color:white; padding:8px; border-radius:4px;'>❗ 測定不能（別路線）</h3>")
                for u in unranked:
                    html.append(f"<div style='margin-bottom: 15px; border-left: 4px solid #95a5a6; padding-left: 10px;'>")
                    html.append(f"  <strong style='font-size:1.1em;'>[{umaban_dict.get(u, '?')}] {u}</strong>")
                    html.append("</div>")
            continue
            
        horses_in_tier = [u for u, s in ranked if tier_map.get(u) == tier]
        if not horses_in_tier: continue
        
        html.append(f"<h3 style='background-color:{tier_colors[tier]}; color:white; padding:8px; border-radius:4px;'>🏆 {tier}ランク</h3>")
        
        for u in horses_in_tier:
            uma = umaban_dict.get(u, "?")
            html.append(f"<div style='margin-bottom: 15px; border-left: 4px solid {tier_colors[tier]}; padding-left: 10px;'>")
            html.append(f"  <strong style='font-size:1.1em;'>[{uma}] {u}</strong>")
            
            # --- 直接対決ブロック ---
            direct_races = {}
            w, d, l = 0, 0, 0
            
            for v in runners:
                if u == v or not match_history[u][v]: continue
                for hist in match_history[u][v]:
                    r_key = (hist['date_str'], hist['course'], hist['distance'], hist['cond'])
                    if r_key not in direct_races:
                        direct_races[r_key] = []
                    direct_races[r_key].append((v, hist['raw_diff'], hist['pts_h1'], hist['cond']))
                    
                    if hist['pts_h1'] > 0: w += 1
                    elif hist['pts_h1'] < 0: l += 1
                    else: d += 1
                    
            if direct_races:
                wdl_str = []
                if w: wdl_str.append(f"{w}勝")
                if d: wdl_str.append(f"{d}分")
                if l: wdl_str.append(f"{l}敗")
                
                html.append(f"<div style='margin-top:5px; font-size:0.9em; font-weight:bold;'>直接対決: {' '.join(wdl_str)}</div>")
                
                # レースごとの結果出力
                sorted_races = sorted(direct_races.items(), key=lambda x: x[0][0] or "", reverse=True)
                for (d_str, course, dist, cond), items in sorted_races:
                    cond_badge = " <span style='color:#e67e22;'>[同条件]</span>" if cond == 'A' else ""
                    html.append(f"<div style='margin-left:10px; font-size:0.85em; color:#555; margin-top:3px;'>🔍{d_str}の{course}{dist}{cond_badge}</div>")
                    
                    # 関係性ごとにグループ化
                    rels = {">>": [], ">": [], "＝": [], "<": [], "<<": []}
                    for v, diff, pts, h_cond in items:
                        rel = get_rel_str(diff, h_cond, is_banei)
                        v_uma = umaban_dict.get(v, "?")
                        rels[rel].append(f"[{v_uma}]{v}({diff:+.1f})")
                        
                    for rel_key in [">>", ">", "＝", "<", "<<"]:
                        if rels[rel_key]:
                            color = "#27ae60" if ">" in rel_key else "#888" if rel_key == "＝" else "#e74c3c"
                            html.append(f"<div style='margin-left:20px; font-size:0.85em;'>└ 本馬 <span style='color:{color}; font-weight:bold;'>{rel_key}</span> {' '.join(rels[rel_key])}</div>")

            # --- 隠れ馬ブロック ---
            indirect_paths = []
            for v in runners:
                if u == v or G.has_edge(u, v): continue
                for h in G.nodes():
                    if h in runners: continue
                    if G.has_edge(u, h) and G.has_edge(h, v):
                        # 最新の対戦を採用
                        uh_hist = G[u][h]['history'][0]
                        hv_hist = G[h][v]['history'][0]
                        tot_diff = uh_hist['raw_diff'] + hv_hist['raw_diff']
                        
                        rel = get_rel_str(tot_diff, 'C', is_banei)
                        v_uma = umaban_dict.get(v, "?")
                        
                        c1 = f"{uh_hist['course'][0]}{uh_hist['distance']}"
                        c2 = f"{hv_hist['course'][0]}{hv_hist['distance']}"
                        
                        indirect_paths.append(f"[{h}]本馬<span style='font-weight:bold;'>{rel}</span>[{v_uma}]{v}({tot_diff:+.1f})※{c1}/{c2}")
            
            if indirect_paths:
                html.append(f"<div style='margin-left:10px; margin-top:6px; font-size:0.85em; font-weight:bold; color:#8e44ad;'>🔗 隠れ馬経由の比較:</div>")
                for path in indirect_paths:
                    html.append(f"<div style='margin-left:20px; font-size:0.85em;'>{path}</div>")
            
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

st.title("🏇 競馬AI 究極相対評価 (ポイント勝負づけ版)")
st.caption("全馬との対戦をポイント化し、「負けない馬」を的確に上位ランクへ引き上げるプロ仕様のAIです。")

url_input = st.text_input("netkeibaのレースURL", placeholder="https://race.netkeiba.com/race/result.html?race_id=202405020111")
water_mode = st.selectbox("水分量フィルタ（ばんえい専用）", ["なし", "軽馬場（dry）", "重馬場（wet）"])

st.markdown("---")
selected_races = [i for i in range(1, 13) if st.checkbox(f"{i}R", key=f"chk_{i}")]
submitted = st.button("🚀 究極分析を開始", type="primary")

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
            r_title, t_course, t_dist, past_races, uma_dict, is_banei = scraper.fetch_past5_data(rid, wmode)
            if not uma_dict:
                results.append((r, f"{r}R (出走なし)", "データなし"))
                continue
                
            G, history = build_matchup_graph(past_races, t_course, t_dist, uma_dict, is_banei)
            tier_map, ranked, unranked = evaluate_league(G, history, uma_dict)
            html_out = build_html_output(tier_map, ranked, unranked, uma_dict, history, G, is_banei)
            
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
